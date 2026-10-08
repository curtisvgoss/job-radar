import pytest

from radar import config


def test_defaults_without_config_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = config.load(tmp_path / "config.yml")
    assert c.base == tmp_path
    assert c.profile == tmp_path / "profile.yml"
    assert c.seen == tmp_path / "state" / "seen.json"
    assert c.compiled == tmp_path / "state" / "profile.compiled.json"
    assert c.credentials == tmp_path / "config" / "credentials.yml"
    assert c.compile_model and c.score_model and c.engine_sha is None


def test_paths_relative_to_config_dir(tmp_path):
    (tmp_path / "inst").mkdir()
    path = tmp_path / "inst" / "config.yml"
    path.write_text(f"profile_path: me.yml\nengine_repo: someone/job-radar\nengine_sha: {'a' * 40}\n")
    c = config.load(path)
    assert c.profile == tmp_path / "inst" / "me.yml" and c.engine_sha == "a" * 40


@pytest.mark.parametrize("body,msg", [
    ("bogus: 1\n", "unknown keys: bogus"),
    ("engine_sha: main\n", "full 40-char"),
    ("engine_sha: v1.2.3\n", "full 40-char"),
    (f"engine_sha: {'A' * 40}\n", "full 40-char"),
    ("engine_repo: not-a-repo\n", "owner/name"),
    ("score_model: ''\n", "score_model"),
])
def test_rejects_bad_config(tmp_path, body, msg):
    path = tmp_path / "config.yml"
    path.write_text(body)
    with pytest.raises(config.ConfigError, match=msg):
        config.load(path)
