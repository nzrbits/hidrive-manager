"""Parse WebDAV multistatus responses into Entry objects."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import paths

DAV = "{DAV:}"

PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:resourcetype/><d:getcontentlength/><d:getlastmodified/>"
    "<d:getetag/><d:getcontenttype/><d:displayname/>"
    "</d:prop></d:propfind>"
)


@dataclass
class Entry:
    path: str
    is_dir: bool
    size: int = 0
    mtime: datetime | None = None
    etag: str | None = None
    content_type: str | None = None

    @property
    def name(self) -> str:
        return paths.name(self.path)

    @property
    def mtime_ts(self) -> float | None:
        return self.mtime.timestamp() if self.mtime else None


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_multistatus(body: bytes | str, base_url: str = "") -> list[Entry]:
    """Return one Entry per <d:response> that carries a 2xx propstat."""
    root = ET.fromstring(body)
    entries: list[Entry] = []
    for resp in root.iter(f"{DAV}response"):
        href = _text(resp.find(f"{DAV}href"))
        if href is None:
            continue
        prop = None
        for ps in resp.findall(f"{DAV}propstat"):
            status = _text(ps.find(f"{DAV}status")) or ""
            if " 2" in status or status == "":
                prop = ps.find(f"{DAV}prop")
                if prop is not None:
                    break
        if prop is None:
            # some servers put <prop> directly under <response>
            prop = resp.find(f"{DAV}prop")
        if prop is None:
            continue
        rtype = prop.find(f"{DAV}resourcetype")
        is_dir = rtype is not None and rtype.find(f"{DAV}collection") is not None
        size_txt = _text(prop.find(f"{DAV}getcontentlength"))
        try:
            size = int(size_txt) if size_txt else 0
        except ValueError:
            size = 0
        entries.append(
            Entry(
                path=paths.from_href(href, base_url),
                is_dir=is_dir,
                size=0 if is_dir else size,
                mtime=_parse_date(_text(prop.find(f"{DAV}getlastmodified"))),
                etag=_text(prop.find(f"{DAV}getetag")),
                content_type=_text(prop.find(f"{DAV}getcontenttype")),
            )
        )
    return entries
