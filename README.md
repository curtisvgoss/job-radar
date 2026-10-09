# job-radar

A daily job-discovery agent that runs entirely on GitHub Actions. It pulls public Greenhouse, Lever
and Ashby job boards, filters postings with deterministic rules derived from a validated profile,
scores only the new survivors with Claude through a forced tool call, and files one ranked GitHub
Issue per day. When a credential fails or nears expiry, it files a named alert issue instead.

```
config.yml ─┐
profile.yml ┴─► validate ──► derive filter rules ─────────────────────────────┐
                    │                                                          │
                    └─► --compile-profile (once per profile edit) ─► state/profile.compiled.json
                                                                   (rubric, summary, tier weights,
                                                                    profile_sha256)
daily:  probe key ─► hash check ─► fetch boards ─► filter (titles · seniority · location · seen)
          │              │                                │
       [ALERT]        exit 3                     sort posted_at desc ─► score ≤ --limit
       exit 2                                              │
                                     digest (score ≥ min_score, top 60) ─► issue "Radar YYYY-MM-DD (N matches)"
                                                           │
                                         append scored uids to state/seen.json
```

## Design

- **Engine / instance split.** This repo is the engine: code, tests, workflow templates, an example
  profile. It holds no secrets, no state and no personal values. Each user runs a private *instance*
  repo holding `config.yml`, `profile.yml`, `config/credentials.yml`, `state/` and the workflows; the
  instance installs the engine at a full 40-character commit SHA, so an engine change reaches a
  daily run only when the instance owner bumps `engine_sha`.
- **Deterministic before probabilistic.** Every posting passes title, seniority, location and
  seen-state rules before any model call. Rules are derived mechanically from `profile.yml`; the
  model never decides what gets fetched or filtered.
- **Compile once, score cheaply.** `--compile-profile` sends the narrative, targets and scoring
  preferences to a larger model once and stores a rubric, candidate summary and per-tier weights.
  Daily scoring uses that brief as the system prompt for a small model. The brief is stamped with
  the profile's sha256; a scoring run against a changed profile exits 3 rather than score with a
  stale brief.
- **Structured output only.** Each model call offers a single tool (`compiled_profile`,
  `record_score`) and the tool input is validated; anything malformed is discarded. Scoring forces
  `record_score`; the compile call uses `tool_choice: auto` steered by its prompt (retried once if no
  call is made), because current Sonnet/Opus models reject forced tool use with a 400.
- **Untrusted input stays data.** Posting text is truncated to 6,000 chars and wrapped in
  `<posting>…</posting>`; embedded `<posting>` tags are neutralized and the system prompt states
  that the contents are never instructions.
- **At-least-once scoring.** Only postings that received a valid score are marked seen. Postings
  past `--limit` or whose call failed stay unseen and are retried next run.
- **Supply-chain hygiene.** Every `uses:` in the workflow templates is pinned to a full commit SHA,
  every workflow declares `permissions:`, the test workflow is `contents: read`, nothing uses
  `pull_request_target`, and the API key is referenced by one workflow only. Tests enforce this.

## Layout

| path | role |
|---|---|
| `radar/config.py` | `config.yml` over packaged `radar/defaults.yml`; unknown keys rejected |
| `radar/profile.py` | profile schema validation, filter-rule derivation, content hash |
| `radar/compiler.py` | `--compile-profile`: one `compiled_profile` tool call, request checked for known 400 causes |
| `radar/fetch.py` | one adapter per ATS → `{uid, company, title, location, remote, url, posted_at, comp, text}` |
| `radar/filter.py` | deterministic filter; annotates each kept posting with its best target tier |
| `radar/score.py` | one forced `record_score` call per posting |
| `radar/digest.py` | markdown table, score desc, ≥ `min_score`, max 60 rows |
| `radar/creds.py` | credential probe, expiry tracking, alert issues |
| `radar/main.py` | pipeline and CLI |
| `tools/resolve.py` | find a company's ATS board slugs |
| `ci/workflows/` | `radar.yml`, `resolve.yml` (instance templates), `test.yml` (engine CI) |
| `config/example.yml` | annotated example `profile.yml` for a fictional candidate |

## Setting up an instance

1. Create a **private** repo. Add `config.yml`:

   ```yaml
   engine_repo: <owner>/job-radar
   engine_sha: "<full 40-char commit of this repo>"   # never a tag or branch
   # optional overrides (defaults shown in radar/defaults.yml):
   # profile_path: profile.yml
   # companies_path: companies.yml       # used only if the profile has no companies list
   # credentials_path: config/credentials.yml
   # state_dir: state
   # compile_model: claude-sonnet-5-5
   # score_model: claude-haiku-5-5
   ```

2. Copy `config/example.yml` to `profile.yml` and rewrite it (schema below).
3. Add `config/credentials.yml` (format below) and `state/seen.json` containing `[]`.
4. Copy `ci/workflows/radar.yml` and `ci/workflows/resolve.yml` to `.github/workflows/`.
5. `gh secret set ANTHROPIC_API_KEY -R <owner>/<instance>`.
6. Actions → radar → Run workflow with **compile_profile** checked. This writes and commits
   `state/profile.compiled.json`, then runs the radar.

To upgrade the engine, change `engine_sha` and commit; nothing else moves.

## Profile schema

All top-level keys except `companies` are required; unknown keys anywhere are errors.

