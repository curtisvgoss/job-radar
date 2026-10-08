import pytest

from radar import fetch
from tests.conftest import FIX


def company(name, ats, slug):
    return {"name": name, "ats": ats, "slug": slug}


def test_greenhouse_normalizes_and_strips_encoded_html():
    ps = fetch.fetch_company(company("Acme", "greenhouse", "acme"), fixtures=FIX)
    p = ps[0]
    assert p["uid"] == "greenhouse:acme:1001"
    assert p["location"] == "New York, NY" and p["remote"] is False
    assert p["posted_at"] == "2026-09-30T13:00:00Z"
    assert "<" not in p["text"] and "&lt;" not in p["text"]
    assert "Build pipelines & models." in p["text"] and "Python" in p["text"]
    assert ps[2]["remote"] is True
    assert set(p) == {"uid", "company", "title", "location", "remote", "url", "posted_at", "comp", "text"}


def test_lever_normalizes():
    p = fetch.fetch_company(company("Globex", "lever", "globex"), fixtures=FIX)[0]
    assert p["uid"] == "lever:globex:aaaa-1111"
    assert p["remote"] is True
    assert p["comp"] == "USD 180,000-220,000 per year salary"
    assert "PyTorch" in p["text"] and "Benefits" in p["text"]
    assert p["posted_at"].startswith("2025-10-03")


def test_ashby_normalizes_and_skips_unlisted():
    ps = fetch.fetch_company(company("Initech", "ashby", "initech"), fixtures=FIX)
    assert [p["uid"] for p in ps] == ["ashby:initech:c3d4-0001", "ashby:initech:c3d4-0002", "ashby:initech:c3d4-0004"]
    assert ps[0]["comp"] == "$150K - $190K"
    assert "New York" in ps[0]["location"]


def test_failing_company_is_skipped_not_fatal():
    comps = [company("Broken", "greenhouse", "missing"), company("Acme", "greenhouse", "acme")]
    ps, counts = fetch.fetch_all(comps, fixtures=FIX)
    assert counts == {"Broken": 0, "Acme": 4}
    assert len(ps) == 4


def test_get_json_retries_then_raises(monkeypatch):
    import httpx
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(httpx.HTTPStatusError):
            fetch.get_json(c, "https://example.test/x")
    assert n["calls"] == 3


def test_get_json_does_not_retry_404(monkeypatch):
    import httpx
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(httpx.HTTPStatusError):
            fetch.get_json(c, "https://example.test/x")
    assert n["calls"] == 1


def test_ashby_hybrid_flagged_isremote_is_not_remote():
    ps = {p["uid"]: p for p in fetch.fetch_company(company("Initech", "ashby", "initech"), fixtures=FIX)}
    assert ps["ashby:initech:c3d4-0002"]["remote"] is False  # isRemote=true, workplaceType=Hybrid
    assert ps["ashby:initech:c3d4-0004"]["remote"] is True   # no workplaceType: fall back to isRemote
