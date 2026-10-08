"""Markdown digest of scored postings."""

from __future__ import annotations

MAX_ROWS = 60


def _cell(s) -> str:
    return " ".join(str(s or "").split()).replace("|", "\\|")


def matches(scored: list[dict], min_score: int) -> list[dict]:
    return sorted((p for p in scored if p["score"] >= min_score), key=lambda p: -p["score"])


def render(rows: list[dict]) -> str:
    shown = rows[:MAX_ROWS]
    lines = [
        "| Company | Title | Location | Comp | Score | Fit | Link |",
        "|---|---|---|---|---:|---|---|",
    ]
    for p in shown:
        lines.append("| " + " | ".join([
            _cell(p["company"]), _cell(p["title"]), _cell(p["location"]), _cell(p.get("comp")),
            str(p["score"]), _cell(p["fit"]), f"[apply]({p['url']})" if p.get("url") else "",
        ]) + " |")
    if len(rows) > MAX_ROWS:
        lines.append(f"\n_Showing top {MAX_ROWS} of {len(rows)} matches._")
    return "\n".join(lines) + "\n"
