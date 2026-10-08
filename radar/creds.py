"""Credential probe, expiry tracking and alert issues.

Never prints, logs or embeds any part of a key.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import anthropic
import yaml

from radar import gh

log = logging.getLogger(__name__)

API_KEY_ID = "anthropic-api-key"
DRILL_KEY = "sk-ant-api03-drill"
EXPIRY_WINDOW = 14
COMMENT_DAYS = {7, 3, 2, 1}
RESOLVABLE = ("rejected", "forbidden", "missing")  # a passing probe proves these fixed; never "credits"


class CredentialAlert(Exception):
    def __init__(self, cred_id: str, kind: str, status: int | None, what: str):
        super().__init__(f"{cred_id}: {kind}")
        self.cred_id, self.kind, self.status, self.what = cred_id, kind, status, what


@dataclass
class ProbeResult:
    alert: CredentialAlert | None
    passed: bool


def load(path: str | Path) -> list[dict]:
    entries = yaml.safe_load(Path(path).read_text()) or []
    for e in entries:
        if not isinstance(e["expires"], date):
            e["expires"] = date.fromisoformat(str(e["expires"]))
    return entries


def is_credit_error(e: Exception) -> bool:
    return "credit balance" in str(e).lower()


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def run_url() -> str:
    env = os.environ
    if all(env.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")):
        return f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"
    return "(local run, no run URL)"


def rotate_text(entry: dict) -> str:
    return entry["rotate"].replace("{repo}", os.environ.get("GITHUB_REPOSITORY", "{repo}"))


def verdict(kind: str, today: date, expires: date) -> str:
    if kind in ("rejected", "forbidden"):
        if today >= expires:
            return f"Expired as scheduled on {expires}"
        return "Rejected before its scheduled expiry: revoked, deleted, or the secret is wrong"
    if kind == "credits":
        return "Prepaid balance exhausted: Console -> Billing"
    if kind == "missing":
        return "Secret ANTHROPIC_API_KEY is not set in this repo"
    return kind


def probe(api_key: str | None, client_factory=anthropic.Anthropic) -> ProbeResult:
    """Validate the Anthropic key with a free models.list call."""
    if not api_key:
        return ProbeResult(CredentialAlert(API_KEY_ID, "missing", None,
                                           "ANTHROPIC_API_KEY is empty or absent in the run environment"), False)
    client = client_factory(api_key=api_key, max_retries=2)
    try:
        client.models.list(limit=1)
    except anthropic.AuthenticationError as e:
        return ProbeResult(CredentialAlert(API_KEY_ID, "rejected", e.status_code,
                                           "Anthropic API probe (GET /v1/models) was rejected: authentication_error"), False)
    except anthropic.PermissionDeniedError as e:
        return ProbeResult(CredentialAlert(API_KEY_ID, "forbidden", e.status_code,
                                           "Anthropic API probe (GET /v1/models) was refused: permission_error"), False)
    except anthropic.APIStatusError as e:
        log.warning("credential probe inconclusive: HTTP %s; continuing", e.status_code)
        return ProbeResult(None, False)
    except anthropic.APIConnectionError as e:
        log.warning("credential probe inconclusive: %s; continuing", type(e).__name__)
        return ProbeResult(None, False)
    return ProbeResult(None, True)


class Alerts:
    """Files alert issues (or prints them under --dry-run). Drill mode prefixes titles and never closes."""

    def __init__(self, entries: list[dict], today: date, *, dry_run=False, drill=False, gh_run=gh.run, out=print):
        self.entries = {e["id"]: e for e in entries}
        self.today, self.dry_run, self.drill = today, dry_run, drill
        self.gh, self.out = gh_run, out
        self._labels_made: set[str] = set()

    # -- helpers ---------------------------------------------------------------------------
    def _title(self, t: str) -> str:
        return f"[DRILL] {t}" if self.drill else t

    def _labels(self, cred_id: str) -> list[str]:
        return ["alert", f"cred:{cred_id}"] + (["drill"] if self.drill else [])

    def _ensure_labels(self, labels: list[str]) -> None:
        for label in labels:
            if label not in self._labels_made:
                self.gh(["label", "create", label, "--force"])
                self._labels_made.add(label)

    def _open(self, cred_id: str, marker: str) -> list[dict]:
        """Open issues for this credential whose title carries marker, matching drill mode."""
        try:
            issues = gh.list_open(["alert", f"cred:{cred_id}"], gh=self.gh)
        except subprocess.CalledProcessError:
            return []
        return [i for i in issues if marker in i["title"] and (("drill" in i["labels"]) == self.drill)]

    def _body(self, entry: dict, what: str, status, verdict_text: str) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        return (
            f"**What failed:** {what}\n\n"
            f"**HTTP status:** {status if status is not None else 'n/a'}\n\n"
            f"**When (UTC):** {now}\n\n"
            f"**Run:** {run_url()}\n\n"
            f"**Verdict:** {verdict_text}\n\n"
            f"**Credential:** {entry['label']} (used by {entry['used_by']}; console: {entry['console']}; "
            f"expires {entry['expires']})\n\n"
            f"**Rotate:**\n{rotate_text(entry)}\n"
            + ("\n_This is a drill._\n" if self.drill else "")
        )

    def _file(self, cred_id: str, marker: str, title: str, body: str) -> None:
        if self.dry_run:
            self.out(f"{title}\n{body}")
            return
        labels = self._labels(cred_id)
        self._ensure_labels(labels)
        existing = self._open(cred_id, marker)
        if existing:
            n = str(existing[0]["number"])
            self.gh(["issue", "comment", n, "--body-file", "-"], input=f"Repeat: **{title}**\n\n{body}")
        else:
            gh.create_issue(title, body, labels, gh=self.gh)

    # -- public ----------------------------------------------------------------------------
    def alert(self, a: CredentialAlert) -> None:
        entry = self.entries[a.cred_id]
        status = a.status if a.status is not None else "n/a"
        title = self._title(f"[ALERT] {entry['label']}: {a.kind} ({status})")
        body = self._body(entry, a.what, a.status, verdict(a.kind, self.today, entry["expires"]))
        self._file(a.cred_id, f": {a.kind} (", title, body)

    def resolve(self, cred_id: str = API_KEY_ID) -> None:
        """Probe passed: close open non-drill rejected/forbidden/missing [ALERT] issues for this credential.

        credits alerts stay open: a free models.list call says nothing about the prepaid balance.
        """
        if self.drill or self.dry_run:
            return
        for i in self._open(cred_id, "[ALERT]"):
            if not any(f": {kind} (" in i["title"] for kind in RESOLVABLE):
                continue
            self.gh(["issue", "close", str(i["number"]), "--comment", f"Resolved by run {run_url()}"])

    def expiry(self, entry: dict) -> int:
        days = (entry["expires"] - self.today).days
        title = self._title(f"[EXPIRING] {entry['label']}: {days} days ({entry['expires']})")
        if days > EXPIRY_WINDOW:
            if not (self.drill or self.dry_run):
                for i in self._open(entry["id"], "[EXPIRING]"):
                    self.gh(["issue", "close", str(i["number"]), "--comment",
                             f"Expiry is now {entry['expires']} ({days} days out); closing. Run {run_url()}"])
            return days
        v = (f"Expired as scheduled on {entry['expires']}" if days <= 0
             else f"Expires on {entry['expires']}; rotate before then")
        body = self._body(entry, f"{entry['label']} expires in {days} days ({entry['expires']})", None, v)
        if self.dry_run:
            self.out(f"{title}\n{body}")
            return days
        labels = self._labels(entry["id"])
        self._ensure_labels(labels)
        existing = self._open(entry["id"], "[EXPIRING]")
        if not existing:
            gh.create_issue(title, body, labels, gh=self.gh)
        elif (days in COMMENT_DAYS or days <= 0) and existing[0]["title"] != title:
            # The title carries the day count and is updated with each comment, so an unchanged
            # title means this threshold was already announced today: one comment per day.
            n = str(existing[0]["number"])
            self.gh(["issue", "edit", n, "--title", title])
            self.gh(["issue", "comment", n, "--body-file", "-"], input=f"**{title}**\n\n{body}")
        return days


def expiry(entries: list[dict], alerts: Alerts) -> dict[str, int]:
    """days = expires - today (UTC) per entry; opens/comments/closes [EXPIRING] issues."""
    return {e["id"]: alerts.expiry(e) for e in entries}
