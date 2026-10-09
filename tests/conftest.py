import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest
import yaml

FIX = Path(__file__).parent / "fixtures"
SCORE_MODEL = "test-score-model"
COMPILE_MODEL = "test-compile-model"
COMPILED = {
    "rubric": "90-100 ideal. 60-74 plausible. 0-39 poor.",
    "candidate_summary": "Test candidate. Data and ML engineering.",
    "title_synonyms": ["Analytics Engineer", "Data Platform Engineer", "BI Developer"],
    "tier_weights": {"1": 1.0, "2": 0.6},
}


def api_error(cls, status, message="error", body=None):
    resp = httpx.Response(status, request=httpx.Request("POST", "https://api.anthropic.com/v1/x"))
    return cls(message, response=resp, body=body)


class FakeClient:
    """Stands in for anthropic.Anthropic: models.list for the probe, messages.create for scoring."""

    def __init__(self, probe_exc=None, scores=None, score_exc=None, compiled=None):
        self.probe_exc, self.scores, self.score_exc = probe_exc, scores or {}, score_exc
        self.compiled = compiled if compiled is not None else COMPILED
        self.calls = []
        self.models = SimpleNamespace(list=self._list)
        self.messages = SimpleNamespace(create=self._create)
        self.api_key = None

    def __call__(self, api_key=None, **kw):  # acts as the client factory
        self.api_key = api_key
        return self

    def _list(self, **kw):
        if self.probe_exc:
            raise self.probe_exc
        return []

    def _create(self, **kw):
        self.calls.append(kw)
        if self.score_exc:
            raise self.score_exc
        if kw["tools"][0]["name"] == "compiled_profile":
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="compiled_profile",
                                                            input=self.compiled)])
        text = kw["messages"][0]["content"]
        for title, inp in self.scores.items():
            if f"title: {title}\n" in text:
                if isinstance(inp, Exception):
                    raise inp
                return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="record_score", input=inp)])
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="record_score",
                                                        input={"score": 10, "fit": "Meh.", "red_flags": []})])


class FakeGH:
    """Records gh CLI calls and keeps an in-memory issue list."""

    def __init__(self, issues=None):
        self.calls = []
        self.issues = issues or []

    def __call__(self, args, input=None):
        self.calls.append((args, input))
        if args[:2] == ["issue", "list"]:
            want = {args[i + 1] for i, a in enumerate(args) if a == "--label"}
            return json.dumps([{"number": i["number"], "title": i["title"],
                                "labels": [{"name": l} for l in i["labels"]]}
                               for i in self.issues if i["state"] == "open" and want <= set(i["labels"])])
        if args[:2] == ["issue", "create"]:
            labels = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
            n = len(self.issues) + 1
            self.issues.append({"number": n, "title": args[args.index("--title") + 1], "labels": labels,
                                "state": "open", "body": input, "args": args})
            return f"https://github.com/o/r/issues/{n}\n"
        if args[:2] == ["issue", "close"]:
            for i in self.issues:
                if i["number"] == int(args[2]):
                    i["state"] = "closed"
        if args[:2] == ["issue", "edit"]:
            for i in self.issues:
                if i["number"] == int(args[2]):
                    i["title"] = args[args.index("--title") + 1]
        return ""

    def cmds(self, *prefix):
        return [c for c in self.calls if c[0][: len(prefix)] == list(prefix)]


def write_config(tmp_path, **over) -> Path:
    cfg = {"profile_path": str(FIX / "profile.yml"), "companies_path": str(FIX / "companies.yml"),
           "credentials_path": str(FIX / "credentials.yml"), "state_dir": str(tmp_path / "state"),
           "compile_model": COMPILE_MODEL, "score_model": SCORE_MODEL, **over}
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(cfg))
    return path


def write_compiled(path: Path, profile_path: Path = FIX / "profile.yml", sha: str | None = None) -> None:
    from radar import compiler, profile
    p = profile.load(profile_path)
    compiler.write(path, COMPILED, sha or profile.sha256(p), COMPILE_MODEL)


@pytest.fixture
def env(monkeypatch, tmp_path):
    import radar.main as m
    cfg = write_config(tmp_path)
    state = tmp_path / "state"
    write_compiled(state / "profile.compiled.json")
    for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_REPOSITORY_OWNER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/job-radar")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "owner")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-secret-value")
    return SimpleNamespace(state=state / "seen.json", compiled=state / "profile.compiled.json", main=m,
                           config=cfg, tmp=tmp_path, base=["--config", str(cfg)])
