# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Keyframe-aware seeking: where to start decoding so the frame shown at a time is the frame at that time.

ffmpeg's input ``-ss`` asks the demuxer to seek, and what it lands on depends on the container:
MPEG-TS, MPEG-PS and Ogg land near the time and decoding then waits for the next keyframe, up to
a whole keyframe interval late; MP4 and MOV land on a keyframe chosen by decoding order, so the
frames an open-GOP keyframe shows before itself are lost; AVI and FLV whose video starts a little
after the file jump to the second keyframe for a time before the first. So every decoding route
reads the video stream's packet list once (up to the latest time it needs), chooses the keyframe
itself, seeks to just before it, and drops the frames before the wanted time after decoding.
"""

import bisect
import collections
import math
import os
import threading
from array import array
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

from sashimi.config._limits import Limits, resolve_limits, rough_length_text
from sashimi.core._ffmpeg import (
    FRAME_PROGRESS,
    INPUT_PROTOCOLS,
    base_args,
    ffprobe_binary,
    file_arg,
    fmt_seconds,
    stream_ffmpeg,
)
from sashimi.core.probe import MediaInfo, _file_key, _lines, _signed_float
from sashimi.errors import BinaryNotFoundError, MediaTooLargeError, ProcessingError

__all__ = ["VideoIndex", "Decoding", "video_index", "decoding_args", "FORWARD_SEEKING", "DEFAULT_FRAME_SECONDS"]

FORWARD_SEEKING = frozenset({"mpegts", "mpeg", "ogg"})
"""Demuxers whose seek lands after the time when keyframes are far apart, which stream copies must avoid too."""
SHOWN_SEEKING = frozenset({"mov"})
"""Demuxers that seek a stream copy to the keyframe decoded at or before the time, which can be shown after it (open GOPs)."""
_PLAIN_SEEKING = frozenset({"gif"})
DEFAULT_FRAME_SECONDS = 0.04
_TIE = 5e-4
"""How far below a half tick a time may read and still round up: frame times come from ffprobe to the microsecond, while the ``fps`` filter rounds exact integer timestamps, halves away from zero."""
NEAR_THE_START = 1.0
"""Seconds after the first keyframe is shown within which decoding always starts from the file's start."""
_READ_PAST = 2.0
_REORDER = 1.0
_CHUNK = 1 << 16
_CACHE_SIZE = 64


