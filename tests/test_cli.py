import json

import pytest

from hidrive_manager import cli, config


@pytest.fixture
def run(dav, isolated_config, monkeypatch, capsys):
    client, session = dav
    monkeypatch.setattr(cli, "_client", lambda args: client)
    config.save(config.Settings(username="u"))

    def _run(*argv):
        code = cli.main(list(argv))
        out, err = capsys.readouterr()
        return code, out, err

    _run.session = session
    return _run


def test_ls_plain_and_long(run):
    code, out, _ = run("ls", "/users/home-1")
    assert code == 0
    assert out.splitlines() == ["photos/", "readme.txt"]
    code, out, _ = run("ls", "-l", "/users/home-1")
    assert out.splitlines()[0].startswith("d")
    assert "5 B" in out


def test_ls_recursive_and_json(run):
    code, out, _ = run("ls", "-R", "--json", "/users/home-1")
    data = json.loads(out)
    assert {d["path"] for d in data} == {
        "/users/home-1/photos",
        "/users/home-1/readme.txt",
        "/users/home-1/photos/a.jpg",
        "/users/home-1/photos/b.jpg",
    }


def test_ls_missing_path_exits_1(run):
    code, _, err = run("ls", "/nope")
    assert code == 1 and "not found" in err


def test_tree(run):
    code, out, _ = run("tree", "/users/home-1")
    assert code == 0
    assert "├── photos/" in out and "│   ├── a.jpg" in out and "└── readme.txt" in out
    assert out.strip().endswith("1 directories, 3 files")


def test_stat(run):
    code, out, _ = run("stat", "--json", "/users/home-1/readme.txt")
    assert json.loads(out)["size"] == 5


