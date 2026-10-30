# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
The metadata carried by image files (EXIF, XMP, IPTC and Photoshop resources, comments, PNG
text), read from the file's own structure without decoding pixels; what each ``metadata`` mode
keeps; how an output format stores it; and removing it from a file without re-encoding.
"""

import math
import os
import re
import struct
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from typing import Any

from PIL import Image

from sashimi.config import MetadataMode
from sashimi.core._tiffmeta import (
    EXIF_IFD,
    GPS_IFD,
    INTEROP_IFD,
    TiffEditor,
    TiffStructureError,
    edit_exif,
    exif_is_trivial,
    exif_orientation,
    gps_directory,
    orientation_only,
)
from sashimi.errors import UnsupportedMediaError

EXIF_PREFIX = b"Exif\x00\x00"
XMP_JPEG = b"http://ns.adobe.com/xap/1.0/\x00"
XMP_EXTENSION_JPEG = b"http://ns.adobe.com/xmp/extension/\x00"
PHOTOSHOP_JPEG = b"Photoshop 3.0\x00"
XMP_PNG_KEY = b"XML:com.adobe.xmp"
GIF_XMP_ID = b"XMP DataXMP"
GIF_XMP_TRAILER = b"\x01" + bytes(range(255, -1, -1)) + b"\x00"
_MAX_JPEG_SEGMENT = 65533

TIFF_STRUCTURAL = frozenset(
    {254, 255, 256, 257, 258, 259, 262, 263, 264, 265, 266, 273, 277, 278, 279, 280, 281, 282, 283, 284}
    | {286, 287, 290, 291, 292, 293, 296, 297, 301, 317, 318, 319, 320, 321, 322, 323, 324, 325, 330}
    | {332, 334, 338, 339, 340, 341, 342, 347, 512, 513, 514, 515, 517, 518, 519, 520, 521, 529, 530}
    | {531, 532, 32995, 32996, 32997, 32998, 34675}
)
"""TIFF tags that describe the image itself (size, layout, colour, strips), so are never metadata."""
TIFF_ICC = 34675
TIFF_XMP = 700
TIFF_IPTC = 33723
TIFF_PHOTOSHOP = 34377
TIFF_DNG_VERSION = 50706

IPTC_LOCATION = frozenset({26, 27, 90, 92, 95, 100, 101})
"""IPTC IIM record 2 datasets that name a place: location code and name, city, sublocation, province or state, country code and name."""
PHOTOSHOP_IPTC = 0x0404
PHOTOSHOP_EXIF = 0x0422
PHOTOSHOP_EXIF_2 = 0x0423
PHOTOSHOP_XMP = 0x0424
PHOTOSHOP_THUMBNAILS = frozenset({0x0409, 0x040C})

_XMP_LOCATION_NAME = re.compile(
    rb"(?i)^(gps\w*|\w*latitude|\w*longitude|\w*altitude|city|state|country|countrycode|countryname|"
    rb"provincestate|provincename|sublocation|location\w*|worldregion|\w*geo\w*)$"
)
_XMP_SKIP_PREFIXES = frozenset({b"rdf", b"x", b"xml", b"xmlns"})
_NAME = rb"[A-Za-z_][\w.-]*"
_XMP_ATTRIBUTE = re.compile(rb"\s(" + _NAME + rb"):(" + _NAME + rb")\s*=\s*(\"[^\"]*\"|'[^']*')")
_XMP_EMPTY_ELEMENT = re.compile(rb"<(" + _NAME + rb"):(" + _NAME + rb")(\s[^<>]*)?/>")
_XMP_START = re.compile(rb"<(" + _NAME + rb"):(" + _NAME + rb")(\s[^<>]*)?>")
_XMP_ORIENTATION_ATTRIBUTE = re.compile(rb"(:Orientation\s*=\s*[\"'])\s*\d+\s*([\"'])")
_XMP_ORIENTATION_ELEMENT = re.compile(rb"(<(" + _NAME + rb"):Orientation>)\s*\d+\s*(</\2:Orientation>)")
_PNG_LOCATION_KEY = re.compile(rb"(?i)(gps|location|latitude|longitude|geo|city|country)")
_PNG_RAW_PROFILE = re.compile(rb"(?i)^raw profile type (exif|app1|xmp|iptc|8bim)$")
_PNG_TEXT = frozenset({b"tEXt", b"zTXt", b"iTXt"})
_PNG_DISPLAY = frozenset(
    {b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"sBIT", b"bKGD", b"pHYs", b"hIST", b"sPLT", b"acTL", b"fcTL", b"fdAT"}
    | {b"cICP", b"mDCv", b"cLLi"}
)
"""PNG ancillary chunks that affect how the picture or animation is shown, so are kept by ``remove_all``."""
_WEBP_IMAGE = frozenset({b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF"})
_GIF_PLAYBACK = (b"NETSCAPE2.0", b"ANIMEXTS1.0")
_GIF_ICC = b"ICCRGBG1012"

CANT_STRIP = "Removing metadata from this kind of image isn't supported."
DAMAGED = "This image file is damaged or incomplete."


@dataclass
class Carriers:
    """The source metadata an output can carry, as raw bytes in standard layouts."""

    exif: bytes | None = None
    """An EXIF block: a TIFF structure, without the ``Exif\\0\\0`` prefix."""
    xmp: bytes | None = None
    photoshop: bytes | None = None
    """Photoshop image resources (8BIM blocks); IPTC is resource 0x0404."""
    comments: list[bytes] = field(default_factory=list)
    png_text: list[tuple[bytes, bytes]] = field(default_factory=list)
    """PNG text chunks other than XMP, as (chunk type, payload), from a PNG source."""
    found: list[str] = field(default_factory=list)
    """The carriers found in the source, including those no output carries (such as extended XMP)."""
    dpi: tuple[float, float] | None = None
    """The resolution the source states outside EXIF, in dots per inch (see :func:`stated_dpi`)."""


def _note(carriers: Carriers, name: str) -> None:
    if name not in carriers.found:
        carriers.found.append(name)


def jpeg_pieces(data: bytes) -> tuple[list[tuple[int | None, bytes]], bytes]:
    """
    A JPEG split into its marker segments, as (marker, whole segment), and its entropy-coded data,
    as (None, bytes), ending with EOI; plus whatever follows EOI. Raises ValueError if damaged.
    """
    if data[:2] != b"\xff\xd8":
        raise ValueError("no SOI")
    pieces: list[tuple[int | None, bytes]] = [(0xD8, data[:2])]
    pos, size = 2, len(data)
    while pos < size:
        if data[pos] != 0xFF:
            raise ValueError(f"expected a marker at {pos}")
        while pos + 1 < size and data[pos + 1] == 0xFF:
            pos += 1
        if pos + 1 >= size:
            raise ValueError("truncated marker")
        marker = data[pos + 1]
        if marker == 0xD9:
            pieces.append((0xD9, b"\xff\xd9"))
            return pieces, data[pos + 2 :]
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            pieces.append((marker, data[pos : pos + 2]))
            pos += 2
            continue
        if pos + 4 > size:
            raise ValueError("truncated segment")
        length = struct.unpack_from(">H", data, pos + 2)[0]
        if length < 2 or pos + 2 + length > size:
            raise ValueError(f"segment at {pos} runs past the data")
        pieces.append((marker, data[pos : pos + 2 + length]))
        pos += 2 + length
        if marker == 0xDA:
            scan = pos
            while True:
                found = data.find(b"\xff", scan)
                if found < 0 or found + 1 >= size:
                    pieces.append((None, data[pos:]))
                    return pieces, b""
                following = data[found + 1]
                if following == 0 or 0xD0 <= following <= 0xD7 or following == 0xFF:
                    scan = found + 1
                    continue
                break
            pieces.append((None, data[pos:found]))
            pos = found
    return pieces, b""


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def png_chunks(data: bytes) -> Iterator[tuple[bytes, int, int]]:
    """Each chunk of a PNG as (type, start of the chunk, end of the chunk). Raises ValueError if damaged."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("no PNG signature")
    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        kind = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if end > len(data):
            raise ValueError("chunk runs past the data")
        yield kind, pos, end
        pos = end
        if kind == b"IEND":
            return


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def png_text(kind: bytes, payload: bytes) -> tuple[bytes, bytes]:
    """The keyword and text of a tEXt, zTXt or iTXt chunk (decompressed)."""
    key, _, rest = payload.partition(b"\0")
    try:
        if kind == b"tEXt":
            return key, rest
        if kind == b"zTXt":
            return key, zlib.decompress(rest[1:])
        compressed, rest = rest[:1], rest[2:]
        _, _, rest = rest.partition(b"\0")
        _, _, text = rest.partition(b"\0")
        return key, zlib.decompress(text) if compressed == b"\x01" else text
    except zlib.error:
        return key, b""