@dataclass(frozen=True)
class VideoIndex:
    """
    The first video stream's packets, in decoding order, as far as they were read. Times are
    seconds from the file's start, the clock ``ffmpeg -ss`` counts on.
    """

    shown: array
    """When each packet's frame is shown: its pts, or for a packet without one its dts plus the stream's reordering delay."""
    decoded: array
    """When each packet is decoded: its dts, or its pts when it has none."""
    keys: tuple[int, ...]
    """Indexes (into the packets) of the keyframes."""
    missing_pts: bool
    """Whether some packets have no pts, so ffmpeg guesses frame times after a seek until it settles."""
    frame: float
    """One frame's length in seconds (from the frame rate), used as a margin."""
    read_to: float
    """The time up to which every frame shown was read: ``math.inf`` once the whole stream was."""
    capped: int | None = None
    """The packet limit that stopped the reading, if one did."""
    forward: bool = False
    """Whether the demuxer's own seek lands after the time (MPEG-TS, MPEG-PS, Ogg), so it is never used."""

    def start_for(self, time: float) -> float | None:
        """
        Where to decode from for `time`: None when ffmpeg's own seek (``-ss time``) lands right, which
        is cheaper (the demuxer marks the frames before the time, and the decoder skips those no
        other frame needs), else :meth:`decode_from`. ffmpeg's seek is used unless the demuxer seeks
        forward (:attr:`forward`), `time` is within :data:`NEAR_THE_START` of the first keyframe being
        shown (AVI and FLV whose video starts after the file jump to the second keyframe), or a
        keyframe is decoded at or before `time` but shown after it (MP4 and MOV pick keyframes by
        decoding time, and lose the frames an open-GOP keyframe shows before itself).
        """
        if self.forward or not self.keys:
            return self.decode_from(time)
        if time < self.shown[self.keys[0]] + NEAR_THE_START:
            return self.decode_from(time)
        for packet in self.keys:
            if self.decoded[packet] <= time + 1e-6 < self.shown[packet]:
                return self.decode_from(time)
        return None

    def decode_from(self, time: float) -> float:
        """
        Where decoding must start (seconds; 0 means the start of the file) for the frame shown at
        `time` to be decoded: just before the last keyframe shown at or before `time`, or the one
        before it when the stream lacks pts. 0 when that keyframe is the first.
        """
        chosen = -1
        for position, packet in enumerate(self.keys):
            if self.shown[packet] <= time + 1e-6:
                chosen = position
        if chosen >= 0 and self.missing_pts:
            chosen -= 1
        if chosen <= 0:
            return 0.0
        start = self.decoded[self.keys[chosen]] - self.frame / 2
        first = self.decoded[self.keys[0]] - self.frame / 2
        return round(start, 3) if start > max(first, 0.0) else 0.0

    def copy_from(self, time: float, *, at_shown: bool = False) -> float:
        """
        Where a stream copy starting at `time` must seek so it starts at the keyframe at or before
        `time`, as a copy does: just before that keyframe is decoded, or with `at_shown` (MP4 and
        MOV, which seek by display time) at the time it is shown; 0 when it is the first.
        """
        chosen = -1
        for position, packet in enumerate(self.keys):
            if self.shown[packet] <= time + 1e-6:
                chosen = position
        if chosen <= 0:
            return 0.0
        packet = self.keys[chosen]
        start = self.shown[packet] if at_shown else self.decoded[packet] - self.frame / 2
        return round(start, 3) if start > 0 else 0.0

    def decoded_before(self, time: float) -> int:
        """How many packets are decoded before `time` (in decoding order): a measure of decoding work."""
        return bisect.bisect_left(self.decoded, time - 1e-6)

    def frame_times(self, start: float, end: float) -> list[float]:
        """The times frames are shown from `start` up to (not including) `end`, sorted."""
        return sorted(t for t in self.shown if start - 1e-6 <= t < end - 1e-6)


@dataclass(frozen=True)
class Decoding:
    """How a command decodes from a time: input options, and the filters that drop what comes before it."""

    inputs: list[str]
    """Options before ``-i``: the seek to where decoding starts, and how long to read."""
    video: str | None
    """The video filter that keeps the frames from the time (for the length) with timestamps from 0, or None."""
    audio: str | None
    """The same for audio, or None."""


def decoding_args(decode_from: float, time: float, length: float | None = None) -> Decoding:
    """
    Decoding from `decode_from` (seconds; 0 is the start of the file) and keeping `length`
    seconds (None: to the end) from `time`: what an input ``-ss time -t length`` does when the
    demuxer lands on the right keyframe, with the same frames and timestamps. Pure.
    """
    inputs = ["-ss", fmt_seconds(decode_from)] if decode_from > 0 else []
    skip = round(time - decode_from, 3)
    if length is not None:
        inputs += ["-t", fmt_seconds(length + skip)]
    if skip <= 0:
        return Decoding(inputs, None, None)
    span = f"start={fmt_seconds(skip)}" + (f":end={fmt_seconds(skip + length)}" if length is not None else "")
    shift = f"PTS-{fmt_seconds(skip)}/TB"
    return Decoding(inputs, f"trim={span},setpts={shift}", f"atrim={span},asetpts={shift}")


def with_filter(skip: str | None, vf: str) -> str:
    """`vf` after `skip` (a filter from :class:`Decoding`), if there is one. Pure."""
    return f"{skip},{vf}" if skip else vf


_indexes: collections.OrderedDict[tuple[int, int, int, int], VideoIndex] = collections.OrderedDict()
_indexes_lock = threading.Lock()


def needs_index(info: MediaInfo) -> bool:
    """Whether seeking in this file goes through its keyframe index: every video container except GIF."""
    return info.has_video and not info.container_names <= _PLAIN_SEEKING


def _too_many(cap: int) -> MediaTooLargeError:
    return MediaTooLargeError(
        "Finding where to start decoding this video means reading its frames from the start, and it has "
        f"more than {cap:,} before the point needed (about {rough_length_text(cap / 30)} at 30 fps); the limit is "
        f"{cap:,} packets.",
        detail=f"more than {cap} video packets before the time asked for",
        limit="max_scan_packets",
    )


