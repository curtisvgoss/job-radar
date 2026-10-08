import copy

import pytest
import yaml

from radar import profile as prof
from tests.conftest import FIX

BASE = yaml.safe_load((FIX / "profile.yml").read_text())


def p(**over):
    d = copy.deepcopy(BASE)
    d.update(over)
    return d


def errors(data) -> str:
    with pytest.raises(prof.ProfileError) as ei:
        prof.validate(data)
    return str(ei.value)


def test_fixture_and_example_validate():
    prof.load(FIX / "profile.yml")
    ex = prof.load(FIX.parent.parent / "config" / "example.yml")
    assert len(ex["companies"]) == 3


@pytest.mark.parametrize("key", ["identity", "status", "constraints", "targets", "exclude",
                                 "industries", "skills", "narrative", "scoring"])
def test_missing_required_top_level_key(key):
    d = p()
    del d[key]
    assert f"{key}: missing" in errors(d)


def test_companies_optional():
    assert "companies" not in prof.validate(p())


def test_extra_keys_rejected_at_every_level():
    msg = errors(p(about="old v2 key", constraints={"locations": [], "remote_ok": True, "zip": "x"}))
    assert "about: unknown key" in msg and "constraints.zip: unknown key" in msg
    msg = errors(p(targets=[{"tier": 1, "label": "x", "patterns": ["a"], "weight": 2}]))
    assert "targets[0].weight: unknown key" in msg


def test_nested_missing_and_types():
    msg = errors(p(exclude={"seniority_words": "intern"}))
    assert "exclude.seniority_words: expected a list" in msg and "exclude.title_substrings: missing" in msg
    assert "constraints.remote_ok: expected bool" in errors(p(constraints={"locations": [], "remote_ok": "yes"}))
    assert "targets[0].tier: expected an integer" in errors(p(targets=[{"tier": True, "label": "x", "patterns": ["a"]}]))
    assert "companies[0].ats: must be one of" in errors(p(companies=[{"name": "A", "ats": "workday", "slug": "a"}]))


def test_semantic_checks():
    assert "at least one target" in errors(p(targets=[]))
    assert "tier: must be >= 1" in errors(p(targets=[{"tier": 0, "label": "x", "patterns": ["a"]}]))
    assert "non-empty pattern" in errors(p(targets=[{"tier": 1, "label": "x", "patterns": [" "]}]))
    assert "min_score: must be 0-100" in errors(p(scoring={"min_score": 101}))


def test_filter_rules_derived_from_fixture():
    r = prof.rules(prof.load(FIX / "profile.yml"))
    assert r.include == ("data engineer", "analytics engineer", "engineer", "analyst")
    assert [(t.tier, t.label) for t in r.targets] == [(1, "Data"), (2, "Broad")]
    assert r.seniority_words == ("intern", "director")
    assert r.title_substrings == ("sales",)
    assert r.locations == ("new york",) and r.remote_ok is True
    assert prof.min_score(BASE) == 60
    assert prof.min_score(p(scoring={})) == 60


def test_sha256_tracks_content_not_formatting():
    a = prof.sha256(prof.validate(p()))
    reformatted = yaml.safe_load(yaml.safe_dump(p(), default_flow_style=False, sort_keys=True))
    assert prof.sha256(prof.validate(reformatted)) == a
    assert prof.sha256(prof.validate(p(narrative="Different."))) != a
