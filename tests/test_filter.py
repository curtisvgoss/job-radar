import dataclasses

import yaml

from radar import fetch, filter as flt, profile as prof
from tests.conftest import FIX

PROFILE = prof.rules(prof.load(FIX / "profile.yml"))


def posting(title, location="New York, NY", remote=False, uid="x:y:1"):
    return {"uid": uid, "title": title, "location": location, "remote": remote}


def test_title_include_exclude_case_insensitive():
    assert flt.keep(posting("DATA ENGINEER"), PROFILE)
    assert not flt.keep(posting("Product Manager"), PROFILE)
    assert not flt.keep(posting("Sales Engineer"), PROFILE)


def test_seniority_whole_word():
    assert not flt.keep(posting("Engineering Intern"), PROFILE)
    assert flt.keep(posting("Internal Tools Engineer"), PROFILE)
    assert not flt.keep(posting("Director, Engineer Relations"), PROFILE)


def test_location_and_remote():
    assert not flt.keep(posting("Engineer", "Berlin"), PROFILE)
    assert flt.keep(posting("Engineer", "Berlin", remote=True), PROFILE)
    assert not flt.keep(posting("Engineer", "Berlin", remote=True), dataclasses.replace(PROFILE, remote_ok=False))
    assert flt.keep(posting("Engineer", "Berlin"), dataclasses.replace(PROFILE, locations=()))


def test_seen_and_duplicates_dropped():
    ps = [posting("Engineer", uid="a"), posting("Engineer", uid="b"), posting("Engineer", uid="b")]
    assert [p["uid"] for p in flt.apply(ps, PROFILE, {"a"})] == ["b"]


def test_fixture_pipeline():
    comps = yaml.safe_load((FIX / "companies.yml").read_text())
    ps, _ = fetch.fetch_all(comps, fixtures=FIX)
    kept = {p["uid"]: (p["tier"], p["target"]) for p in flt.apply(ps, PROFILE, set())}
    assert kept == {"greenhouse:acme:1001": (1, "Data"), "greenhouse:acme:1003": (2, "Broad"),
                    "lever:globex:aaaa-1111": (2, "Broad"), "ashby:initech:c3d4-0001": (1, "Data")}


def test_best_tier_wins_regardless_of_profile_order():
    p = prof.load(FIX / "profile.yml")
    p = {**p, "targets": list(reversed(p["targets"]))}
    t = flt.match(posting("Senior Data Engineer"), prof.rules(p))
    assert (t.tier, t.label) == (1, "Data")