def video_index(
    input_path: str | os.PathLike[str], info: MediaInfo, until: float, limits: Limits | None = None
) -> VideoIndex | None:
    """
    The first video stream's packets up to `until` seconds (plus a little), read once per file
    and remembered while the file is unchanged. None for files ffmpeg seeks in correctly by
    itself (GIFs) and when the packets hold no keyframe. Raises MediaTooLargeError
    (``limit="max_scan_packets"``) when that means reading more than ``limits.max_scan_packets``
    video packets.
    """
    if not needs_index(info):
        return None
    cap = resolve_limits(limits).max_scan_packets
    key = _file_key(input_path)
    known = None
    if key is not None:
        with _indexes_lock:
            known = _indexes.get(key)
            if known is not None:
                _indexes.move_to_end(key)
    if known is not None and known.read_to >= until:
        return known if known.keys else None
    if known is not None and known.capped is not None and cap is not None and cap <= known.capped:
        raise _too_many(cap)
    index = _scan(input_path, info, until, cap)
    if key is not None and key == _file_key(input_path):
        with _indexes_lock:
            _indexes[key] = index
            _indexes.move_to_end(key)
            while len(_indexes) > _CACHE_SIZE:
                _indexes.popitem(last=False)
    if index.read_to < until:
        assert cap is not None
        raise _too_many(cap)
    return index if index.keys else None


def _parse(lines: Iterable[bytes]) -> Iterable[tuple[float | None, float | None, bool]]:
    for line in lines:
        fields = line.decode("ascii", "replace").strip().split(",")
        if len(fields) >= 3:
            yield _signed_float(fields[0]), _signed_float(fields[1]), "K" in fields[2]


def _scan(input_path: str | os.PathLike[str], info: MediaInfo, until: float, cap: int | None) -> VideoIndex:
    end = info.start_time + until + _READ_PAST
    cmd = [ffprobe_binary(), "-hide_banner", "-loglevel", "error", *INPUT_PROTOCOLS, "-select_streams", "V:0"]
    cmd += ["-read_intervals", f"%{fmt_seconds(end)}", "-show_entries", "packet=pts_time,dts_time,flags"]
    cmd += ["-of", "csv=p=0", file_arg(input_path)]
    packets: list[tuple[float | None, float | None, bool]] = []
    capped = None
    chunks = stream_ffmpeg(cmd, _CHUNK)
    try:
        for packet in _parse(_lines(chunks)):
            if cap is not None and len(packets) >= cap:
                capped = cap
                break
            packets.append(packet)
    except BinaryNotFoundError:
        raise
    except ProcessingError:
        pass
    finally:
        chunks.close()
    return _index(packets, info, until, end, capped)


def _index(
    packets: Sequence[tuple[float | None, float | None, bool]],
    info: MediaInfo,
    until: float,
    end: float,
    capped: int | None,
) -> VideoIndex:
    origin = info.start_time
    delay = max((pts - dts for pts, dts, _ in packets if pts is not None and dts is not None), default=0.0)
    shown, decoded, keys = array("d"), array("d"), []
    missing = False
    last = -math.inf
    latest = -math.inf
    for pts, dts, key in packets:
        if pts is None and dts is None:
            continue
        missing = missing or pts is None
        show = pts if pts is not None else dts + delay  # type: ignore[operator]
        decode = dts if dts is not None else pts
        assert decode is not None
        decode = max(decode - origin, last)
        last = decode
        latest = max(latest, show - origin)
        if key:
            keys.append(len(shown))
        shown.append(show - origin)
        decoded.append(decode)
    frame = 1.0 / info.fps if info.fps and info.fps > 1 else DEFAULT_FRAME_SECONDS
    if capped is not None:
        read_to = max(last - _REORDER, -1.0)
    elif latest + origin < end - _READ_PAST / 2:
        read_to = math.inf
    else:
        read_to = until
    forward = bool(info.container_names & FORWARD_SEEKING)
    return VideoIndex(shown, decoded, tuple(keys), missing, frame, read_to, capped, forward)


