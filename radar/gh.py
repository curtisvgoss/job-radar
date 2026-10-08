"""Thin wrapper over the gh CLI (auth via env GH_TOKEN)."""

from __future__ import annotations

import json
import os
import subprocess


def run(args: list[str], input: str | None = None) -> str:
    r = subprocess.run(["gh", *args], input=input, text=True, capture_output=True, check=True)
    return r.stdout


def assignee_args() -> list[str]:
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER")
    return ["--assignee", owner] if owner else []


def create_issue(title: str, body: str, labels: list[str] = (), gh=run) -> str:
    args = ["issue", "create", "--title", title, "--body-file", "-", *assignee_args()]
    for label in labels:
        args += ["--label", label]
    return gh(args, input=body).strip()


def list_open(labels: list[str], gh=run) -> list[dict]:
    args = ["issue", "list", "--state", "open", "--limit", "100", "--json", "number,title,labels"]
    for label in labels:
        args += ["--label", label]
    out = gh(args)
    issues = json.loads(out or "[]")
    for i in issues:
        i["labels"] = {l["name"] if isinstance(l, dict) else l for l in i.get("labels", [])}
    return issues
