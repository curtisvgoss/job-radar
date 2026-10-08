"""profile.yml: schema validation, deterministic filter rules, content hash.

Schema notation: a dict is a mapping (keys prefixed "?" are optional, unknown keys are errors),
[x] a list of x, a tuple an enum, a type a scalar of that type.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

ATS = ("greenhouse", "lever", "ashby")
DEFAULT_MIN_SCORE = 60

SCHEMA = {
    "identity": {"name": str, "?headline": str, "?location": str, "?links": [str]},
    "status": {"situation": str, "?notes": str},
    "constraints": {"locations": [str], "remote_ok": bool, "?relocate": bool, "?comp_floor": int, "?notes": str},
    "targets": [{"tier": int, "label": str, "patterns": [str]}],
    "exclude": {"seniority_words": [str], "title_substrings": [str]},
    "industries": {"prefer": [str], "avoid": [str]},
    "skills": {"core": [str], "?familiar": [str]},
    "narrative": str,
    "scoring": {"?min_score": int, "?priorities": [str], "?deal_breakers": [str]},
    "?companies": [{"name": str, "ats": ATS, "slug": str}],
}


class ProfileError(ValueError):
    pass


def _join(where: str, key) -> str:
    return f"{where}.{key}" if where else str(key)


def _check(value, schema, where: str, errors: list[str]) -> None:
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            errors.append(f"{where or 'profile'}: expected a mapping")
            return
        spec = {k.lstrip("?"): k for k in schema}
        for k in value:
            if k not in spec:
                errors.append(f"{_join(where, k)}: unknown key")
        for k, raw in spec.items():
            if k in value:
                _check(value[k], schema[raw], _join(where, k), errors)
            elif not raw.startswith("?"):
                errors.append(f"{_join(where, k)}: missing")
    elif isinstance(schema, list):
        if not isinstance(value, list):
            errors.append(f"{where}: expected a list")
            return
        for i, v in enumerate(value):
            _check(v, schema[0], f"{where}[{i}]", errors)
    elif isinstance(schema, tuple):
        if value not in schema:
            errors.append(f"{where}: must be one of {', '.join(schema)}")
    elif schema is int:
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{where}: expected an integer")
    elif not isinstance(value, schema):
        errors.append(f"{where}: expected {schema.__name__}")


def validate(data) -> dict:
    errors: list[str] = []
    _check(data, SCHEMA, "", errors)
    if not errors:
        if not data["targets"]:
            errors.append("targets: at least one target is required")
        for i, t in enumerate(data["targets"]):
            if t["tier"] < 1:
                errors.append(f"targets[{i}].tier: must be >= 1")
            if not [p for p in t["patterns"] if p.strip()]:
                errors.append(f"targets[{i}].patterns: at least one non-empty pattern is required")
        ms = data["scoring"].get("min_score", DEFAULT_MIN_SCORE)
        if not 0 <= ms <= 100:
            errors.append("scoring.min_score: must be 0-100")
    if errors:
        raise ProfileError("invalid profile:\n  " + "\n  ".join(errors))
    return data


def load(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise ProfileError(f"profile not found: {path}")
    return validate(yaml.safe_load(path.read_text()))


def sha256(profile: dict) -> str:
    """Hash of the parsed profile: comment and formatting edits do not force a recompile."""
    blob = json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def min_score(profile: dict) -> int:
    return profile["scoring"].get("min_score", DEFAULT_MIN_SCORE)


def _norm(xs) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.strip().lower() for x in xs if x.strip()))


@dataclass(frozen=True)
class Target:
    tier: int
    label: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class Rules:
    targets: tuple[Target, ...]          # ascending tier: first match is the best tier
    title_substrings: tuple[str, ...]    # excluded by substring
    seniority_words: tuple[str, ...]     # excluded by whole word
    locations: tuple[str, ...]           # substring of the posting location; empty = any
    remote_ok: bool

    @property
    def include(self) -> tuple[str, ...]:
        """Union of all target patterns."""
        return tuple(dict.fromkeys(p for t in self.targets for p in t.patterns))


def rules(profile: dict) -> Rules:
    targets = sorted(profile["targets"], key=lambda t: t["tier"])
    return Rules(
        targets=tuple(Target(t["tier"], t["label"], _norm(t["patterns"])) for t in targets),
        title_substrings=_norm(profile["exclude"]["title_substrings"]),
        seniority_words=_norm(profile["exclude"]["seniority_words"]),
        locations=_norm(profile["constraints"]["locations"]),
        remote_ok=profile["constraints"]["remote_ok"],
    )
