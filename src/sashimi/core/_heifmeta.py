# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Removing metadata from HEIF images (HEIC, AVIF) in place: the EXIF and XMP items are rewritten at
the same offsets and lengths, so every image item (the photo, its tiles, gain map, depth map and
thumbnails) keeps its bytes and nothing is re-encoded. Pure.
"""

import struct
from dataclasses import dataclass, field

from sashimi.config import MetadataMode
from sashimi.core._tiffmeta import GPS_IFD, TiffEditor, TiffStructureError, exif_orientation, orientation_only

EMPTY_XMP = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"/>'
"""The XMP packet left in place of one whose properties are all removed: valid, and saying nothing."""
_EMPTY_TIFF = b"II*\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00"
"""A TIFF structure (EXIF block) with one empty directory."""


class HeifStructureError(ValueError):
    """The bytes aren't a HEIF structure this module can rewrite."""


@dataclass
class _Item:
    item_id: int
    kind: bytes = b""
    content_type: bytes = b""
    extents: list[tuple[int, int]] = field(default_factory=list)
    """Each extent as (offset in the file, length)."""
    describes: list[int] = field(default_factory=list)
    """The items a ``cdsc`` reference says this metadata item describes."""


def _boxes(data: bytes, start: int, end: int) -> list[tuple[bytes, int, int, int]]:
    """The boxes between `start` and `end`, as (type, start, payload start, end)."""
    found = []
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            if pos + 16 > end:
                raise HeifStructureError("box header runs past the data")
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise HeifStructureError(f"box {kind!r} at {pos} runs past its parent")
        found.append((kind, pos, pos + header, pos + size))
        pos += size
    return found


def _uint(data: bytes, pos: int, size: int) -> int:
    if size == 0:
        return 0
    if pos + size > len(data):
        raise HeifStructureError("field runs past the data")
    return int.from_bytes(data[pos : pos + size], "big")


def _items(data: bytes, meta_start: int, meta_end: int) -> tuple[dict[int, _Item], int | None]:
    """The items of the ``meta`` box whose payload (after its version and flags) spans the range, and the primary item."""
    items: dict[int, _Item] = {}
    children = {kind: (payload, end) for kind, _, payload, end in _boxes(data, meta_start, meta_end)}
    primary = None
    if b"pitm" in children:
        payload, _ = children[b"pitm"]
        primary = _uint(data, payload + 4, 2 if data[payload] == 0 else 4)
    if b"iinf" in children:
        payload, end = children[b"iinf"]
        version = data[payload]
        first = payload + 4 + (2 if version == 0 else 4)
        for kind, _, entry, entry_end in _boxes(data, first, end):
            if kind != b"infe" or data[entry] < 2:
                continue
            version = data[entry]
            pos = entry + 4
            width = 2 if version == 2 else 4
            item_id = _uint(data, pos, width)
            pos += width + 2
            item_type = bytes(data[pos : pos + 4])
            pos += 4
            content_type = b""
            if item_type == b"mime":
                parts = bytes(data[pos:entry_end]).split(b"\0")
                content_type = parts[1] if len(parts) > 1 else b""
            items[item_id] = _Item(item_id, item_type, content_type)
    idat = children.get(b"idat", (None, None))[0]
    if b"iloc" in children:
        payload, _ = children[b"iloc"]
        _locations(data, payload, items, idat)
    if b"iref" in children:
        payload, end = children[b"iref"]
        width = 2 if data[payload] == 0 else 4
        for kind, _, pos, _ in _boxes(data, payload + 4, end):
            source = _uint(data, pos, width)
            count = _uint(data, pos + width, 2)
            targets = [_uint(data, pos + width + 2 + index * width, width) for index in range(count)]
            if kind == b"cdsc" and source in items:
                items[source].describes += targets
    return items, primary


def _locations(data: bytes, payload: int, items: dict[int, _Item], idat: int | None) -> None:
    version = data[payload]
    pos = payload + 4
    sizes = _uint(data, pos, 2)
    offset_size, length_size = sizes >> 12, (sizes >> 8) & 15
    base_size, index_size = (sizes >> 4) & 15, (sizes & 15 if version in (1, 2) else 0)
    pos += 2
    count = _uint(data, pos, 2 if version < 2 else 4)
    pos += 2 if version < 2 else 4
    for _ in range(count):
        item_id = _uint(data, pos, 2 if version < 2 else 4)
        pos += 2 if version < 2 else 4
        method = 0
        if version in (1, 2):
            method = _uint(data, pos, 2) & 15
            pos += 2
        pos += 2
        base = _uint(data, pos, base_size)
        pos += base_size
        extent_count = _uint(data, pos, 2)
        pos += 2
        extents = []
        for _ in range(extent_count):
            pos += index_size
            offset = _uint(data, pos, offset_size)
            pos += offset_size
            length = _uint(data, pos, length_size)
            pos += length_size
            extents.append((base + offset, length))
        item = items.get(item_id)
        if item is None:
            continue
        if method == 1:
            if idat is None:
                raise HeifStructureError("item data in a missing idat box")
            extents = [(idat + offset, length) for offset, length in extents]
        elif method != 0:
            raise HeifStructureError(f"item {item_id} is stored by reference (construction method {method})")
        if any(length == 0 for _, length in extents):
            raise HeifStructureError(f"item {item_id} runs to the end of the file")
        item.extents = extents


def _read(data: bytes, item: _Item) -> bytes:
    for offset, length in item.extents:
        if offset + length > len(data):
            raise HeifStructureError(f"item {item.item_id} runs past the data")
    return b"".join(bytes(data[offset : offset + length]) for offset, length in item.extents)


