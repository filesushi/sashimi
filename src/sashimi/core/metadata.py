# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Reading what metadata a file holds, for display, and removing it without re-encoding."""

import json
import os
import re
import struct
import tempfile
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
from sashimi.core._mediameta import dropped_streams, is_location_key, merged_movflags, metadata_args, quicktime_tag_args
from sashimi.core._tiffmeta import GPS_IFD
from sashimi.core.fidelity import Fidelity, Outcome
from sashimi.core.image import _open_image
from sashimi.core.probe import MediaInfo, parse_ffprobe_json, probe
from sashimi.errors import BinaryNotFoundError, InvalidOptionsError, ProcessingError, UnsupportedMediaError

__all__ = ["MetadataInfo", "StripResult", "build_strip_command", "read", "strip", "strip_format"]

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
    display_rotation: int = 0
    """
    A video's display rotation: the clockwise turn players apply to show it (0, 90, 180 or 270).
    It says how to show the picture, so every ``metadata`` mode keeps it; ``rotate`` changes it.
    """
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
    fields["display_rotation"] = info.display_turn
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
        return UnsupportedMediaError(
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
        if brand == "qt":
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
    from sashimi.core.fidelity import COLOUR_PROFILE, differences, fidelity, image_inventory

    source, output = image_inventory(input_path), image_inventory(output_path)
    asked = [name for name in source.items if name in _STRIPPED_WITH_METADATA and name not in output.items]
    if not keep_icc:
        asked.append(COLOUR_PROFILE)
    lost, removed = differences(source, output, removed_metadata=mode, removed=asked)
    return fidelity("picture", lost=lost, removed=removed, notes=notes, checked=source.checked)


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
    the carriers sashimi knows of (see the README). An MPO's extra pictures are dropped, leaving
    its main JPEG. HEIC and AVIF are rewritten in place (see below). Raises InvalidOptionsError for
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

    JPEG motion photos and Ultra HDR JPEGs keep their embedded video and gain map under
    ``remove_location`` only when they hold no location (the video's own tags can); a JPEG's
    data after the picture (the video, the gain map image) is otherwise removed, and so is the XMP
    that points at it. Written atomically.
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
            raise _refuse_image(image_format)
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
        told = _image_strip_fidelity(input_path, output_path, mode, keep_icc, notes)
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
    from sashimi.core.video import _mode_removals, _timecode_kept, hidden_tail, tail_text

    tail = hidden_tail(input_path, info) if info.has_video else 0
    told = media_fidelity(
        input_path,
        output_path,
        "picture and sound" if info.has_video and info.has_audio else "picture" if info.has_video else "sound",
        changed=[tail_text(tail)] if tail else [],
        removed_metadata=mode,
        removed=_mode_removals(info, mode),
        kept=_timecode_kept(info),
    )
    if on_progress:
        on_progress(1.0, "done")
    return StripResult(format=_STRIP_MUXERS[muxer], fidelity=told)
