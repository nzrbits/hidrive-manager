from datetime import timezone

from hidrive_manager.davxml import parse_multistatus

HIDRIVE_SAMPLE = b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:g0="DAV:">
<D:response>
<D:href>/</D:href>
<D:propstat><D:prop>
<D:resourcetype><D:collection/></D:resourcetype>
<D:getlastmodified>Mon, 28 Sep 2026 10:00:00 GMT</D:getlastmodified>
</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
<D:propstat><D:prop><g0:quota-available-bytes/></D:prop>
<D:status>HTTP/1.1 404 Not Found</D:status></D:propstat>
</D:response>
<D:response>
<D:href>/Musik%20Backup/</D:href>
<D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop>
<D:status>HTTP/1.1 200 OK</D:status></D:propstat>
</D:response>
<D:response>
<D:href>/users/home-1/song%20%C3%A4.flac</D:href>
<D:propstat><D:prop>
<D:resourcetype/>
<D:getcontentlength>12345</D:getcontentlength>
<D:getlastmodified>Sun, 27 Sep 2026 08:30:15 GMT</D:getlastmodified>
<D:getetag>"abc-123"</D:getetag>
<D:getcontenttype>audio/flac</D:getcontenttype>
</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
</D:response>
</D:multistatus>"""


def test_parse_hidrive_sample():
    entries = parse_multistatus(HIDRIVE_SAMPLE)
    by_path = {e.path: e for e in entries}
    assert set(by_path) == {"/", "/Musik Backup", "/users/home-1/song ä.flac"}
    root = by_path["/"]
    assert root.is_dir and root.size == 0
    assert root.mtime.tzinfo is not None and root.mtime.astimezone(timezone.utc).hour == 10
    f = by_path["/users/home-1/song ä.flac"]
    assert not f.is_dir
    assert f.size == 12345
    assert f.etag == '"abc-123"'
    assert f.content_type == "audio/flac"
    assert f.name == "song ä.flac"
    assert abs(f.mtime_ts - 1790497815) < 1  # 2026-09-27T08:30:15Z


def test_missing_props_are_tolerated():
    body = b'<D:multistatus xmlns:D="DAV:"><D:response><D:href>/x</D:href><D:propstat><D:prop><D:resourcetype/></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>'
    (e,) = parse_multistatus(body)
    assert e.path == "/x" and e.size == 0 and e.mtime is None and e.mtime_ts is None


def test_absolute_hrefs_are_stripped():
    body = b'<D:multistatus xmlns:D="DAV:"><D:response><D:href>https://webdav.hidrive.ionos.com/a/b</D:href><D:propstat><D:prop><D:resourcetype/></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>'
    (e,) = parse_multistatus(body, "https://webdav.hidrive.ionos.com")
    assert e.path == "/a/b"