def webp_chunks(data: bytes) -> Iterator[tuple[bytes, int, int]]:
    """Each chunk of a WebP as (fourcc, start, end including padding). Raises ValueError if damaged."""
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("not a RIFF WebP")
    pos = 12
    while pos + 8 <= len(data):
        size = struct.unpack_from("<I", data, pos + 4)[0]
        end = pos + 8 + size + (size & 1)
        if pos + 8 + size > len(data):
            raise ValueError("chunk runs past the data")
        yield data[pos : pos + 4], pos, min(end, len(data))
        pos = end


def gif_blocks(data: bytes) -> Iterator[tuple[str, int, int]]:
    """
    The blocks of a GIF as (kind, start, end): ``header`` (with the global colour table),
    ``extension`` (label at start + 1), ``image`` and ``trailer``. Raises ValueError if damaged.
    """
    if len(data) < 13 or data[:3] != b"GIF":
        raise ValueError("not a GIF")
    pos = 13
    if data[10] & 0x80:
        pos += 3 << ((data[10] & 7) + 1)
    yield "header", 0, pos

    def sub_blocks(at: int) -> int:
        while True:
            if at >= len(data):
                raise ValueError("sub-blocks run past the data")
            if data[at] == 0:
                return at + 1
            at += 1 + data[at]

    while pos < len(data):
        kind = data[pos]
        if kind == 0x3B:
            yield "trailer", pos, pos + 1
            return
        if kind == 0x21:
            end = sub_blocks(pos + 2)
            yield "extension", pos, end
        elif kind == 0x2C:
            if pos + 10 > len(data):
                raise ValueError("truncated image descriptor")
            packed = data[pos + 9]
            start = pos + 10 + ((3 << ((packed & 7) + 1)) if packed & 0x80 else 0)
            end = sub_blocks(start + 1)
            yield "image", pos, end
        else:
            raise ValueError(f"unknown GIF block 0x{kind:02x}")
        pos = end


def _gif_sub_blocks(data: bytes, start: int, end: int) -> bytes:
    out = bytearray()
    at = start
    while at < end and data[at]:
        out += data[at + 1 : at + 1 + data[at]]
        at += 1 + data[at]
    return bytes(out)


def _gif_application(data: bytes, start: int) -> bytes:
    return data[start + 3 : start + 3 + data[start + 2]] if start + 3 <= len(data) else b""


def _gif_xmp(data: bytes, start: int, end: int) -> bytes:
    raw = data[start + 14 : end]
    return raw[: -len(GIF_XMP_TRAILER)] if raw.endswith(GIF_XMP_TRAILER) else raw[:-1]


def _gif_xmp_extension(xmp: bytes) -> bytes:
    return b"\x21\xff\x0b" + GIF_XMP_ID + xmp + GIF_XMP_TRAILER


def _gif_comment_extension(comment: bytes) -> bytes:
    blocks = b"".join(bytes([len(part)]) + part for part in (comment[i : i + 255] for i in range(0, len(comment), 255)))
    return b"\x21\xfe" + blocks + b"\x00"


def xmp_has_location(xmp: bytes) -> bool:
    """True if an XMP packet has a property that looks like a location (GPS, latitude, city, …)."""
    return xmp_without_location(xmp) != xmp


def _location_property(prefix: bytes, name: bytes) -> bool:
    return prefix not in _XMP_SKIP_PREFIXES and _XMP_LOCATION_NAME.match(name) is not None


def _without_location_elements(xmp: bytes) -> bytes:
    out = bytearray()
    pos = 0
    while (match := _XMP_START.search(xmp, pos)) is not None:
        if not _location_property(match[1], match[2]):
            out += xmp[pos : match.end()]
            pos = match.end()
            continue
        close = re.compile(rb"</" + re.escape(match[1]) + rb":" + re.escape(match[2]) + rb"\s*>").search(
            xmp, match.end()
        )
        out += xmp[pos : match.start()]
        pos = close.end() if close is not None else match.end()
    return bytes(out + xmp[pos:])


def xmp_without_location(xmp: bytes) -> bytes:
    """An XMP packet without its location properties (as attributes or elements); the rest is unchanged."""
    xmp = _XMP_ATTRIBUTE.sub(lambda m: b"" if _location_property(m[1], m[2]) else m[0], xmp)
    xmp = _XMP_EMPTY_ELEMENT.sub(lambda m: b"" if _location_property(m[1], m[2]) else m[0], xmp)
    return _without_location_elements(xmp)


_MOTION_NAME = rb"(?:MotionPhoto\w*|MicroVideo\w*)"
_ATTACHMENT_PROPERTIES = (
    (rb"GCamera", _MOTION_NAME),
    (rb"hdrgm", rb"\w+"),
    (rb"xmpNote", rb"HasExtendedXMP"),
)
"""XMP properties (prefix, name) that point at data stored after a JPEG's picture or in extended XMP segments."""
_MOTION_PROPERTIES = ((rb"GCamera", _MOTION_NAME), (rb"xmpNote", rb"HasExtendedXMP"))
_CONTAINER_DIRECTORY = re.compile(rb"<Container:Directory\b.*?</Container:Directory\s*>\s*", re.S)
_MOTION_ITEM = re.compile(
    rb"<rdf:li\b[^>]*>\s*<Container:Item\b[^>]*Item:Semantic\s*=\s*[\"']MotionPhoto[\"'][^>]*/>\s*</rdf:li\s*>\s*",
    re.S,
)
_PRIMARY_ITEM = re.compile(rb"<Container:Item\b[^>]*Item:Semantic\s*=\s*[\"']Primary[\"'][^>]*/>", re.S)
_ITEM_SIZE = re.compile(rb"(\sItem:(?:Length|Padding)\s*=\s*)([\"'])\d*\2")


