import json

import anthropic
import pytest

from radar import compiler, profile as prof
from tests.conftest import COMPILE_MODEL, COMPILED, FIX, FakeClient, FakeGH, api_error

PROFILE = prof.load(FIX / "profile.yml")


def run(env, args, client=None, gh=None):
    out = []
    client = client or FakeClient()
    gh = gh if gh is not None else FakeGH()
    rc = env.main.main([*env.base, *args], client_factory=client, gh_run=gh, out=out.append)
    return rc, out, client, gh


def test_compile_writes_brief_with_profile_hash(env):
    env.compiled.unlink()
    rc, out, client, gh = run(env, ["--compile-profile"])
    assert rc == 0
    kw = client.calls[0]
    assert kw["model"] == COMPILE_MODEL
    assert kw["tool_choice"] == {"type": "tool", "name": "compiled_profile"}
    assert [t["name"] for t in kw["tools"]] == ["compiled_profile"]
    content = kw["messages"][0]["content"]
    assert "narrative:" in content and "targets:" in content and "scoring:" in content
    assert "identity" not in content  # only narrative + targets + scoring are sent
    doc = json.loads(env.compiled.read_text())
    assert doc["profile_sha256"] == prof.sha256(PROFILE)
    assert doc["model"] == COMPILE_MODEL
    assert {k: doc[k] for k in COMPILED} == COMPILED
    # synonyms already covered by a pattern are not suggested
    assert "  - BI Developer" in out and not any("Analytics Engineer" in o for o in out)
    assert gh.calls == []


def test_compiled_brief_unblocks_scoring_run(env):
    env.compiled.unlink()
    assert run(env, ["--compile-profile"])[0] == 0
    rc, *_ = run(env, ["--fixtures", str(FIX), "--companies", str(FIX / "companies.yml"), "--today", "2030-01-07"])
    assert rc == 0


def test_validate_rejects_bad_tool_output():
    with pytest.raises(compiler.CompileError, match="missing tiers 2"):
        compiler.validate({**COMPILED, "tier_weights": {"1": 1}}, PROFILE)
    with pytest.raises(compiler.CompileError, match="rubric"):
        compiler.validate({**COMPILED, "rubric": " "}, PROFILE)
    with pytest.raises(compiler.CompileError, match="tier_weights"):
        compiler.validate({**COMPILED, "tier_weights": {"1": "high", "2": 1}}, PROFILE)


def test_validate_drops_unknown_tiers_and_dedupes_synonyms():
    r = compiler.validate({**COMPILED, "tier_weights": {"2": 0.5, "1": 1, "9": 0.1},
                           "title_synonyms": ["A", "A", " "]}, PROFILE)
    assert r["tier_weights"] == {"1": 1.0, "2": 0.5} and r["title_synonyms"] == ["A"]


def test_invalid_tool_output_exits_1_and_writes_nothing(env):
    env.compiled.unlink()
    rc, *_ = run(env, ["--compile-profile"], FakeClient(compiled={"rubric": "x"}))
    assert rc == 1 and not env.compiled.exists()


def test_compile_credit_balance_files_alert(env):
    exc = api_error(anthropic.BadRequestError, 400, "Your credit balance is too low")
    rc, out, client, gh = run(env, ["--compile-profile"], FakeClient(score_exc=exc))
    assert rc == 2 and gh.issues[0]["title"] == "[ALERT] Anthropic API key 'example': credits (400)"


def test_compile_dry_run_makes_no_call(env):
    rc, out, client, gh = run(env, ["--compile-profile", "--dry-run"])
    assert rc == 0 and client.calls == [] and out[0].startswith(f"would compile with {COMPILE_MODEL}")


def test_load_matching():
    assert compiler.load_matching(FIX / "nope.json", "x") is None
