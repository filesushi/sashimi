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


def opus_kbps(requested: int, channels: int | None) -> int:
    """
    The Opus bitrate to ask for: `requested`, lowered to Opus's ceiling of
    :data:`~sashimi.config.OPUS_MAX_KBPS_PER_CHANNEL` per channel when `channels` is known.
    """
    if channels is None or channels < 1:
        return requested
    return min(requested, OPUS_MAX_KBPS_PER_CHANNEL * channels)
