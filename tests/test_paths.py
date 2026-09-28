import pytest

from hidrive_manager import paths


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", "/"),
        ("/", "/"),
        ("users", "/users"),
        ("/users/", "/users"),
        ("/a//b/./c/../d", "/a/b/d"),
        ("\\win\\style", "/win/style"),
        ("/Musik Backup/", "/Musik Backup"),
    ],
)
def test_normalize(raw, expected):
    assert paths.normalize(raw) == expected


def test_join_and_name_parent():
    assert paths.join("/users", "home", "x.txt") == "/users/home/x.txt"
    assert paths.join("/", "a") == "/a"
    assert paths.join("/a/", "/b/") == "/a/b"
    assert paths.name("/a/b.txt") == "b.txt"
    assert paths.name("/") == "/"
    assert paths.parent("/a/b.txt") == "/a"
    assert paths.parent("/a") == "/"
    assert paths.parent("/") == "/"


def test_to_url_percent_encodes_but_keeps_slashes():
    assert paths.to_url("https://h.test/", "/Musik Backup/ä.txt") == "https://h.test/Musik%20Backup/%C3%A4.txt"
    assert paths.to_url("https://h.test", "/") == "https://h.test/"


def test_from_href_variants():
    assert paths.from_href("/Musik%20Backup/") == "/Musik Backup"
    assert paths.from_href("https://h.test/users/home-1/a%20b.txt") == "/users/home-1/a b.txt"
    assert paths.from_href("/") == "/"
    assert paths.from_href("/dav/users/x", base_url="https://h.test/dav") == "/users/x"


def test_relative_and_within():
    assert paths.relative("/a/b/c", "/a") == "b/c"
    assert paths.relative("/a", "/a") == ""
    assert paths.relative("/a/b", "/") == "a/b"
    with pytest.raises(ValueError):
        paths.relative("/x/y", "/a")
    assert paths.is_within("/a/b", "/a")
    assert not paths.is_within("/ab", "/a")
    assert paths.is_within("/anything", "/")
