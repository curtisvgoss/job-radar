"""Instance configuration: config.yml merged over the packaged defaults.yml."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULTS = Path(__file__).with_name("defaults.yml")
_SHA = re.compile(r"[0-9a-f]{40}")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    base: Path
    engine_repo: str | None
    engine_sha: str | None
    profile_path: str
    companies_path: str
    credentials_path: str
    state_dir: str
    compile_model: str
    score_model: str

    def _p(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.base / p

    @property
    def profile(self) -> Path:
        return self._p(self.profile_path)

    @property
    def companies(self) -> Path:
        return self._p(self.companies_path)

    @property
    def credentials(self) -> Path:
        return self._p(self.credentials_path)

    @property
    def seen(self) -> Path:
        return self._p(self.state_dir) / "seen.json"

    @property
    def compiled(self) -> Path:
        return self._p(self.state_dir) / "profile.compiled.json"


def load(path: str | Path = "config.yml") -> Config:
    """Read config.yml if it exists (defaults otherwise); base dir is config.yml's parent, else cwd."""
    values = yaml.safe_load(DEFAULTS.read_text())
    path = Path(path)
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: expected a mapping")
        unknown = sorted(set(data) - set(values))
        if unknown:
            raise ConfigError(f"{path}: unknown keys: {', '.join(unknown)}")
        values.update(data)
        base = path.resolve().parent
    else:
        base = Path.cwd()
    for k, v in values.items():
        if k in ("engine_repo", "engine_sha"):
            continue
        if not isinstance(v, str) or not v.strip():
            raise ConfigError(f"config {k}: expected a non-empty string")
    sha, repo = values["engine_sha"], values["engine_repo"]
    if sha is not None and not (isinstance(sha, str) and _SHA.fullmatch(sha)):
        raise ConfigError("config engine_sha: must be a full 40-char lowercase commit SHA, never a tag or branch")
    if repo is not None and not (isinstance(repo, str) and _REPO.fullmatch(repo)):
        raise ConfigError("config engine_repo: expected owner/name")
    return Config(base=base, **values)
