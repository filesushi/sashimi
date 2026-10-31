# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Every field :func:`sashimi.metadata.read` finds, grouped by where it was found, as readable text."""

import io
import math
import re
import struct
from collections.abc import Iterable, Mapping
from fractions import Fraction
from typing import Any
from xml.etree import ElementTree

from PIL import ExifTags, Image
from pydantic import BaseModel, ConfigDict

from sashimi.core._tiffmeta import GPS_IFD

MAX_FIELDS = 200
"""The most fields one section lists; the rest are counted in ``MetadataSection.omitted``."""
MAX_VALUE_CHARS = 300
"""The longest value shown, in characters; a longer one is cut and says how long it was."""
MAX_SECTIONS = 100
"""The most sections one file lists; the rest are counted in ``MetadataInfo.sections_omitted``."""
MAX_XMP_BYTES = 4 << 20
"""XMP packets larger than this are named with their size rather than listed."""

EXIF_IFD = 0x8769
INTEROP_IFD = 0xA005
MAKER_NOTE = 0x927C
_POINTERS = frozenset({EXIF_IFD, GPS_IFD, INTEROP_IFD, 0x014A, 0x0201, 0x0202})
_XP_TAGS = frozenset({0x9C9B, 0x9C9C, 0x9C9D, 0x9C9E, 0x9C9F})
_USER_COMMENT = 0x9286
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_MAKERS = (
    (b"Apple iOS", "Apple"),
    (b"Nikon", "Nikon"),
    (b"OLYMPUS", "Olympus"),
    (b"OM SYSTEM", "OM System"),
    (b"FUJIFILM", "Fujifilm"),
    (b"Panasonic", "Panasonic"),
    (b"SONY", "Sony"),
    (b"LEICA", "Leica"),
    (b"AOC\0", "Pentax"),
    (b"PENTAX", "Pentax"),
    (b"SAMSUNG", "Samsung"),
    (b"SIGMA", "Sigma"),
    (b"Ricoh", "Ricoh"),
    (b"RICOH", "Ricoh"),
    (b"Nintendo", "Nintendo"),
    (b"GoPro", "GoPro"),
    (b"DJI", "DJI"),
    (b"HUAWEI", "Huawei"),
)
ORIENTATIONS = {
    1: "upright",
    2: "mirrored",
    3: "turned 180°",
    4: "flipped upside down",
    5: "mirrored and turned 90° anticlockwise",
    6: "turned 90° clockwise",
    7: "mirrored and turned 90° clockwise",
    8: "turned 90° anticlockwise",
}
"""What each EXIF orientation tells a viewer to do to show the picture."""

IPTC_NAMES = {
    (1, 90): "CodedCharacterSet",
    (2, 0): "RecordVersion",
    (2, 3): "ObjectTypeReference",
    (2, 5): "ObjectName",
    (2, 7): "EditStatus",
    (2, 10): "Urgency",
    (2, 12): "SubjectReference",
    (2, 15): "Category",
    (2, 20): "SupplementalCategories",
    (2, 22): "FixtureIdentifier",
    (2, 25): "Keywords",
    (2, 26): "ContentLocationCode",
    (2, 27): "ContentLocationName",
    (2, 30): "ReleaseDate",
    (2, 35): "ReleaseTime",
    (2, 37): "ExpirationDate",
    (2, 38): "ExpirationTime",
    (2, 40): "SpecialInstructions",
    (2, 45): "ReferenceService",
    (2, 47): "ReferenceDate",
    (2, 50): "ReferenceNumber",
    (2, 55): "DateCreated",
    (2, 60): "TimeCreated",
    (2, 62): "DigitalCreationDate",
    (2, 63): "DigitalCreationTime",
    (2, 65): "OriginatingProgram",
    (2, 70): "ProgramVersion",
    (2, 75): "ObjectCycle",
    (2, 80): "By-line",
    (2, 85): "By-lineTitle",
    (2, 90): "City",
    (2, 92): "Sub-location",
    (2, 95): "Province-State",
    (2, 100): "Country-PrimaryLocationCode",
    (2, 101): "Country-PrimaryLocationName",
    (2, 103): "OriginalTransmissionReference",
    (2, 105): "Headline",
    (2, 110): "Credit",
    (2, 115): "Source",
    (2, 116): "CopyrightNotice",
    (2, 118): "Contact",
    (2, 120): "Caption-Abstract",
    (2, 122): "Writer-Editor",
    (2, 130): "ImageType",
    (2, 131): "ImageOrientation",
    (2, 135): "LanguageIdentifier",
}
"""IPTC IIM dataset names, as the standard spells them."""

