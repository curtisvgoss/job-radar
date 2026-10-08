"""Static checks on the ci/workflows templates."""

import re
from pathlib import Path

import pytest
import yaml

CI = Path(__file__).resolve().parent.parent / "ci" / "workflows"
FILES = sorted(CI.glob("*.yml"))
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$")
PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")


def test_templates_present():
    assert {f.name for f in FILES} == {"radar.yml", "resolve.yml", "test.yml"}


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_uses_pinned_to_full_sha_with_version_comment(path):
    uses = [USES.match(l) for l in path.read_text().splitlines()]
    uses = [m for m in uses if m]
    assert uses, f"{path.name}: no uses: lines found"
    for m in uses:
        ref, rest = m.group(1), m.group(2)
        assert PINNED.fullmatch(ref), f"{path.name}: unpinned uses: {ref}"
        assert re.fullmatch(r"\s+#\s*v\d+(\.\d+)*\s*", rest), f"{path.name}: {ref} lacks a '# vN' comment"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_explicit_permissions_and_no_pull_request_target(path):
    text = path.read_text()
    doc = yaml.safe_load(text)
    assert isinstance(doc.get("permissions"), dict) and doc["permissions"]
    assert "pull_request_target" not in text


def test_test_workflow_is_read_only():
    doc = yaml.safe_load((CI / "test.yml").read_text())
    assert doc["permissions"] == {"contents": "read"}
    assert "${{ secrets." not in (CI / "test.yml").read_text()


def test_api_key_only_in_radar_yml():
    for f in FILES:
        assert ("ANTHROPIC_API_KEY" in f.read_text()) == (f.name == "radar.yml"), f.name


@pytest.mark.parametrize("name", ["radar.yml", "resolve.yml"])
def test_instance_templates_install_engine_at_full_sha(name):
    text = (CI / name).read_text()
    assert 'uv pip install --system "git+https://github.com/${repo}@${sha}"' in text
    assert "engine_sha:[[:space:]]*\"?([0-9a-f]{40})" in text
    assert "uv sync" not in text


def test_radar_runs_engine_module():
    text = (CI / "radar.yml").read_text()
    assert "run: python -m radar.main\n" in text
    assert 'python -m radar.main --check-credentials --drill "$DRILL"' in text
