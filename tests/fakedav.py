"""In-memory WebDAV server that mimics HiDrive's Apache mod_dav, plugged in as a requests Session."""
from __future__ import annotations

import time
from email.utils import formatdate
from urllib.parse import quote, unquote, urlsplit
from xml.sax.saxutils import escape

import requests

BASE = "https://fake.hidrive.test"


class _Raw:
    def __init__(self, body: bytes):
        self._body = body

    def stream(self, n: int, decode_content: bool = True):
        if decode_content:
            raise AssertionError("client must read raw bytes with decode_content=False")
        for i in range(0, len(self._body), n):
            yield self._body[i : i + n]


class FakeResponse:
    """Like HiDrive's Apache: for *.gz paths the body carries Content-Encoding: gzip although
    the bytes are the stored file, so every decoded access raises like requests would."""

    def __init__(self, status: int, body: bytes = b"", headers: dict | None = None, reason: str = "", bogus_encoding: bool = False):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.reason = reason
        self.closed = False
        self.raw = _Raw(body)
        self._bogus = bogus_encoding

    def _decoded(self) -> bytes:
        if self._bogus:
            raise requests.exceptions.ContentDecodingError("Received response with content-encoding: gzip, but failed to decode it.")
        return self._body

    @property
    def content(self) -> bytes:
        return self._decoded()

    @property
    def text(self) -> str:
        return self._decoded().decode("utf-8", "replace")

    def iter_content(self, n: int):
        body = self._decoded()
        for i in range(0, len(body), n):
            yield body[i : i + n]

    def close(self) -> None:
        self.closed = True


