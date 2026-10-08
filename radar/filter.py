"""Deterministic filtering: titles, seniority, location/remote, seen state."""

from __future__ import annotations

import re

from radar.profile import Rules, Target


def match(p: dict, rules: Rules) -> Target | None:
    """Best-tier target the posting passes, or None if it is filtered out."""
    title = p["title"].lower()
    target = next((t for t in rules.targets if any(pat in title for pat in t.patterns)), None)
    if target is None:
        return None
    if any(s in title for s in rules.title_substrings):
        return None
    for w in rules.seniority_words:
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", title):
            return None
    if not rules.locations:
        return target
    if p.get("remote") and rules.remote_ok:
        return target
    loc = (p.get("location") or "").lower()
    return target if any(l in loc for l in rules.locations) else None


def keep(p: dict, rules: Rules) -> bool:
    return match(p, rules) is not None


def apply(postings: list[dict], rules: Rules, seen: set[str]) -> list[dict]:
    """Kept postings, each annotated with the matched target's tier and label."""
    out, uids = [], set()
    for p in postings:
        if p["uid"] in seen or p["uid"] in uids:
            continue
        t = match(p, rules)
        if t is not None:
            uids.add(p["uid"])
            out.append({**p, "tier": t.tier, "target": t.label})
    return out
