import pytest

from fakedav import FakeDavSession, make_client
from hidrive_manager.webdav import AuthError, NotFound, WebDavError


def test_listdir_sorts_dirs_first_and_excludes_self(dav):
    client, _ = dav
    names = [e.name for e in client.listdir("/users/home-1")]
    assert names == ["photos", "readme.txt"]


def test_stat_and_exists(dav):
    client, _ = dav
    e = client.stat("/users/home-1/readme.txt")
    assert e is not None and e.size == 5 and not e.is_dir
    assert client.stat("/nope") is None
    assert client.exists("/Musik Backup")
    assert not client.exists("/Musik Backup/x")


def test_walk_yields_topdown(dav):
    client, _ = dav
    seen = [(d, [x.name for x in dirs], [x.name for x in files]) for d, dirs, files in client.walk("/users/home-1")]
    assert seen == [
        ("/users/home-1", ["photos"], ["readme.txt"]),
        ("/users/home-1/photos", [], ["a.jpg", "b.jpg"]),
    ]


def test_wrong_password_raises_auth_error():
    session = FakeDavSession(user="u", password="right")
    client, _ = make_client(session)
    client.session.auth = ("u", "wrong")
    with pytest.raises(AuthError):
        client.check_login()


def test_makedirs_creates_parents_and_is_idempotent(dav):
    client, session = dav
    client.makedirs("/users/home-1/new/deep/dir")
    assert session.files["/users/home-1/new/deep/dir"] is None
    client.makedirs("/users/home-1/new/deep/dir")  # no error
    assert client.mkdir("/users/home-1/new") is False


def test_upload_streams_with_progress_and_content_length(dav, tmp_path):
    client, session = dav
    src = tmp_path / "big.bin"
    src.write_bytes(b"x" * 200_000)
    got = []
    client.upload(src, "/users/home-1/big.bin", got.append)
    assert session.files["/users/home-1/big.bin"] == b"x" * 200_000
    assert sum(got) == 200_000


def test_upload_empty_file(dav, tmp_path):
    client, session = dav
    src = tmp_path / "empty"
    src.write_bytes(b"")
    client.upload(src, "/users/home-1/empty")
    assert session.files["/users/home-1/empty"] == b""


def test_upload_retries_on_5xx_and_rolls_back_progress(dav, tmp_path):
    client, session = dav
    src = tmp_path / "f"
    src.write_bytes(b"data" * 10)
    session.fail_next = [503]
    got = []
    client.upload(src, "/users/home-1/f", got.append)
    assert session.files["/users/home-1/f"] == b"data" * 10
    assert sum(got) == 40  # 40 sent, -0 or -40 rolled back, 40 again → net 40 (fail before body read → no rollback)


def test_upload_gives_up_after_retries(dav, tmp_path):
    client, session = dav
    src = tmp_path / "f"
    src.write_bytes(b"1")
    session.fail_next = [500, 500, 500, 500]
    with pytest.raises(WebDavError):
        client.upload(src, "/users/home-1/f")


def test_upload_does_not_retry_4xx(dav, tmp_path):
    client, session = dav
    src = tmp_path / "f"
    src.write_bytes(b"1")
    n_before = len(session.calls)
    with pytest.raises(WebDavError) as ei:
        client.upload(src, "/does-not-exist/f")
    assert ei.value.status == 409
    assert len(session.calls) - n_before == 1


def test_download_and_resume(dav, tmp_path):
    client, session = dav
    target = tmp_path / "b.jpg"
    part = tmp_path / "b.jpg.part"
    part.write_bytes(b"B" * 1000)  # pretend an earlier run stopped here
    got = []
    client.download("/users/home-1/photos/b.jpg", target, got.append, expected_size=5000)
    assert target.read_bytes() == b"B" * 5000
    assert not part.exists()
    assert sum(got) == 5000
    assert ("GET", "/users/home-1/photos/b.jpg") in session.calls