def _write(buf: bytearray, item: _Item, content: bytes) -> None:
    pos = 0
    for offset, length in item.extents:
        buf[offset : offset + length] = content[pos : pos + length]
        pos += length


def drop_gps_in_place(tiff: bytes) -> bytes:
    """
    An EXIF block (a TIFF structure) without its GPS directory, the same length as `tiff`: the GPS
    directory and its values are zeroed and its entry is taken out of IFD0 where it stands, so
    every other value (maker notes included) keeps its offset.
    """
    editor = TiffEditor(tiff)
    first = editor.first
    entries, following, length = editor.directory(first)
    kept = [entry for entry in entries if entry.tag != GPS_IFD]
    if len(kept) == len(entries):
        return tiff
    for entry in entries:
        if entry.tag == GPS_IFD:
            editor.erase_directory(editor.pointer(entry))
    rows = b"".join(editor.buf[entry.position : entry.position + 12] for entry in kept)
    head = struct.pack(editor.order + "H", len(kept))
    tail = struct.pack(editor.order + "I", following)
    rewritten = head + rows + tail
    editor.buf[first : first + length] = rewritten + bytes(length - len(rewritten))
    return bytes(editor.buf)


def _new_exif(item: bytes, mode: MetadataMode) -> bytes:
    """An EXIF item's new content, the same length: a 4-byte offset to the TIFF header, then the TIFF structure."""
    if len(item) < 4:
        return bytes(len(item))
    start = 4 + int.from_bytes(item[:4], "big")
    if start > len(item):
        return bytes(len(item))
    tiff = item[start:]
    try:
        if mode == "remove_location":
            edited = drop_gps_in_place(tiff)
        else:
            orientation = exif_orientation(tiff)
            edited = orientation_only(orientation) if orientation not in (None, 1) else _EMPTY_TIFF
    except (TiffStructureError, struct.error):
        edited = _EMPTY_TIFF
    content = item[:start] + edited
    if len(content) > len(item):
        content = item[:start] + _EMPTY_TIFF
    if len(content) > len(item):
        return bytes(len(item))
    return content + bytes(len(item) - len(content))


def _new_xmp(item: bytes, mode: MetadataMode, keep: bool) -> bytes:
    """An XMP item's new content, the same length, padded with spaces (which XML allows after the packet)."""
    from sashimi.core._imagemeta import xmp_without_location

    content = xmp_without_location(item) if keep else EMPTY_XMP
    if len(content) > len(item):
        content = EMPTY_XMP if len(EMPTY_XMP) <= len(item) else b""
    return content + b" " * (len(item) - len(content))


def _auxiliary(data: bytes, meta_start: int, meta_end: int) -> set[int]:
    """Items an ``auxl`` reference makes auxiliary images (gain maps, depth and portrait mattes) of another."""
    found: set[int] = set()
    for kind, _, payload, end in _boxes(data, meta_start, meta_end):
        if kind != b"iref":
            continue
        width = 2 if data[payload] == 0 else 4
        for ref, _, pos, _ in _boxes(data, payload + 4, end):
            if ref == b"auxl":
                found.add(_uint(data, pos, width))
    return found


def _drop_icc(buf: bytearray, data: bytes, meta_start: int, meta_end: int) -> None:
    """Turns ICC colour boxes (``colr`` of type ``prof`` or ``rICC``) into zeroed ``free`` boxes, keeping property order."""
    for kind, _, payload, end in _boxes(data, meta_start, meta_end):
        if kind != b"iprp":
            continue
        for container, _, inner, inner_end in _boxes(data, payload, end):
            if container != b"ipco":
                continue
            for prop, start, body, prop_end in _boxes(data, inner, inner_end):
                if prop == b"colr" and bytes(data[body : body + 4]) in (b"prof", b"rICC"):
                    buf[start + 4 : start + 8] = b"free"
                    buf[body:prop_end] = bytes(prop_end - body)


def strip_heif(data: bytes, mode: MetadataMode, keep_icc: bool) -> bytes:
    """
    A HEIF image (HEIC or AVIF) without its metadata, as `mode` says, the same size and with every
    image item's bytes unchanged. ``remove_location`` removes the GPS directory from the EXIF item
    and location properties from every XMP item; ``remove_all`` empties the EXIF item (keeping an
    EXIF orientation other than 1) and every XMP item except those describing auxiliary images
    (gain maps, depth and portrait mattes), which say how to show them and lose only their location
    properties. The orientation that HEIF viewers apply (``irot``/``imir``) is never touched.
    `keep_icc` False turns the colour profile into padding. Raises :class:`HeifStructureError`.
    """
    top = _boxes(data, 0, len(data))
    meta = next(((payload, end) for kind, _, payload, end in top if kind == b"meta"), None)
    if meta is None:
        raise HeifStructureError("no meta box")
    meta_start, meta_end = meta[0] + 4, meta[1]
    items, _ = _items(data, meta_start, meta_end)
    auxiliary = _auxiliary(data, meta_start, meta_end)
    buf = bytearray(data)
    for item in items.values():
        if not item.extents:
            continue
        if item.kind == b"Exif":
            _write(buf, item, _new_exif(_read(data, item), mode))
        elif item.kind == b"mime" and item.content_type.lower() == b"application/rdf+xml":
            keep = mode == "remove_location" or (bool(item.describes) and set(item.describes) <= auxiliary)
            _write(buf, item, _new_xmp(_read(data, item), mode, keep))
    if not keep_icc:
        _drop_icc(buf, data, meta_start, meta_end)
    return bytes(buf)
