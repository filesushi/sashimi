# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""The ffmpeg arguments that carry out a ``metadata`` mode, and Opus's per-channel bitrate ceiling. Pure."""

import re
from collections.abc import Sequence

from sashimi.config import OPUS_MAX_KBPS_PER_CHANNEL, MetadataMode
from sashimi.core.probe import MediaInfo, StreamInfo

LOCATION_KEYS = (
    "location",
    "location-eng",
    "com.apple.quicktime.location.ISO6709",
    "com.apple.quicktime.location.name",
    "com.apple.quicktime.location.body",
    "com.apple.quicktime.location.note",
    "com.apple.quicktime.location.role",
    "com.apple.quicktime.location.date",
    "com.apple.quicktime.location.accuracy.horizontal",
    "com.android.location",
)
"""Location tags ``remove_location`` always removes, whatever the source shows; matching is case-insensitive."""
_LOCATION_KEY = re.compile(r"(?i)(location|gps|geo|latitude|longitude|coordinates|iso6709)")


def is_location_key(key: str) -> bool:
    """True for a tag name that holds a location (``location``, ``…ISO6709``, ``GPS…``, ``LATITUDE``, …)."""
    return _LOCATION_KEY.search(key) is not None


def first_stream(info: MediaInfo | None, kind: str) -> int | None:
    """The index of the first stream of `kind` (what ffmpeg's ``0:v:0`` or ``0:a:0`` picks), if any."""
    if info is None:
        return None
    return next((index for index, stream in enumerate(info.streams) if stream.type == kind), None)


def dropped_streams(info: MediaInfo | None, mode: MetadataMode) -> list[int]:
    """
    The source streams a copy of every stream leaves out under `mode`: QuickTime chapter tracks
    always (the muxer writes the chapters from the chapter list); timed-metadata (data) streams,
    such as GPS tracks, unless ``keep``; and cover art for ``remove_all``.
    """
    if info is None:
        return []
    return [
        index
        for index, stream in enumerate(info.streams)
        if stream.chapter_track
        or (mode != "keep" and stream.type == "data")
        or (mode == "remove_all" and stream.attached_pic)
    ]


def _tag(stream: StreamInfo, name: str) -> str | None:
    return next((value for key, value in stream.tags.items() if key.lower() == name), None)


def metadata_args(mode: MetadataMode, info: MediaInfo | None, outputs: Sequence[int | None]) -> list[str]:
    """
    Output options that carry out `mode`. `outputs` gives, for each output stream in order, the
    source stream it comes from (None if unknown), so stream tags can be removed or put back:
    ``remove_all`` keeps each stream's language, and an attachment's file name and type, which
    players need.
    """
    if mode == "keep":
        return []
    streams = info.streams if info is not None else []
    if mode == "remove_all":
        args = ["-map_metadata", "-1", "-map_chapters", "-1"]
        for out, source in enumerate(outputs):
            if source is None or source >= len(streams):
                continue
            stream = streams[source]
            if stream.language:
                args += [f"-metadata:s:{out}", f"language={stream.language}"]
            if stream.type == "attachment":
                for name in ("filename", "mimetype"):
                    value = _tag(stream, name)
                    if value:
                        args += [f"-metadata:s:{out}", f"{name}={value}"]
        return args
    lowered = {key.lower() for key in LOCATION_KEYS}
    keys = [*LOCATION_KEYS]
    for key in info.tags if info is not None else {}:
        if is_location_key(key) and key.lower() not in lowered:
            keys.append(key)
            lowered.add(key.lower())
    args = []
    for key in keys:
        args += ["-metadata:g", f"{key}="]
    for out, source in enumerate(outputs):
        if source is None or source >= len(streams):
            continue
        for key in streams[source].tags:
            if is_location_key(key):
                args += [f"-metadata:s:{out}", f"{key}="]
    return args


QUICKTIME_MUXERS = frozenset({"mp4", "mov", "ipod", "3gp", "3g2"})
"""The muxers that write QuickTime/ISO tags, where ``use_metadata_tags`` decides which tags survive."""
_ITUNES_TAGS = frozenset(
    {
        "title",
        "artist",
        "album_artist",
        "composer",
        "album",
        "date",
        "encoder",
        "comment",
        "genre",
        "copyright",
        "grouping",
        "lyrics",
        "description",
        "synopsis",
        "show",
        "episode_id",
        "network",
        "keywords",
        "media_type",
        "hd_video",
        "gapless_playback",
        "compilation",
        "track",
        "disc",
    }
)
_MOV_TAGS = frozenset({"title", "artist", "album", "date", "comment", "genre", "copyright", "make", "model"})
MATROSKA_MUXERS = frozenset({"matroska", "webm"})
"""The muxers that write a Matroska segment, whose ``DateUTC`` holds the creation time."""
_PLAIN_TAGS = {"mov": _MOV_TAGS | {"location", "location-eng"}}
"""
The tags each muxer writes without ``use_metadata_tags`` (measured on FFmpeg 8.1.3 and 9.0.2):
MP4 and M4A keep iTunes' list, MOV QuickTime's user-data strings; every other tag is dropped.
"""
_CONTAINER_KEYS = ("major_brand", "minor_version", "compatible_brands")
"""Source tags that describe the source's file type, not its content: never written as tags."""
_ISO_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z?")


