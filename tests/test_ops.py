import os
import time

from hidrive_manager import ops
from hidrive_manager.progress import Progress


def kinds(plan):
    remote_kinds = ("upload", "mkdir", "delete_remote")
    return sorted((a.kind, a.remote if a.kind in remote_kinds else str(a.local)) for a in plan.actions if a.kind != "skip")


def test_local_tree_excludes(tmp_path):
    (tmp_path / "keep.txt").write_text("k")
    (tmp_path / ".DS_Store").write_text("junk")
    (tmp_path / "sub" / "node_modules").mkdir(parents=True)
    (tmp_path / "sub" / "node_modules" / "x.js").write_text("x")
    (tmp_path / "sub" / "ok.md").write_text("ok")
    files, dirs = ops.local_tree(tmp_path, excludes=[".DS_Store", "node_modules"])
    assert set(files) == {"keep.txt", "sub/ok.md"}
    assert dirs == {"sub"}


def test_plan_upload_single_file_and_dir(dav, tmp_path):
    client, _ = dav
    f = tmp_path / "new.txt"
    f.write_text("n")
    d = tmp_path / "album"
    (d / "disc1").mkdir(parents=True)
    (d / "disc1" / "01.flac").write_bytes(b"f" * 10)
    (d / "cover.jpg").write_bytes(b"c" * 3)
    plan = ops.plan_upload(client, [f, d], "/users/home-1/music")
    assert kinds(plan) == [
        ("mkdir", "/users/home-1/music"),
        ("mkdir", "/users/home-1/music/album"),
        ("mkdir", "/users/home-1/music/album/disc1"),
        ("upload", "/users/home-1/music/album/cover.jpg"),
        ("upload", "/users/home-1/music/album/disc1/01.flac"),
        ("upload", "/users/home-1/music/new.txt"),
    ]
    # mkdir actions are ordered parents first
    mk = [a.remote for a in plan.of("mkdir")]
    assert mk == ["/users/home-1/music", "/users/home-1/music/album", "/users/home-1/music/album/disc1"]
    assert plan.transfer_bytes == 14


def test_plan_upload_skips_up_to_date_files(dav, tmp_path):
    client, session = dav
    f = tmp_path / "readme.txt"
    f.write_bytes(b"hello")
    os.utime(f, (1_600_000_000, 1_600_000_000))  # older than remote
    plan = ops.plan_upload(client, [f], "/users/home-1")
    assert plan.is_noop
    assert plan.of("skip")[0].reason == "up to date"
    # size differs → upload
    f.write_bytes(b"hello!!")
    os.utime(f, (1_600_000_000, 1_600_000_000))
    assert kinds(ops.plan_upload(client, [f], "/users/home-1")) == [("upload", "/users/home-1/readme.txt")]
    # same size but local newer → upload
    f.write_bytes(b"HELLO")
    os.utime(f, (1_800_000_000, 1_800_000_000))
    assert kinds(ops.plan_upload(client, [f], "/users/home-1")) == [("upload", "/users/home-1/readme.txt")]
    assert ops.plan_upload(client, [f], "/users/home-1", overwrite="never").is_noop
    f.write_bytes(b"hello")
    os.utime(f, (1_600_000_000, 1_600_000_000))
    assert not ops.plan_upload(client, [f], "/users/home-1", overwrite="always").is_noop


def test_execute_upload_then_second_run_is_noop(dav, tmp_path):
    client, session = dav
    d = tmp_path / "album"
    d.mkdir()
    (d / "a.bin").write_bytes(b"a" * 100)
    (d / "b.bin").write_bytes(b"b" * 200)
    plan = ops.plan_upload(client, [d], "/users/home-1/music")
    res = ops.execute(client, plan, jobs=2, progress=Progress(plan.transfer_bytes, 2, enabled=False))
    assert res.failed == [] and res.ok == 4  # 2 mkdir + 2 uploads
    assert session.files["/users/home-1/music/album/b.bin"] == b"b" * 200
    plan2 = ops.plan_upload(client, [d], "/users/home-1/music")
    assert plan2.is_noop