def _without_properties(xmp: bytes, properties: tuple[tuple[bytes, bytes], ...]) -> bytes:
    for prefix, name in properties:
        xmp = re.sub(rb"\s" + prefix + rb":" + name + rb"\s*=\s*(\"[^\"]*\"|'[^']*')", b"", xmp)
        xmp = re.sub(rb"<" + prefix + rb":" + name + rb"\b[^<>]*/>\s*", b"", xmp)
        xmp = re.sub(rb"<(" + prefix + rb":" + name + rb")\b[^<>]*>.*?</\1\s*>\s*", b"", xmp, flags=re.S)
    return xmp


def xmp_without_attachments(xmp: bytes) -> bytes:
    """
    An XMP packet without the properties that point at data an output doesn't carry: a motion
    photo's video (``GCamera:MotionPhoto…``, ``MicroVideo…``), an Ultra HDR photo's gain map
    (``hdrgm:…``), the container directory listing them, and extended XMP (``HasExtendedXMP``).
    The rest is unchanged.
    """
    return _CONTAINER_DIRECTORY.sub(b"", _without_properties(xmp, _ATTACHMENT_PROPERTIES))


def xmp_without_motion(xmp: bytes, primary_length: int) -> bytes:
    """
    An XMP packet without a motion photo's video (its properties and its container item) and
    without extended XMP, for a JPEG that keeps its gain map: the primary picture's stated length
    (if any) becomes `primary_length`. With nothing left to list, the container directory goes too.
    """
    xmp = _MOTION_ITEM.sub(b"", _without_properties(xmp, _MOTION_PROPERTIES))
    if re.search(rb"Item:Semantic\s*=\s*[\"'](?!Primary)", xmp) is None or b"MotionPhoto" in xmp:
        return _CONTAINER_DIRECTORY.sub(b"", xmp)

    def sized(match: re.Match[bytes]) -> bytes:
        return _ITEM_SIZE.sub(
            lambda m: m[1] + m[2] + (str(primary_length).encode() if b"Length" in m[1] else b"0") + m[2], match[0]
        )

    return _PRIMARY_ITEM.sub(sized, xmp)


def xmp_upright(xmp: bytes) -> bytes:
    """An XMP packet with its orientation (if any) set to 1, for a picture sashimi has turned upright."""
    xmp = _XMP_ORIENTATION_ATTRIBUTE.sub(rb"\g<1>1\g<2>", xmp)
    return _XMP_ORIENTATION_ELEMENT.sub(rb"\g<1>1\g<3>", xmp)


def iptc_datasets(data: bytes) -> Iterator[tuple[int, int, int, int]]:
    """Each IPTC IIM dataset as (record, dataset, start, end); stops at anything it can't read."""
    pos = 0
    while pos + 5 <= len(data) and data[pos] == 0x1C:
        record, number, length = data[pos + 1], data[pos + 2], struct.unpack_from(">H", data, pos + 3)[0]
        head = 5
        if length & 0x8000:
            width = length & 0x7FFF
            if width > 4 or pos + 5 + width > len(data):
                return
            length = int.from_bytes(data[pos + 5 : pos + 5 + width], "big")
            head += width
        end = pos + head + length
        if end > len(data):
            return
        yield record, number, pos, end
        pos = end


def iptc_without_location(data: bytes) -> bytes:
    out = bytearray()
    last = 0
    for record, number, start, end in iptc_datasets(data):
        if not (record == 2 and number in IPTC_LOCATION):
            out += data[start:end]
        last = end
    return bytes(out + data[last:])


def iptc_has_location(data: bytes) -> bool:
    return any(record == 2 and number in IPTC_LOCATION for record, number, _, _ in iptc_datasets(data))


def iptc_value(data: bytes, dataset: int) -> str | None:
    for record, number, start, end in iptc_datasets(data):
        if record == 2 and number == dataset:
            head = 5 + ((struct.unpack_from(">H", data, start + 3)[0] & 0x7FFF) if data[start + 3] & 0x80 else 0)
            return data[start + head : end].decode("utf-8", "replace").strip("\0 ") or None
    return None


def photoshop_resources(data: bytes) -> list[tuple[bytes, int, bytes, bytes]]:
    """Photoshop image resources as (signature, id, name as stored, data)."""
    resources = []
    pos = 0
    while pos + 12 <= len(data) and data[pos : pos + 4] in (b"8BIM", b"MeSa", b"PHUT", b"AgHg", b"DCSR"):
        signature, number = data[pos : pos + 4], struct.unpack_from(">H", data, pos + 4)[0]
        name_length = data[pos + 6]
        name_end = pos + 7 + name_length + ((name_length + 1) & 1)
        if name_end + 4 > len(data):
            break
        size = struct.unpack_from(">I", data, name_end)[0]
        start = name_end + 4
        if start + size > len(data):
            break
        resources.append((signature, number, data[pos + 6 : name_end], data[start : start + size]))
        pos = start + size + (size & 1)
    return resources


def _photoshop_bytes(resources: list[tuple[bytes, int, bytes, bytes]]) -> bytes:
    out = bytearray()
    for signature, number, name, value in resources:
        out += signature + struct.pack(">H", number) + name + struct.pack(">I", len(value)) + value
        if len(value) & 1:
            out += b"\0"
    return bytes(out)


def photoshop_iptc(data: bytes) -> bytes | None:
    for _, number, _, value in photoshop_resources(data):
        if number == PHOTOSHOP_IPTC:
            return value
    return None


def _photoshop_filtered(data: bytes, *, location: bool, thumbnails: bool) -> bytes | None:
    resources = []
    for signature, number, name, value in photoshop_resources(data):
        if thumbnails and number in PHOTOSHOP_THUMBNAILS:
            continue
        if location:
            if number == PHOTOSHOP_IPTC:
                value = iptc_without_location(value)
            elif number in (PHOTOSHOP_EXIF, PHOTOSHOP_EXIF_2):
                value = _exif_or_none(value, drop_gps=True) or b""
            elif number == PHOTOSHOP_XMP:
                value = xmp_without_location(value)
        resources.append((signature, number, name, value))
    return _photoshop_bytes(resources) if resources else None


def photoshop_has_location(data: bytes) -> bool:
    for _, number, _, value in photoshop_resources(data):
        if number == PHOTOSHOP_IPTC and iptc_has_location(value):
            return True
        if number in (PHOTOSHOP_EXIF, PHOTOSHOP_EXIF_2) and gps_directory(value):
            return True
        if number == PHOTOSHOP_XMP and xmp_has_location(value):
            return True
    return False


def _exif_or_none(exif: bytes, **edits: Any) -> bytes | None:
    """`exif` edited (see ``edit_exif``), or None if it can't be read, so it is dropped rather than copied blind."""
    try:
        return edit_exif(exif, **edits)
    except (TiffStructureError, struct.error):
        return None


def _iptc_from_tiff(value: Any, order: str) -> bytes | None:
    if isinstance(value, bytes):
        return value
    if isinstance(value, int):
        value = (value,)
    if isinstance(value, tuple) and all(isinstance(v, int) for v in value):
        return struct.pack(order + f"{len(value)}I", *value)
    return None


def _photoshop_with_iptc(iptc: bytes) -> bytes:
    return _photoshop_bytes([(b"8BIM", PHOTOSHOP_IPTC, b"\0\0", iptc)])


