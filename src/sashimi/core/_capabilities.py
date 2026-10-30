# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
What the FFmpeg sashimi runs can do, read once per executable, and per-task refusals when it
can't: an encoder or filter a command needs that the build lacks, or a task an older FFmpeg is
known to get wrong. Nothing is refused for a version number alone, and a newer series than
sashimi is tested with is reported, not refused.
"""

import logging
import os
import re
import subprocess
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from sashimi.core._ffmpeg import ffmpeg_paths, parse_ffmpeg_version
from sashimi.errors import FFmpegTooOldError, MissingEncodersError, MissingFiltersError

__all__ = [
    "BEHAVIOURS",
    "ENCODERS",
    "FILTERS",
    "TESTED_SERIES",
    "FFmpegInfo",
    "check_command",
    "ffmpeg_info",
    "require_behaviour",
]

logger = logging.getLogger("sashimi")

TESTED_SERIES = ((8, 1), (9, 0))
"""The FFmpeg release series sashimi's test suite passes on."""

ENCODERS = {
    "libx264": ("H.264", "MP4 conversions, rotations and accurate cuts"),
    "libx265": ("HEVC", "MP4s that keep HDR (--keep-hdr)"),
    "libvpx-vp9": ("VP9", "WebM conversions and accurate cuts"),
    "libopus": ("Opus", "WebM sound and Ogg audio"),
    "libmp3lame": ("MP3", "MP3 audio"),
    "libwebp_anim": ("animated WebP", "animated WebP from video, previews and animated sheets"),
    "libwebp": ("WebP", "WebP from video"),
}
"""Encoders a build may lack (others, such as AAC and GIF, are always built in): what each encodes, and the tasks that need it."""

FILTERS = {
    "zscale": ("zscale (libzimg)", "tone-mapping HDR video"),
    "tonemap": ("tonemap", "tone-mapping HDR video"),
    "sidedata": ("sidedata", "tone-mapping HDR video"),
    "palettegen": ("palettegen", "GIFs from video"),
    "paletteuse": ("paletteuse", "GIFs from video"),
}
"""Filters a build may lack (``zscale`` needs libzimg): their names for people, and the tasks that need them."""

Behaviour = Literal["lossless cuts", "start times"]
BEHAVIOURS: dict[str, tuple[tuple[int, int], str]] = {
    "lossless cuts": (
        (8, 1),
        "its stream copies with a start time or a length come out wrong (a 2-second cut holds 3 seconds, and a cut "
        "starting after the last frame holds the whole video)",
    ),
    "start times": (
        (8, 1),
        "a conversion starting after the video's last frame writes a frame from before the start instead of "
        "nothing, so sashimi can't tell it from a real result",
    ),
}
"""
Tasks FFmpeg releases before a series get wrong, measured on FFmpeg 8.0.1 (Ubuntu 26.04's) on
2026-10-29: the oldest series that gets them right, and what goes wrong before it.
"""


@dataclass(frozen=True)
class FFmpegInfo:
    """The FFmpeg sashimi runs, and what it can do (see :func:`ffmpeg_info`)."""

    ffmpeg: str
    """The ffmpeg executable."""
    ffprobe: str
    """The ffprobe executable, chosen with it."""
    origin: str
    """Where the choice came from: ``PATH``, ``the SASHIMI_FFMPEG environment variable``, a sashimi.toml, or ``sashimi.set_ffmpeg_paths()``."""
    version: str | None
    """The version as ``ffmpeg -version`` states it (``n8.1.3-24-g92611900bb-20261026``), or None if it can't be read."""
    series: tuple[int, int] | None
    """The release series, (8, 1); None for a development build or an unreadable version."""
    encoders: frozenset[str] = field(default_factory=frozenset)
    """The optional encoders (:data:`ENCODERS`) it has."""
    filters: frozenset[str] = field(default_factory=frozenset)
    """The optional filters (:data:`FILTERS`) it has."""

    @property
    def status(self) -> Literal["tested", "newer", "older", "unknown"]:
        """``tested`` (a series in :data:`TESTED_SERIES`), ``newer`` or ``older`` than those, or ``unknown`` (no readable version)."""
        if self.series is None:
            return "unknown"
        if self.series in TESTED_SERIES:
            return "tested"
        return "newer" if self.series > max(TESTED_SERIES) else "older"

    @property
    def missing(self) -> dict[str, str]:
        """Each optional encoder or filter it lacks, named for people, with the tasks that need it."""
        found = {
            f"{ENCODERS[name][0]} encoder ({name})": ENCODERS[name][1] for name in ENCODERS if name not in self.encoders
        }
        found |= {f"{FILTERS[name][0]} filter": FILTERS[name][1] for name in FILTERS if name not in self.filters}
        return found

    @property
    def refused(self) -> dict[str, str]:
        """The tasks it gets wrong (:data:`BEHAVIOURS`), and why; none for a tested, newer or unknown series."""
        if self.series is None:
            return {}
        return {task: why for task, (oldest, why) in BEHAVIOURS.items() if self.series < oldest}

    def describe(self) -> str:
        """``FFmpeg 8.0.1-3ubuntu1 (/usr/bin/ffmpeg, from PATH)``."""
        return f"FFmpeg {self.version or 'of unknown version'} ({self.ffmpeg}, from {self.origin})"