def test_plan_download_dir_and_execute_sets_mtime(dav, tmp_path):
    client, _ = dav
    plan = ops.plan_download(client, ["/users/home-1/photos"], tmp_path)
    assert kinds(plan) == [
        ("download", str(tmp_path / "photos" / "a.jpg")),
        ("download", str(tmp_path / "photos" / "b.jpg")),
        ("mkdir_local", str(tmp_path / "photos")),
    ]
    res = ops.execute(client, plan, jobs=2)
    assert res.failed == []
    a = tmp_path / "photos" / "a.jpg"
    assert a.read_bytes() == b"A" * 3000
    assert abs(a.stat().st_mtime - 1_700_000_100) < 1
    assert ops.plan_download(client, ["/users/home-1/photos"], tmp_path).is_noop


def test_plan_sync_push_with_delete(dav, tmp_path):
    client, session = dav
    src = tmp_path / "home"
    (src / "photos").mkdir(parents=True)
    (src / "photos" / "a.jpg").write_bytes(b"A" * 3000)
    os.utime(src / "photos" / "a.jpg", (1_700_000_100, 1_700_000_100))  # identical → skip
    (src / "photos" / "c.jpg").write_bytes(b"C")  # new → upload
    (src / "notes").mkdir()
    (src / "notes" / "n.md").write_text("n")  # new dir + file
    plan = ops.plan_sync(client, src, "/users/home-1", delete=True)
    assert kinds(plan) == [
        ("delete_remote", "/users/home-1/photos/b.jpg"),
        ("delete_remote", "/users/home-1/readme.txt"),
        ("mkdir", "/users/home-1/notes"),
        ("upload", "/users/home-1/notes/n.md"),
        ("upload", "/users/home-1/photos/c.jpg"),
    ]
    res = ops.execute(client, plan)
    assert res.failed == []
    assert "/users/home-1/readme.txt" not in session.files
    assert session.files["/users/home-1/notes/n.md"] == b"n"
    assert ops.plan_sync(client, src, "/users/home-1", delete=True).is_noop


def test_plan_sync_without_delete_keeps_extra_remote(dav, tmp_path):
    client, _ = dav
    src = tmp_path / "empty"
    src.mkdir()
    plan = ops.plan_sync(client, src, "/users/home-1")
    assert plan.of("delete_remote") == [] and plan.is_noop


def test_plan_sync_pull_with_delete(dav, tmp_path):
    client, _ = dav
    dst = tmp_path / "mirror"
    dst.mkdir()
    (dst / "stale.txt").write_text("old")
    plan = ops.plan_sync(client, dst, "/users/home-1", pull=True, delete=True)
    assert kinds(plan) == [
        ("delete_local", str(dst / "stale.txt")),
        ("download", str(dst / "photos" / "a.jpg")),
        ("download", str(dst / "photos" / "b.jpg")),
        ("download", str(dst / "readme.txt")),
        ("mkdir_local", str(dst / "photos")),
    ]
    res = ops.execute(client, plan)
    assert res.failed == []
    assert not (dst / "stale.txt").exists()
    assert (dst / "readme.txt").read_text() == "hello"
    assert ops.plan_sync(client, dst, "/users/home-1", pull=True, delete=True).is_noop


def test_plan_sync_creates_missing_remote_root(dav, tmp_path):
    client, _ = dav
    src = tmp_path / "s"
    src.mkdir()
    (src / "x").write_text("x")
    plan = ops.plan_sync(client, src, "/users/home-1/brand-new")
    assert kinds(plan) == [("mkdir", "/users/home-1/brand-new"), ("upload", "/users/home-1/brand-new/x")]


def test_execute_reports_failures_and_continues(dav, tmp_path):
    client, session = dav
    a = tmp_path / "a"
    a.write_bytes(b"a")
    b = tmp_path / "b"
    b.write_bytes(b"b")
    plan = ops.plan_upload(client, [a, b], "/users/home-1")
    session.fail_next = [409]  # first upload fails hard on 4xx (no retry), second succeeds
    res = ops.execute(client, plan, jobs=1)
    assert len(res.failed) == 1
    assert res.ok == 1


def test_du_and_find(dav):
    client, _ = dav
    assert ops.du(client, "/users/home-1") == (8005, 3, 1)
    names = sorted(e.name for e in ops.find(client, "/", pattern="*.jpg"))
    assert names == ["a.jpg", "b.jpg"]
    assert [e.name for e in ops.find(client, "/", kind="d")] == ["Musik Backup", "users", "home-1", "photos"]
    assert [e.name for e in ops.find(client, "/", min_size=4000)] == ["b.jpg"]
