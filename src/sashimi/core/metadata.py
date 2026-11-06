# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Reading what metadata a file holds, for display, and removing it without re-encoding."""

import json
import logging
import math
import os
import re
import struct
import tempfile
import zlib
from collections.abc import Callable
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict

from sashimi.config import Limits, MetadataMode
from sashimi.core._atomic import PathLike, atomic_output
from sashimi.core._ffmpeg import (
    INPUT_PROTOCOLS,
    OUTPUT_FLAGS,
    ProgressCallback,
    base_args,
    ffmpeg_binary,
    ffprobe_binary,
    file_arg,
    run_ffmpeg,
)
from sashimi.core._imagemeta import (
    Carriers,
    PngTexts,
    iptc_has_location,
    iptc_value,
    photoshop_has_location,
    photoshop_iptc,
    png_keyword,
    read_carriers,
    strip_image,
    MAX_XMP_SCAN,
    xmp_has_location,
)
from sashimi.core._metaread import (
    MAX_NOT_COUNTED,
    MAX_READ_BYTES,
    MAX_SECTIONS,
    MAX_TAGS,
    ORIENTATIONS,
    ExifRead,
    IccRead,
    MetadataField,
    MetadataSection,
    UncountedDetail,
    XmpRead,
    bounded_text,
    extended_xmp,
    extended_xmp_section,
    iptc_section,
    maker_of,
    photoshop_section,
    readable,
    within_budget,
)
from sashimi.core._metaread import section as _section
from sashimi.core._mediameta import dropped_streams, is_location_key, merged_movflags, metadata_args, quicktime_tag_args
from sashimi.core.fidelity import Content, Fidelity, Outcome, checked_after_writing
from sashimi.core._faults import caller_progress, file_at_fault
from sashimi.core.image import DAMAGED_IMAGE, _open_image, refuse_unexpected, stated_image_format
from sashimi.core.probe import MediaInfo, parse_ffprobe_json, probe
from sashimi.errors import (
    BinaryNotFoundError,
    InvalidOptionsError,
    MissingReaderError,
    NothingToDoError,
    ProcessingError,
    UnsupportedMediaError,
)

__all__ = [
    "MetadataField",
    "MetadataInfo",
    "MetadataSection",
    "UncountedDetail",
    "StripResult",
    "build_strip_command",
    "read",
    "strip",
    "strip_format",
]

logger = logging.getLogger("sashimi")

NOT_SUPPORTED = "Removing metadata from this kind of file isn't supported."
NOT_MEDIA = "This file doesn't look like an image, video or audio file we can read."

_TRIVIAL_TAGS = re.compile(
    r"(?i)^(major_brand|minor_version|compatible_brands|handler_name|vendor_id|duration|language|bps|"
    r"number_of_frames|number_of_bytes|_statistics_\w+|filename|mimetype|encoder)$"
)
_ISO6709 = re.compile(r"^\s*([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?")
_FAMILY = (
    ("mp3", "ID3"),
    ("flac", "Vorbis comments"),
    ("ogg", "Vorbis comments"),
    ("wav", "RIFF INFO"),
    ("matroska", "Matroska tags"),
    ("mov", "QuickTime/MP4 tags"),
)


class MetadataInfo(BaseModel):
    """
    The metadata sashimi found in a file, for showing to people: what it found, not a promise
    that nothing else is there. A camera or app may store data where no tool looks, so treat an
    empty result as "nothing sashimi knows of", and say what was looked for (``looked_for``) and
    what is still in the file (``not_counted``).

    The named fields are highlights; ``sections`` lists every field read, grouped by where it was
    found (an EXIF directory, an XMP packet, a track's tags). Every text is clean (control
    characters as spaces) and capped, and a whole read is about ``MAX_READ_BYTES`` of JSON at most:
    what didn't fit is counted in ``not_counted_omitted``, ``tags_omitted``, ``sections_omitted``
    and each section's ``omitted``.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["image", "video", "audio"]
    """What the file is."""
    carriers: list[str] = []
    """
    Where metadata was found, e.g. ``EXIF``, ``GPS``, ``XMP``, ``IPTC``, ``Photoshop``, ``comment``,
    ``PNG text``, ``QuickTime/MP4 tags``, ``Matroska tags``, ``ID3``, ``Vorbis comments``, ``RIFF
    INFO``, ``stream tags``, ``chapters``, ``cover art``, ``timed metadata track``. Empty when
    nothing sashimi counts as metadata was found: what showing or playing the file needs (an
    orientation, a colour profile, encoder tags) isn't counted, and is listed in ``not_counted``.
    """
    looked_for: list[str] = []
    """The carriers sashimi reads in this kind of file, in the names ``carriers`` uses."""
    not_counted: list[UncountedDetail] = []
    """
    What sashimi saw in the file but doesn't count as metadata to remove, because showing or
    playing the file needs it or the format always records it: the orientation, colour profile
    and resolution of an image; the writing application, each track's encoder, handler name and
    language, the file type and display rotation of video and audio. At most ``MAX_NOT_COUNTED``.
    """
    not_counted_omitted: int = 0
    """How many more ``not_counted`` entries the file has than are listed."""
    sections: list[MetadataSection] = []
    """Every field read, grouped by where it was found, at most ``MAX_SECTIONS`` sections."""
    sections_omitted: int = 0
    """How many more sections the file holds than are listed."""
    has_location: bool = False
    """True if the file holds location data: coordinates, or place names such as a city."""
    latitude: float | None = None
    """Degrees north (negative is south), when the coordinates can be read."""
    longitude: float | None = None
    """Degrees east (negative is west), when the coordinates can be read."""
    altitude: float | None = None
    """Metres above sea level, when recorded."""
    image_direction: float | None = None
    """The direction the camera faced, in degrees clockwise from north (``image_direction_ref`` says which north)."""
    image_direction_ref: Literal["true", "magnetic"] | None = None
    """Whether ``image_direction`` is from true or magnetic north."""
    place: str | None = None
    """A place name recorded with the file (city, region, country), if any."""
    camera_make: str | None = None
    camera_model: str | None = None
    lens_make: str | None = None
    lens_model: str | None = None
    """The lens as the camera names it, e.g. ``iPhone 16 Pro front TrueDepth camera 2.69mm f/1.9``."""
    exposure_time: float | None = None
    """The exposure time, in seconds (1/33 s is 0.0303)."""
    f_number: float | None = None
    """The aperture's f-number (1.9 for f/1.9)."""
    iso: int | None = None
    """The ISO speed."""
    focal_length: float | None = None
    """The lens's focal length, in millimetres."""
    focal_length_35mm: float | None = None
    """The focal length a 35 mm camera would need for the same view, in millimetres."""
    date_taken: str | None = None
    """
    When the photo or video was taken, as stored: EXIF ``DateTimeOriginal`` (``2026:05:18
    04:04:36``), XMP or IPTC creation dates, QuickTime ``com.apple.quicktime.creationdate``, a
    recorded date. Never a file's or container's own date (when it was written or remuxed; see
    ``creation_time``).
    """
    date_taken_offset: str | None = None
    """The time zone of ``date_taken`` as an offset from UTC (``+03:00``), when stored."""
    date_taken_subseconds: str | None = None
    """The fraction of a second of ``date_taken`` as stored digits (``586`` for .586), when stored."""
    software: str | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    creation_time: str | None = None
    """
    When a video or audio file says it was made, as stored: its container's ``creation_time``
    (usually when it was written, so a remux records the remux), else its ``date`` tag.
    """
    comment: str | None = None
    has_color_profile: bool = False
    """True if the file has a colour profile (``keep_icc`` decides whether outputs keep it)."""
    color_profile: str | None = None
    """The colour profile's description, e.g. ``Display P3`` or ``sRGB IEC61966-2.1``."""
    thumbnail_size: tuple[int, int] | None = None
    """The width and height of a thumbnail the EXIF holds (it can show the picture before an edit)."""
    maker_note: str | None = None
    """
    Who wrote the maker note the EXIF holds (``Apple``, ``Canon``, ``unknown``): a camera
    maker's private data, which sashimi doesn't decode; None when there is none.
    """
    display_rotation: int = 0
    """
    A video's display rotation: the clockwise turn players apply to show it (0, 90, 180 or 270).
    It says how to show the picture, so every ``metadata`` mode keeps it; ``rotate`` changes it.
    """
    tags: dict[str, str] = {}
    """Every other named field found (global tags of video and audio; a few EXIF and PNG text fields), at most ``MAX_TAGS``."""
    tags_omitted: int = 0
    """How many more ``tags`` the file has than are listed."""

    @property
    def has_metadata(self) -> bool:
        """True if anything worth removing was found."""
        return bool(self.carriers)