def _stat_key(path: str) -> tuple[str, int, int]:
    try:
        stat = os.stat(path)
    except OSError:
        return (path, -1, -1)
    return (path, stat.st_size, stat.st_mtime_ns)


_cache: dict[tuple[str, str, int, int], str] = {}
_cache_lock = threading.Lock()


def _listing(ffmpeg: str, option: str) -> str:
    """The output of ``ffmpeg -hide_banner <option>``, run once per executable (path, size and mtime)."""
    key = (option, *_stat_key(ffmpeg))
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    try:
        text = subprocess.run(
            [ffmpeg, "-hide_banner", option], capture_output=True, text=True, timeout=30, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        text = ""
    with _cache_lock:
        _cache[key] = text
    return text


def remember_listing(ffmpeg: str, option: str, text: str) -> None:
    """Keeps a listing already run elsewhere (``-version``, when the executable is checked), so it isn't run again."""
    with _cache_lock:
        _cache[(option, *_stat_key(ffmpeg))] = text


def _names(listing: str) -> frozenset[str]:
    """The names in an ``-encoders`` or ``-filters`` listing: the word after the flags, on each line after the ``------`` rule."""
    lines = listing.splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if line.strip().startswith("---")), len(lines))
    return frozenset(parts[1] for line in lines[start:] if len(parts := line.split()) >= 2)


def encoders_of(ffmpeg: str) -> frozenset[str]:
    """Every encoder `ffmpeg` lists (``-encoders``), read once per executable."""
    return _names(_listing(ffmpeg, "-encoders"))


def filters_of(ffmpeg: str) -> frozenset[str]:
    """Every filter `ffmpeg` lists (``-filters``), read once per executable."""
    return _names(_listing(ffmpeg, "-filters"))


_reported: set[tuple[str, int, int]] = set()


def version_of(ffmpeg: str) -> tuple[str | None, tuple[int, int] | None]:
    """The version `ffmpeg` states and its release series (see :func:`~sashimi.core._ffmpeg.parse_ffmpeg_version`), read once per executable."""
    banner = _listing(ffmpeg, "-version").splitlines()
    first = banner[0] if banner else ""
    match = re.match(r"\S+ version (\S+)", first)
    return (match.group(1) if match else None), parse_ffmpeg_version(first)


def ffmpeg_info(ffmpeg: str | None = None) -> FFmpegInfo:
    """
    The FFmpeg sashimi runs (or the ffmpeg at `ffmpeg`): its paths, where they came from, its
    version and the optional encoders and filters it has. Runs ``-version``, ``-encoders`` and
    ``-filters`` at most once per executable per process (about 0.1 s in all).
    """
    paths = ffmpeg_paths()
    binary = ffmpeg or paths.ffmpeg or "ffmpeg"
    version, series = version_of(binary)
    info = FFmpegInfo(
        ffmpeg=binary,
        ffprobe=paths.ffprobe or "ffprobe",
        origin=paths.origin,
        version=version,
        series=series,
        encoders=encoders_of(binary) & ENCODERS.keys(),
        filters=filters_of(binary) & FILTERS.keys(),
    )
    _report(info)
    return info


def _report(info: FFmpegInfo) -> None:
    key = _stat_key(info.ffmpeg)
    if key in _reported or info.status in ("tested", "older"):
        return
    _reported.add(key)
    tested = " and ".join(".".join(map(str, series)) for series in TESTED_SERIES)
    if info.status == "newer":
        logger.info(f"{info.describe()} is newer than the series sashimi is tested with ({tested}).")
    else:
        logger.info(f"{info.describe()} states no release version; sashimi is tested with {tested}.")


