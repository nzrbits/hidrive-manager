import os
import stat

from hidrive_manager import config


def test_save_load_roundtrip_and_permissions(isolated_config):
    p = config.save(config.Settings(username="home-1", url="https://x.test", timeout=30, jobs=2))
    assert p == isolated_config
    s = config.load()
    assert (s.username, s.url, s.timeout, s.jobs) == ("home-1", "https://x.test", 30, 2)
    if os.name != "nt":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_env_overrides(isolated_config, monkeypatch):
    config.save(config.Settings(username="file-user"))
    monkeypatch.setenv("HIDRIVE_USERNAME", "env-user")
    monkeypatch.setenv("HIDRIVE_URL", "https://env.test")
    s = config.load()
    assert s.username == "env-user" and s.url == "https://env.test"
    assert config.get_password("anyone") == "p"  # from HIDRIVE_PASSWORD


def test_corrupt_config_falls_back_to_defaults(isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text("{not json")
    s = config.load()
    assert s.username == "" and s.url == config.DEFAULT_URL


def test_password_without_env_and_username_is_none(isolated_config, monkeypatch):
    monkeypatch.delenv("HIDRIVE_PASSWORD")
    assert config.get_password("") is None
