"""Minimal, dependency-light WebDAV client tuned for HiDrive (Apache mod_dav)."""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import paths
from .davxml import PROPFIND_BODY, Entry, parse_multistatus

CHUNK = 1024 * 1024
ProgressCb = Callable[[int], None]


class WebDavError(Exception):
    def __init__(self, status: int, message: str, path: str = ""):
        self.status = status
        self.path = path
        super().__init__(f"{message} ({status})" + (f": {path}" if path else ""))


class NotFound(WebDavError):
    pass


class AuthError(WebDavError):
    pass


class _ProgressReader:
    """File wrapper that reports read bytes; exposes __len__ so requests sets Content-Length."""

    def __init__(self, fh, size: int, cb: ProgressCb | None):
        self._fh, self._size, self._cb = fh, size, cb

    def __len__(self) -> int:
        return self._size

    def read(self, n: int = -1) -> bytes:
        data = self._fh.read(n)
        if data and self._cb:
            self._cb(len(data))
        return data


def _raise_for(resp: requests.Response, path: str) -> None:
    s = resp.status_code
    if s < 400:
        return
    if s == 404:
        raise NotFound(s, "not found", path)
    if s in (401, 403):
        raise AuthError(s, "authentication failed" if s == 401 else "forbidden", path)
    try:
        text = (resp.text or "").strip().splitlines()
    except requests.RequestException:  # e.g. bogus Content-Encoding on a .gz path
        text = []
    detail = text[0][:120] if text else resp.reason
    raise WebDavError(s, detail or "request failed", path)


def _raw_chunks(resp: requests.Response, size: int) -> Iterator[bytes]:
    """Iterate the response body as stored on the server.

    Apache tags resources named *.gz / *.tgz with `Content-Encoding: gzip` even though the
    bytes are the file itself, so decoding must be off or requests would gunzip the archive.
    """
    yield from resp.raw.stream(size, decode_content=False)


class WebDavClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: int = 60,
        session: requests.Session | None = None,
        retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        if session is None:
            session = requests.Session()
            retry = Retry(
                total=retries,
                backoff_factor=0.5,
                status_forcelist=(500, 502, 503, 504),
                allowed_methods=frozenset({"GET", "HEAD", "OPTIONS", "PROPFIND", "DELETE", "MKCOL", "MOVE", "COPY"}),
                raise_on_status=False,
            )
            session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
            session.mount("http://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
        session.auth = (username, password)
        session.headers.setdefault("User-Agent", "hidrive-manager")
        session.headers.setdefault("Accept-Encoding", "identity")
        self.session = session

    # --- low level ---------------------------------------------------------------

    def url(self, path: str) -> str:
        return paths.to_url(self.base_url, path)

    def _request(self, method: str, path: str, **kw) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        return self.session.request(method, self.url(path), **kw)

    def _modify(self, method: str, path: str, **kw) -> requests.Response:
        """Request whose response body is irrelevant; never decoded, always closed."""
        resp = self._request(method, path, stream=True, **kw)
        try:
            _raise_for(resp, path)
        finally:
            resp.close()
        return resp

    # --- metadata ----------------------------------------------------------------

    def propfind(self, path: str, depth: int = 1) -> list[Entry]:
        resp = self._request(
            "PROPFIND",
            path,
            headers={"Depth": str(depth), "Content-Type": "application/xml; charset=utf-8"},
            data=PROPFIND_BODY.encode(),
        )
        _raise_for(resp, path)
        if resp.status_code != 207:
            raise WebDavError(resp.status_code, "unexpected PROPFIND response", path)
        return parse_multistatus(resp.content, self.base_url)

    def stat(self, path: str) -> Entry | None:
        try:
            entries = self.propfind(path, depth=0)
        except NotFound:
            return None
        return entries[0] if entries else None

    def exists(self, path: str) -> bool:
        return self.stat(path) is not None

    def listdir(self, path: str) -> list[Entry]:
        path = paths.normalize(path)
        entries = self.propfind(path, depth=1)
        children = [e for e in entries if e.path != path]
        children.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return children

    def walk(self, path: str) -> Iterator[tuple[str, list[Entry], list[Entry]]]:
        """Yield (dirpath, subdirs, files) top-down, like os.walk."""
        path = paths.normalize(path)
        children = self.listdir(path)
        dirs = [c for c in children if c.is_dir]
        files = [c for c in children if not c.is_dir]
        yield path, dirs, files
        for d in dirs:
            yield from self.walk(d.path)

    def check_login(self) -> None:
        self.propfind("/", depth=0)

    # --- directories -------------------------------------------------------------

    def mkdir(self, path: str) -> bool:
        """Create one directory. Returns False when it already existed."""
        resp = self._request("MKCOL", path, stream=True)
        try:
            if resp.status_code == 405:  # method not allowed: collection already exists
                return False
            _raise_for(resp, path)
        finally:
            resp.close()
        return True

    def makedirs(self, path: str) -> None:
        path = paths.normalize(path)
        if path == "/":
            return
        if self.exists(path):
            return
        self.makedirs(paths.parent(path))
        self.mkdir(path)

    # --- transfer ----------------------------------------------------------------

    def upload(self, local: Path, remote: str, progress: ProgressCb | None = None) -> None:
        size = local.stat().st_size
        attempt = 0
        while True:
            attempt += 1
            sent = 0

            def cb(n: int) -> None:
                nonlocal sent
                sent += n
                if progress:
                    progress(n)

            try:
                with open(local, "rb") as fh:
                    body = _ProgressReader(fh, size, cb) if size else b""
                    self._modify("PUT", remote, data=body, headers={"Content-Length": str(size)})
                return
            except (requests.ConnectionError, requests.Timeout, WebDavError) as exc:
                retryable = not isinstance(exc, WebDavError) or exc.status >= 500
                if not retryable or attempt > self.retries:
                    raise
                if progress and sent:
                    progress(-sent)  # roll back the bar for the retry
                time.sleep(0.5 * attempt)

    def download(
        self, remote: str, local: Path, progress: ProgressCb | None = None, resume: bool = True, expected_size: int | None = None
    ) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        part = local.with_name(local.name + ".part")
        offset = part.stat().st_size if (resume and part.exists()) else 0
        if expected_size is not None and offset > expected_size:
            offset = 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        resp = self._request("GET", remote, headers=headers, stream=True)
        try:
            _raise_for(resp, remote)
            if offset and resp.status_code != 206:
                offset = 0  # server ignored the range; start over
            elif offset and progress:
                progress(offset)
            mode = "ab" if offset else "wb"
            with open(part, mode) as fh:
                for chunk in _raw_chunks(resp, CHUNK):
                    if chunk:
                        fh.write(chunk)
                        if progress:
                            progress(len(chunk))
        finally:
            resp.close()
        os.replace(part, local)

    def stream(self, remote: str, chunk: int = CHUNK) -> Iterator[bytes]:
        """Yield the raw bytes of a remote file."""
        resp = self._request("GET", remote, stream=True)
        try:
            _raise_for(resp, remote)
            yield from _raw_chunks(resp, chunk)
        finally:
            resp.close()

    # --- modify ------------------------------------------------------------------

    def delete(self, path: str) -> None:
        self._modify("DELETE", path)

    def _move_or_copy(self, method: str, src: str, dst: str, overwrite: bool) -> None:
        resp = self._request(method, src, stream=True, headers={"Destination": self.url(dst), "Overwrite": "T" if overwrite else "F"})
        try:
            if resp.status_code == 412:
                raise WebDavError(412, "destination exists (use --force to overwrite)", dst)
            _raise_for(resp, src)
        finally:
            resp.close()

    def move(self, src: str, dst: str, overwrite: bool = False) -> None:
        self._move_or_copy("MOVE", src, dst, overwrite)

    def copy(self, src: str, dst: str, overwrite: bool = False) -> None:
        self._move_or_copy("COPY", src, dst, overwrite)