def _advice(missing: str) -> str:
    return (
        f"Install a current FFmpeg (8.1 or 9.0{missing}), or point sashimi at one: set SASHIMI_FFMPEG to its ffmpeg, "
        "run sashimi config set ffmpeg.path <its ffmpeg>, or call sashimi.set_ffmpeg_paths()."
    )


def require_behaviour(task: Behaviour, ffmpeg: str) -> None:
    """Raises FFmpegTooOldError when `ffmpeg`'s series is known to get `task` wrong (:data:`BEHAVIOURS`); nothing for a tested, newer or unreadable version."""
    _, series = version_of(ffmpeg)
    oldest, why = BEHAVIOURS[task]
    if series is None or series >= oldest:
        return
    info = ffmpeg_info(ffmpeg)
    raise FFmpegTooOldError(
        f"{info.describe()} can't be trusted with {task}: {why}. FFmpeg {'.'.join(map(str, oldest))} and newer get them "
        f"right. {_advice('')}",
        task=task,
        info=info,
    )


_ENCODER_OPTIONS = ("-c", "-codec", "-vcodec", "-acodec", "-scodec")
_GRAPH_OPTIONS = frozenset({"-vf", "-af", "-filter_complex", "-lavfi", "-filter:v", "-filter:a", "-filter"})


def needs(cmd: Sequence[str]) -> tuple[list[str], list[str]]:
    """The optional encoders and filters (:data:`ENCODERS`, :data:`FILTERS`) an ffmpeg command uses. Pure."""
    encoders: list[str] = []
    filters: list[str] = []
    for option, value in zip(cmd, cmd[1:], strict=False):
        if (option in _ENCODER_OPTIONS or option.startswith(("-c:", "-codec:"))) and value in ENCODERS:
            encoders.append(value)
        if option in _GRAPH_OPTIONS or option.startswith(("-filter:", "-filter_complex")):
            for name in FILTERS:
                if re.search(rf"(?:^|[,;\]\s]){name}(?=[=,;\[\s]|$)", value):
                    filters.append(name)
    return list(dict.fromkeys(encoders)), list(dict.fromkeys(filters))


def check_command(cmd: Sequence[str]) -> None:
    """
    Raises MissingEncodersError or MissingFiltersError (both :class:`~sashimi.errors.UnsuitableFFmpegError`)
    when the ffmpeg in `cmd` lacks an optional encoder or filter the command uses, before it runs.
    ffprobe commands pass unchecked.
    """
    if not cmd or os.path.basename(cmd[0]).lower().startswith("ffprobe"):
        return
    encoders, filters = needs(cmd)
    if not encoders and not filters:
        return
    listed_encoders = encoders_of(cmd[0]) if encoders else frozenset()
    listed_filters = filters_of(cmd[0]) if filters else frozenset()
    lacking_encoders = [name for name in encoders if listed_encoders and name not in listed_encoders]
    lacking_filters = [name for name in filters if listed_filters and name not in listed_filters]
    if not lacking_encoders and not lacking_filters:
        return
    info = ffmpeg_info(cmd[0])
    if lacking_encoders:
        names = [f"the {ENCODERS[name][0]} encoder ({name})" for name in lacking_encoders]
        raise MissingEncodersError(
            f"{info.describe()} lacks {_joined(names)}, which {ENCODERS[lacking_encoders[0]][1]} need. "
            f"{_advice(', with ' + _joined(lacking_encoders))}",
            missing=lacking_encoders,
            info=info,
        )
    names = [FILTERS[name][0] for name in lacking_filters]
    raise MissingFiltersError(
        f"{info.describe()} lacks the {_joined(names)} filter{'s' if len(names) > 1 else ''}, which "
        f"{FILTERS[lacking_filters[0]][1]} needs. {_advice(', with ' + _joined(lacking_filters))}",
        missing=lacking_filters,
        info=info,
    )


def _joined(items: Iterable[str]) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def advice(missing: Sequence[str] = ()) -> str:
    """What to do about an unsuitable FFmpeg: install a current one (with `missing`), or point sashimi at one."""
    return _advice(", with " + _joined(missing) if missing else "")