def _tiff_carriers(img: Image.Image) -> Carriers:
    carriers = Carriers()
    tags = getattr(img, "tag_v2", None)
    if tags is None:
        return carriers
    order = getattr(tags, "_endian", "<")
    xmp = tags.get(TIFF_XMP)
    if isinstance(xmp, bytes) and xmp:
        carriers.xmp = xmp
        _note(carriers, "XMP")
    photoshop = tags.get(TIFF_PHOTOSHOP)
    if isinstance(photoshop, bytes) and photoshop:
        carriers.photoshop = photoshop
        _note(carriers, "Photoshop")
        if photoshop_iptc(photoshop):
            _note(carriers, "IPTC")
    iptc = _iptc_from_tiff(tags.get(TIFF_IPTC), order)
    if iptc:
        _note(carriers, "IPTC")
        if carriers.photoshop is None or photoshop_iptc(carriers.photoshop) is None:
            existing = photoshop_resources(carriers.photoshop or b"")
            carriers.photoshop = _photoshop_bytes([*existing, (b"8BIM", PHOTOSHOP_IPTC, b"\0\0", iptc)])
    try:
        exif = Image.Exif()
        exif.load(img.getexif().tobytes())
        for tag in list(exif):
            if tag in TIFF_STRUCTURAL or tag in (TIFF_XMP, TIFF_IPTC, TIFF_PHOTOSHOP):
                del exif[tag]
        if len(exif):
            _add_exif(carriers, exif.tobytes())
    except (ValueError, TypeError, KeyError, struct.error, SyntaxError):
        carriers.exif = None
    return carriers


def _info_carriers(img: Image.Image) -> Carriers:
    carriers = Carriers()
    exif = img.info.get("exif")
    if isinstance(exif, bytes) and exif:
        _add_exif(carriers, exif)
    xmp = img.info.get("xmp")
    if isinstance(xmp, bytes) and xmp and b"rdf:" in xmp:
        carriers.xmp = xmp
        _note(carriers, "XMP")
    return carriers


def _add_exif(carriers: Carriers, exif: bytes) -> None:
    carriers.exif = exif.removeprefix(EXIF_PREFIX)
    if not exif_is_trivial(carriers.exif):
        _note(carriers, "EXIF")
    if gps_directory(carriers.exif):
        _note(carriers, "GPS")


def _jpeg_carriers(data: bytes) -> Carriers:
    carriers = Carriers()
    pieces, _ = jpeg_pieces(data)
    photoshop = b""
    for marker, segment in pieces:
        payload = segment[4:]
        if marker == 0xE1 and payload.startswith(b"Exif\x00") and carriers.exif is None:
            _add_exif(carriers, payload[6:])
        elif marker == 0xE1 and payload.startswith(XMP_JPEG) and carriers.xmp is None:
            carriers.xmp = payload[len(XMP_JPEG) :]
            _note(carriers, "XMP")
        elif marker == 0xE1 and payload.startswith(XMP_EXTENSION_JPEG):
            _note(carriers, "extended XMP")
        elif marker == 0xED and payload.startswith(PHOTOSHOP_JPEG):
            photoshop += payload[len(PHOTOSHOP_JPEG) :]
        elif marker == 0xFE:
            carriers.comments.append(payload)
            _note(carriers, "comment")
    if photoshop:
        carriers.photoshop = photoshop
        _note(carriers, "Photoshop")
        if photoshop_iptc(photoshop):
            _note(carriers, "IPTC")
    return carriers


def _png_carriers(data: bytes) -> Carriers:
    carriers = Carriers()
    for kind, start, end in png_chunks(data):
        payload = data[start + 8 : end - 4]
        if kind == b"eXIf" and carriers.exif is None:
            _add_exif(carriers, payload)
        elif kind in _PNG_TEXT:
            key, text = png_text(kind, payload)
            if key == XMP_PNG_KEY:
                if carriers.xmp is None:
                    carriers.xmp = text
                    _note(carriers, "XMP")
                continue
            carriers.png_text.append((kind, payload))
            _note(carriers, "PNG text")
            if key.lower() == b"comment":
                carriers.comments.append(text)
                _note(carriers, "comment")
    return carriers


def _webp_carriers(data: bytes) -> Carriers:
    carriers = Carriers()
    for fourcc, start, _end in webp_chunks(data):
        size = struct.unpack_from("<I", data, start + 4)[0]
        payload = data[start + 8 : start + 8 + size]
        if fourcc == b"EXIF" and carriers.exif is None:
            _add_exif(carriers, payload)
        elif fourcc == b"XMP " and carriers.xmp is None:
            carriers.xmp = payload
            _note(carriers, "XMP")
    return carriers


def _gif_carriers(data: bytes) -> Carriers:
    carriers = Carriers()
    for kind, start, end in gif_blocks(data):
        if kind != "extension":
            continue
        label = data[start + 1]
        if label == 0xFE:
            carriers.comments.append(_gif_sub_blocks(data, start + 2, end))
            _note(carriers, "comment")
        elif label == 0xFF and _gif_application(data, start) == GIF_XMP_ID and carriers.xmp is None:
            carriers.xmp = _gif_xmp(data, start, end)
            _note(carriers, "XMP")
    return carriers


def _read(path: str | os.PathLike[str]) -> bytes:
    with open(os.fspath(path), "rb") as f:
        return f.read()


def stated_dpi(img: Image.Image) -> tuple[float, float] | None:
    """
    The resolution an image states outside EXIF (which counts as EXIF), in dots per inch: a JFIF
    density in dots per inch or centimetre, a PNG ``pHYs`` in metres, TIFF resolution tags, or a
    BMP's pixels per metre; None when it states none. Reads only what Pillow parsed from the header.
    """
    fmt = img.format
    stated = False
    if fmt in ("JPEG", "MPO"):
        stated = img.info.get("jfif_unit") in (1, 2) and any(v > 0 for v in img.info.get("jfif_density", (0, 0)))
    elif fmt == "PNG":
        stated = "dpi" in img.info
    elif fmt == "TIFF":
        tags = getattr(img, "tag_v2", {})
        stated = 282 in tags and tags.get(296, 2) != 1
    elif fmt == "BMP":
        stated = True
    dpi = img.info.get("dpi")
    if not stated or not dpi:
        return None
    try:
        x, y = (float(value) for value in dpi)
    except (TypeError, ValueError):
        return None
    if not (x > 0 and y > 0) or not all(math.isfinite(v) for v in (x, y)):
        return None
    return (x, y)


DPI_FORMATS = frozenset({"JPEG", "PNG", "TIFF", "BMP"})
"""The Pillow formats sashimi writes a resolution (DPI) into; the others can't hold one outside EXIF."""


def read_carriers(path: str | os.PathLike[str], img: Image.Image) -> Carriers:
    """The source metadata of the image file `img` was opened from, and its stated resolution. Decodes no pixels."""
    carriers = _read_carriers(path, img)
    carriers.dpi = stated_dpi(img)
    return carriers


