from datetime import date, timedelta

import anthropic
import pytest

from radar import creds
from tests.conftest import FakeClient, FakeGH, api_error

KEY = "sk-ant-test-secret-value"
ENTRY_LABEL = "Anthropic API key 'example'"
TOKEN_LABEL = "GitHub token 'example'"
API_EXP = date(2030, 6, 15)    # fixture credentials.yml
TOKEN_EXP = date(2030, 6, 14)
BEFORE = "2030-01-01"


def before(n: int, exp: date = TOKEN_EXP) -> str:
    """Date n days before exp."""
    return (exp - timedelta(days=n)).isoformat()


def run(env, args, client=None, gh=None):
    out = []
    client = client or FakeClient()
    gh = gh if gh is not None else FakeGH()
    rc = env.main.main([*env.base, *args], client_factory=client, gh_run=gh, out=out.append)
    return rc, out, client, gh


# -- probe ------------------------------------------------------------------------------------

def test_probe_missing():
    r = creds.probe(None, FakeClient())
    assert r.alert.kind == "missing" and r.alert.status is None and not r.passed


@pytest.mark.parametrize("cls,status,kind", [(anthropic.AuthenticationError, 401, "rejected"),
                                             (anthropic.PermissionDeniedError, 403, "forbidden")])
def test_probe_rejected_forbidden(cls, status, kind):
    r = creds.probe(KEY, FakeClient(probe_exc=api_error(cls, status)))
    assert r.alert.kind == kind and r.alert.status == status


@pytest.mark.parametrize("exc", [api_error(anthropic.InternalServerError, 503),
                                 anthropic.APIConnectionError(request=None)])
def test_probe_inconclusive_continues(exc):
    r = creds.probe(KEY, FakeClient(probe_exc=exc))
    assert r.alert is None and not r.passed


def test_probe_pass():
    assert creds.probe(KEY, FakeClient()).passed


# -- verdicts ---------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["rejected", "forbidden"])
def test_verdict_both_sides_of_expiry(kind):
    exp = API_EXP
    assert creds.verdict(kind, exp - timedelta(days=1), exp).startswith("Rejected before its scheduled expiry")
    assert creds.verdict(kind, exp, exp) == "Expired as scheduled on 2030-06-15"
    assert creds.verdict(kind, exp + timedelta(days=30), exp) == "Expired as scheduled on 2030-06-15"


def test_verdict_credits_missing():
    assert creds.verdict("credits", date(2030, 1, 1), API_EXP) == "Prepaid balance exhausted: Console -> Billing"
    assert creds.verdict("missing", date(2030, 1, 1), API_EXP) == "Secret ANTHROPIC_API_KEY is not set in this repo"


# -- alerts via main --------------------------------------------------------------------------

def test_rejected_alert_issue_and_dedupe(env):
    gh = FakeGH()
    client = FakeClient(probe_exc=api_error(anthropic.AuthenticationError, 401))
    rc, *_ = run(env, ["--check-credentials", "--today", BEFORE], client, gh)
    assert rc == 2
    issue = gh.issues[0]
    assert issue["title"] == f"[ALERT] {ENTRY_LABEL}: rejected (401)"
    assert issue["labels"] == ["alert", "cred:anthropic-api-key"]
    assert issue["args"][issue["args"].index("--assignee") + 1] == "owner"
    body = issue["body"]
    assert "Rejected before its scheduled expiry" in body
    assert "https://github.com/owner/job-radar/actions/runs/42" in body
    assert "gh secret set ANTHROPIC_API_KEY -R owner/job-radar" in body
    assert "**HTTP status:** 401" in body
    assert ["label", "create", "cred:anthropic-api-key", "--force"] in [c[0] for c in gh.calls]
    # second failure comments instead of opening another issue
    rc, *_ = run(env, ["--check-credentials", "--today", "2030-01-02"], client, gh)
    assert len(gh.issues) == 1 and len(gh.cmds("issue", "comment")) == 1


def test_expired_verdict_after_expiry(env):
    gh = FakeGH()
    client = FakeClient(probe_exc=api_error(anthropic.AuthenticationError, 401))
    run(env, ["--check-credentials", "--today", "2030-06-16"], client, gh)
    alert = [i for i in gh.issues if "[ALERT]" in i["title"]][0]
    assert "Expired as scheduled on 2030-06-15" in alert["body"]


def test_key_never_leaks(env, capsys):
    gh = FakeGH()
    client = FakeClient(probe_exc=api_error(anthropic.AuthenticationError, 401))
    _, out, *_ = run(env, ["--check-credentials", "--today", BEFORE], client, gh)
    blob = repr(gh.calls) + "\n".join(out) + capsys.readouterr().err
    assert "sk-ant-test" not in blob and "secret-value" not in blob


def test_probe_pass_closes_non_drill_alerts_only(env):
    labels = ["alert", "cred:anthropic-api-key"]
    gh = FakeGH(issues=[
        {"number": 1, "title": f"[ALERT] {ENTRY_LABEL}: rejected (401)", "labels": labels, "state": "open"},
        {"number": 2, "title": f"[DRILL] [ALERT] {ENTRY_LABEL}: rejected (401)", "labels": labels + ["drill"], "state": "open"},
        {"number": 3, "title": f"[ALERT] {ENTRY_LABEL}: missing (n/a)", "labels": labels, "state": "open"},
        {"number": 4, "title": f"[ALERT] {ENTRY_LABEL}: forbidden (403)", "labels": labels, "state": "open"},
    ])
    rc, *_ = run(env, ["--check-credentials", "--today", BEFORE], FakeClient(), gh)
    assert rc == 0
    assert [i["state"] for i in gh.issues] == ["closed", "open", "closed", "closed"]
    close = gh.cmds("issue", "close")[0][0]
    assert close[-1] == "Resolved by run https://github.com/owner/job-radar/actions/runs/42"


