"""job-radar pipeline: probe -> fetch -> filter -> sort -> score -> digest -> issue -> state.

Runs from an instance repo root: config.yml (optional; engine defaults otherwise) names the profile,
credentials and state paths, and the compile/score models.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import anthropic
import yaml

from radar import compiler, config, creds, digest, fetch, gh
from radar import filter as flt
from radar import profile as prof
from radar.creds import Alerts, CredentialAlert
from radar.score import score_all

log = logging.getLogger("radar")

EXIT_ALERT = 2
EXIT_PROFILE = 3
PROFILE_CHANGED = "profile changed; run --compile-profile"


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="radar.main")
    ap.add_argument("--config", default="config.yml", help="instance config (default config.yml)")
    ap.add_argument("--dry-run", action="store_true", help="no API calls, no issue; print what would be scored")
    ap.add_argument("--companies", help="companies YAML; overrides the profile's companies and companies_path")
    ap.add_argument("--fixtures", type=Path, help="read saved payloads from DIR/<ats>/<slug>.json")
    ap.add_argument("--limit", type=int, default=150, help="max postings scored per run")
    ap.add_argument("--compile-profile", action="store_true", help="compile the profile into the scoring brief, then exit")
    ap.add_argument("--check-credentials", action="store_true", help="probe + expiry only, then exit")
    ap.add_argument("--today", type=date.fromisoformat, help="override today's date (YYYY-MM-DD)")
    ap.add_argument("--drill", choices=["key-rejected", "expiring"], help="fire a labelled practice alert")
    return ap.parse_args(argv)


def load_yaml(path):
    return yaml.safe_load(Path(path).read_text()) or {}


def load_seen(path: Path) -> set[str]:
    return set(json.loads(path.read_text())) if path.exists() else set()


def save_seen(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(seen), indent=0) + "\n")


def companies_for(args, cfg: config.Config, profile: dict) -> list[dict]:
    if args.companies:
        return load_yaml(args.companies) or []
    if "companies" in profile:
        return profile["companies"]
    if not cfg.companies.exists():
        raise config.ConfigError(f"no companies: add companies to the profile or create {cfg.companies}")
    return load_yaml(cfg.companies) or []


def check_credentials(entries, alerts: Alerts, client_factory) -> int:
    r = creds.probe(os.environ.get("ANTHROPIC_API_KEY"), client_factory)
    rc = 0
    if r.alert:
        alerts.alert(r.alert)
        rc = EXIT_ALERT
    elif r.passed:
        alerts.resolve(creds.API_KEY_ID)
    creds.expiry(entries, alerts)
    return rc


def run_drill(kind: str, entries, alerts: Alerts, client_factory) -> int:
    if kind == "key-rejected":
        r = creds.probe(creds.DRILL_KEY, client_factory)
        if r.alert:
            alerts.alert(r.alert)
            return EXIT_ALERT
        log.warning("drill: probe with the drill key was not rejected (inconclusive); no alert filed")
        return 0
    creds.expiry(entries, alerts)
    return 0


def compile_cmd(args, cfg: config.Config, profile: dict, alerts: Alerts, client_factory, out) -> int:
    if args.dry_run:
        out(f"would compile with {cfg.compile_model}:\n{compiler.request(profile)}")
        return 0
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log.error("ANTHROPIC_API_KEY is not set")
        return 1
    client = client_factory(api_key=key, max_retries=2)
    try:
        compiled = compiler.compile_profile(client, profile, cfg.compile_model)
    except anthropic.BadRequestError as e:
        if creds.is_credit_error(e):
            alerts.alert(CredentialAlert(creds.API_KEY_ID, "credits", e.status_code,
                                         "Profile compile call refused: credit balance too low"))
            return EXIT_ALERT
        log.error("compile failed: %s", creds.api_error_detail(e))
        return 1
    except anthropic.APIStatusError as e:
        log.error("compile failed: %s", creds.api_error_detail(e))
        return 1
    except anthropic.APIConnectionError as e:
        log.error("compile failed: %s", type(e).__name__)
        return 1
    except compiler.CompileError as e:
        log.error("compile failed: %s", e)
        return 1
    compiler.write(cfg.compiled, compiled, prof.sha256(profile), cfg.compile_model)
    out(f"wrote {cfg.compiled}")
    covered = prof.rules(profile).include
    new = [s for s in compiled["title_synonyms"] if not any(p in s.lower() for p in covered)]
    if new:
        out("title synonyms not matched by any targets[].patterns (add the ones you want):")
        for s in new:
            out(f"  - {s}")
    return 0


def main(argv=None, *, client_factory=anthropic.Anthropic, gh_run=gh.run, out=print) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    args = parse_args(argv)
    try:
        return _main(args, client_factory, gh_run, out)
    except (config.ConfigError, prof.ProfileError, FileNotFoundError) as e:
        log.error("%s", e)
        return 1


def _main(args, client_factory, gh_run, out) -> int:
    cfg = config.load(args.config)
    needs_creds = args.drill or args.check_credentials or not args.dry_run
    if needs_creds and not cfg.credentials.exists():
        raise config.ConfigError(f"credentials file not found: {cfg.credentials}")
    entries = creds.load(cfg.credentials) if cfg.credentials.exists() else []
    today = args.today or creds.utc_today()
    if args.drill == "expiring":
        today = min(e["expires"] for e in entries) - timedelta(days=10)
    alerts = Alerts(entries, today, dry_run=args.dry_run, drill=bool(args.drill), gh_run=gh_run, out=out)

    if args.drill:
        return run_drill(args.drill, entries, alerts, client_factory)
    if args.check_credentials:
        return check_credentials(entries, alerts, client_factory)

    profile = prof.load(cfg.profile)
    if args.compile_profile:
        return compile_cmd(args, cfg, profile, alerts, client_factory, out)

    compiled = None
    if not args.dry_run:
        rc = check_credentials(entries, alerts, client_factory)
        if rc:
            return rc
        compiled = compiler.load_matching(cfg.compiled, prof.sha256(profile))
        if compiled is None:
            log.error(PROFILE_CHANGED)
            return EXIT_PROFILE

    companies = companies_for(args, cfg, profile)
    seen = load_seen(cfg.seen)

    postings, fetched = fetch.fetch_all(companies, fixtures=args.fixtures)
    kept = flt.apply(postings, prof.rules(profile), seen)
    kept.sort(key=lambda p: p["posted_at"] or "", reverse=True)

    if args.dry_run:
        for name, n in fetched.items():
            out(f"{name}: fetched {n}, kept {sum(p['company'] == name for p in kept)}")
        for p in kept:
            out(f"{p['company']} | {p['title']} | {p['location']} | {p['url']}")
        return 0

    client = client_factory(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=2)
    batch = kept[: args.limit]
    log.info("fetched %d, kept %d, scoring %d with %s", len(postings), len(kept), len(batch), cfg.score_model)
    try:
        scored = score_all(client, compiled, cfg.score_model, batch)
    except CredentialAlert as a:
        alerts.alert(a)
        return EXIT_ALERT

    rows = digest.matches(scored, prof.min_score(profile))
    if rows:
        url = gh.create_issue(f"Radar {today.isoformat()} ({len(rows)} matches)", digest.render(rows), gh=gh_run)
        log.info("filed %s", url)
    else:
        log.info("no matches; no issue filed")

    save_seen(cfg.seen, seen | {p["uid"] for p in scored})
    log.info("scored %d of %d; %d left unseen", len(scored), len(kept), len(kept) - len(scored))
    return 0


if __name__ == "__main__":
    sys.exit(main())