def _read_carriers(path: str | os.PathLike[str], img: Image.Image) -> Carriers:
    fmt = img.format
    try:
        if fmt in ("JPEG", "MPO"):
            return _jpeg_carriers(_read(path))
        if fmt == "PNG":
            return _png_carriers(_read(path))
        if fmt == "WEBP":
            return _webp_carriers(_read(path))
        if fmt == "GIF":
            return _gif_carriers(_read(path))
    except (ValueError, struct.error):
        return _info_carriers(img)
    if fmt == "TIFF":
        return _tiff_carriers(img)
    return _info_carriers(img)


def apply_mode(
    carriers: Carriers,
    mode: MetadataMode,
    *,
    upright: bool = False,
    edited: bool = False,
    size: tuple[int, int] | None = None,
) -> Carriers:
    """
    What an output keeps of `carriers` under `mode`. `upright`: sashimi applied the orientation,
    so it is reset to 1; otherwise ``remove_all`` keeps it (alone), since it says how to show the
    picture. `edited`: the picture was edited, so embedded thumbnails (which show the original)
    are dropped. `size`: the output's size, written into EXIF's pixel dimensions.
    """
    if mode == "remove_all":
        orientation = exif_orientation(carriers.exif) if carriers.exif is not None and not upright else None
        return Carriers(exif=orientation_only(orientation) if orientation not in (None, 1) else None)
    location = mode == "remove_location"
    exif = carriers.exif
    if exif is not None:
        exif = _exif_or_none(exif, drop_gps=location, drop_thumbnail=edited, reset_orientation=upright, size=size)
    xmp = carriers.xmp
    if xmp is not None:
        xmp = xmp_without_attachments(xmp)
        if location:
            xmp = xmp_without_location(xmp)
        if upright:
            xmp = xmp_upright(xmp)
    photoshop = carriers.photoshop
    if photoshop is not None and (location or edited):
        photoshop = _photoshop_filtered(photoshop, location=location, thumbnails=edited)
    text = carriers.png_text
    if location:
        text = [(kind, payload) for kind, payload in text if not _png_location_text(kind, payload)]
    return replace(carriers, exif=exif, xmp=xmp, photoshop=photoshop, png_text=text)


def _png_location_text(kind: bytes, payload: bytes) -> bool:
    key, _ = png_text(kind, payload)
    return bool(_PNG_LOCATION_KEY.search(key) or _PNG_RAW_PROFILE.match(key))


def save_arguments(carriers: Carriers, pil_format: str) -> dict[str, Any]:
    """The Pillow save() keyword arguments that write `carriers` into `pil_format`, as far as it can hold them."""
    kwargs: dict[str, Any] = {}
    if carriers.dpi is not None and pil_format in DPI_FORMATS:
        kwargs["dpi"] = carriers.dpi
    if pil_format == "JPEG":
        extra = b""
        if carriers.exif is not None and len(carriers.exif) + len(EXIF_PREFIX) <= _MAX_JPEG_SEGMENT:
            kwargs["exif"] = EXIF_PREFIX + carriers.exif
        if carriers.xmp is not None and len(carriers.xmp) + len(XMP_JPEG) <= _MAX_JPEG_SEGMENT:
            extra += _segment(0xE1, XMP_JPEG + carriers.xmp)
        if carriers.photoshop:
            step = _MAX_JPEG_SEGMENT - len(PHOTOSHOP_JPEG)
            for i in range(0, len(carriers.photoshop), step):
                extra += _segment(0xED, PHOTOSHOP_JPEG + carriers.photoshop[i : i + step])
        comments = [c[:_MAX_JPEG_SEGMENT] for c in carriers.comments]
        if comments:
            kwargs["comment"] = comments[0]
            extra += b"".join(_segment(0xFE, c) for c in comments[1:])
        if extra:
            kwargs["extra"] = extra
    elif pil_format == "PNG":
        from PIL.PngImagePlugin import PngInfo

        info = PngInfo()
        chunks = 0
        if carriers.exif is not None:
            kwargs["exif"] = carriers.exif
        if carriers.xmp is not None:
            info.add(b"iTXt", XMP_PNG_KEY + b"\0\0\0\0\0" + carriers.xmp)
            chunks += 1
        if carriers.png_text:
            for kind, payload in carriers.png_text:
                info.add(kind, payload)
                chunks += 1
        else:
            for comment in carriers.comments:
                info.add(b"tEXt", b"Comment\0" + comment)
                chunks += 1
        if chunks:
            kwargs["pnginfo"] = info
    elif pil_format in ("WEBP", "AVIF"):
        if carriers.exif is not None:
            kwargs["exif"] = carriers.exif
        if carriers.xmp is not None:
            kwargs["xmp"] = carriers.xmp
    elif pil_format == "TIFF":
        exif = _tiff_exif(carriers)
        if exif is not None:
            kwargs["exif"] = exif
    elif pil_format == "GIF":
        if carriers.comments:
            kwargs["comment"] = b"\n".join(carriers.comments)
    return kwargs


def _tiff_exif(carriers: Carriers) -> Image.Exif | None:
    exif = Image.Exif()
    if carriers.exif is not None:
        try:
            exif.load(EXIF_PREFIX + carriers.exif)
            for tag in list(exif):
                if tag in TIFF_STRUCTURAL or tag in (EXIF_IFD, GPS_IFD):
                    del exif[tag]
        except (ValueError, TypeError, KeyError, struct.error, SyntaxError):
            exif = Image.Exif()
    if carriers.xmp is not None:
        exif[TIFF_XMP] = carriers.xmp
    if carriers.photoshop:
        exif[TIFF_PHOTOSHOP] = carriers.photoshop
        iptc = photoshop_iptc(carriers.photoshop)
        if iptc:
            exif[TIFF_IPTC] = iptc
    return exif if len(exif) else None


def tiff_groups(carriers: Carriers) -> dict[int, dict[int, Any]]:
    """The EXIF and GPS directories of `carriers`, which a TIFF output gets with :func:`add_tiff_groups`."""
    if carriers.exif is None:
        return {}
    exif = Image.Exif()
    groups: dict[int, dict[int, Any]] = {}
    try:
        exif.load(EXIF_PREFIX + carriers.exif)
        for tag in (EXIF_IFD, GPS_IFD):
            if tag in exif:
                values = {key: value for key, value in exif.get_ifd(tag).items() if key != INTEROP_IFD}
                if values:
                    groups[tag] = values
    except (ValueError, TypeError, KeyError, struct.error, SyntaxError):
        return {}
    return groups


def add_tiff_groups(path: str | os.PathLike[str], groups: dict[int, dict[int, Any]]) -> None:
    """
    Adds EXIF and GPS directories to the first page of the TIFF file at `path`. Pillow's TIFF
    writer can't write them with compression, so they are appended after it has written the file.
    """
    if not groups:
        return
    from PIL.TiffImagePlugin import ImageFileDirectory_v2

    data = _read(path)
    editor = TiffEditor(data)
    rows = []
    for tag, values in groups.items():
        directory = ImageFileDirectory_v2(ifh=data[:4] + b"\0\0\0\0", group=tag)
        for key, value in values.items():
            try:
                directory[key] = value
            except (ValueError, TypeError, KeyError):
                continue
        if len(editor.buf) % 2:
            editor.buf.append(0)
        offset = len(editor.buf)
        editor.buf += directory.tobytes(offset)
        rows.append((tag, 4, 1, struct.pack(editor.order + "I", offset)))
    first = editor.first
    new = editor.rewrite(first, lambda entry: entry.tag not in groups, add=rows)
    if new is not None:
        editor.put_u32(4, new)
    with open(os.fspath(path), "wb") as f:
        f.write(editor.buf)