def test_probe_pass_never_closes_credits_alert(env):
    gh = FakeGH(issues=[{"number": 1, "title": f"[ALERT] {ENTRY_LABEL}: credits (400)",
                         "labels": ["alert", "cred:anthropic-api-key"], "state": "open"}])
    rc, *_ = run(env, ["--check-credentials", "--today", BEFORE], FakeClient(), gh)
    assert rc == 0 and gh.issues[0]["state"] == "open" and gh.cmds("issue", "close") == []


def test_dry_run_check_credentials_prints_instead_of_filing(env):
    gh = FakeGH()
    rc, out, *_ = run(env, ["--dry-run", "--check-credentials", "--today", BEFORE],
                      FakeClient(probe_exc=api_error(anthropic.PermissionDeniedError, 403)), gh)
    assert rc == 2 and gh.calls == []
    assert out[0].startswith(f"[ALERT] {ENTRY_LABEL}: forbidden (403)")


# -- expiry -----------------------------------------------------------------------------------
# Fixture credentials: example-token expires 2030-06-14, anthropic-api-key 2030-06-15.

def expiring(gh):
    return [i for i in gh.issues if "[EXPIRING]" in i["title"]]


def token_comments(gh):
    n = next(str(i["number"]) for i in expiring(gh) if TOKEN_LABEL in i["title"])
    return [c for c in gh.cmds("issue", "comment") if c[0][2] == n]


def test_expiry_quiet_outside_window(env):
    gh = FakeGH()
    run(env, ["--check-credentials", "--today", before(15)], FakeClient(), gh)  # 15 and 16 days
    assert expiring(gh) == []


def test_expiry_opens_at_14_then_comments_on_thresholds(env):
    gh = FakeGH()
    run(env, ["--check-credentials", "--today", before(14)], FakeClient(), gh)  # token 14, api 15
    assert [i["title"] for i in expiring(gh)] == [f"[EXPIRING] {TOKEN_LABEL}: 14 days (2030-06-14)"]
    assert expiring(gh)[0]["labels"] == ["alert", "cred:example-token"]
    for n in range(13, -2, -1):
        prev = len(token_comments(gh))
        run(env, ["--check-credentials", "--today", before(n)], FakeClient(), gh)
        assert len(token_comments(gh)) - prev == (1 if n in (7, 3, 2, 1) or n <= 0 else 0), n
    assert expiring(gh)[0]["title"] == f"[EXPIRING] {TOKEN_LABEL}: -1 days (2030-06-14)"
    assert sum(1 for i in expiring(gh) if TOKEN_LABEL in i["title"]) == 1


@pytest.mark.parametrize("n", [7, 0, -3])
def test_expiry_threshold_comment_once_per_day(env, n):
    gh = FakeGH()
    run(env, ["--check-credentials", "--today", before(n + 1)], FakeClient(), gh)
    for _ in range(3):  # reruns on the same day
        run(env, ["--check-credentials", "--today", before(n)], FakeClient(), gh)
    assert len(token_comments(gh)) == 1


def test_expiry_closes_once_rotated(env):
    gh = FakeGH()
    run(env, ["--check-credentials", "--today", before(5)], FakeClient(), gh)
    assert len(expiring(gh)) == 2
    run(env, ["--check-credentials", "--today", before(30)], FakeClient(), gh)  # > 14 days out again
    assert all(i["state"] == "closed" for i in expiring(gh))


def test_expiring_never_fails_run(env):
    rc, *_ = run(env, ["--check-credentials", "--today", "2030-08-01"], FakeClient(), FakeGH())
    assert rc == 0


# -- drills -----------------------------------------------------------------------------------

def test_drill_key_rejected_uses_literal_key_and_labels(env):
    gh = FakeGH()
    client = FakeClient(probe_exc=api_error(anthropic.AuthenticationError, 401))
    rc, *_ = run(env, ["--check-credentials", "--drill", "key-rejected"], client, gh)
    assert rc == 2 and client.api_key == "sk-ant-api03-drill"
    assert gh.issues[0]["title"] == f"[DRILL] [ALERT] {ENTRY_LABEL}: rejected (401)"
    assert gh.issues[0]["labels"] == ["alert", "cred:anthropic-api-key", "drill"]
    assert gh.cmds("issue", "close") == []


def test_drill_expiring_sets_today_and_never_closes(env):
    gh = FakeGH(issues=[{"number": 1, "title": f"[EXPIRING] {TOKEN_LABEL}: 3 days (2030-06-14)",
                         "labels": ["alert", "cred:example-token"], "state": "open"}])
    rc, *_ = run(env, ["--check-credentials", "--drill", "expiring"], FakeClient(), gh)
    assert rc == 0
    titles = sorted(i["title"] for i in gh.issues[1:])
    assert titles == [f"[DRILL] [EXPIRING] {ENTRY_LABEL}: 11 days (2030-06-15)",
                      f"[DRILL] [EXPIRING] {TOKEN_LABEL}: 10 days (2030-06-14)"]
    assert all("drill" in i["labels"] for i in gh.issues[1:])
    assert gh.issues[0]["state"] == "open" and gh.cmds("issue", "close") == []
