import json

import anthropic

from tests.conftest import FIX, SCORE_MODEL, FakeClient, FakeGH, api_error, write_compiled

ARGS = ["--fixtures", str(FIX), "--today", "2030-01-07"]


def run(env, extra=(), client=None, gh=None):
    out = []
    client = client or FakeClient()
    gh = gh if gh is not None else FakeGH()
    rc = env.main.main([*env.base, *ARGS, *extra], client_factory=client, gh_run=gh, out=out.append)
    return rc, out, client, gh


def test_dry_run_prints_counts_and_rows_without_text_or_calls(env):
    rc, out, client, gh = run(env, ["--dry-run"])
    assert rc == 0
    assert out[:4] == ["Acme: fetched 4, kept 2", "Globex: fetched 2, kept 1",
                       "Initech: fetched 3, kept 1", "Broken Co: fetched 0, kept 0"]
    rows = out[4:]
    assert len(rows) == 4 and rows[0].startswith("Initech | Analytics Engineer |")  # posted_at desc
    joined = "\n".join(out)
    assert "Build pipelines" not in joined and "Ignore previous" not in joined
    assert client.calls == [] and client.api_key is None and gh.calls == []
    assert not env.state.exists()


def test_full_run_files_issue_and_saves_only_scored(env):
    scores = {"Analytics Engineer": {"score": 88, "fit": "Strong.", "red_flags": []},
              "Quant Analyst": {"score": 61, "fit": "Ok.", "red_flags": []},
              "Senior Data Engineer": api_error(anthropic.InternalServerError, 500)}
    rc, out, client, gh = run(env, ["--limit", "3"], client=FakeClient(scores=scores))
    assert rc == 0
    created = gh.cmds("issue", "create")
    assert len(created) == 1
    args, body = created[0]
    assert args[args.index("--title") + 1] == "Radar 2030-01-07 (2 matches)"
    assert args[args.index("--assignee") + 1] == "owner"
    assert body.index("Analytics Engineer") < body.index("Quant Analyst")
    # limit 3 by posted_at desc: Initech, Acme 1003, Acme 1001 (errors -> unseen); Globex (2025) capped out
    seen = set(json.loads(env.state.read_text()))
    assert seen == {"ashby:initech:c3d4-0001", "greenhouse:acme:1003"}
    assert client.api_key == "sk-ant-test-secret-value"
    assert {c["model"] for c in client.calls} == {SCORE_MODEL}
    assert "tier 1: 1" in client.calls[0]["system"] and "Matched target: tier 1 (Data)" in client.calls[0]["messages"][0]["content"]


def test_no_issue_when_zero_matches(env):
    rc, out, client, gh = run(env)
    assert rc == 0 and gh.cmds("issue", "create") == []
    assert len(json.loads(env.state.read_text())) == 4


def test_seen_uids_skipped_next_run(env):
    env.state.write_text(json.dumps(["ashby:initech:c3d4-0001"]))
    rc, out, client, gh = run(env, ["--dry-run"])
    assert not any(r.startswith("Initech | Analytics") for r in out)


def test_credits_alert_skips_digest_and_state(env):
    exc = api_error(anthropic.BadRequestError, 400, "Your credit balance is too low")
    rc, out, client, gh = run(env, client=FakeClient(score_exc=exc))
    assert rc == 2
    titles = [i["title"] for i in gh.issues]
    assert titles == ["[ALERT] Anthropic API key 'example': credits (400)"]
    assert "Prepaid balance exhausted: Console -> Billing" in gh.issues[0]["body"]
    assert len(client.calls) == 1
    assert not env.state.exists()


def test_probe_failure_stops_before_fetch(env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    rc, out, client, gh = run(env)
    assert rc == 2 and client.calls == []
    assert gh.issues[0]["title"] == "[ALERT] Anthropic API key 'example': missing (n/a)"
    assert not env.state.exists()


def test_profile_hash_mismatch_exits_3(env, caplog):
    write_compiled(env.compiled, sha="0" * 64)
    rc, out, client, gh = run(env)
    assert rc == 3 and client.calls == [] and gh.cmds("issue", "create") == []
    assert "profile changed; run --compile-profile" in caplog.text
    assert not env.state.exists()


def test_missing_compiled_exits_3(env):
    env.compiled.unlink()
    rc, *_ = run(env)
    assert rc == 3


def test_dry_run_ignores_compiled_hash(env):
    env.compiled.unlink()
    rc, out, *_ = run(env, ["--dry-run"])
    assert rc == 0 and out


def test_profile_companies_replace_companies_yml(env, tmp_path):
    import yaml
    p = yaml.safe_load((FIX / "profile.yml").read_text())
    p["companies"] = [{"name": "Globex", "ats": "lever", "slug": "globex"}]
    (tmp_path / "profile.yml").write_text(yaml.safe_dump(p))
    from tests.conftest import write_config
    cfg = write_config(tmp_path, profile_path=str(tmp_path / "profile.yml"))
    out = []
    rc = env.main.main(["--config", str(cfg), "--fixtures", str(FIX), "--dry-run"], out=out.append)
    assert rc == 0 and out[0] == "Globex: fetched 2, kept 1"
    assert len(out) == 2


def test_invalid_profile_exits_1(env, tmp_path, caplog):
    (tmp_path / "bad.yml").write_text("identity: {name: X}\nsurprise: 1\n")
    from tests.conftest import write_config
    cfg = write_config(tmp_path, profile_path=str(tmp_path / "bad.yml"))
    rc = env.main.main(["--config", str(cfg), "--dry-run"], out=lambda s: None)
    assert rc == 1 and "surprise: unknown key" in caplog.text