def _kept_tags(mode: MetadataMode, info: MediaInfo) -> list[str]:
    lowered = {key.lower() for key in LOCATION_KEYS}
    return [
        key
        for key in info.tags
        if key.lower() not in _CONTAINER_KEYS
        and key.lower() != "creation_time"
        and not (mode == "remove_location" and (key.lower() in lowered or is_location_key(key)))
    ]


def quicktime_tag_args(mode: MetadataMode, info: MediaInfo | None, muxer: str) -> list[str]:
    """
    Output options that keep the source's tags and creation time in an MP4, MOV or M4A output.
    FFmpeg's plain writer keeps only iTunes' or
    QuickTime's fixed list, so when the source has any other tag that `mode` keeps (an iPhone's
    ``com.apple.quicktime.*`` keys, a camera's ``firmware``), the tags are written as QuickTime
    metadata keys (``use_metadata_tags``), without the source's file-type tags. The source's
    creation time is written as given, so reruns stay byte-identical; Matroska and WebM outputs
    get it too, in the segment's own date field (``DateUTC``), which FFmpeg
    and sashimi read back as ``creation_time``. Other muxers (MP3, Ogg, …) only leave out the
    source's file-type tags (``major_brand`` and the like, which FFmpeg would copy as tags, and
    FFmpeg 9.0 joins with ``;``), as Matroska and WebM do too. Nothing for ``remove_all``, which
    drops every tag; merge with :func:`merged_movflags`.
    """
    if info is None or mode == "remove_all":
        return []
    created = next((value for key, value in info.tags.items() if key.lower() == "creation_time"), "")
    stated = next((part.strip() for part in created.split(";") if _ISO_TIME.fullmatch(part.strip())), None)
    when = ["-metadata:g", f"creation_time={stated}"] if stated else []
    if muxer not in QUICKTIME_MUXERS:
        args = [arg for key in info.tags if key.lower() in _CONTAINER_KEYS for arg in ("-metadata:g", f"{key}=")]
        return [*args, *when] if muxer in MATROSKA_MUXERS else args
    args = []
    plain = _PLAIN_TAGS.get(muxer, _ITUNES_TAGS)
    if any(key.lower() not in plain for key in _kept_tags(mode, info)):
        args += ["-movflags", "+use_metadata_tags"]
        for key in info.tags:
            if key.lower() in _CONTAINER_KEYS:
                args += ["-metadata:g", f"{key}="]
    return [*args, *when]


def merged_movflags(cmd: list[str]) -> list[str]:
    """`cmd` with every ``-movflags`` combined into the first (FFmpeg keeps only the last one given). Pure."""
    at = [index for index, arg in enumerate(cmd) if arg == "-movflags" and index + 1 < len(cmd)]
    if len(at) < 2:
        return cmd
    flags = "".join(cmd[index + 1] for index in at)
    merged = list(cmd)
    merged[at[0] + 1] = flags
    for index in reversed(at[1:]):
        del merged[index : index + 2]
    return merged


def opus_kbps(requested: int, channels: int | None) -> int:
    """
    The Opus bitrate to ask for: `requested`, lowered to Opus's ceiling of
    :data:`~sashimi.config.OPUS_MAX_KBPS_PER_CHANNEL` per channel when `channels` is known.
    """
    if channels is None or channels < 1:
        return requested
    return min(requested, OPUS_MAX_KBPS_PER_CHANNEL * channels)


OPUS_LAYOUTS = ("mono", "stereo", "3.0", "quad", "5.0", "5.1", "6.1", "7.1")
"""The channel layouts Opus encodes with speaker positions (Vorbis channel order) that browsers play."""


def opus_layout_filter(channels: int | None) -> str | None:
    """
    An audio filter that gives libopus a layout it takes, for sound of more than two channels (a
    5.1 with side speakers becomes 5.1, 7.1 wide becomes 7.1, and so on, as ffmpeg remixes them),
    or None for mono and stereo. A layout Opus already takes passes through unchanged.
    """
    if channels is None or channels <= 2:
        return None
    return "aformat=channel_layouts=" + "|".join(OPUS_LAYOUTS)
