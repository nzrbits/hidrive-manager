import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakedav import FakeDavSession, make_client  # noqa: E402


@pytest.fixture
def dav():
    """(client, session) pair with a small pre-populated tree."""
    session = FakeDavSession()
    session.add_dir("/users/home-1")
    session.add_file("/users/home-1/readme.txt", b"hello", mtime=1_700_000_000)
    session.add_file("/users/home-1/photos/a.jpg", b"A" * 3000, mtime=1_700_000_100)
    session.add_file("/users/home-1/photos/b.jpg", b"B" * 5000, mtime=1_700_000_200)
    session.add_dir("/Musik Backup")
    client, _ = make_client(session)
    return client, session


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point config at a temp file, provide password via env so keyring is never touched."""
    monkeypatch.setenv("HIDRIVE_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("HIDRIVE_USERNAME", raising=False)
    monkeypatch.delenv("HIDRIVE_URL", raising=False)
    monkeypatch.setenv("HIDRIVE_PASSWORD", "p")
    return tmp_path / "config.json"