def test_put_dry_run_and_real(run, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    code, out, _ = run("put", "-n", str(f), "/users/home-1")
    assert code == 0 and "upload" in out and "/users/home-1/x.txt" in out
    assert "/users/home-1/x.txt" not in run.session.files
    code, out, _ = run("-q", "put", str(f), "/users/home-1")
    assert code == 0
    assert run.session.files["/users/home-1/x.txt"] == b"x"
    code, out, _ = run("put", str(f), "/users/home-1")
    assert "nothing to do" in out


def test_put_missing_local(run, tmp_path):
    code, _, err = run("put", str(tmp_path / "missing"), "/users/home-1")
    assert code == 1 and "local path not found" in err


def test_get(run, tmp_path):
    code, _, _ = run("-q", "get", "/users/home-1/photos", "-o", str(tmp_path))
    assert code == 0
    assert (tmp_path / "photos" / "a.jpg").stat().st_size == 3000


def test_sync_asks_before_delete_and_respects_yes(run, tmp_path, monkeypatch):
    src = tmp_path / "s"
    src.mkdir()
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    code, out, _ = run("sync", "--delete", str(src), "/users/home-1")
    assert code == 1 and "aborted" in out
    assert "/users/home-1/readme.txt" in run.session.files
    code, out, _ = run("-q", "sync", "--delete", "-y", str(src), "/users/home-1")
    assert code == 0
    assert "/users/home-1/readme.txt" not in run.session.files


def test_rm_requires_recursive_for_dirs_and_confirmation(run, monkeypatch):
    code, _, err = run("rm", "-y", "/users/home-1/photos")
    assert code == 1 and "is a directory" in err
    code, out, _ = run("rm", "-r", "-y", "/users/home-1/photos")
    assert code == 0 and "deleted /users/home-1/photos" in out
    assert "/users/home-1/photos/a.jpg" not in run.session.files
    code, _, err = run("rm", "-y", "/users/home-1/photos")
    assert code == 1
    code, _, _ = run("rm", "-y", "-f", "/users/home-1/photos")
    assert code == 0


def test_rm_refuses_without_tty(run, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    code, _, err = run("rm", "/users/home-1/readme.txt")
    assert code == 1 and "--yes" in err
    assert "/users/home-1/readme.txt" in run.session.files


def test_mv_into_directory_and_cp(run):
    code, out, _ = run("mv", "/users/home-1/readme.txt", "/Musik Backup")
    assert code == 0 and out.strip() == "/users/home-1/readme.txt -> /Musik Backup/readme.txt"
    code, out, _ = run("cp", "/Musik Backup/readme.txt", "/users/home-1/readme-copy.txt")
    assert code == 0
    assert run.session.files["/users/home-1/readme-copy.txt"] == b"hello"
    code, _, err = run("cp", "/Musik Backup/readme.txt", "/users/home-1/readme-copy.txt")
    assert code == 1 and "--force" in err


def test_mkdir(run):
    code, out, _ = run("mkdir", "/users/home-1/a/b/c")
    assert code == 0 and run.session.files["/users/home-1/a/b/c"] is None


def test_du(run):
    code, out, _ = run("du", "/users/home-1")
    assert code == 0
    lines = out.splitlines()
    assert lines[0].endswith("/users/home-1/photos")
    assert "(total)" in lines[-2]
    assert lines[-1] == "3 files, 1 directories"
    code, out, _ = run("du", "--json", "-s", "/users/home-1")
    assert json.loads(out)["bytes"] == 8005


def test_find(run):
    code, out, _ = run("find", "/", "--name", "*.jpg", "--min-size", "4k")
    assert out.split() == ["/users/home-1/photos/b.jpg"]
    code, out, _ = run("find", "/", "--type", "d")
    assert "/users/home-1/photos" in out


def test_cat(dav, isolated_config, monkeypatch, capsysbinary):
    client, _ = dav
    monkeypatch.setattr(cli, "_client", lambda args: client)
    code = cli.main(["cat", "/users/home-1/readme.txt"])
    assert code == 0
    assert capsysbinary.readouterr().out == b"hello"


def test_config_set_and_show(run):
    code, out, _ = run("config", "--set", "jobs=8")
    assert code == 0 and "jobs        : 8" in out
    assert config.load().jobs == 8
    code, _, err = run("config", "--set", "bogus=1")
    assert code == 1 and "unknown setting" in err


def test_parse_size():
    assert cli._parse_size("100") == 100
    assert cli._parse_size("4k") == 4096
    assert cli._parse_size("1.5M") == int(1.5 * 1024**2)
    assert cli._parse_size("2GiB") == 2 * 1024**3


def test_missing_credentials_exit_2(isolated_config, monkeypatch, capsys):
    monkeypatch.delenv("HIDRIVE_PASSWORD")
    code = cli.main(["ls", "/"])
    assert code == 2
    assert "hidrive login" in capsys.readouterr().err


def test_version(capsys):
    with pytest.raises(SystemExit) as ei:
        cli.main(["--version"])
    assert ei.value.code == 0
    assert "hidrive-manager" in capsys.readouterr().out


def test_login_password_stdin_stores_in_keyring(dav, isolated_config, monkeypatch, capsys):
    import io

    client, session = dav
    stored = {}
    monkeypatch.setattr(cli, "WebDavClient", lambda *a, **k: client)
    monkeypatch.setattr(config, "set_password", lambda u, p: stored.update({u: p}))
    monkeypatch.delenv("HIDRIVE_PASSWORD")
    monkeypatch.setattr("sys.stdin", io.StringIO("p\n"))
    code = cli.main(["login", "u", "--password-stdin"])
    assert code == 0
    assert stored == {"u": "p"}
    assert config.load().username == "u"


def test_login_without_tty_and_without_stdin_flag_fails_clearly(isolated_config, monkeypatch, capsys):
    monkeypatch.delenv("HIDRIVE_PASSWORD")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    code = cli.main(["login", "u"])
    assert code == 1
    assert "--password-stdin" in capsys.readouterr().err