def clean_info(
    img: Image.Image, keep: tuple[str, ...] = ("transparency", "duration", "loop", "background")
) -> Image.Image:
    """
    The image ready to save with only the metadata sashimi passes: its ``info`` keeps only what
    drawing it needs, and a TIFF opened from a file is copied, because Pillow's TIFF writer
    otherwise copies the source's tags.
    """
    if hasattr(img, "tag_v2") or hasattr(img, "tag"):
        img = img.copy()
    img.info = {key: value for key, value in img.info.items() if key in keep or key == "version"}
    return img


_MP_ENTRY = 0xB002


def _mpf_entries(segment: bytes) -> tuple[str, int, list[tuple[int, int]]] | None:
    """
    An MPF segment's byte order, where its MP entry values start (from the MPF header), and each
    picture's (size, offset from the MPF header); None if it can't be read.
    """
    try:
        editor = TiffEditor(segment[8:])
        entries, _, _ = editor.directory(editor.first)
    except (TiffStructureError, struct.error):
        return None
    for entry in entries:
        if entry.tag == _MP_ENTRY and entry.size >= 16:
            start = editor.value_offset(entry)
            if start is None or start + entry.size > len(editor.buf):
                return None
            pictures = [
                struct.unpack_from(editor.order + "II", editor.buf, start + 16 * index + 4)
                for index in range(entry.size // 16)
            ]
            return editor.order, start, [(size, offset) for size, offset in pictures]
    return None


def _has_location(jpeg: bytes) -> bool:
    try:
        carriers = _jpeg_carriers(jpeg)
    except (ValueError, struct.error):
        return True
    return "GPS" in carriers.found or (carriers.xmp is not None and xmp_has_location(carriers.xmp))


def _clean_picture(jpeg: bytes, everything: bool) -> bool:
    """
    Whether an extra picture (a gain map) can stay in a stripped JPEG: it holds no location, and for
    ``remove_all`` (`everything`) nothing but the XMP that says how to apply it.
    """
    if _has_location(jpeg):
        return False
    if not everything:
        return True
    try:
        found = set(_jpeg_carriers(jpeg).found)
    except (ValueError, struct.error):
        return False
    return found <= {"XMP"}


def _kept_pictures(
    data: bytes, pieces: list[tuple[int | None, bytes]], everything: bool = False
) -> list[tuple[int, int, int]]:
    """
    The extra pictures an MPF segment lists after the main JPEG (an Ultra HDR photo's gain map),
    as (MP entry index, position, size), that can stay (see :func:`_clean_picture`).
    """
    main = sum(len(segment) for _, segment in pieces)
    position = 0
    for marker, segment in pieces:
        if marker == 0xE2 and segment[4:8] == b"MPF\x00":
            found = _mpf_entries(segment)
            if found is None:
                return []
            header = position + 8
            kept = []
            for index, (size, offset) in enumerate(found[2]):
                start = header + offset
                if index and offset and size and main <= start and start + size <= len(data):
                    if data[start : start + 2] == b"\xff\xd8" and _clean_picture(
                        data[start : start + size], everything
                    ):
                        kept.append((index, start, size))
            return kept
        position += len(segment)
    return []


def _patch_mpf(out: bytearray, pictures: list[tuple[int, int, int]]) -> None:
    """Points the MPF segment in `out` at each kept picture's new position (index, position in `out`, size)."""
    pieces, _ = jpeg_pieces(bytes(out))
    position = 0
    for marker, segment in pieces:
        if marker == 0xE2 and segment[4:8] == b"MPF\x00":
            found = _mpf_entries(segment)
            if found is None:
                return
            order, start, entries = found
            header = position + 8
            kept = {index: (where, size) for index, where, size in pictures}
            for index in range(1, len(entries)):
                where, size = kept.get(index, (header, 0))
                struct.pack_into(
                    order + "II", out, header + start + 16 * index + 4, size, where - header if size else 0
                )
            return
        position += len(segment)


KEEP_VIDEO = b""
"""Passed as `video` to :func:`strip_image`: keep a motion photo's video (and what follows it) byte for byte."""


@dataclass(frozen=True)
class MotionVideo:
    """Where a JPEG motion photo's video is: ``data[start:end]``, and whether a Samsung trailer (SEF) follows it."""

    start: int
    end: int
    samsung: bool


_MOTION_LENGTH = re.compile(rb"<Container:Item\b[^>]*Item:Semantic\s*=\s*[\"']MotionPhoto[\"'][^>]*>", re.S)
_LENGTH_VALUE = re.compile(rb"Item:Length\s*=\s*[\"'](\d+)[\"']")
_MICRO_OFFSET = re.compile(rb"GCamera:MicroVideoOffset\s*=\s*[\"'](\d+)[\"']")


def motion_video(data: bytes) -> MotionVideo | None:
    """
    The video of a JPEG motion photo (Google's ``Container`` directory or ``MicroVideoOffset``, as
    Pixel and Galaxy phones write them), or None when there is none sashimi can find. The video
    is the last item, unless a Samsung trailer follows it; its first box must be ``ftyp``.
    """
    try:
        carriers = _jpeg_carriers(data)
        pieces, trailer = jpeg_pieces(data)
    except (ValueError, struct.error):
        return None
    xmp = carriers.xmp or b""
    length = None
    if (item := _MOTION_LENGTH.search(xmp)) is not None and (value := _LENGTH_VALUE.search(item[0])) is not None:
        length = int(value[1])
    elif (offset := _MICRO_OFFSET.search(xmp)) is not None:
        length = int(offset[1])
    if not length or not trailer:
        return None
    primary = len(data) - len(trailer)
    samsung = data.endswith(b"SEFT")
    if samsung:
        marker = data.find(b"MotionPhoto_Data", primary)
        if marker < 0:
            return None
        start = marker + len(b"MotionPhoto_Data")
    else:
        start = len(data) - length
    end = start + length
    if start < primary or end > len(data) or data[start + 4 : start + 8] != b"ftyp":
        return None
    return MotionVideo(start, end, samsung)


def _sized_xmp(xmp: bytes, primary_length: int, video_length: int | None) -> bytes:
    """`xmp` with the primary picture's stated length (if any) and the video's (Container item, ``MicroVideoOffset``) set."""

    def item(match: re.Match[bytes]) -> bytes:
        text = match[0]
        if re.search(rb"Item:Semantic\s*=\s*[\"']Primary[\"']", text):
            return _LENGTH_VALUE.sub(lambda m: m[0].replace(m[1], str(primary_length).encode()), text)
        if video_length is not None and re.search(rb"Item:Semantic\s*=\s*[\"']MotionPhoto[\"']", text):
            return _LENGTH_VALUE.sub(lambda m: m[0].replace(m[1], str(video_length).encode()), text)
        return text

    xmp = re.sub(rb"<Container:Item\b[^>]*>", item, xmp, flags=re.S)
    if video_length is not None:
        xmp = _MICRO_OFFSET.sub(lambda m: m[0].replace(m[1], str(video_length).encode()), xmp)
    return xmp


_DISPLAY_ATTRIBUTES = re.compile(
    rb"\s(?:hdrgm:Version|GCamera:MotionPhoto\w*|GCamera:MicroVideo\w*)\s*=\s*(?:\"[^\"]*\"|'[^']*')"
)
_NAMESPACES = {
    b"hdrgm": b"http://ns.adobe.com/hdr-gain-map/1.0/",
    b"GCamera": b"http://ns.google.com/photos/1.0/camera/",
    b"Container": b"http://ns.google.com/photos/1.0/container/",
    b"Item": b"http://ns.google.com/photos/1.0/container/item/",
}


def display_xmp(xmp: bytes, *, gain_map: bool, video: bool) -> bytes | None:
    """
    The part of a photo's XMP that viewers need to show what a stripped JPEG keeps, as a packet of
    its own (``remove_all``): ``hdrgm:Version`` when the gain map stays, the motion photo's
    ``GCamera`` properties when the video stays, and the container directory listing them. None
    when nothing needs saying.
    """
    if not gain_map and not video:
        return None
    attributes = [
        found[0]
        for found in _DISPLAY_ATTRIBUTES.finditer(xmp)
        if (gain_map and b"hdrgm:" in found[0]) or (video and b"GCamera:" in found[0])
    ]
    directory = _CONTAINER_DIRECTORY.search(xmp)
    body = directory[0] if directory is not None else b""
    if body and not video:
        body = _MOTION_ITEM.sub(b"", body)
    if body and not gain_map:
        body = re.sub(
            rb"<rdf:li\b[^>]*>\s*<Container:Item\b[^>]*Item:Semantic\s*=\s*[\"']GainMap[\"'][^>]*/>\s*</rdf:li\s*>\s*",
            b"",
            body,
            flags=re.S,
        )
    if not attributes and not body:
        return None
    used = [prefix for prefix in _NAMESPACES if any(prefix + b":" in part for part in (*attributes, body))]
    declarations = b"".join(b" xmlns:" + prefix + b'="' + _NAMESPACES[prefix] + b'"' for prefix in used)
    return (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description rdf:about=""'
        + declarations
        + b"".join(attributes)
        + b">"
        + body.strip()
        + b"</rdf:Description></rdf:RDF></x:xmpmeta>"
    )


def _strip_jpeg(data: bytes, mode: MetadataMode, keep_icc: bool, video: bytes | None = None) -> bytes:
    """
    `data` without its metadata. Gain maps the MPF segment lists stay (see :func:`_clean_picture`).
    `video` is what becomes of a motion photo's video (:func:`motion_video`): :data:`KEEP_VIDEO`
    keeps it and everything after the picture byte for byte, other bytes replace it (Google's
    layout only), None drops it.
    """
    pieces, _ = jpeg_pieces(data)
    everything = mode == "remove_all"
    listed = _listed_pictures(data, pieces)
    pictures = _kept_pictures(data, pieces, everything)
    motion = motion_video(data) if video is not None else None
    if motion is None or (video == KEEP_VIDEO and len(pictures) != listed) or (video and motion.samsung):
        video, motion = None, None
    original = sum(len(segment) for _, segment in pieces)
    primary = original
    length = None if motion is None else (motion.end - motion.start if video == KEEP_VIDEO else len(video or b""))
    for _ in range(4):
        out = _strip_jpeg_pieces(pieces, mode, keep_icc, bool(pictures), primary, length)
        if len(out) == primary:
            break
        primary = len(out)
    if motion is not None and video == KEEP_VIDEO:
        shift = len(out) - original
        out += data[original:]
        _patch_mpf(out, [(index, start + shift, size) for index, start, size in pictures])
        return bytes(out)
    placed = []
    for index, start, size in pictures:
        placed.append((index, len(out), size))
        out += data[start : start + size]
    if pictures:
        _patch_mpf(out, placed)
    if motion is not None and video:
        out += video
    return bytes(out)


def _listed_pictures(data: bytes, pieces: list[tuple[int | None, bytes]]) -> int:
    """How many extra pictures the MPF segment lists after the main one (0 without MPF)."""
    for marker, segment in pieces:
        if marker == 0xE2 and segment[4:8] == b"MPF\x00":
            found = _mpf_entries(segment)
            return max(0, len(found[2]) - 1) if found is not None else 0
    return 0


def _strip_jpeg_pieces(
    pieces: list[tuple[int | None, bytes]],
    mode: MetadataMode,
    keep_icc: bool,
    pictures: bool,
    primary: int,
    video: int | None = None,
) -> bytearray:
    location = mode == "remove_location"
    out = bytearray()
    for marker, segment in pieces:
        if marker is None or not (0xE0 <= marker <= 0xEF or marker == 0xFE):
            out += segment
            continue
        payload = segment[4:]
        if marker == 0xE2 and payload.startswith(b"ICC_PROFILE\x00"):
            if keep_icc:
                out += segment
            continue
        if marker == 0xE2 and payload.startswith(b"MPF\x00"):
            if pictures:
                out += segment
            continue
        if marker == 0xE0 and payload.startswith(b"JFIF\x00"):
            out += segment
            continue
        if marker == 0xEE and payload.startswith(b"Adobe"):
            out += segment
            continue
        if not location:
            if marker == 0xE1 and payload.startswith(b"Exif\x00"):
                orientation = exif_orientation(payload[6:])
                if orientation not in (None, 1):
                    out += _segment(0xE1, EXIF_PREFIX + orientation_only(orientation))
            elif marker == 0xE1 and payload.startswith(XMP_JPEG):
                shown = display_xmp(payload[len(XMP_JPEG) :], gain_map=pictures, video=video is not None)
                if shown is not None:
                    out += _segment(0xE1, XMP_JPEG + _sized_xmp(shown, primary, video))
            continue
        if marker == 0xE1 and payload.startswith(b"Exif\x00"):
            exif = _exif_or_none(payload[6:], drop_gps=True)
            if exif is not None:
                out += _segment(0xE1, payload[:6] + exif)
        elif marker == 0xE1 and payload.startswith(XMP_JPEG):
            xmp = xmp_without_location(payload[len(XMP_JPEG) :])
            if video is not None:
                xmp = _sized_xmp(xmp, primary, video)
            else:
                xmp = xmp_without_motion(xmp, primary) if pictures else xmp_without_attachments(xmp)
            out += _segment(0xE1, XMP_JPEG + xmp)
        elif marker == 0xE1 and payload.startswith(XMP_EXTENSION_JPEG):
            continue
        elif marker == 0xED and payload.startswith(PHOTOSHOP_JPEG):
            filtered = _photoshop_filtered(payload[len(PHOTOSHOP_JPEG) :], location=True, thumbnails=False)
            if filtered:
                out += _segment(0xED, PHOTOSHOP_JPEG + filtered)
        else:
            out += segment
    return out


def _strip_png(data: bytes, mode: MetadataMode, keep_icc: bool) -> bytes:
    location = mode == "remove_location"
    out = bytearray(data[:8])
    for kind, start, end in png_chunks(data):
        payload = data[start + 8 : end - 4]
        if kind == b"iCCP":
            if keep_icc:
                out += data[start:end]
            continue
        critical = kind[:1].isupper()
        if not location:
            if critical or kind in _PNG_DISPLAY:
                out += data[start:end]
            elif kind == b"eXIf":
                orientation = exif_orientation(payload.removeprefix(EXIF_PREFIX))
                if orientation not in (None, 1):
                    out += _png_chunk(kind, orientation_only(orientation))
            continue
        if kind == b"eXIf":
            exif = _exif_or_none(payload.removeprefix(EXIF_PREFIX), drop_gps=True)
            if exif is not None:
                out += _png_chunk(kind, exif)
        elif kind in _PNG_TEXT:
            key, text = png_text(kind, payload)
            if key == XMP_PNG_KEY:
                out += _png_chunk(b"iTXt", XMP_PNG_KEY + b"\0\0\0\0\0" + xmp_without_location(text))
            elif not _png_location_text(kind, payload):
                out += data[start:end]
        else:
            out += data[start:end]
    return bytes(out)


_VP8X_ICC, _VP8X_EXIF, _VP8X_XMP = 0x20, 0x08, 0x04


def _strip_webp(data: bytes, mode: MetadataMode, keep_icc: bool) -> bytes:
    location = mode == "remove_location"
    chunks: list[bytes] = []
    for fourcc, start, end in webp_chunks(data):
        size = struct.unpack_from("<I", data, start + 4)[0]
        payload = data[start + 8 : start + 8 + size]
        if fourcc == b"ICCP":
            if keep_icc:
                chunks.append(data[start:end])
        elif fourcc in _WEBP_IMAGE:
            chunks.append(data[start:end])
        elif not location and fourcc == b"EXIF":
            orientation = exif_orientation(payload.removeprefix(EXIF_PREFIX))
            if orientation not in (None, 1):
                chunks.append(_riff_chunk(fourcc, orientation_only(orientation)))
        elif location and fourcc == b"EXIF":
            exif = _exif_or_none(payload.removeprefix(EXIF_PREFIX), drop_gps=True)
            if exif is not None:
                chunks.append(_riff_chunk(fourcc, exif))
        elif location and fourcc == b"XMP ":
            chunks.append(_riff_chunk(fourcc, xmp_without_location(payload)))
        elif location:
            chunks.append(data[start:end])
    present = {chunk[:4] for chunk in chunks}
    for index, chunk in enumerate(chunks):
        if chunk[:4] == b"VP8X":
            flags = chunk[8]
            for bit, fourcc in ((_VP8X_ICC, b"ICCP"), (_VP8X_EXIF, b"EXIF"), (_VP8X_XMP, b"XMP ")):
                flags = flags | bit if fourcc in present else flags & ~bit
            chunks[index] = chunk[:8] + bytes([flags]) + chunk[9:]
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _riff_chunk(fourcc: bytes, payload: bytes) -> bytes:
    return fourcc + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) & 1 else b"")