PHOTOSHOP_NAMES = {
    0x03E9: "Macintosh print info",
    0x03ED: "Resolution info",
    0x03F3: "Print flags",
    0x0404: "IPTC-NAA record",
    0x0406: "JPEG quality",
    0x0408: "Grid and guides",
    0x0409: "Thumbnail (Photoshop 4)",
    0x040A: "Copyright flag",
    0x040B: "URL",
    0x040C: "Thumbnail",
    0x040D: "Global angle",
    0x040F: "ICC profile",
    0x0410: "Watermark",
    0x0411: "ICC untagged",
    0x0414: "Document-specific IDs",
    0x0419: "Global altitude",
    0x041A: "Slices",
    0x041E: "URL list",
    0x0421: "Version info",
    0x0422: "EXIF data 1",
    0x0423: "EXIF data 3",
    0x0424: "XMP metadata",
    0x0425: "Caption digest",
    0x0426: "Print scale",
    0x0428: "Pixel aspect ratio",
    0x043A: "Print information",
    0x043B: "Print style",
    0x2710: "Print flags info",
}
"""Photoshop image resource names (Adobe's file format specification)."""


class MetadataField(BaseModel):
    """One field as stored: its name as its standard spells it, and its value as readable text."""

    model_config = ConfigDict(frozen=True)

    name: str
    """The field's name (``Make``, ``GPSLatitude``, ``dc:title``, ``com.apple.quicktime.make``); unknown EXIF tags by number (``0xA460``)."""
    value: str
    """
    The value as stored, as text: numbers as numbers (rationals as ``1/33`` or ``1.9``), lists
    joined with commas, binary data as hex when short or ``(N bytes)``, never raw bytes. Values
    over ``MAX_VALUE_CHARS`` characters are cut and end with how long they were.
    """


class MetadataSection(BaseModel):
    """The fields found in one place in a file: an EXIF directory, an XMP packet, a track's tags, …"""

    model_config = ConfigDict(frozen=True)

    carrier: str
    """
    The kind of metadata, named as ``MetadataInfo.carriers`` names it (``EXIF``, ``GPS``, ``XMP``,
    ``IPTC``, ``Photoshop``, ``comment``, ``PNG text``, ``QuickTime/MP4 tags``, ``Matroska tags``,
    ``ID3``, ``Vorbis comments``, ``RIFF INFO``, ``stream tags``, ``chapters``, ``cover art``,
    ``timed metadata track``), or ``ICC profile`` for the colour profile.
    """
    name: str
    """
    A heading naming the carrier and which part of it: ``EXIF IFD0``, ``EXIF Exif IFD``, ``EXIF GPS
    IFD``, ``EXIF thumbnail (IFD1)``, ``EXIF maker note``, ``XMP``, ``ICC profile``,
    ``QuickTime/MP4 tags``, ``Stream 0 tags (video, h264)``, ``Chapters``.
    """
    stream: int | None = None
    """The stream (track) index the section belongs to, for per-track sections of video and audio."""
    counted: bool = True
    """
    True if sashimi counts what this section holds as metadata (so its carrier is in
    ``carriers``, and ``strip`` with ``remove_all`` removes it). False for sections holding only
    what showing or playing the file needs (an EXIF block with only the orientation and
    resolution, a colour profile, encoder and handler tags).
    """
    fields: list[MetadataField] = []
    """The fields, in the order stored, at most ``MAX_FIELDS``."""
    omitted: int = 0
    """How many more fields the section holds than are listed."""