| key | shape | used for |
|---|---|---|
| `identity` | `{name, headline?, location?, links?}` | context |
| `status` | `{situation, notes?}` | context |
| `constraints` | `{locations: [str], remote_ok: bool, relocate?, comp_floor?: int, notes?}` | location filter |
| `targets` | `[{tier: int ≥ 1, label, patterns: [str]}]` | title filter, tier weights |
| `exclude` | `{seniority_words: [str], title_substrings: [str]}` | title filter |
| `industries` | `{prefer: [str], avoid: [str]}` | context |
| `skills` | `{core: [str], familiar?: [str]}` | context |
| `narrative` | prose | compile input |
| `scoring` | `{min_score?: 0-100 (default 60), priorities?, deal_breakers?}` | compile input, digest threshold |
| `companies` | `[{name, ats: greenhouse\|lever\|ashby, slug}]` | replaces `companies.yml` |

Derived filter rules, all case-insensitive:

- **include**: the title contains at least one pattern from the union of `targets[].patterns`; the
  posting takes the lowest tier it matches.
- **exclude**: any `exclude.title_substrings` substring, or any `exclude.seniority_words` whole word
  (`intern` drops "Engineering Intern", not "Internal Tools").
- **location**: `constraints.locations` empty → any; otherwise remote postings pass when
  `remote_ok`, others need a location substring match.

The hash covers the parsed profile, so comments and formatting do not force a recompile; any value
change does. Find board slugs with `python -m tools.resolve "Acme,Globex"` or the **resolve**
workflow (hits land in the job summary as ready-to-paste lines).

## Running

```
python -m radar.main                      # daily run (what the workflow does)
python -m radar.main --compile-profile    # after any profile edit
python -m radar.main --dry-run            # no API calls, no issue: per-company counts + kept rows
```

| flag | effect |
|---|---|
| `--config PATH` | instance config, default `config.yml`; relative paths resolve against its directory |
| `--dry-run` | no API calls, no issue; prints `company: fetched N, kept M`, then `company \| title \| location \| url` |
| `--companies PATH` | overrides the profile's `companies` and `companies_path` |
| `--fixtures DIR` | read saved payloads from `DIR/<ats>/<slug>.json` instead of the network |
| `--limit N` | max postings scored per run, default 150 |
| `--compile-profile` | compile the profile, write `state/profile.compiled.json`, list uncovered title synonyms |
| `--check-credentials` | probe + expiry only, then exit |
| `--today YYYY-MM-DD` | date override |
| `--drill key-rejected\|expiring` | fire a labelled practice alert |

Exit codes: 0 ok, 1 config/profile/compile error, 2 credential alert, 3 profile changed since the
last compile.

Try the engine without any instance:

```
uv sync
uv run pytest
uv run python -m radar.main --config tests/fixtures/config.yml --dry-run --fixtures tests/fixtures
```

## Credential alerts

`config/credentials.yml` (instance only) lists tracked credentials:

```yaml
- id: anthropic-api-key            # the id the probe reports against
  label: "Anthropic API key 'my-key'"
  used_by: "radar.yml, repo secret ANTHROPIC_API_KEY"
  console: "where to manage it"
  expires: 2030-01-31
  rotate: "1) ... 2) gh secret set ANTHROPIC_API_KEY -R {repo} ..."   # {repo} -> $GITHUB_REPOSITORY
```

Every non-dry run first probes the key with a free `models.list` call.

| condition | kind | effect |
|---|---|---|
| secret unset | `missing` | `[ALERT]` issue, exit 2 |
| HTTP 401 | `rejected` | `[ALERT]` issue, exit 2 |
| HTTP 403 | `forbidden` | `[ALERT]` issue, exit 2 |
| 400 "credit balance" while scoring or compiling | `credits` | stops at once, `[ALERT]`, exit 2 |
| network / 5xx after retries | none | warning, run continues |
| an entry expires in ≤ 14 days | | `[EXPIRING]` issue; comments at 7, 3, 2, 1 and ≤ 0 days, at most once per day; never fails the run |

An `[ALERT]` skips the digest and leaves state unsaved; exit 2 fails the workflow, so GitHub's
failure email is a second channel. Issues are assigned to the repo owner and labelled `alert` +
`cred:<id>`; at most one is open per credential and kind, and repeats become comments. A passing
probe closes open `rejected` / `forbidden` / `missing` alerts; `credits` alerts stay open because a
free probe says nothing about the balance. An `[EXPIRING]` issue closes once its entry's `expires`
is more than 14 days out.

Drills (Actions → radar → Run workflow → drill): `key-rejected` probes with the literal key
`sk-ant-api03-drill`; `expiring` sets today to the earliest `expires` minus 10 days. Drill issues
are titled `[DRILL] …`, labelled `drill`, never close anything and are never auto-closed.
`--dry-run --check-credentials` prints alerts instead of filing them.

## Cost guardrails

- Scoring: one small-model call per new posting; text truncated to 6,000 chars, `max_tokens` 400,
  roughly 2.5k input + 150 output tokens. `--limit` (default 150) caps calls per run, and the
  seen-state means each posting is scored once. Check current per-token prices for `score_model`
  and multiply: at Haiku-class pricing a capped run costs well under $1.
- Compile: one larger-model call per profile edit, `max_tokens` 4096.
- Deterministic filters run before any API call; tighten `targets[].patterns` and
  `constraints.locations` to cut volume, and use `--dry-run` to see exactly what would be scored.
- Use a dedicated Console workspace with a prepaid balance and a spend limit; exhaustion raises a
  `credits` alert instead of failing silently.

## License

MIT. See [LICENSE](LICENSE).