class FakeDavSession:
    """Holds a tree of {path: bytes | None}; None marks a directory. Root '/' always exists."""

    def __init__(self, user: str = "u", password: str = "p", clock: float | None = None):
        self.files: dict[str, bytes | None] = {"/": None}
        self.mtimes: dict[str, float] = {"/": clock or time.time()}
        self.auth = None
        self.headers: dict[str, str] = {}
        self.creds = (user, password)
        self.calls: list[tuple[str, str]] = []
        self.fail_next: list[int] = []  # statuses to return before behaving normally
        self.clock = clock

    # --- helpers ---
    def _now(self) -> float:
        return self.clock if self.clock is not None else time.time()

    def add_dir(self, path: str, mtime: float | None = None) -> None:
        parts = [p for p in path.strip("/").split("/") if p]
        cur = ""
        for part in parts:
            cur += "/" + part
            self.files.setdefault(cur, None)
            self.mtimes.setdefault(cur, mtime or self._now())

    def add_file(self, path: str, data: bytes, mtime: float | None = None) -> None:
        parent = path.rsplit("/", 1)[0]
        if parent:
            self.add_dir(parent)
        self.files[path] = data
        self.mtimes[path] = mtime or self._now()

    def _path(self, url: str) -> str:
        p = unquote(urlsplit(url).path)
        if len(p) > 1 and p.endswith("/"):
            p = p[:-1]
        return p or "/"

    def _children(self, path: str) -> list[str]:
        prefix = "" if path == "/" else path
        return [p for p in self.files if p != path and p.startswith(prefix + "/") and "/" not in p[len(prefix) + 1 :]]

    def _descendants(self, path: str) -> list[str]:
        prefix = "" if path == "/" else path
        return [p for p in self.files if p == path or p.startswith(prefix + "/")]

    def _prop_xml(self, path: str) -> str:
        data = self.files[path]
        href = quote(path + ("/" if data is None and path != "/" else ""), safe="/")
        if path == "/":
            href = "/"
        mtime = formatdate(self.mtimes[path], usegmt=True)
        if data is None:
            rtype = "<D:resourcetype><D:collection/></D:resourcetype>"
            length = ""
        else:
            rtype = "<D:resourcetype/>"
            length = f"<D:getcontentlength>{len(data)}</D:getcontentlength>"
        return (
            f"<D:response><D:href>{escape(href)}</D:href><D:propstat><D:prop>"
            f"{rtype}{length}<D:getlastmodified>{mtime}</D:getlastmodified>"
            f'<D:getetag>"{abs(hash((path, self.mtimes[path])))}"</D:getetag>'
            f"</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
        )

    # --- requests.Session API subset ---
    def request(self, method: str, url: str, **kw) -> FakeResponse:
        path = self._path(url)
        self.calls.append((method, path))
        if self.auth != self.creds:
            return FakeResponse(401, reason="Unauthorized")
        if self.fail_next:
            return FakeResponse(self.fail_next.pop(0), reason="injected")
        headers = {k.lower(): v for k, v in (kw.get("headers") or {}).items()}
        h = getattr(self, "_" + method.lower(), None)
        if h is None:
            return FakeResponse(405)
        resp = h(path, headers, kw)
        if path.endswith((".gz", ".tgz")) and method != "PROPFIND":
            resp._bogus = True  # Apache AddEncoding on .gz names
            if method == "PUT":
                resp._body = b"<html>Resource created</html>"
                resp.raw = _Raw(resp._body)
        return resp

    def _propfind(self, path, headers, kw):
        if path not in self.files:
            return FakeResponse(404)
        depth = headers.get("depth", "1")
        targets = [path]
        if depth != "0" and self.files[path] is None:
            targets += self._children(path)
        body = '<?xml version="1.0" encoding="utf-8"?><D:multistatus xmlns:D="DAV:">' + "".join(self._prop_xml(p) for p in targets) + "</D:multistatus>"
        return FakeResponse(207, body.encode())

    def _get(self, path, headers, kw):
        data = self.files.get(path)
        if path not in self.files:
            return FakeResponse(404)
        if data is None:
            return FakeResponse(200, b"<html>index</html>")
        rng = headers.get("range")
        if rng and rng.startswith("bytes="):
            start = int(rng[6:].split("-")[0])
            return FakeResponse(206, data[start:], {"Content-Range": f"bytes {start}-{len(data)-1}/{len(data)}"})
        return FakeResponse(200, data)

    def _put(self, path, headers, kw):
        parent = path.rsplit("/", 1)[0] or "/"
        if parent not in self.files or self.files[parent] is not None:
            return FakeResponse(409, reason="Conflict")
        body = kw.get("data", b"")
        if hasattr(body, "read"):
            chunks = []
            while True:
                c = body.read(65536)
                if not c:
                    break
                chunks.append(c)
            body = b"".join(chunks)
        existed = path in self.files
        self.files[path] = bytes(body)
        self.mtimes[path] = self._now()
        return FakeResponse(204 if existed else 201)

    def _mkcol(self, path, headers, kw):
        if path in self.files:
            return FakeResponse(405)
        parent = path.rsplit("/", 1)[0] or "/"
        if parent not in self.files:
            return FakeResponse(409)
        self.files[path] = None
        self.mtimes[path] = self._now()
        return FakeResponse(201)

    def _delete(self, path, headers, kw):
        if path not in self.files or path == "/":
            return FakeResponse(404)
        for p in self._descendants(path):
            del self.files[p]
            self.mtimes.pop(p, None)
        return FakeResponse(204)

    def _move(self, path, headers, kw, keep=False):
        if path not in self.files:
            return FakeResponse(404)
        dst = self._path(headers["destination"])
        overwrite = headers.get("overwrite", "T") == "T"
        if dst in self.files and not overwrite:
            return FakeResponse(412)
        if dst in self.files:
            for p in self._descendants(dst):
                del self.files[p]
        for p in self._descendants(path):
            new = dst + p[len(path) :]
            self.files[new] = self.files[p]
            self.mtimes[new] = self.mtimes.get(p, self._now())
            if not keep:
                del self.files[p]
        return FakeResponse(201)

    def _copy(self, path, headers, kw):
        return self._move(path, headers, kw, keep=True)

    def _options(self, path, headers, kw):
        return FakeResponse(200, headers={"DAV": "1,2"})


def make_client(session: FakeDavSession | None = None, **kw):
    from hidrive_manager.webdav import WebDavClient

    session = session or FakeDavSession()
    client = WebDavClient(BASE, session.creds[0], session.creds[1], session=session, **kw)
    return client, session