def clip_route(index: VideoIndex | None, clips: Sequence[tuple[float, float]]) -> Literal["seek", "pass"]:
    """
    Whether `clips` (``(start, length)``, ascending) are cheaper to decode with one seek each
    (``seek``) or in one pass over the video (``pass``), counted in frames decoded. Pure.
    """
    if index is None or not clips:
        return "seek"
    per_clip = sum(
        index.decoded_before(start + length) - index.decoded_before(index.decode_from(start)) + 1
        for start, length in clips
    )
    first, (last, last_length) = clips[0][0], clips[-1]
    one_pass = index.decoded_before(last + last_length) - index.decoded_before(index.decode_from(first)) + 1
    return "pass" if one_pass < per_clip else "seek"


def build_pass_command(
    input_path: str | os.PathLike[str],
    windows: Sequence[tuple[float, float]],
    vf: str,
    threads: int,
    ffmpeg: str = "ffmpeg",
    *,
    decode_from: float = 0.0,
) -> list[str]:
    """
    Decodes the first video stream once, from `decode_from` to the end of the last of `windows`
    (``(start, end)`` in seconds from the file's start, ascending), and writes the frames shown
    inside any window, through `vf`, as raw RGB on stdout, printing each one's time on stderr
    (:data:`~sashimi.core._ffmpeg.FRAME_PROGRESS`; add `decode_from` to it). Pure.
    """
    seek = ["-ss", fmt_seconds(decode_from)] if decode_from > 0 else []
    picks = "+".join(
        f"between(t,{fmt_seconds(max(start - decode_from, 0.0))},{fmt_seconds(end - decode_from)})"
        for start, end in windows
    )
    reach = windows[-1][1] - decode_from + 1.0
    return [
        ffmpeg,
        *base_args(threads),
        *INPUT_PROTOCOLS,
        "-threads",
        str(threads),
        *seek,
        "-t",
        fmt_seconds(reach),
        "-i",
        file_arg(input_path),
        "-map",
        "0:V:0",
        "-vf",
        f"select='{picks}',{vf},{FRAME_PROGRESS}",
        "-fps_mode",
        "passthrough",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]


def _ticks(seconds: float, fps: int) -> int:
    """`seconds` in frames of `fps`, rounded to the nearest (halves away from zero), as ffmpeg's ``fps`` filter rounds."""
    value = seconds * fps
    return int(math.floor(abs(value) + 0.5 + _TIE)) * (1 if value >= 0 else -1)


def clip_frames(
    frames: Iterable[tuple[float, bytes]], clips: Sequence[tuple[float, float]], fps: int
) -> Iterator[tuple[int, bytes]]:
    """
    Resamples timed frames (``(seconds from the file's start, frame)``, in display order) to `fps`
    for each clip (``(start, length)``) as ffmpeg's ``fps`` filter does after ``-ss start -t
    length`` into raw video: each tick shows the last frame whose time, rounded to the nearest
    tick, isn't after it, and the first frame also fills the ticks before its own (as the raw
    output's constant frame rate does). Yields ``(clip, frame)``
    in the order they are settled, ``round(length * fps)`` frames per clip at most; a clip whose
    video ends early repeats its last frame. Pure.
    """
    counts = [max(1, round(length * fps)) for _, length in clips]
    clips = [(float(fmt_seconds(start)), float(fmt_seconds(length))) for start, length in clips]
    done = [0] * len(clips)
    held: list[bytes | None] = [None] * len(clips)
    tick: list[int | None] = [None] * len(clips)
    lead = [0] * len(clips)

    def emit(slot: int, frame: bytes) -> Iterator[tuple[int, bytes]]:
        for _ in range(1 + lead[slot]):
            if done[slot] < counts[slot]:
                yield slot, frame
                done[slot] += 1
        lead[slot] = 0

    for time, data in frames:
        for slot, (start, length) in enumerate(clips):
            offset = time - start
            if offset < -1e-6 or offset >= length - 1e-6 or done[slot] >= counts[slot]:
                continue
            at = _ticks(offset, fps)
            current = tick[slot]
            if current is None:
                tick[slot], held[slot], lead[slot] = at, data, max(at, 0)
                continue
            shown = held[slot]
            assert shown is not None
            while done[slot] < counts[slot] and at > current:
                yield from emit(slot, shown)
                current += 1
            tick[slot], held[slot] = current, data
    for slot in range(len(clips)):
        last = held[slot]
        if last is None:
            continue
        while done[slot] < counts[slot]:
            yield from emit(slot, last)
