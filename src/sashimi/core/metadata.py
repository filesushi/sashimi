# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Reading what metadata a file holds, for display, and removing it without re-encoding."""

import json
import os
import re
import struct
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
    iptc_has_location,
    iptc_value,
    photoshop_has_location,
    photoshop_iptc,
    png_text,
    read_carriers,
    strip_image,
    xmp_has_location,
)
from sashimi.core._mediameta import dropped_streams, is_location_key, metadata_args
from sashimi.core._tiffmeta import GPS_IFD
from sashimi.core.image import _open_image
from sashimi.core.probe import MediaInfo, parse_ffprobe_json, probe
from sashimi.errors import BinaryNotFoundError, InvalidOptionsError, ProcessingError, UnsupportedMediaError

__all__ = ["MetadataInfo", "build_strip_command", "read", "strip", "strip_format"]

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
    empty result as "nothing sashimi knows of".
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["image", "video", "audio"]
    """What the file is."""
    carriers: list[str] = []
    """
    Where metadata was found, e.g. ``EXIF``, ``GPS``, ``XMP``, ``IPTC``, ``Photoshop``, ``comment``,
    ``PNG text``, ``QuickTime/MP4 tags``, ``Matroska tags``, ``ID3``, ``Vorbis comments``, ``RIFF
    INFO``, ``stream tags``, ``chapters``, ``cover art``, ``timed metadata track``. Empty when
    nothing was found. The colour profile isn't listed (see ``has_color_profile``).
    """
    has_location: bool = False
    """True if the file holds location data: coordinates, or place names such as a city."""
    latitude: float | None = None
    """Degrees north (negative is south), when the coordinates can be read."""
    longitude: float | None = None
    """Degrees east (negative is west), when the coordinates can be read."""
    altitude: float | None = None
    """Metres above sea level, when recorded."""
    place: str | None = None
    """A place name recorded with the file (city, region, country), if any."""
    camera_make: str | None = None
    camera_model: str | None = None
    date_taken: str | None = None
    """When the photo or video was taken (or, failing that, the file's recorded date), as stored."""
    software: str | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    creation_time: str | None = None
    """A video's or song's creation time, as stored."""
    comment: str | None = None
    has_color_profile: bool = False
    """True if the file has a colour profile (``keep_icc`` decides whether outputs keep it)."""
    tags: dict[str, str] = {}
    """Every other named field found (global tags of video and audio; a few EXIF and PNG text fields)."""

    @property
    def has_metadata(self) -> bool:
        """True if anything worth removing was found."""
        return bool(self.carriers)


def _text(value: Any) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if value is None:
        return None
    text = str(value).strip("\0 \t\r\n")
    return text or None