def _strip_gif(data: bytes, mode: MetadataMode, keep_icc: bool) -> bytes:
    location = mode == "remove_location"
    out = bytearray()
    for kind, start, end in gif_blocks(data):
        if kind != "extension":
            out += data[start:end]
            continue
        label = data[start + 1]
        if label == 0xFE:
            if location:
                out += data[start:end]
            continue
        if label == 0xFF:
            application = _gif_application(data, start)
            if application in _GIF_PLAYBACK:
                out += data[start:end]
            elif application == _GIF_ICC:
                if keep_icc:
                    out += data[start:end]
            elif location and application == GIF_XMP_ID:
                out += _gif_xmp_extension(xmp_without_location(_gif_xmp(data, start, end)))
            elif location:
                out += data[start:end]
            continue
        out += data[start:end]
    return bytes(out)


TIFF_METADATA = frozenset(
    {269, 270, 271, 272, 285, 305, 306, 315, 316, 33432, TIFF_XMP, TIFF_IPTC, TIFF_PHOTOSHOP, EXIF_IFD, GPS_IFD}
    | {37724, 50341, 42016, 18246, 18249, 40091, 40092, 40093, 40094, 40095, 59932, 59933}
)
"""TIFF tags ``remove_all`` drops: names, descriptions, camera, software, dates, people, XMP, IPTC, Photoshop, EXIF, GPS."""


