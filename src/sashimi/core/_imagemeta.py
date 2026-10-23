# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
The metadata carried by image files (EXIF, XMP, IPTC and Photoshop resources, comments, PNG
text), read from the file's own structure without decoding pixels; what each ``metadata`` mode
keeps; how an output format stores it; and removing it from a file without re-encoding.
"""

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
    if isinstance(xmp, bytes) and xmp:
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


def read_carriers(path: str | os.PathLike[str], img: Image.Image) -> Carriers:
    """The source metadata of the image file `img` was opened from. Decodes no pixels."""
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
    elif pil_format == "WEBP":
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


def _strip_jpeg(data: bytes, mode: MetadataMode, keep_icc: bool) -> bytes:
    pieces, _ = jpeg_pieces(data)
    location = mode == "remove_location"
    out = bytearray()
    mpo = any(marker == 0xE2 and segment[4:8] == b"MPF\x00" for marker, segment in pieces)
    for marker, segment in pieces:
        if marker is None or not (0xE0 <= marker <= 0xEF or marker == 0xFE):
            out += segment
            continue
        payload = segment[4:]
        if marker == 0xE2 and payload.startswith(b"ICC_PROFILE\x00"):
            if keep_icc:
                out += segment
            continue
        if marker == 0xE2 and payload.startswith(b"MPF\x00") and mpo:
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
            continue
        if marker == 0xE1 and payload.startswith(b"Exif\x00"):
            exif = _exif_or_none(payload[6:], drop_gps=True)
            if exif is not None:
                out += _segment(0xE1, payload[:6] + exif)
        elif marker == 0xE1 and payload.startswith(XMP_JPEG):
            out += _segment(0xE1, XMP_JPEG + xmp_without_location(payload[len(XMP_JPEG) :]))
        elif marker == 0xE1 and payload.startswith(XMP_EXTENSION_JPEG):
            continue
        elif marker == 0xED and payload.startswith(PHOTOSHOP_JPEG):
            filtered = _photoshop_filtered(payload[len(PHOTOSHOP_JPEG) :], location=True, thumbnails=False)
            if filtered:
                out += _segment(0xED, PHOTOSHOP_JPEG + filtered)
        else:
            out += segment
    return bytes(out)


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


def strip_image(data: bytes, pil_format: str, mode: MetadataMode, keep_icc: bool) -> bytes:
    """
    The image file `data` without its metadata, as `mode` says (``remove_location`` or
    ``remove_all``), without decoding or re-encoding anything: the pixels' bytes are copied.
    """
    try:
        if pil_format in ("JPEG", "MPO"):
            return _strip_jpeg(data, mode, keep_icc)
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
    if pil_format == "BMP":
        return data
    raise UnsupportedMediaError(CANT_STRIP, detail=f"Pillow format {pil_format}")
