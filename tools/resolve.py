"""Find ATS boards for company names.

    python -m tools.resolve "Name A,Name B"

Probes greenhouse, lever and ashby with slug guesses and prints hits as companies.yml lines.
"""

from __future__ import annotations

import json
import sys

import httpx

from radar.fetch import TIMEOUT, URLS

SHAPES = {
    "greenhouse": lambda d: isinstance(d, dict) and isinstance(d.get("jobs"), list),
    "lever": lambda d: isinstance(d, list),
    "ashby": lambda d: isinstance(d, dict) and isinstance(d.get("jobs"), list),
}


def slugs(name: str) -> list[str]:
    n = " ".join(name.lower().split())
    return list(dict.fromkeys([n, n.replace(" ", "-"), n.replace(" ", "")]))


def probe(client: httpx.Client, ats: str, slug: str) -> int | None:
    """Return the job count if the board exists, else None."""
    try:
        r = client.get(URLS[ats].format(slug=slug))
        if r.status_code != 200:
            return None
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not SHAPES[ats](data):
        return None
    return len(data) if ats == "lever" else len(data["jobs"])


def resolve(names: list[str], client: httpx.Client) -> list[str]:
    lines = []
    for name in names:
        for ats in URLS:
            for slug in slugs(name):
                n = probe(client, ats, slug)
                if n is not None:
                    lines.append(f"- {{name: {json.dumps(name)}, ats: {ats}, slug: {slug}}}  # {n} jobs")
    return lines


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    names = [n.strip() for n in ",".join(argv).split(",") if n.strip()]
    if not names:
        print('usage: python -m tools.resolve "Name A,Name B"', file=sys.stderr)
        return 1
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "job-radar"}) as client:
        lines = resolve(names, client)
    for line in lines:
        print(line)
    if not lines:
        print("# no boards found", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