def _ratio(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _dms(value: Any, ref: Any, negative: str) -> float | None:
    if not isinstance(value, tuple) or not value:
        return None
    parts = [_ratio(part) for part in value]
    if any(part is None for part in parts):
        return None
    degrees = sum(part / 60**index for index, part in enumerate(parts) if part is not None)
    return -degrees if _text(ref) == negative else degrees


def _xmp_value(xmp: bytes, name: str) -> str | None:
    pattern = rb"(?:\w+):" + name.encode() + rb"\s*=\s*\"([^\"]*)\"|<(?:\w+):" + name.encode() + rb">([^<]*)<"
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


def _image_info(path: PathLike, img: Image.Image, carriers: Carriers) -> MetadataInfo:
    fields: dict[str, Any] = {"kind": "image", "carriers": list(carriers.found)}
    tags: dict[str, str] = {}
    location = False
    if carriers.exif is not None:
        exif = Image.Exif()
        try:
            exif.load(b"Exif\x00\x00" + carriers.exif)
            fields["camera_make"] = _text(exif.get(0x010F))
            fields["camera_model"] = _text(exif.get(0x0110))
            fields["software"] = _text(exif.get(0x0131))
            fields["artist"] = _text(exif.get(0x013B))
            details = exif.get_ifd(0x8769)
            fields["date_taken"] = _text(details.get(0x9003)) or _text(exif.get(0x0132))
            description = _text(exif.get(0x010E))
            if description:
                tags["ImageDescription"] = description
            gps = exif.get_ifd(GPS_IFD)
            if gps:
                location = True
                fields["latitude"] = _dms(gps.get(2), gps.get(1), "S")
                fields["longitude"] = _dms(gps.get(4), gps.get(3), "W")
                altitude = _ratio(gps.get(6))
                if altitude is not None:
                    fields["altitude"] = -altitude if gps.get(5) in (1, b"\x01") else altitude
        except (ValueError, TypeError, KeyError, struct.error, SyntaxError):
            pass
    if carriers.xmp is not None:
        xmp = carriers.xmp
        if xmp_has_location(xmp):
            location = True
            if fields.get("latitude") is None:
                fields["latitude"] = _xmp_coordinate(_xmp_value(xmp, "GPSLatitude"))
                fields["longitude"] = _xmp_coordinate(_xmp_value(xmp, "GPSLongitude"))
        fields["camera_make"] = fields.get("camera_make") or _xmp_value(xmp, "Make")
        fields["camera_model"] = fields.get("camera_model") or _xmp_value(xmp, "Model")
        fields["software"] = fields.get("software") or _xmp_value(xmp, "CreatorTool")
        fields["date_taken"] = fields.get("date_taken") or _xmp_value(xmp, "DateTimeOriginal")
        fields["date_taken"] = fields.get("date_taken") or _xmp_value(xmp, "CreateDate")
        places = [_xmp_value(xmp, name) for name in ("City", "State", "Country")]
        if any(places):
            fields["place"] = ", ".join(place for place in places if place)
    if carriers.photoshop is not None:
        location = location or photoshop_has_location(carriers.photoshop)
        iptc = photoshop_iptc(carriers.photoshop)
        if iptc:
            location = location or iptc_has_location(iptc)
            places = [iptc_value(iptc, number) for number in (90, 95, 101)]
            if any(places) and not fields.get("place"):
                fields["place"] = ", ".join(place for place in places if place)
    for kind, payload in carriers.png_text:
        key, value = png_text(kind, payload)
        name = key.decode("latin-1", "replace")
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
        elif lowered != "comment" and not lowered.startswith("raw profile type"):
            tags[name] = text[:200]
        if is_location_key(name):
            location = True
    if carriers.comments:
        fields["comment"] = _text(carriers.comments[0])
    fields["has_location"] = location
    fields["has_color_profile"] = bool(img.info.get("icc_profile"))
    fields["tags"] = tags
    return MetadataInfo(**{key: value for key, value in fields.items() if value is not None})


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


def _interesting(tags: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in tags.items()
        if not _TRIVIAL_TAGS.match(key) or (key.lower() == "encoder" and not value.startswith("Lav"))
    }


def _media_info(data: dict[str, Any]) -> MetadataInfo:
    info = parse_ffprobe_json(data)
    names = info.container_names
    kind: Literal["video", "audio"] = "video" if info.has_video else "audio"
    global_tags = _interesting(info.tags)
    carriers: list[str] = []
    if global_tags:
        family = next((label for name, label in _FAMILY if name in names), "tags")
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
    fields["camera_make"] = _tag(every, "com.apple.quicktime.make", "make", "com.android.manufacturer")
    fields["camera_model"] = _tag(every, "com.apple.quicktime.model", "model", "com.android.model")
    fields["software"] = _tag(every, "com.apple.quicktime.software", "software", "encoded_by")
    fields["title"] = _tag(every, "title")
    fields["artist"] = _tag(every, "artist", "album_artist", "author")
    fields["album"] = _tag(every, "album")
    fields["creation_time"] = _tag(every, "com.apple.quicktime.creationdate", "creation_time", "date")
    fields["date_taken"] = _tag(every, "com.apple.quicktime.creationdate", "creation_time")
    fields["comment"] = _tag(every, "comment", "description")
    fields["tags"] = global_tags
    return MetadataInfo(**{key: value for key, value in fields.items() if value is not None})


def _as_image(path: PathLike, limits: Limits | None) -> MetadataInfo | None:
    try:
        with _open_image(path, limits, load=False) as img:
            return _image_info(path, img, read_carriers(path, img))
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
    audio sashimi reads, and MediaTooLargeError for images over ``limits.max_pixels``.
    """
    found = _as_image(input_path, limits)
    if found is not None:
        return found
    return _media_info(_probe_full(input_path))


_STRIP_IMAGE_EXTENSIONS = {"JPEG": "jpg", "MPO": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "TIFF": "tif"}
_STRIP_MUXERS = {"matroska": "mkv", "webm": "webm", "mov": "mov", "mp4": "mp4", "ipod": "m4a"}
_STRIP_MUXERS.update({"mp3": "mp3", "flac": "flac", "ogg": "ogg", "wav": "wav"})


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
        if brand == "qt":
            return "mov"
        if brand in ("m4a", "m4b"):
            return "ipod"
        return "mp4"
    for name in ("mp3", "flac", "ogg", "wav"):
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
            raise UnsupportedMediaError(NOT_SUPPORTED, detail=f"Pillow format {fmt}")
        return _STRIP_IMAGE_EXTENSIONS[fmt]
    except UnsupportedMediaError as e:
        if e.user_message == NOT_SUPPORTED:
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
    cmd = [ffmpeg, *base_args(1), *INPUT_PROTOCOLS, "-i", file_arg(input_path), "-map", "0"]
    for index in dropped:
        cmd += ["-map", f"-0:{index}"]
    cmd += ["-c", "copy"]
    if muxer in ("mov", "mp4", "ipod"):
        cmd += ["-movflags", "+faststart"]
    kept = [index for index in range(len(source.streams)) if index not in dropped]
    cmd += metadata_args(mode, source, kept)
    return [*cmd, *OUTPUT_FLAGS, "-f", muxer, file_arg(output_path)]


def strip(
    input_path: PathLike,
    output_path: PathLike,
    mode: MetadataMode = "remove_all",
    *,
    keep_icc: bool = True,
    scratch_dir: PathLike | None = None,
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> str:
    """
    Writes a copy of a file without its metadata, in the same format, re-encoding nothing:
    images keep their compressed pixel data byte for byte (JPEG segments, PNG chunks, WebP
    chunks, GIF extensions and TIFF tags are dropped or filtered), and video and audio streams
    are copied. Returns the format's extension (see :func:`strip_format`).

    `mode` is ``remove_all`` (keep only what showing or playing the file needs: the picture or
    streams, their layout, dispositions and languages, the display rotation, and the colour
    profile unless `keep_icc` is False) or ``remove_location`` (GPS data, location tags and place
    names, and timed metadata tracks; everything else stays). Removal is best effort: it covers
    the carriers sashimi knows of (see the README). An MPO's extra pictures are dropped, leaving
    its main JPEG. Raises InvalidOptionsError for ``keep``, and UnsupportedMediaError for files it
    can't handle: HEIC, BMP (which holds no metadata), BigTIFF, DNG, and containers other than
    MKV, WebM, MP4, MOV, M4A, MP3, FLAC, OGG and WAV. Written atomically.
    """
    if mode not in ("remove_location", "remove_all"):
        raise InvalidOptionsError("Choose remove_location or remove_all.")
    image_format: str | None = None
    try:
        with _open_image(input_path, limits, load=False) as img:
            image_format = str(img.format)
    except UnsupportedMediaError:
        image_format = None
    if image_format is not None:
        if image_format not in _STRIP_IMAGE_EXTENSIONS:
            raise UnsupportedMediaError(NOT_SUPPORTED, detail=f"Pillow format {image_format}")
        with open(os.fspath(input_path), "rb") as f:
            data = f.read()
        stripped = strip_image(data, image_format, mode, keep_icc)
        try:
            with atomic_output(output_path, scratch_dir) as temp, open(temp, "wb") as out:
                out.write(stripped)
        except OSError as e:
            raise ProcessingError(f"Cannot write {os.fspath(output_path)}: {e}") from e
        if on_progress:
            on_progress(1.0, "done")
        return _STRIP_IMAGE_EXTENSIONS[image_format]
    info = probe(input_path, limits=limits)
    if not info.streams or not (info.has_video or info.has_audio):
        raise UnsupportedMediaError(NOT_MEDIA, detail=f"container={info.container}")
    muxer = _media_muxer(input_path, info)
    with atomic_output(output_path, scratch_dir) as temp:
        cmd = build_strip_command(input_path, temp, mode, source=info, muxer=muxer, ffmpeg=ffmpeg_binary())
        run_ffmpeg(cmd)
    if on_progress:
        on_progress(1.0, "done")
    return _STRIP_MUXERS[muxer]