def test_download_ignores_oversized_part(dav, tmp_path):
    client, _ = dav
    target = tmp_path / "a.jpg"
    (tmp_path / "a.jpg.part").write_bytes(b"Z" * 9000)
    client.download("/users/home-1/photos/a.jpg", target, expected_size=3000)
    assert target.read_bytes() == b"A" * 3000


def test_download_missing_raises_not_found(dav, tmp_path):
    client, _ = dav
    with pytest.raises(NotFound):
        client.download("/nope", tmp_path / "x")


def test_delete_move_copy(dav):
    client, session = dav
    client.copy("/users/home-1/readme.txt", "/users/home-1/copy.txt")
    assert session.files["/users/home-1/copy.txt"] == b"hello"
    client.move("/users/home-1/copy.txt", "/users/home-1/moved.txt")
    assert "/users/home-1/copy.txt" not in session.files
    with pytest.raises(WebDavError) as ei:
        client.move("/users/home-1/moved.txt", "/users/home-1/readme.txt")
    assert ei.value.status == 412
    client.move("/users/home-1/moved.txt", "/users/home-1/readme.txt", overwrite=True)
    client.delete("/users/home-1/photos")
    assert not any(p.startswith("/users/home-1/photos") for p in session.files)


def test_urls_are_encoded(dav):
    client, session = dav
    client.stat("/Musik Backup/ä b")
    assert ("PROPFIND", "/Musik Backup/ä b") in session.calls
    assert client.url("/Musik Backup/ä b") == "https://fake.hidrive.test/Musik%20Backup/%C3%A4%20b"


def test_gz_upload_survives_bogus_content_encoding(dav, tmp_path):
    client, session = dav
    src = tmp_path / "x.tar.gz"
    src.write_bytes(b"\x1f\x8b" + b"z" * 5000)
    client.upload(src, "/users/home-1/x.tar.gz")
    assert session.files["/users/home-1/x.tar.gz"] == src.read_bytes()
    assert client.stat("/users/home-1/x.tar.gz").size == 5002


def test_gz_download_and_stream_return_stored_bytes(dav, tmp_path):
    client, session = dav
    payload = b"\x1f\x8b" + b"q" * 3000
    session.add_file("/users/home-1/a.tar.gz", payload)
    target = tmp_path / "a.tar.gz"
    client.download("/users/home-1/a.tar.gz", target, expected_size=len(payload))
    assert target.read_bytes() == payload
    assert b"".join(client.stream("/users/home-1/a.tar.gz", 1000)) == payload


def test_gz_delete_move_copy_do_not_read_body(dav):
    client, session = dav
    session.add_file("/users/home-1/b.tgz", b"bb")
    client.copy("/users/home-1/b.tgz", "/users/home-1/c.tgz")
    client.move("/users/home-1/c.tgz", "/users/home-1/d.tgz")
    client.delete("/users/home-1/d.tgz")
    assert "/users/home-1/d.tgz" not in session.files and "/users/home-1/b.tgz" in session.files


def test_error_on_gz_path_still_reports_status(dav):
    client, _ = dav
    with pytest.raises(NotFound):
        client.delete("/users/home-1/missing.tar.gz")


def test_gz_propfind_reads_raw_body(dav):
    client, session = dav
    session.add_file("/users/home-1/p.tar.gz", b"p" * 77)
    e = client.stat("/users/home-1/p.tar.gz")
    assert e is not None and e.size == 77


def test_propfind_gunzips_a_really_compressed_body(dav, monkeypatch):
    import gzip as _gzip

    client, session = dav
    orig = session._propfind

    def compressed(path, headers, kw):
        r = orig(path, headers, kw)
        body = _gzip.compress(r._body)
        r._body, r.raw = body, __import__("fakedav")._Raw(body)
        return r

    monkeypatch.setattr(session, "_propfind", compressed)
    assert client.stat("/users/home-1/readme.txt").size == 5
