import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest
import yaml

from radar import compiler, creds, profile as prof
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
    assert kw["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
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


def default_models():
    return yaml.safe_load((Path(compiler.__file__).parent / "defaults.yml").read_text())


def test_default_models_are_plain_ids():
    d = default_models()
    for key in ("compile_model", "score_model"):
        assert re.fullmatch(r"claude-[a-z]+(-\d+)+", d[key]), (key, d[key])  # no date suffix, no whitespace


def test_built_compile_request_passes_static_checks():
    req = compiler.build_request(PROFILE, default_models()["compile_model"])
    assert compiler.check_request(req) == []
    assert req["tool_choice"]["type"] == "auto"
    json.dumps(req)


@pytest.mark.parametrize("mutate, problem", [
    (lambda r: r.update(model=""), "model"),
    (lambda r: r.update(model="claude-sonnet-5-5 "), "model"),
    (lambda r: r["tools"][0]["input_schema"]["properties"].update(x={"$ref": "#/defs/x"}), "'$ref'"),
    (lambda r: r["tools"][0]["input_schema"].update(required=["rubric", "nope"]), "not in properties: nope"),
    (lambda r: r["tools"][0]["input_schema"]["properties"].update(x={"type": "string", "format": "date"}),
     "'format'"),
    (lambda r: r["tools"][0]["input_schema"].update(type="array"), "type object"),
    (lambda r: r.update(tool_choice={"type": "tool", "name": "compiled_profile"}), "forced 'tool'"),
    (lambda r: r.update(tool_choice={"type": "any"}), "forced 'any'"),
    (lambda r: r.update(tool_choice={"type": "tool", "name": "other"}), "not in tools"),
    (lambda r: r.update(max_tokens=200_000), "max_tokens"),
    (lambda r: r.update(max_tokens=0), "max_tokens"),
    (lambda r: r["messages"][0].update(content="  "), "messages[0].content"),
    (lambda r: r.update(system=""), "system"),
    (lambda r: r.update(metadata={"when": object()}), "JSON-serializable"),
])
def test_check_request_flags_known_400_causes(mutate, problem):
    req = copy.deepcopy(compiler.build_request(PROFILE, "claude-sonnet-5-5"))
    mutate(req)
    errors = compiler.check_request(req)
    assert any(problem in e for e in errors), errors


def test_empty_profile_part_is_refused_before_any_call():
    client = FakeClient()
    with pytest.raises(compiler.CompileError, match="narrative"):
        compiler.compile_profile(client, {**PROFILE, "narrative": ""}, COMPILE_MODEL)
    assert client.calls == []


def test_compile_retries_once_when_no_tool_call():
    client = FakeClient()
    replies = [SimpleNamespace(content=[SimpleNamespace(type="text", text="Here is the brief.")])]
    real = client.messages.create
    client.messages.create = lambda **kw: replies.pop(0) if replies else real(**kw)
    assert compiler.compile_profile(client, PROFILE, COMPILE_MODEL)["rubric"] == COMPILED["rubric"]


def test_compile_gives_up_after_retry():
    calls = []
    client = SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: calls.append(kw) or SimpleNamespace(content=[])))
    with pytest.raises(compiler.CompileError, match="no compiled_profile tool call"):
        compiler.compile_profile(client, PROFILE, COMPILE_MODEL)
    assert len(calls) == compiler.ATTEMPTS


def test_compile_400_logs_error_type_and_message_not_key(env, caplog):
    body = {"type": "error", "error": {
        "type": "invalid_request_error",
        "message": 'tool_choice: type "tool" and "any" are not supported for this model. sk-ant-api03-leak'}}
    exc = api_error(anthropic.BadRequestError, 400, "bad", body=body)
    rc, *_ = run(env, ["--compile-profile"], FakeClient(score_exc=exc))
    assert rc == 1
    assert ('compile failed: HTTP 400 invalid_request_error: tool_choice: type "tool" and "any" are not '
            "supported for this model.") in caplog.text
    assert "sk-ant-api03-leak" not in caplog.text and "sk-ant-test-secret-value" not in caplog.text


def test_compile_5xx_logs_error_body(env, caplog):
    body = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    exc = api_error(anthropic.InternalServerError, 529, "overloaded", body=body)
    rc, *_ = run(env, ["--compile-profile"], FakeClient(score_exc=exc))
    assert rc == 1 and "compile failed: HTTP 529 overloaded_error: Overloaded" in caplog.text


def test_api_error_detail_without_body_uses_message():
    e = api_error(anthropic.NotFoundError, 404, "model: claude-nope")
    assert creds.api_error_detail(e) == "HTTP 404 error: model: claude-nope"
