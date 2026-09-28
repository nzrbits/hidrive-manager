"""Configuration and credential storage.

Non-secret settings live in a JSON file (XDG config dir); the password lives in the
OS keychain via `keyring`. Environment variables override both for scripting.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_URL = "https://webdav.hidrive.ionos.com"
KEYRING_SERVICE = "hidrive-manager"

ENV_USERNAME = "HIDRIVE_USERNAME"
ENV_PASSWORD = "HIDRIVE_PASSWORD"
ENV_URL = "HIDRIVE_URL"
ENV_CONFIG = "HIDRIVE_CONFIG"


def config_path() -> Path:
    if os.environ.get(ENV_CONFIG):
        return Path(os.environ[ENV_CONFIG]).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "hidrive-manager" / "config.json"


@dataclass
class Settings:
    username: str = ""
    url: str = DEFAULT_URL
    timeout: int = 60
    jobs: int = 4

    def to_dict(self) -> dict:
        return {"username": self.username, "url": self.url, "timeout": self.timeout, "jobs": self.jobs}


def load() -> Settings:
    p = config_path()
    data: dict = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    s = Settings(
        username=str(data.get("username", "")),
        url=str(data.get("url", DEFAULT_URL)),
        timeout=int(data.get("timeout", 60)),
        jobs=int(data.get("jobs", 4)),
    )
    if os.environ.get(ENV_USERNAME):
        s.username = os.environ[ENV_USERNAME]
    if os.environ.get(ENV_URL):
        s.url = os.environ[ENV_URL]
    return s


def save(s: Settings) -> Path:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s.to_dict(), indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(p, 0o600)
    return p


def delete() -> None:
    p = config_path()
    if p.exists():
        p.unlink()


# --- password -----------------------------------------------------------------

def _keyring():
    import keyring  # imported lazily: slow on some platforms, optional in tests

    return keyring


def get_password(username: str) -> str | None:
    if os.environ.get(ENV_PASSWORD):
        return os.environ[ENV_PASSWORD]
    if not username:
        return None
    try:
        return _keyring().get_password(KEYRING_SERVICE, username)
    except Exception:
        return None


def set_password(username: str, password: str) -> None:
    _keyring().set_password(KEYRING_SERVICE, username, password)


def delete_password(username: str) -> None:
    try:
        _keyring().delete_password(KEYRING_SERVICE, username)
    except Exception:
        pass
