"""Remote path helpers. Remote paths are always absolute, '/'-separated, no trailing slash except root."""
from __future__ import annotations

import posixpath
from urllib.parse import quote, unquote, urlsplit


def normalize(path: str) -> str:
    """Return a canonical remote path: leading '/', no trailing '/', no '.' or '..' segments."""
    if not path or path in ("/", "."):
        return "/"
    path = path.replace("\\", "/")
    if not path.startswith("/"):
        path = "/" + path
    norm = posixpath.normpath(path)
    if norm == "//":
        norm = "/"
    return norm


def join(base: str, *parts: str) -> str:
    out = normalize(base)
    for p in parts:
        p = p.strip("/")
        if p:
            out = out.rstrip("/") + "/" + p
    return normalize(out)


def name(path: str) -> str:
    path = normalize(path)
    return "/" if path == "/" else posixpath.basename(path)


def parent(path: str) -> str:
    path = normalize(path)
    return "/" if path == "/" else posixpath.dirname(path) or "/"


def to_url(base_url: str, path: str) -> str:
    """Percent-encode a remote path and append it to the WebDAV base URL."""
    path = normalize(path)
    encoded = quote(path, safe="/")
    return base_url.rstrip("/") + encoded


def from_href(href: str, base_url: str = "") -> str:
    """Turn a PROPFIND href (absolute URL or path, percent-encoded) into a normalized remote path."""
    if "://" in href:
        href = urlsplit(href).path
    if base_url:
        base_path = urlsplit(base_url).path.rstrip("/")
        if base_path and href.startswith(base_path):
            href = href[len(base_path):]
    return normalize(unquote(href))


def is_within(child: str, ancestor: str) -> bool:
    child, ancestor = normalize(child), normalize(ancestor)
    if ancestor == "/":
        return True
    return child == ancestor or child.startswith(ancestor + "/")


def relative(path: str, base: str) -> str:
    """Path of `path` relative to `base` ('' when equal)."""
    path, base = normalize(path), normalize(base)
    if base == "/":
        return path.lstrip("/")
    if path == base:
        return ""
    if not path.startswith(base + "/"):
        raise ValueError(f"{path} is not below {base}")
    return path[len(base) + 1:]