class UncountedDetail(BaseModel):
    """Something in the file that sashimi read but doesn't count as metadata to remove."""

    model_config = ConfigDict(frozen=True)

    name: str
    """
    What it is: ``orientation``, ``colour profile``, ``resolution``, ``writing application``,
    ``encoder``, ``handler name``, ``vendor``, ``language``, ``file type`` or ``display rotation``.
    """
    value: str
    """Its value, as text: ``6 (turned 90° clockwise)``, ``Display P3``, ``72 dpi``, ``Lavf62.12.103``."""
    stream: int | None = None
    """The stream (track) it belongs to, for a track's encoder, handler name or language."""


def cap(text: str) -> str:
    """`text` cut to ``MAX_VALUE_CHARS`` characters, saying how long it was. Pure."""
    if len(text) <= MAX_VALUE_CHARS:
        return text
    return f"{text[:MAX_VALUE_CHARS]}… ({len(text):,} characters)"


def clean_text(text: str) -> str:
    """`text` without NULs and surrounding space, control characters (but tabs and newlines) as spaces. Pure."""
    return _CONTROL.sub(" ", text.strip("\0 \t\r\n"))


def _number(value: float) -> str:
    if not math.isfinite(value):
        return str(value)
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.6g}"


def _rational(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "undefined" if numerator == 0 else "infinite"
    if numerator % denominator == 0:
        return str(numerator // denominator)
    if abs(numerator) == 1 and denominator > 1:
        return f"{numerator}/{denominator}"
    return _number(numerator / denominator)


def bytes_text(data: bytes) -> str:
    """Bytes as text a person can read: short ASCII as it is, a few bytes as hex, else ``(N bytes)``. Pure."""
    stripped = data.rstrip(b"\0")
    if stripped and len(stripped) <= 64 and all(32 <= byte < 127 for byte in stripped):
        return stripped.decode("ascii")
    if len(data) <= 16:
        return " ".join(f"{byte:02X}" for byte in data)
    return f"({len(data):,} bytes)"


def readable(value: Any) -> str:
    """`value` (from Pillow, ffprobe or a parser) as text a person can read, capped. Pure."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, bytes | bytearray):
        return cap(bytes_text(bytes(value)))
    if isinstance(value, str):
        return cap(clean_text(value))
    numerator, denominator = getattr(value, "numerator", None), getattr(value, "denominator", None)
    if isinstance(numerator, int) and isinstance(denominator, int) and not isinstance(value, int):
        return _rational(numerator, denominator)
    if isinstance(value, Fraction):
        return _rational(value.numerator, value.denominator)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, tuple | list):
        if value and all(isinstance(item, int) and 0 <= item < 256 for item in value) and len(value) > 16:
            return f"({len(value):,} bytes)"
        return cap(", ".join(readable(item) for item in value))
    try:
        return _number(float(value))
    except (TypeError, ValueError, OverflowError):
        return cap(clean_text(str(value)))


def section(
    carrier: str,
    name: str,
    pairs: Iterable[tuple[str, Any]],
    *,
    stream: int | None = None,
    counted: bool = True,
) -> MetadataSection:
    """A section of the (name, value) `pairs`, values made readable, at most ``MAX_FIELDS`` listed. Pure."""
    fields: list[MetadataField] = []
    total = 0
    for key, value in pairs:
        total += 1
        if len(fields) < MAX_FIELDS:
            fields.append(MetadataField(name=cap(clean_text(key)) or "(unnamed)", value=readable(value)))
    return MetadataSection(
        carrier=carrier, name=name, stream=stream, counted=counted, fields=fields, omitted=total - len(fields)
    )


def _tag_name(tag: int, names: Mapping[int, str]) -> str:
    return names.get(tag) or f"0x{tag:04X}"


def _xp_text(value: Any) -> str | None:
    if isinstance(value, tuple) and all(isinstance(item, int) for item in value):
        value = bytes(item & 0xFF for item in value)
    if isinstance(value, bytes):
        return value.decode("utf-16-le", "replace").rstrip("\0")
    return None


def user_comment(value: Any) -> Any:
    """An EXIF UserComment's text, decoded by its 8-byte character code; other values as they are. Pure."""
    if not isinstance(value, bytes) or len(value) < 8:
        return value
    code, text = value[:8], value[8:]
    if code.startswith(b"UNICODE"):
        encoding = "utf-16-be" if text[:2] != b"\xff\xfe" and (len(text) < 2 or text[0] == 0) else "utf-16-le"
        return text.decode(encoding, "replace").strip("\0 ")
    if code.startswith((b"ASCII", b"\0\0\0\0\0\0\0\0")):
        return text.decode("latin-1").strip("\0 ")
    if code.startswith(b"JIS"):
        return text.decode("shift_jis", "replace").strip("\0 ")
    return value


def _exif_value(tag: int, value: Any) -> Any:
    if tag in _XP_TAGS:
        return _xp_text(value) or value
    if tag == _USER_COMMENT:
        return user_comment(value)
    return value


def _pairs(
    directory: Mapping[int, Any], names: Mapping[int, str], skip: frozenset[int] = _POINTERS
) -> list[tuple[str, Any]]:
    return [
        (_tag_name(tag, names), _exif_value(tag, value))
        for tag, value in sorted(directory.items(), key=lambda item: int(item[0]))
        if tag not in skip and not isinstance(value, dict)
    ]


def maker_of(note: bytes, make: str | None) -> str:
    """Who wrote a maker note: its header names most makers; else the camera's make, else ``unknown``. Pure."""
    for prefix, maker in _MAKERS:
        if note.startswith(prefix):
            return maker
    return make or "unknown"


def thumbnail_size(block: bytes, ifd1: Mapping[int, Any]) -> tuple[int, int] | None:
    """The width and height of the JPEG thumbnail an EXIF block's IFD1 points at, from its header. Pure."""
    offset, length = ifd1.get(0x0201), ifd1.get(0x0202)
    if not isinstance(offset, int) or not isinstance(length, int) or length <= 0 or offset + length > len(block):
        return None
    try:
        with Image.open(io.BytesIO(block[offset : offset + length])) as thumb:
            return thumb.size
    except (OSError, ValueError, SyntaxError, struct.error):
        return None


class ExifRead:
    """An EXIF block read with Pillow: its directories, maker, thumbnail, and its sections."""

    def __init__(self, block: bytes):
        self.block = block
        self.exif = Image.Exif()
        self.ifd0: dict[int, Any] = {}
        self.details: dict[int, Any] = {}
        self.gps: dict[int, Any] = {}
        self.interop: dict[int, Any] = {}
        self.ifd1: dict[int, Any] = {}
        self.readable = True
        try:
            self.exif.load(b"Exif\x00\x00" + block)
            self.ifd0 = dict(self.exif)
        except (ValueError, TypeError, KeyError, IndexError, struct.error, SyntaxError, OSError):
            self.readable = False
            return
        self.details = self._ifd(EXIF_IFD) if EXIF_IFD in self.ifd0 else {}
        self.gps = self._ifd(GPS_IFD) if GPS_IFD in self.ifd0 else {}
        self.interop = self._ifd(INTEROP_IFD) if INTEROP_IFD in self.details else {}
        self.ifd1 = self._ifd(ExifTags.IFD.IFD1)

    def _ifd(self, tag: int) -> dict[int, Any]:
        try:
            return dict(self.exif.get_ifd(tag))
        except (ValueError, TypeError, KeyError, IndexError, struct.error, SyntaxError, OSError):
            return {}

    @property
    def make(self) -> str | None:
        value = self.ifd0.get(0x010F)
        return clean_text(value) or None if isinstance(value, str) else None

    @property
    def maker_note(self) -> bytes | None:
        note = self.details.get(MAKER_NOTE)
        return note if isinstance(note, bytes) and note.strip(b"\0") else None

    @property
    def thumbnail(self) -> tuple[int, int] | None:
        return thumbnail_size(self.block, self.ifd1) if self.ifd1 else None

    def sections(self) -> list[MetadataSection]:
        if not self.readable:
            return [section("EXIF", "EXIF (unreadable)", [("Size", f"({len(self.block):,} bytes)")])]
        from sashimi.core._tiffmeta import exif_is_trivial

        found = []
        if self.ifd0:
            pairs = _pairs(self.ifd0, ExifTags.TAGS)
            if pairs:
                found.append(section("EXIF", "EXIF IFD0", pairs, counted=not exif_is_trivial(self.block)))
        if self.details:
            found.append(
                section("EXIF", "EXIF Exif IFD", _pairs(self.details, ExifTags.TAGS, _POINTERS | {MAKER_NOTE}))
            )
        if self.maker_note is not None:
            note = self.maker_note
            pairs = [("Maker", maker_of(note, self.make)), ("Size", f"{len(note):,} bytes")]
            found.append(section("EXIF", "EXIF maker note", pairs))
        if self.interop:
            found.append(section("EXIF", "EXIF interoperability IFD", _pairs(self.interop, _INTEROP_NAMES)))
        if self.ifd1:
            pairs = _pairs(self.ifd1, ExifTags.TAGS)
            size = self.thumbnail
            length = self.ifd1.get(0x0202)
            if size:
                pairs.append(("Image size", f"{size[0]}x{size[1]}"))
            if isinstance(length, int):
                pairs.append(("Thumbnail size", f"{length:,} bytes"))
            found.append(section("EXIF", "EXIF thumbnail (IFD1)", pairs))
        if self.gps:
            found.append(section("GPS", "EXIF GPS IFD", _pairs(self.gps, ExifTags.GPSTAGS)))
        return found


_INTEROP_NAMES = {1: "InteropIndex", 2: "InteropVersion", 0x1000: "RelatedImageFileFormat"}
_INTEROP_NAMES |= {0x1001: "RelatedImageWidth", 0x1002: "RelatedImageHeight"}


def _icc_text(data: bytes, offset: int, size: int) -> str | None:
    tag = data[offset : offset + size]
    kind = tag[:4]
    try:
        if kind == b"desc" and len(tag) >= 12:
            count = struct.unpack_from(">I", tag, 8)[0]
            return tag[12 : 12 + count].split(b"\0")[0].decode("latin-1").strip() or None
        if kind == b"text":
            return tag[8:].split(b"\0")[0].decode("latin-1").strip() or None
        if kind == b"mluc" and len(tag) >= 16:
            records, record_size = struct.unpack_from(">II", tag, 8)
            best = None
            for index in range(min(records, 64)):
                start = 16 + index * record_size
                language, length, where = tag[start : start + 4], *struct.unpack_from(">II", tag, start + 4)
                text = tag[where : where + length].decode("utf-16-be", "replace").strip("\0 ")
                if best is None or language.startswith(b"en"):
                    best = text
                    if language == b"enUS":
                        break
            return best or None
    except (struct.error, IndexError, UnicodeDecodeError):
        return None
    return None


def _signature(data: bytes) -> str | None:
    text = data.decode("latin-1").strip("\0 ")
    return text if text and text.isprintable() else None


class IccRead:
    """A colour profile's header and text tags (its description, copyright, device), parsed without decoding anything else."""

    def __init__(self, data: bytes):
        self.data = data
        self.tags: dict[bytes, tuple[int, int]] = {}
        if len(data) >= 132:
            try:
                count = struct.unpack_from(">I", data, 128)[0]
                for index in range(min(count, 256)):
                    signature, offset, size = struct.unpack_from(">4sII", data, 132 + index * 12)
                    if offset + size <= len(data):
                        self.tags[signature] = (offset, size)
            except struct.error:
                pass

    def text(self, signature: bytes) -> str | None:
        where = self.tags.get(signature)
        return _icc_text(self.data, *where) if where else None

    @property
    def description(self) -> str | None:
        return self.text(b"desc")

    def section(self) -> MetadataSection:
        data = self.data
        pairs: list[tuple[str, Any]] = []
        if self.description:
            pairs.append(("Description", self.description))
        for signature, name in ((b"cprt", "Copyright"), (b"dmnd", "Device manufacturer"), (b"dmdd", "Device model")):
            if text := self.text(signature):
                pairs.append((name, text))
        if len(data) >= 128:
            major, minor = data[8], data[9]
            pairs.append(("Version", f"{major}.{minor >> 4}.{minor & 0xF}"))
            for start, name in ((12, "Class"), (16, "Colour space"), (20, "Connection space"), (4, "CMM")):
                if value := _signature(data[start : start + 4]):
                    pairs.append((name, value))
            for start, name in ((40, "Platform"), (48, "Device maker"), (52, "Device model code")):
                if value := _signature(data[start : start + 4]):
                    pairs.append((name, value))
            year, month, day, hour, minute, second = struct.unpack_from(">6H", data, 24)
            if year:
                pairs.append(("Created", f"{year:04}-{month:02}-{day:02} {hour:02}:{minute:02}:{second:02}"))
        pairs.append(("Size", f"{len(data):,} bytes"))
        return section("ICC profile", "ICC profile", pairs, counted=False)


_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_XML = "http://www.w3.org/XML/1998/namespace"


def _qualified(name: str, prefixes: Mapping[str, str]) -> str:
    if name.startswith("{"):
        namespace, _, local = name[1:].partition("}")
        prefix = prefixes.get(namespace)
        return f"{prefix}:{local}" if prefix else local
    return name


def _xmp_values(element: ElementTree.Element, prefixes: Mapping[str, str], path: str) -> list[tuple[str, str]]:
    """(property path, value) for an XMP property element: text, an rdf:Seq/Bag/Alt joined, or a struct's fields."""
    pairs: list[tuple[str, str]] = []
    for key, value in element.attrib.items():
        if key.startswith("{" + _RDF) or key.startswith("{" + _XML):
            continue
        pairs.append((f"{path}/{_qualified(key, prefixes)}", value))
    children = list(element)
    if not children:
        resource = element.attrib.get(f"{{{_RDF}}}resource")
        text = (element.text or "").strip()
        if text or resource or not pairs:
            pairs.insert(0, (path, text or resource or ""))
        return pairs
    for child in children:
        if child.tag in (f"{{{_RDF}}}Seq", f"{{{_RDF}}}Bag", f"{{{_RDF}}}Alt"):
            items = []
            for number, item in enumerate(child, 1):
                struct_attributes = [k for k in item.attrib if not k.startswith(("{" + _RDF, "{" + _XML))]
                if len(item) or struct_attributes:
                    pairs += _xmp_description(item, prefixes, f"{path}[{number}]")
                else:
                    items.append((item.text or "").strip())
            if items:
                pairs.insert(0, (path, ", ".join(items)))
        elif child.tag == f"{{{_RDF}}}Description":
            pairs += _xmp_description(child, prefixes, path)
        else:
            pairs += _xmp_values(child, prefixes, f"{path}/{_qualified(child.tag, prefixes)}")
    return pairs


def _xmp_description(
    element: ElementTree.Element, prefixes: Mapping[str, str], path: str = ""
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for key, value in element.attrib.items():
        if key.startswith("{" + _RDF) or key.startswith("{" + _XML):
            continue
        name = _qualified(key, prefixes)
        pairs.append((f"{path}/{name}" if path else name, value))
    for child in element:
        if child.tag == f"{{{_RDF}}}Description":
            pairs += _xmp_description(child, prefixes, path)
            continue
        name = _qualified(child.tag, prefixes)
        pairs += _xmp_values(child, prefixes, f"{path}/{name}" if path else name)
    return pairs


def xmp_pairs(xmp: bytes) -> list[tuple[str, str]] | None:
    """
    Every property of an XMP packet as (``prefix:Name``, value), structs as ``a:B/c:D``; None if it
    can't be parsed. A packet with a DTD or entities (which XMP never has) is not parsed.
    """
    if b"<!DOCTYPE" in xmp or b"<!ENTITY" in xmp or len(xmp) > MAX_XMP_BYTES:
        return None
    start = xmp.find(b"<x:xmpmeta")
    if start < 0:
        start = xmp.find(b"<rdf:RDF")
    end_meta = xmp.rfind(b"</x:xmpmeta>")
    if start < 0:
        return None
    body = xmp[start : end_meta + len(b"</x:xmpmeta>")] if end_meta > start else xmp[start:]
    body = body.rstrip(b"\0 \r\n\t")
    prefixes: dict[str, str] = {}
    try:
        for _event, item in ElementTree.iterparse(io.BytesIO(body), events=("start-ns",)):
            prefix, namespace = item  # type: ignore[misc]
            prefixes.setdefault(namespace, prefix)
        root = ElementTree.fromstring(body)
    except (ElementTree.ParseError, ValueError, UnicodeDecodeError):
        return None
    pairs: list[tuple[str, str]] = []
    nested = _nested_descriptions(root)
    try:
        for description in root.iter(f"{{{_RDF}}}Description"):
            if description in nested:
                continue
            pairs += _xmp_description(description, prefixes)
    except RecursionError:
        return None
    return pairs


def _nested_descriptions(root: ElementTree.Element) -> set[ElementTree.Element]:
    nested: set[ElementTree.Element] = set()
    for rdf in root.iter(f"{{{_RDF}}}RDF"):
        for top in rdf:
            for inner in top.iter(f"{{{_RDF}}}Description"):
                if inner is not top:
                    nested.add(inner)
    return nested


def xmp_section(xmp: bytes, name: str = "XMP", *, counted: bool = True) -> MetadataSection:
    """A section of an XMP packet's properties, or its size when it can't be read."""
    pairs = xmp_pairs(xmp)
    if pairs is None:
        return section("XMP", f"{name} (unreadable)", [("Size", f"{len(xmp):,} bytes")])
    return section("XMP", name, pairs, counted=counted)


_DISPLAY_XMP = re.compile(r"^(?:hdrgm:Version|GCamera:(?:MotionPhoto|MicroVideo)\w*|Container:Directory\b.*)$")


def display_xmp_parts(xmp: bytes) -> list[str]:
    """
    What an XMP packet tells viewers how to show (``gain map``, ``motion photo``) when it holds
    nothing else, as :func:`strip` leaves it under ``remove_all``; empty when it holds anything else.
    """
    pairs = xmp_pairs(xmp)
    if not pairs or not all(_DISPLAY_XMP.match(name) for name, _ in pairs):
        return []
    parts = []
    if any(name.startswith("hdrgm:") or value == "GainMap" for name, value in pairs):
        parts.append("gain map")
    if any(name.startswith("GCamera:") or value == "MotionPhoto" for name, value in pairs):
        parts.append("motion photo")
    return parts or ["picture's layout"]


def iptc_section(data: bytes) -> MetadataSection:
    """A section of IPTC IIM datasets by name (unknown ones as ``2:NNN``), repeated datasets each listed."""
    from sashimi.core._imagemeta import iptc_datasets

    pairs: list[tuple[str, Any]] = []
    for record, number, start, end in iptc_datasets(data):
        head = 5
        if data[start + 3] & 0x80:
            head += struct.unpack_from(">H", data, start + 3)[0] & 0x7FFF
        value = data[start + head : end]
        name = IPTC_NAMES.get((record, number), f"{record}:{number:03}")
        if record == 1 and number == 90:
            pairs.append((name, "UTF-8" if value == b"\x1b%G" else bytes_text(value)))
            continue
        if number == 0 and len(value) == 2:
            pairs.append((name, int.from_bytes(value, "big")))
            continue
        if any(byte < 32 and byte not in (9, 10, 13) for byte in value):
            pairs.append((name, value))
            continue
        try:
            text: Any = value.decode("utf-8")
        except UnicodeDecodeError:
            text = value.decode("latin-1")
        pairs.append((name, text))
    return section("IPTC", "IPTC IIM", pairs)


def photoshop_section(data: bytes) -> MetadataSection:
    """A section naming each Photoshop image resource and its size (IPTC in it has a section of its own)."""
    from sashimi.core._imagemeta import photoshop_resources

    pairs = []
    for _kind, number, _name, value in photoshop_resources(data):
        label = PHOTOSHOP_NAMES.get(number, f"0x{number:04X}")
        if number == 0x03ED and len(value) >= 16:
            x, y = struct.unpack_from(">I", value, 0)[0] / 65536, struct.unpack_from(">I", value, 8)[0] / 65536
            pairs.append((label, f"{_number(x)}x{_number(y)} dpi"))
        else:
            pairs.append((label, f"({len(value):,} bytes)"))
    return section("Photoshop", "Photoshop image resources", pairs)