def _text(value: Any) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if value is None:
        return None
    return bounded_text(value) or None


def _ratio(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> float | None:
    number = _ratio(value[0] if isinstance(value, tuple) and value else value)
    return number if number is not None and number > 0 else None


def _dms(value: Any, ref: Any, negative: str) -> float | None:
    if not isinstance(value, tuple) or not value:
        return None
    parts = [_ratio(part) for part in value]
    if any(part is None for part in parts):
        return None
    degrees = sum(part / 60**index for index, part in enumerate(parts) if part is not None)
    return -degrees if _text(ref) == negative else degrees


def _xmp_value(xmp: bytes, name: str) -> str | None:
    if len(xmp) > MAX_XMP_SCAN:
        return None
    pattern = rb"(?<!\w)\w+:" + name.encode() + rb"\s*=\s*\"([^\"]*)\"|<\w+:" + name.encode() + rb">([^<]*)<"
    match = re.search(pattern, xmp)
    if not match:
        return None
    return _text(match.group(1) if match.group(1) is not None else match.group(2))


def _xmp_coordinate(text: str | None) -> float | None:
    if not text:
        return None
    match = re.match(r"^\s*(\d+)(?:,(\d+(?:\.\d+)?))?(?:,(\d+(?:\.\d+)?))?\s*([NSEW])\s*$", text)
    if not match:
        return _ratio(text)
    degrees, minutes, seconds, hemisphere = match.groups()
    value = float(degrees) + float(minutes or 0) / 60 + float(seconds or 0) / 3600
    return -value if hemisphere in "SW" else value


_OFFSET = re.compile(r"(?:(?<=\d)(Z)|([+-])(\d{2}):?(\d{2}))$")


def offset_of(text: str | None) -> str | None:
    """
    The UTC offset a date or time ends with (``+0200``, ``+02:00``, ``Z``), or that a stored
    offset is (EXIF ``OffsetTimeOriginal``), as ``+02:00``; None without one. Pure.
    """
    if not text:
        return None
    match = _OFFSET.search(text.strip())
    if not match:
        return None
    if match.group(1):
        return "+00:00"
    return f"{match.group(2)}{match.group(3)}:{match.group(4)}"


def _iptc_date(iptc: bytes) -> tuple[str | None, str | None]:
    date, time = iptc_value(iptc, 55), iptc_value(iptc, 60)
    if not date or not re.fullmatch(r"\d{8}", date):
        return None, None
    text = f"{date[:4]}:{date[4:6]}:{date[6:]}"
    if time and re.match(r"\d{6}", time):
        text += f" {time[:2]}:{time[2:4]}:{time[4:6]}"
        return text, offset_of(time[6:]) if len(time) > 6 else None
    return text, None


def _resolution(img: Image.Image, exif: ExifRead | None) -> str | None:
    from sashimi.core._imagemeta import stated_dpi

    dpi = stated_dpi(img)
    if dpi is None and exif is not None and exif.ifd0.get(0x0128, 2) in (2, 3):
        x, y = _ratio(exif.ifd0.get(0x011A)), _ratio(exif.ifd0.get(0x011B))
        if x and y and x > 0 and y > 0:
            dpi = (x * 2.54, y * 2.54) if exif.ifd0.get(0x0128) == 3 else (x, y)
    if dpi is None:
        return None
    x, y = (round(value, 2) for value in dpi)
    return f"{x:g} dpi" if x == y else f"{x:g}x{y:g} dpi"


def _orientation(exif: ExifRead | None, xmp: bytes | None) -> int | None:
    value = exif.ifd0.get(0x0112) if exif is not None else None
    if not isinstance(value, int) and xmp is not None:
        stored = _xmp_value(xmp, "Orientation")
        value = int(stored) if stored and re.fullmatch(r"[0-9]{1,6}", stored) else None
    return value if isinstance(value, int) else None


def _image_looked_for(fmt: str) -> list[str]:
    if fmt in ("JPEG", "MPO"):
        return ["EXIF", "GPS", "XMP", "extended XMP", "IPTC", "Photoshop", "comment"]
    if fmt == "PNG":
        return ["EXIF", "GPS", "XMP", "PNG text", "comment"]
    if fmt == "GIF":
        return ["XMP", "comment"]
    if fmt == "TIFF":
        return ["EXIF", "GPS", "XMP", "IPTC", "Photoshop"]
    return ["EXIF", "GPS", "XMP"]


class _ImageReading:
    """What a read of an image has found so far; each carrier is read by a step that can't stop the others."""

    def __init__(self, img: Image.Image, carriers: Carriers):
        self.img = img
        self.carriers = carriers
        self.fields: dict[str, Any] = {"kind": "image", "carriers": list(carriers.found)}
        self.tags: dict[str, str] = {}
        self.sections: list[MetadataSection] = []
        self.uncounted: list[UncountedDetail] = []
        self.location = False
        self.exif: ExifRead | None = None

    def step(self, carrier: str, name: str, read: Callable[[], None], *, counted: bool = True) -> None:
        """Runs `read`, listing a section that says so when the carrier can't be read."""
        try:
            read()
        except _READ_FAULTS as e:
            if not file_at_fault(e, self.img.format):
                raise
            logger.info(f"Couldn't read the {name}: {type(e).__name__}: {e}")
            self.sections.append(_section(carrier, f"{name} (unreadable)", [], counted=counted))

    def read_exif(self) -> None:
        carriers, fields, tags = self.carriers, self.fields, self.tags
        assert carriers.exif is not None
        exif = ExifRead(carriers.exif)
        self.exif = exif
        self.sections += exif.sections()
        ifd0, details, gps = exif.ifd0, exif.details, exif.gps
        fields["camera_make"] = _text(ifd0.get(0x010F))
        fields["camera_model"] = _text(ifd0.get(0x0110))
        fields["software"] = _text(ifd0.get(0x0131))
        fields["artist"] = _text(ifd0.get(0x013B))
        fields["date_taken"] = _text(details.get(0x9003))
        if fields["date_taken"]:
            fields["date_taken_offset"] = offset_of(_text(details.get(0x9011)) or "")
            subseconds = _text(details.get(0x9291))
            fields["date_taken_subseconds"] = (
                subseconds if subseconds and re.fullmatch(r"[0-9]{1,18}", subseconds) else None
            )
        fields["lens_make"] = _text(details.get(0xA433))
        fields["lens_model"] = _text(details.get(0xA434))
        fields["exposure_time"] = _positive(details.get(0x829A))
        fields["f_number"] = _positive(details.get(0x829D))
        iso = details.get(0x8827)
        iso = iso[0] if isinstance(iso, tuple) and iso else iso
        fields["iso"] = iso if isinstance(iso, int) and iso > 0 else None
        fields["focal_length"] = _positive(details.get(0x920A))
        fields["focal_length_35mm"] = _positive(details.get(0xA405))
        if exif.maker_note is not None:
            fields["maker_note"] = _text(maker_of(exif.maker_note, exif.make))
        fields["thumbnail_size"] = exif.thumbnail
        description = _text(ifd0.get(0x010E))
        if description:
            tags["ImageDescription"] = description[:200]
        if gps:
            self.location = True
            fields["latitude"] = _dms(gps.get(2), gps.get(1), "S")
            fields["longitude"] = _dms(gps.get(4), gps.get(3), "W")
            altitude = _ratio(gps.get(6))
            if altitude is not None:
                fields["altitude"] = -altitude if gps.get(5) in (1, b"\x01") else altitude
            direction = _ratio(gps.get(0x11))
            if direction is not None and 0 <= direction <= 360:
                fields["image_direction"] = direction
                reference = _text(gps.get(0x10))
                fields["image_direction_ref"] = {"T": "true", "M": "magnetic"}.get(reference or "")

    def read_xmp(self) -> None:
        carriers, fields = self.carriers, self.fields
        xmp = carriers.xmp
        assert xmp is not None
        parsed = XmpRead(xmp)
        has_location = xmp_has_location(xmp)
        shown = [] if has_location or carriers.more_xmp or carriers.xmp_omitted else parsed.display_parts()
        self.sections.append(parsed.section(counted=not shown))
        if shown:
            fields["carriers"] = [name for name in fields["carriers"] if name != "XMP"]
            self.uncounted.append(UncountedDetail(name="display XMP", value=", ".join(shown)))
        if has_location:
            self.location = True
            if fields.get("latitude") is None:
                fields["latitude"] = _xmp_coordinate(_xmp_value(xmp, "GPSLatitude"))
                fields["longitude"] = _xmp_coordinate(_xmp_value(xmp, "GPSLongitude"))
        fields["camera_make"] = fields.get("camera_make") or _xmp_value(xmp, "Make")
        fields["camera_model"] = fields.get("camera_model") or _xmp_value(xmp, "Model")
        fields["lens_model"] = fields.get("lens_model") or _xmp_value(xmp, "LensModel") or _xmp_value(xmp, "Lens")
        fields["software"] = fields.get("software") or _xmp_value(xmp, "CreatorTool")
        if not fields.get("date_taken"):
            taken = _xmp_value(xmp, "DateTimeOriginal") or _xmp_value(xmp, "DateCreated")
            fields["date_taken"] = taken
            fields["date_taken_offset"] = offset_of(taken)
        places = [_xmp_value(xmp, name) for name in ("City", "State", "Country")]
        if any(places):
            fields["place"] = ", ".join(place for place in places if place)

    def read_more_xmp(self) -> None:
        for number, xmp in enumerate(self.carriers.more_xmp, 2):
            self.sections.append(XmpRead(xmp).section(f"XMP packet {number}"))
            self.location = self.location or xmp_has_location(xmp)
        if self.carriers.xmp_omitted:
            self.location = True
            self.sections.append(_section("XMP", "XMP packets not read", [("Count", self.carriers.xmp_omitted)]))

    def read_extended_xmp(self) -> None:
        self.sections.append(extended_xmp_section(self.carriers.extended_xmp))
        self.location = self.location or xmp_has_location(extended_xmp(self.carriers.extended_xmp))

    def read_photoshop(self) -> None:
        carriers, fields = self.carriers, self.fields
        assert carriers.photoshop is not None
        self.location = self.location or photoshop_has_location(carriers.photoshop)
        listed = photoshop_section(carriers.photoshop, skip=_MADE_IPTC if carriers.iptc_from_tag else frozenset())
        if listed is not None:
            self.sections.append(listed)
        iptc = photoshop_iptc(carriers.photoshop)
        if iptc:
            self.sections.append(iptc_section(iptc))
            self.location = self.location or iptc_has_location(iptc)
            places = [iptc_value(iptc, number) for number in (90, 95, 101)]
            if any(places) and not fields.get("place"):
                fields["place"] = ", ".join(place for place in places if place)
            if not fields.get("date_taken"):
                fields["date_taken"], fields["date_taken_offset"] = _iptc_date(iptc)
            fields["artist"] = fields.get("artist") or iptc_value(iptc, 80)

    def read_pictures(self) -> None:
        """What the pictures after a JPEG's main one hold: it stays in the file, whatever the main picture holds."""
        for number, carriers in enumerate(self.carriers.pictures, 2):
            picture = _ImageReading(self.img, carriers)
            picture.read_carriers()
            self.location = self.location or picture.location
            self.sections += [
                found.model_copy(update={"name": f"Picture {number}: {found.name}"})
                for found in picture.sections
                if found.counted
            ]

    def read_carriers(self) -> None:
        """Reads every carrier that can hold metadata, each by a step of its own."""
        carriers = self.carriers
        if carriers.exif is not None:
            self.step("EXIF", "EXIF", self.read_exif)
        if carriers.xmp is not None:
            self.step("XMP", "XMP", self.read_xmp)
        if carriers.more_xmp or carriers.xmp_omitted:
            self.step("XMP", "XMP packets", self.read_more_xmp)
        if carriers.extended_xmp:
            self.step("extended XMP", "Extended XMP", self.read_extended_xmp)
        if carriers.photoshop is not None:
            self.step("Photoshop", "Photoshop image resources", self.read_photoshop)
        if carriers.png_text:
            self.step("PNG text", "PNG text", self.read_png_text)
        if carriers.comments:
            self.step("comment", "Comments", self.read_comments)

    def read_png_text(self) -> None:
        fields, tags = self.fields, self.tags
        text_pairs: list[tuple[str, Any]] = []
        texts = PngTexts()
        for kind, payload in self.carriers.png_text:
            name = png_keyword(payload).decode("latin-1", "replace")
            value = texts.read(kind, payload)
            if is_location_key(name):
                self.location = True
            if value is None:
                text_pairs.append((name, "(not read)"))
                continue
            raw = _PNG_RAW.match(name)
            text_pairs.append((name, f"({len(value):,} bytes of encoded {raw.group(1).upper()})" if raw else value))
            text = _text(value)
            if not text:
                continue
            lowered = name.lower()
            if lowered == "title":
                fields["title"] = text
            elif lowered == "author":
                fields["artist"] = text
            elif lowered == "software":
                fields["software"] = fields.get("software") or text
            elif lowered == "creation time":
                fields["date_taken"] = fields.get("date_taken") or text
            elif lowered != "comment" and not raw:
                tags[name] = text[:200]
        if text_pairs:
            self.sections.append(_section("PNG text", "PNG text", text_pairs))

    def read_comments(self) -> None:
        comments = self.carriers.comments
        self.fields["comment"] = _text(comments[0])
        if self.img.format != "PNG":
            self.sections.append(_section("comment", "Comments", [("Comment", c) for c in comments]))

    def read_icc(self) -> None:
        icc = self.img.info.get("icc_profile")
        self.fields["has_color_profile"] = bool(icc)
        if isinstance(icc, bytes) and icc:
            profile = IccRead(icc)
            description = profile.description
            self.fields["color_profile"] = description
            self.uncounted.append(UncountedDetail(name="colour profile", value=description or f"({len(icc):,} bytes)"))
            self.sections.append(profile.section())

    def read_layout(self) -> None:
        orientation = _orientation(self.exif, self.carriers.xmp)
        if orientation is not None:
            meaning = ORIENTATIONS.get(orientation)
            value = f"{orientation} ({meaning})" if meaning else str(orientation)
            self.uncounted.insert(0, UncountedDetail(name="orientation", value=value))
        resolution = _resolution(self.img, self.exif)
        if resolution:
            self.uncounted.append(UncountedDetail(name="resolution", value=resolution))


_READ_FAULTS: tuple[type[Exception], ...] = (
    ArithmeticError,
    AttributeError,
    LookupError,
    OSError,
    RuntimeError,
    SyntaxError,
    TypeError,
    ValueError,
    struct.error,
    zlib.error,
    Image.DecompressionBombError,
)
_MADE_IPTC = frozenset({0x0404})
"""The Photoshop resource a TIFF's IPTC tag is carried in internally: the file itself holds no such resource."""


def _image_info(path: PathLike, img: Image.Image, carriers: Carriers) -> MetadataInfo:
    reading = _ImageReading(img, carriers)
    fields = reading.fields
    reading.read_carriers()
    reading.step("ICC profile", "ICC profile", reading.read_icc, counted=False)
    reading.step("EXIF", "Orientation and resolution", reading.read_layout, counted=False)
    if carriers.pictures:
        reading.step("EXIF", "Other pictures", reading.read_pictures)
    if carriers.tail_unread:
        reading.location = True
        fields["carriers"].append(TAIL_UNREAD)
        reading.uncounted.append(UncountedDetail(name=TAIL_UNREAD, value="not read to its end"))
    if not fields.get("date_taken"):
        fields["date_taken_offset"] = fields["date_taken_subseconds"] = None
    for found in reading.sections:
        if found.counted and found.carrier not in fields["carriers"]:
            fields["carriers"].append(found.carrier)
    fields["has_location"] = reading.location
    fields["tags"] = reading.tags
    fields["looked_for"] = _image_looked_for(str(img.format))
    fields["not_counted"] = reading.uncounted
    return _bounded_info(fields, reading.sections)


_COORDINATE_RANGES = {"latitude": 90.0, "longitude": 180.0}
_NUMBERS = frozenset({"altitude", "exposure_time", "f_number", "focal_length", "focal_length_35mm", "image_direction"})


def _bounded_info(fields: dict[str, Any], sections: list[MetadataSection]) -> MetadataInfo:
    """
    The :class:`MetadataInfo` of what a read found, holding its promises whatever the file says:
    every text clean and capped, numbers finite and coordinates on Earth, and the whole within
    ``MAX_READ_BYTES``, with what was left out counted.
    """
    kept: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = bounded_text(value)
        elif key in _COORDINATE_RANGES and not abs(value) <= _COORDINATE_RANGES[key]:
            continue
        elif key in _NUMBERS and not math.isfinite(value):
            continue
        kept[key] = value
    tags = {bounded_text(key): bounded_text(value) for key, value in fields.get("tags", {}).items()}
    kept["tags"] = dict(list(tags.items())[:MAX_TAGS])
    kept["tags_omitted"] = max(0, len(tags) - MAX_TAGS)
    details = fields.get("not_counted", [])
    kept["not_counted"] = [
        detail.model_copy(update={"name": bounded_text(detail.name), "value": bounded_text(detail.value)})
        for detail in details[:MAX_NOT_COUNTED]
    ]
    kept["not_counted_omitted"] = max(0, len(details) - MAX_NOT_COUNTED)
    kept["sections_omitted"] = max(0, len(sections) - MAX_SECTIONS)
    rest = len(MetadataInfo(**kept).model_dump_json().encode())
    kept["sections"] = within_budget(sections[:MAX_SECTIONS], MAX_READ_BYTES - rest)
    return MetadataInfo(**kept)


_PNG_RAW = re.compile(r"(?i)^raw profile type (\w+)$")


def _probe_full(path: PathLike) -> dict[str, Any]:
    cmd = [
        ffprobe_binary(),
        "-hide_banner",
        "-loglevel",
        "error",
        *INPUT_PROTOCOLS,
        "-show_format",
        "-show_streams",
        "-show_chapters",
        "-of",
        "json",
        file_arg(path),
    ]
    try:
        data = json.loads(run_ffmpeg(cmd, capture_stdout=True) or b"{}")
    except BinaryNotFoundError:
        raise
    except ProcessingError as e:
        raise UnsupportedMediaError(NOT_MEDIA, detail=str(e)) from e
    except ValueError as e:
        raise ProcessingError(f"ffprobe returned invalid JSON for {os.fspath(path)}: {e}") from e
    if not isinstance(data, dict) or not data.get("format"):
        raise UnsupportedMediaError(NOT_MEDIA)
    return data


def _tag(tags: dict[str, str], *names: str) -> str | None:
    lowered = {key.lower(): value for key, value in tags.items()}
    for name in names:
        value = _text(lowered.get(name.lower()))
        if value:
            return value
    return None


def _trivial(key: str, value: str) -> bool:
    return bool(_TRIVIAL_TAGS.match(key)) and not (key.lower() == "encoder" and not value.startswith("Lav"))


def _interesting(tags: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in tags.items() if not _trivial(key, value)}


_UNCOUNTED_TAGS = {
    "encoder": "writing application",
    "handler_name": "handler name",
    "vendor_id": "vendor",
    "language": "language",
}
_FILE_TYPE = ("major_brand", "minor_version", "compatible_brands")


def _uncounted(tags: dict[str, str], stream: int | None) -> list[UncountedDetail]:
    found = []
    lowered = {key.lower(): value for key, value in tags.items()}
    for key, name in _UNCOUNTED_TAGS.items():
        value = _text(lowered.get(key))
        if value and _trivial(key, value) and not (key == "language" and value == "und"):
            label = "encoder" if key == "encoder" and stream is not None else name
            found.append(UncountedDetail(name=label, value=readable(value), stream=stream))
    brands = [lowered[key].strip() for key in ("major_brand", "compatible_brands") if _text(lowered.get(key))]
    if brands:
        found.append(UncountedDetail(name="file type", value=readable(" ".join(dict.fromkeys(brands))), stream=stream))
    return found


def _stream_heading(index: int, stream: dict[str, Any], what: str = "tags") -> str:
    kind = stream.get("codec_type", "unknown")
    codec = stream.get("codec_name") or stream.get("codec_tag_string") or "unknown"
    if codec == "unknown" and stream.get("codec_tag_string"):
        codec = stream["codec_tag_string"]
    return f"Stream {index} {what} ({kind}, {codec})" if what else f"Stream {index} ({kind}, {codec})"


def _chapter_section(chapters: list[dict[str, Any]]) -> MetadataSection:
    from sashimi.core._ffmpeg import fmt_seconds

    pairs = []
    for number, chapter in enumerate(chapters, 1):
        start, end = _ratio(chapter.get("start_time")), _ratio(chapter.get("end_time"))
        span = f"{fmt_seconds(start)}s-{fmt_seconds(end)}s" if start is not None and end is not None else ""
        title = _text((chapter.get("tags") or {}).get("title"))
        pairs.append((f"Chapter {number}", " ".join(part for part in (span, title and f"{title}") if part)))
        for key, value in (chapter.get("tags") or {}).items():
            if key.lower() != "title":
                pairs.append((f"Chapter {number} {key}", value))
    return _section("chapters", "Chapters", pairs)


def _media_sections(data: dict[str, Any], family: str, info: MediaInfo) -> list[MetadataSection]:
    sections = []
    fmt_tags = info.tags
    if fmt_tags:
        sections.append(_section(family, family, fmt_tags.items(), counted=bool(_interesting(fmt_tags))))
    raw_streams = data.get("streams", [])
    for index, stream in enumerate(info.streams):
        raw = raw_streams[index] if index < len(raw_streams) else {}
        if stream.attached_pic:
            heading = _stream_heading(index, raw, "cover art")
            pairs: list[tuple[str, Any]] = [("Codec", stream.codec)]
            if raw.get("width") and raw.get("height"):
                pairs.append(("Size", f"{raw['width']}x{raw['height']}"))
            pairs += list(stream.tags.items())
            sections.append(_section("cover art", heading, pairs, stream=index))
        elif stream.type == "data" and not stream.chapter_track:
            heading = _stream_heading(index, raw, "timed metadata")
            pairs = [("Codec tag", stream.codec_tag or "unknown"), *stream.tags.items()]
            sections.append(_section("timed metadata track", heading, pairs, stream=index))
        elif stream.tags:
            heading = _stream_heading(index, raw)
            counted = bool(_interesting({k: v for k, v in stream.tags.items() if k.lower() != "title" or v}))
            sections.append(_section("stream tags", heading, stream.tags.items(), stream=index, counted=counted))
    if data.get("chapters"):
        sections.append(_chapter_section(data["chapters"]))
    return sections


def _media_info(data: dict[str, Any]) -> MetadataInfo:
    info = parse_ffprobe_json(data)
    names = info.container_names
    kind: Literal["video", "audio"] = "video" if info.has_video else "audio"
    global_tags = _interesting(info.tags)
    family = next((label for name, label in _FAMILY if name in names), "tags")
    carriers: list[str] = []
    if global_tags:
        carriers.append(family)
    stream_tags = [_interesting({k: v for k, v in s.tags.items() if k.lower() != "title" or v}) for s in info.streams]
    if any(stream_tags):
        carriers.append("stream tags")
    if data.get("chapters"):
        carriers.append("chapters")
    if any(stream.attached_pic for stream in info.streams):
        carriers.append("cover art")
    if any(stream.type == "data" and not stream.chapter_track for stream in info.streams):
        carriers.append("timed metadata track")
    fields: dict[str, Any] = {"kind": kind, "carriers": carriers}
    every = dict(info.tags)
    for tags in stream_tags:
        for key, value in tags.items():
            every.setdefault(key, value)
    coordinates = _tag(every, "com.apple.quicktime.location.ISO6709", "location", "location-eng")
    location = any(is_location_key(key) for key in every)
    if coordinates:
        match = _ISO6709.match(coordinates)
        if match:
            fields["latitude"] = float(match.group(1))
            fields["longitude"] = float(match.group(2))
            if match.group(3):
                fields["altitude"] = float(match.group(3))
    fields["has_location"] = location
    fields["place"] = _tag(every, "com.apple.quicktime.location.name")
    fields["camera_make"] = _tag(every, "com.apple.quicktime.make", "make", "com.android.manufacturer", "manufacturer")
    fields["camera_model"] = _tag(every, "com.apple.quicktime.model", "model", "com.android.model", "camera_type")
    fields["lens_model"] = _tag(every, "com.apple.quicktime.camera.lens_model")
    fields["focal_length_35mm"] = _positive(_tag(every, "com.apple.quicktime.camera.focal_length.35mm_equivalent"))
    fields["software"] = _tag(every, "com.apple.quicktime.software", "software", "encoded_by")
    fields["title"] = _tag(every, "title")
    fields["artist"] = _tag(every, "artist", "album_artist", "author")
    fields["album"] = _tag(every, "album")
    fields["creation_time"] = _tag(info.tags, "creation_time", "date") or _tag(every, "creation_time")
    taken = _tag(every, *_CAPTURE_DATES)
    fields["date_taken"] = taken
    fields["date_taken_offset"] = offset_of(taken)
    fields["comment"] = _tag(every, "comment", "description")
    fields["display_rotation"] = info.display_turn
    fields["tags"] = {key: value for key, value in global_tags.items()}
    uncounted = _uncounted(info.tags, None)
    for index, stream in enumerate(info.streams):
        uncounted += _uncounted(stream.tags, index)
    if info.display_turn:
        uncounted.append(UncountedDetail(name="display rotation", value=f"{info.display_turn}° clockwise"))
    fields["not_counted"] = uncounted
    fields["looked_for"] = [family, "stream tags", "chapters", "cover art", "timed metadata track"]
    return _bounded_info(fields, _media_sections(data, family, info))


_CAPTURE_DATES = ("com.apple.quicktime.creationdate", "date_recorded")
"""Tags that hold when a video or recording was captured (``creation_time`` is when the file was written)."""


def _as_image(path: PathLike, limits: Limits | None) -> MetadataInfo | None:
    try:
        with _open_image(path, limits, load=False) as img:
            return _image_info(path, img, read_carriers(path, img))
    except MissingReaderError:
        raise
    except UnsupportedMediaError:
        return None


def read(input_path: PathLike, *, limits: Limits | None = None) -> MetadataInfo:
    """
    What metadata a file holds, for showing to people: where it was found (``carriers``), any
    location (coordinates when they can be read, or place names), the camera, dates and software,
    and for video and audio the title, artist, album, creation time and comments.

    It reads the file's own structure (and ffprobe's view of video and audio) and decodes no
    pixels or samples. It reports what it found, not a promise that nothing else is there: a
    device may store data where no tool knows to look. Works on sashimi's outputs too, so the
    two can be compared. Raises UnsupportedMediaError for files that aren't images, video or
    audio sashimi reads (MissingReaderError for a HEIC or AVIF nothing installed can read), and
    MediaTooLargeError for images over ``limits.max_pixels``.
    """
    found = _as_image(input_path, limits)
    if found is not None:
        return found
    with refuse_unexpected():
        return _media_info(_probe_full(input_path))


_STRIP_IMAGE_EXTENSIONS = {
    "JPEG": "jpg",
    "MPO": "jpg",
    "PNG": "png",
    "WEBP": "webp",
    "GIF": "gif",
    "TIFF": "tif",
    "HEIF": "heic",
    "AVIF": "avif",
}
_NO_METADATA = {"BMP": "BMP", "ICO": "ICO"}
"""Image formats that hold no metadata sashimi knows of, so there is nothing to remove."""
_UNSTRIPPABLE = {"TGA": "TGA", "PSD": "Photoshop (PSD)"}
"""Image formats whose metadata sashimi can't remove without re-encoding."""
_STRIP_MUXERS = {"matroska": "mkv", "webm": "webm", "mov": "mov", "mp4": "mp4", "ipod": "m4a"}
_STRIP_MUXERS.update({"mp3": "mp3", "flac": "flac", "ogg": "ogg", "wav": "wav"})
_STRIP_MUXERS.update({"mpegts": "ts", "m2ts": "m2ts", "vob": "vob", "mpeg": "mpg", "3gp": "3gp", "3g2": "3g2"})
_STRIP_MUXERS.update({"flv": "flv", "asf": "wmv", "avi": "avi"})


def _refuse_image(fmt: str) -> UnsupportedMediaError:
    """Why :func:`strip` doesn't take an image of Pillow format `fmt`."""
    if fmt in _NO_METADATA:
        return NothingToDoError(
            f"{_NO_METADATA[fmt]} files hold no metadata, so there's nothing to remove.", detail=f"Pillow format {fmt}"
        )
    if fmt in _UNSTRIPPABLE:
        return UnsupportedMediaError(
            f"Removing metadata from {_UNSTRIPPABLE[fmt]} files isn't supported. Converting the file to PNG with "
            "remove_all leaves its metadata out.",
            detail=f"Pillow format {fmt}",
        )
    return UnsupportedMediaError(NOT_SUPPORTED, detail=f"Pillow format {fmt}")


def _media_muxer(input_path: PathLike, info: MediaInfo) -> str:
    names = info.container_names
    if "matroska" in names:
        with open(os.fspath(input_path), "rb") as f:
            head = f.read(64)
        return "webm" if b"webm" in head else "matroska"
    if "mov" in names:
        with open(os.fspath(input_path), "rb") as f:
            head = f.read(12)
        brand = (
            (head[8:12].decode("latin-1") if head[4:8] == b"ftyp" else info.tags.get("major_brand", "")).strip().lower()
        )
        if brand in ("qt", ""):
            return "mov"
        if brand in ("m4a", "m4b"):
            return "ipod"
        if brand.startswith("3g2"):
            return "3g2"
        if brand.startswith("3gp"):
            return "3gp"
        return "mp4"
    if "mpegts" in names:
        with open(os.fspath(input_path), "rb") as f:
            head = f.read(5)
        return "m2ts" if len(head) == 5 and head[4] == 0x47 and head[0] != 0x47 else "mpegts"
    if "mpeg" in names:
        return "mpeg" if info.video_codec == "mpeg1video" else "vob"
    for name in ("mp3", "flac", "ogg", "wav", "flv", "asf", "avi"):
        if name in names:
            return name
    raise UnsupportedMediaError(NOT_SUPPORTED, detail=f"container={info.container}")


def strip_format(input_path: PathLike, *, limits: Limits | None = None) -> str:
    """
    The extension :func:`strip` writes for a file, which keeps its format (``jpg``, ``png``,
    ``mkv``, ``mp3``, …). Raises UnsupportedMediaError for files :func:`strip` can't handle.
    """
    try:
        with _open_image(input_path, limits, load=False) as img:
            fmt = str(img.format)
        if fmt not in _STRIP_IMAGE_EXTENSIONS:
            raise _refuse_image(fmt)
        return _STRIP_IMAGE_EXTENSIONS[fmt]
    except UnsupportedMediaError as e:
        if e.detail and e.detail.startswith("Pillow format"):
            raise
    return _STRIP_MUXERS[_media_muxer(input_path, probe(input_path, limits=limits))]


def build_strip_command(
    input_path: PathLike,
    output_path: PathLike,
    mode: MetadataMode,
    *,
    source: MediaInfo,
    muxer: str,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """
    The ffmpeg command :func:`strip` runs for video and audio: every stream copied as it is
    (none re-encoded) into `muxer`, without the metadata `mode` removes. Timed metadata (data
    streams, such as GPS tracks) is left out, and cover art too for ``remove_all``. Pure.
    """
    if mode == "keep":
        raise InvalidOptionsError("Choose remove_location or remove_all.")
    dropped = dropped_streams(source, mode)
    generated = ["-fflags", "+genpts"] if muxer in ("vob", "mpeg") else []
    cmd = [ffmpeg, *base_args(1), *INPUT_PROTOCOLS, *generated, "-i", file_arg(input_path), "-map", "0"]
    for index in dropped:
        cmd += ["-map", f"-0:{index}"]
    cmd += ["-c", "copy"]
    if muxer in ("mov", "mp4", "ipod", "3gp", "3g2"):
        cmd += ["-movflags", "+faststart"]
    kept = [index for index in range(len(source.streams)) if index not in dropped]
    cmd += metadata_args(mode, source, kept)
    cmd = merged_movflags([*cmd, *quicktime_tag_args(mode, source, muxer)])
    if muxer == "m2ts":
        return [*cmd, *OUTPUT_FLAGS, "-mpegts_m2ts_mode", "1", "-f", "mpegts", file_arg(output_path)]
    return [*cmd, *OUTPUT_FLAGS, "-f", muxer, file_arg(output_path)]


class StripResult(Outcome):
    """What :func:`strip` wrote: the format's extension, and what the copy kept (``fidelity``)."""

    format: str
    """The extension of the format written, which is the source's (``jpg``, ``png``, ``mkv``, …)."""


TAIL_UNREAD = "data after the picture"
"""What :attr:`MetadataInfo.carriers` and ``not_counted`` name when a read gave up on what follows a JPEG's main picture."""
_STRIPPED_WITH_METADATA = ("motion photo video", "data after the picture")
"""What :func:`strip` may remove from a JPEG with the metadata: data after the picture that holds metadata of its own."""
_HOLDS = {"remove_location": "location (or tracks that can hold it)", "remove_all": "metadata"}
MOTION_SAMSUNG = (
    "The motion photo's video was removed: it holds {what}, and sashimi can't change a Samsung motion photo's "
    "video without breaking the photo"
)
MOTION_FAILED = "The motion photo's video was removed: it holds {what}, and sashimi couldn't remove it from the video"
MOTION_KEPT = {
    "remove_location": "The motion photo's video was kept, without its location and data tracks",
    "remove_all": "The motion photo's video was kept, without its metadata",
}
"""What :func:`strip` says (``fidelity.notes``) about a motion photo's video it changed or removed."""


def _image_strip_fidelity(
    input_path: PathLike, output_path: PathLike, mode: MetadataMode, keep_icc: bool, notes: list[str]
) -> Fidelity:
    from sashimi.core.fidelity import COLOUR_PROFILE, and_list, differences, fidelity, image_inventory

    source, output = image_inventory(input_path), image_inventory(output_path)
    asked = [name for name in source.items if name in _STRIPPED_WITH_METADATA and name not in output.items]
    if not keep_icc:
        asked.append(COLOUR_PROFILE)
    lost, removed = differences(source, output, removed_metadata=mode, removed=asked)
    unlisted, other_gone = _jpeg_tail_report(input_path, output_path, notes)
    lost += unlisted
    if other_gone and "data after the picture" not in removed and "motion photo video" not in removed:
        removed.append("data after the picture")
    after = read(output_path, limits=Limits.unlimited())
    left = {detail.name: detail.value for detail in after.not_counted}
    if "EXIF" in removed and any(s.carrier == "EXIF" for s in after.sections):
        rest = [f"the {name}" for name in ("orientation", "resolution") if _in_exif(after, name)]
        removed[removed.index("EXIF")] = f"EXIF (all but {and_list(rest)})" if rest else "EXIF"
    if "XMP" in removed and "display XMP" in left:
        shown = and_list(left["display XMP"].split(", "))
        removed[removed.index("XMP")] = f"XMP (all but how to show the {shown})"
    kept = [_kept_text(name, value) for name, value in left.items()] if mode == "remove_all" else []
    return fidelity("picture", lost=lost, removed=removed, notes=notes, checked=source.checked, kept=kept)


def _jpeg_tail_report(input_path: PathLike, output_path: PathLike, notes: list[str]) -> tuple[list[str], bool]:
    """
    What a stripped JPEG no longer has of what its source's directory listed, or of the pictures only
    the source's bytes show; and whether it lost data after the picture that was neither a picture
    nor a video. Items that stay may be renamed ``Unknown``, so items are compared under that name.
    """
    from sashimi.core._imagemeta import _jpeg_carriers, _read, motion_video, tail_summary
    from sashimi.core._metaread import DISPLAY_ITEM_VALUES, directory_items
    from sashimi.core.fidelity import counted

    with open(os.fspath(input_path), "rb") as f:
        if f.read(2) != b"\xff\xd8":
            return [], False
    source, output = _read(input_path), _read(output_path)

    def named(item: dict[str, str]) -> str:
        semantic = item.get("Semantic", "")
        return semantic if DISPLAY_ITEM_VALUES["Semantic"].fullmatch(semantic) else "Unknown"

    left = [named(item) for item in directory_items(_jpeg_carriers(output).xmp or b"")]
    lost = []
    for item in directory_items(_jpeg_carriers(source).xmp or b""):
        semantic = named(item)
        if semantic in left:
            left.remove(semantic)
        elif semantic not in ("Primary", "GainMap", "MotionPhoto"):
            lost.append("another item the photo listed" if semantic == "Unknown" else f"the photo's {semantic} item")
    if motion_video(source) is not None and motion_video(output) is None and not notes:
        lost.append("the way to find the motion photo's video")
    pictures, other = tail_summary(source)
    pictures_after, other_after = tail_summary(output)
    gone = pictures - pictures_after - len(lost)
    if gone > 0:
        lost += ["another item the photo listed"] * gone
    return counted(lost), other and not other_after


_EXIF_FIELDS = {"orientation": ("Orientation",), "resolution": ("XResolution", "YResolution")}


def _in_exif(info: MetadataInfo, name: str) -> bool:
    fields = {f.name for s in info.sections if s.carrier == "EXIF" for f in s.fields}
    return any(field in fields for field in _EXIF_FIELDS[name])


def _kept_text(name: str, value: str) -> str:
    """``orientation (turned 90° clockwise)``, ``colour profile (Display P3)``. Pure."""
    if name == "orientation":
        value = value.partition(" (")[2].rstrip(")") or value
    return f"{name} ({value})"


def _needs_stripping(inventory: Any, mode: MetadataMode) -> bool:
    """Whether a motion photo's video holds what `mode` removes: location (or tracks that can hold it), or any metadata."""
    if mode == "remove_all":
        return bool(inventory.metadata)
    tracks = [name for name in inventory.metadata if "track" in inventory.label(name)]
    return bool(inventory.location or tracks)


def _motion_choice(
    data: bytes, mode: MetadataMode, folder: PathLike, limits: Limits | None
) -> tuple[bytes | None, list[str]]:
    """
    What :func:`strip` does with a JPEG motion photo's video, and why when it drops it: kept byte
    for byte when it holds nothing `mode` removes; else stripped the same way (streams copied) and
    checked, then put back (Google's layout only: a Samsung trailer points into the old video);
    else dropped. ``(None, [])`` for a JPEG without a video.
    """
    from sashimi.core._imagemeta import KEEP_VIDEO, motion_video
    from sashimi.core.fidelity import media_inventory

    motion = motion_video(data)
    if motion is None:
        return None, []
    failed = (None, [MOTION_FAILED.format(what=_HOLDS[mode])])
    with tempfile.TemporaryDirectory(prefix=".sashimi-", dir=os.fspath(folder)) as work:
        source = os.path.join(work, "video.mp4")
        with open(source, "wb") as f:
            f.write(data[motion.start : motion.end])
        try:
            inventory = media_inventory(source)
            if not _needs_stripping(inventory, mode):
                return KEEP_VIDEO, []
            if motion.samsung:
                return None, [MOTION_SAMSUNG.format(what=_HOLDS[mode])]
            info = probe(source, limits=limits)
            stripped = os.path.join(work, "stripped.mp4")
            run_ffmpeg(build_strip_command(source, stripped, mode, source=info, muxer="mp4", ffmpeg=ffmpeg_binary()))
            after = media_inventory(stripped)
            if _needs_stripping(after, mode) or not probe(stripped, limits=limits).has_video:
                return failed
            with open(stripped, "rb") as f:
                return f.read(), [MOTION_KEPT[mode]]
        except BinaryNotFoundError:
            raise
        except (ProcessingError, UnsupportedMediaError):
            return failed


def strip(
    input_path: PathLike,
    output_path: PathLike,
    mode: MetadataMode = "remove_all",
    *,
    keep_icc: bool = True,
    scratch_dir: PathLike | None = None,
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> StripResult:
    """
    Writes a copy of a file without its metadata, in the same format, re-encoding nothing:
    images keep their compressed pixel data byte for byte (JPEG segments, PNG chunks, WebP
    chunks, GIF extensions and TIFF tags are dropped or filtered), and video and audio streams
    are copied. Returns the format's extension (see :func:`strip_format`) and what the copy kept
    (:class:`StripResult`).

    `mode` is ``remove_all`` (keep only what showing or playing the file needs: the picture or
    streams, their layout, dispositions and languages, the display rotation, and the colour
    profile unless `keep_icc` is False) or ``remove_location`` (GPS data, location tags and place
    names, and timed metadata tracks; everything else stays). Removal is best effort: it covers
    the carriers sashimi knows of (see the README). HEIC and AVIF are rewritten in place (see
    below). Raises InvalidOptionsError for
    ``keep``, and UnsupportedMediaError for files it can't handle, with the reason: BMP and ICO
    (which hold no metadata), TGA and PSD, BigTIFF, DNG, and containers other than MKV, WebM, MP4,
    MOV, M4A, 3GP, MP3, FLAC, OGG, WAV, MPEG-TS (and M2TS), MPEG-PS (VOB, MPG), FLV, ASF (WMV)
    and AVI.

    HEIC and AVIF keep every image item byte for byte (the photo, its tiles, the gain map, depth
    and portrait mattes, thumbnails) and the orientation viewers apply; the EXIF and XMP items are
    rewritten at the same size. ``remove_location`` removes the GPS directory and XMP location
    properties; ``remove_all`` empties the EXIF item (keeping an orientation other than 1) and the
    photo's XMP, and keeps the XMP that says how to show the gain map and other auxiliary images
    (without location). An iPhone's HDR brightness for its gain map is in the EXIF maker notes on
    older models, so ``remove_all`` makes viewers use their default there.

    A picture after a JPEG's main one (a gain map, depth and confidence maps, an MPO's other eye)
    stays, without what `mode` removes, if MPF or a container directory lists it, and what lists it
    is updated. A motion photo's video stays when it holds nothing `mode` removes or can be made to.
    Anything else after the picture is removed, and so is the XMP that points at what went. Written
    atomically.
    """
    on_progress = caller_progress(on_progress)
    if mode not in ("remove_location", "remove_all"):
        raise InvalidOptionsError("Choose remove_location or remove_all.")
    image_format: str | None = None
    try:
        with _open_image(input_path, limits, load=False) as img:
            image_format = str(img.format)
    except MissingReaderError:
        raise
    except UnsupportedMediaError as e:
        if stated_image_format(input_path) is not None and not isinstance(e, NothingToDoError):
            raise UnsupportedMediaError(DAMAGED_IMAGE, detail=str(e)) from e
        image_format = None
    if image_format is not None:
        if image_format not in _STRIP_IMAGE_EXTENSIONS:
            raise _refuse_image(image_format)
        with refuse_unexpected(image_format):
            with open(os.fspath(input_path), "rb") as f:
                data = f.read()
            video: bytes | None = None
            notes: list[str] = []
            if image_format in ("JPEG", "MPO"):
                folder = scratch_dir or os.path.dirname(os.path.abspath(os.fspath(output_path)))
                video, notes = _motion_choice(data, mode, folder, limits)
            stripped = strip_image(data, image_format, mode, keep_icc, video)
            try:
                with atomic_output(output_path, scratch_dir) as temp, open(temp, "wb") as out:
                    out.write(stripped)
            except OSError as e:
                raise ProcessingError(f"Cannot write {os.fspath(output_path)}: {e}") from e
        told = checked_after_writing(
            "picture", lambda: _image_strip_fidelity(input_path, output_path, mode, keep_icc, notes), identical=True
        )
        if on_progress:
            on_progress(1.0, "done")
        return StripResult(format=_STRIP_IMAGE_EXTENSIONS[image_format], fidelity=told)
    info = probe(input_path, limits=limits)
    if not info.streams or not (info.has_video or info.has_audio):
        raise UnsupportedMediaError(NOT_MEDIA, detail=f"container={info.container}")
    muxer = _media_muxer(input_path, info)
    with atomic_output(output_path, scratch_dir) as temp:
        cmd = build_strip_command(input_path, temp, mode, source=info, muxer=muxer, ffmpeg=ffmpeg_binary())
        run_ffmpeg(cmd)
    from sashimi.core.fidelity import media_fidelity
    from sashimi.core.video import _clip_content, _mode_removals, _timecode_kept, tail_report

    content = _clip_content(info)

    def report() -> Fidelity:
        tail, notes = tail_report(input_path, info) if info.has_video else ([], [])
        return media_fidelity(
            input_path,
            output_path,
            content,
            changed=tail,
            notes=notes,
            removed_metadata=mode,
            removed=_mode_removals(info, mode),
            kept=_timecode_kept(info),
        )

    told = checked_after_writing(content, report, identical=True)
    if on_progress:
        on_progress(1.0, "done")
    return StripResult(format=_STRIP_MUXERS[muxer], fidelity=told)