def _strip_tiff(data: bytes, mode: MetadataMode, keep_icc: bool) -> bytes:
    try:
        editor = TiffEditor(data)
        pages = editor.chain()
        for offset in pages:
            entries, _, _ = editor.directory(offset)
            if any(entry.tag == TIFF_DNG_VERSION for entry in entries):
                raise UnsupportedMediaError("Removing metadata from raw camera files (DNG) isn't supported.")
        location = mode == "remove_location"
        for offset in pages:
            entries, _, _ = editor.directory(offset)
            replace: dict[int, tuple[int, bytes]] = {}
            if location:
                for entry in entries:
                    value = editor.value(entry)
                    if entry.tag == TIFF_XMP:
                        replace[entry.tag] = (entry.type, xmp_without_location(value))
                    elif entry.tag == TIFF_IPTC:
                        filtered = iptc_without_location(value)
                        replace[entry.tag] = (entry.type, filtered.ljust(-(-len(filtered) // 4) * 4, b"\0"))
                    elif entry.tag == TIFF_PHOTOSHOP:
                        replace[entry.tag] = (
                            entry.type,
                            _photoshop_filtered(value, location=True, thumbnails=False) or b"",
                        )

            def keep(entry: Any) -> bool:
                if entry.tag == TIFF_ICC:
                    return keep_icc
                if location:
                    return bool(entry.tag != GPS_IFD)
                return entry.tag not in TIFF_METADATA

            new = editor.rewrite(offset, keep, replace)
            if new is not None:
                editor.relink(offset, new)
        return bytes(editor.buf)
    except TiffStructureError as e:
        if str(e) == "BigTIFF":
            raise UnsupportedMediaError("Removing metadata from BigTIFF files isn't supported.", detail=str(e)) from e
        raise UnsupportedMediaError(DAMAGED, detail=str(e)) from e


def strip_image(data: bytes, pil_format: str, mode: MetadataMode, keep_icc: bool, video: bytes | None = None) -> bytes:
    """
    The image file `data` without its metadata, as `mode` says (``remove_location`` or
    ``remove_all``), without decoding or re-encoding anything: the pixels' bytes are copied.
    A JPEG's gain map stays in both modes; `video` says what becomes of a motion photo's video
    (see :func:`_strip_jpeg`; None drops it).
    """
    try:
        if pil_format in ("JPEG", "MPO"):
            return _strip_jpeg(data, mode, keep_icc, video)
        if pil_format == "PNG":
            return _strip_png(data, mode, keep_icc)
        if pil_format == "WEBP":
            return _strip_webp(data, mode, keep_icc)
        if pil_format == "GIF":
            return _strip_gif(data, mode, keep_icc)
    except (ValueError, struct.error) as e:
        raise UnsupportedMediaError(DAMAGED, detail=str(e)) from e
    if pil_format == "TIFF":
        return _strip_tiff(data, mode, keep_icc)
    if pil_format in ("HEIF", "AVIF"):
        from sashimi.core._heifmeta import HeifStructureError, strip_heif

        try:
            return strip_heif(data, mode, keep_icc)
        except (HeifStructureError, struct.error, IndexError) as e:
            raise UnsupportedMediaError(DAMAGED, detail=str(e)) from e
    raise UnsupportedMediaError(CANT_STRIP, detail=f"Pillow format {pil_format}")
