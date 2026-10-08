"""ATS adapters. Each returns normalized postings:
{uid, company, title, location, remote, url, posted_at, comp, text}.
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

TIMEOUT = 20.0
RETRIES = 2
BACKOFF = 1.0  # seconds; doubles per retry

URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
}

_BLOCK = re.compile(r"<\s*(br|/p|/div|/li|/h[1-6]|li)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n\s*\n+")


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    s = _BLOCK.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = _WS.sub(" ", s)
    return _NL.sub("\n\n", s).strip()


def _iso(value) -> str:
    """Normalize ISO strings or epoch-millis to ISO 8601 UTC; '' if unknown."""
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OSError, OverflowError):
        return ""


def _has_remote(*parts) -> bool:
    return any("remote" in str(p).lower() for p in parts if p)


def parse_greenhouse(payload, company: dict) -> list[dict]:
    out = []
    for j in payload.get("jobs", []):
        loc = (j.get("location") or {}).get("name") or ""
        out.append({
            "uid": f"greenhouse:{company['slug']}:{j['id']}",
            "company": company["name"],
            "title": (j.get("title") or "").strip(),
            "location": loc,
            "remote": _has_remote(loc),
            "url": j.get("absolute_url") or "",
            "posted_at": _iso(j.get("first_published") or j.get("updated_at")),
            "comp": None,
            # content is entity-encoded HTML: unescape to HTML, then strip tags.
            "text": html_to_text(html.unescape(j.get("content") or "")),
        })
    return out


def _lever_comp(sr) -> str | None:
    if not sr or sr.get("min") is None:
        return None
    cur = sr.get("currency") or ""
    lo, hi = sr.get("min"), sr.get("max")
    rng = f"{lo:,}" if hi in (None, lo) else f"{lo:,}-{hi:,}"
    interval = (sr.get("interval") or "").replace("-", " ")
    return " ".join(p for p in (cur, rng, interval) if p)


def parse_lever(payload, company: dict) -> list[dict]:
    out = []
    for j in payload:
        cats = j.get("categories") or {}
        loc = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        lists = "\n\n".join(
            f"{li.get('text', '')}\n{html_to_text(li.get('content'))}" for li in j.get("lists") or []
        )
        text = "\n\n".join(p for p in (j.get("descriptionPlain"), lists, j.get("additionalPlain")) if p)
        out.append({
            "uid": f"lever:{company['slug']}:{j['id']}",
            "company": company["name"],
            "title": (j.get("text") or "").strip(),
            "location": loc,
            "remote": (j.get("workplaceType") or "").lower() == "remote" or _has_remote(loc),
            "url": j.get("hostedUrl") or "",
            "posted_at": _iso(j.get("createdAt")),
            "comp": _lever_comp(j.get("salaryRange")),
            "text": text.strip(),
        })
    return out


def _ashby_remote(j: dict, loc: str) -> bool:
    # Ashby sets isRemote=true on Hybrid roles; workplaceType is authoritative when present.
    wpt = (j.get("workplaceType") or "").lower()
    flag = wpt == "remote" if wpt else bool(j.get("isRemote"))
    return flag or _has_remote(loc)


def parse_ashby(payload, company: dict) -> list[dict]:
    out = []
    for j in payload.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [
            s.get("location", "") for s in j.get("secondaryLocations") or [] if isinstance(s, dict)
        ]
        loc = "; ".join(l for l in locs if l)
        comp = j.get("compensation") or {}
        out.append({
            "uid": f"ashby:{company['slug']}:{j['id']}",
            "company": company["name"],
            "title": (j.get("title") or "").strip(),
            "location": loc,
            "remote": _ashby_remote(j, loc),
            "url": j.get("jobUrl") or j.get("applyUrl") or "",
            "posted_at": _iso(j.get("publishedAt")),
            "comp": comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary"),
            "text": (j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml"))).strip(),
        })
    return out


PARSERS = {"greenhouse": parse_greenhouse, "lever": parse_lever, "ashby": parse_ashby}


def get_json(client: httpx.Client, url: str):
    """GET with 2 retries and exponential backoff on transport errors, 429 and 5xx."""
    for attempt in range(RETRIES + 1):
        try:
            r = client.get(url)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429 and e.response.status_code < 500:
                raise
            if attempt == RETRIES:
                raise
        except httpx.TransportError:
            if attempt == RETRIES:
                raise
        time.sleep(BACKOFF * 2**attempt)


def fetch_company(company: dict, client: httpx.Client | None = None, fixtures: Path | None = None) -> list[dict]:
    ats, slug = company["ats"], company["slug"]
    if ats not in PARSERS:
        raise ValueError(f"unknown ats {ats!r}")
    if fixtures is not None:
        payload = json.loads((Path(fixtures) / ats / f"{slug}.json").read_text())
    else:
        payload = get_json(client, URLS[ats].format(slug=slug))
    return PARSERS[ats](payload, company)


def fetch_all(companies: list[dict], fixtures: Path | None = None) -> tuple[list[dict], dict[str, int]]:
    """Fetch every company; a failing company is logged and skipped. Returns (postings, fetched counts)."""
    postings, counts = [], {}
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "job-radar"}) as client:
        for c in companies:
            try:
                got = fetch_company(c, client=client, fixtures=fixtures)
            except Exception as e:  # never fatal
                log.warning("fetch failed: %s (%s/%s): %s", c.get("name"), c.get("ats"), c.get("slug"),
                            type(e).__name__)
                counts[c.get("name", "?")] = 0
                continue
            counts[c["name"]] = counts.get(c["name"], 0) + len(got)
            postings.extend(got)
    return postings, counts
