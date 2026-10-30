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
import functools
import math
import os
import statistics
import threading
from array import array
from collections.abc import Generator, Iterable, Iterator, Sequence
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
    stream_timed_frames,
)
from sashimi.core.probe import MediaInfo, _file_key, _lines, _signed_float
from sashimi.errors import BinaryNotFoundError, MediaTooLargeError, ProcessingError

__all__ = [
    "VideoIndex",
    "Decoding",
    "video_index",
    "decoding_args",
    "clip_pass",
    "FORWARD_SEEKING",
    "DEFAULT_FRAME_SECONDS",
    "HELD_FRAMES",
]

FORWARD_SEEKING = frozenset({"mpegts", "mpeg", "ogg"})
LATER_ORIGIN = frozenset({"mpegts", "mpeg"})
"""
Demuxers with timestamp discontinuities, for which ffmpeg counts a command's output timestamps
from the later of its seek and the earliest start among the streams it maps, not from the file's
start: a command that maps only a video starting after the audio is shifted by the difference.
"""
"""Demuxers whose seek lands after the time when keyframes are far apart, which stream copies must avoid too."""
SHOWN_SEEKING = frozenset({"mov"})
"""Demuxers that seek a stream copy to the keyframe decoded at or before the time, which can be shown after it (open GOPs)."""
_PLAIN_SEEKING = frozenset({"gif"})
DEFAULT_FRAME_SECONDS = 0.04
HELD_FRAMES = 1.5
"""
How many frame intervals after a time the next frame may start and still be the one shown for it,
as ffmpeg's seek shows it; past that, the frame still on screen (held from before the time, as
variable-frame-rate recordings hold an unchanged picture) is shown instead.
"""
_TIE = 5e-4
"""How far below a half tick a time may read and still round up: frame times come from ffprobe to the microsecond, while the ``fps`` filter rounds exact integer timestamps, halves away from zero."""
NEAR_THE_START = 1.0
"""Seconds after the first keyframe is shown within which decoding always starts from the file's start."""
_READ_PAST = 2.0
_REORDER = 1.0
_CHUNK = 1 << 16
_CACHE_SIZE = 64


def reaches(frame_time: float, time: float, time_base: tuple[int, int] | None) -> bool:
    """
    Whether a frame shown at `frame_time` (seconds) is at or after `time` (read to the millisecond)
    as ffmpeg's seek and ``trim`` decide it: `time` rounded to the nearest tick of `time_base`
    (halves up), against the frame's own tick. Without a time base, to the microsecond. Pure.
    """
    micros = round(round(time, 3) * 1_000_000)
    if time_base is None:
        return round(frame_time * 1_000_000) >= micros
    num, den = time_base
    scale = num * 1_000_000
    return round(frame_time * den / num) >= (micros * den + scale // 2) // scale


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
    interval: float = DEFAULT_FRAME_SECONDS
    """The usual time between frames shown (the median), in seconds: what a held frame is measured against."""
    time_base: tuple[int, int] | None = None
    """The stream's time base as ``(num, den)``, which ffmpeg rounds a seek's time to; None if unknown."""
    video_start: float = 0.0
    """When the video stream starts, in seconds from the file's start (on :data:`LATER_ORIGIN` demuxers; else 0)."""
    audio_start: float | None = None
    """When the first audio stream starts, the same way; None without audio or off :data:`LATER_ORIGIN` demuxers."""
    plain: bool = False
    """Whether ffmpeg's own seek is always right in this file (GIFs), so :meth:`start_for` never overrides it."""

    def origin(self, decode_from: float, *, audio: bool = False) -> float:
        """
        Where a command that seeks to `decode_from` (0: no seek) counts its output timestamps from,
        in seconds from the file's start: `decode_from`, or on :data:`LATER_ORIGIN` demuxers the
        later of it and the earliest start among the streams the command maps (the video, plus
        the first audio stream with `audio`). ``trim`` filters and frame times are measured from it.
        """
        starts = [self.video_start]
        if audio and self.audio_start is not None:
            starts.append(self.audio_start)
        return max(decode_from, min(starts))

    def reaches(self, frame_time: float, time: float) -> bool:
        """Whether a frame shown at `frame_time` counts as at or after `time`, as ffmpeg's seek decides it."""
        return reaches(frame_time, time, self.time_base)

    @functools.cached_property
    def _order(self) -> list[float]:
        return sorted(self.shown)

    def frame_for(self, time: float) -> float:
        """
        When the frame shown for `time` starts (seconds): the first frame at or after `time` when it
        starts within :data:`HELD_FRAMES` frame intervals, as ffmpeg's seek picks it, else the frame
        still on screen at `time`. `time` is read to the millisecond, as commands give it.
        """
        at = round(time, 3)
        order = self._order
        position = bisect.bisect_left(order, at - 1e-6)
        while position > 0 and self.reaches(order[position - 1], at):
            position -= 1
        following = order[position] if position < len(order) else None
        previous = order[position - 1] if position > 0 else None
        if following is not None and (previous is None or following - at <= HELD_FRAMES * self.interval + 1e-6):
            return following
        return previous if previous is not None else at

    def held_from(self, time: float) -> float | None:
        """
        When the frame on screen at `time` started, if that frame is shown for `time` and started
        before it (see :meth:`frame_for`); None when the frame shown starts at or after `time`.
        """
        shown = self.frame_for(time)
        return None if self.reaches(shown, time) else shown

    def seek_time(self, time: float) -> float:
        """
        The time to seek to for :meth:`frame_for`'s frame (the first frame at or after the result):
        `time` itself, or for a held frame its start, rounded down to the millisecond.
        """
        held = self.held_from(time)
        return time if held is None else math.floor(held * 1000 + 1e-6) / 1000

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
        if self.plain:
            return None
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


def decoding_args(
    decode_from: float, time: float, length: float | None = None, origin: float | None = None
) -> Decoding:
    """
    Decoding from `decode_from` (seconds; 0 is the start of the file) and keeping `length`
    seconds (None: to the end) from `time`: what an input ``-ss time -t length`` does when the
    demuxer lands on the right keyframe, with the same frames and timestamps. `origin` is where
    the command's output timestamps start (:meth:`VideoIndex.origin`; None: `decode_from`). Pure.
    """
    inputs = ["-ss", fmt_seconds(decode_from)] if decode_from > 0 else []
    if length is not None:
        inputs += ["-t", fmt_seconds(length + round(time - decode_from, 3))]
    skip = round(time - (decode_from if origin is None else origin), 3)
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
    """Whether a file has a video stream to index (GIFs too, for their frame times; ffmpeg seeks in them by itself)."""
    return info.has_video


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
    and remembered while the file is unchanged. None when the packets hold no keyframe. A GIF's
    index is :attr:`~VideoIndex.plain`: ffmpeg seeks in it by itself, and the index only gives
    its frame times. Raises MediaTooLargeError
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


_Packet = tuple[float | None, float | None, bool]


def _parse(lines: Iterable[bytes]) -> Iterable[_Packet | tuple[int, int]]:
    """Packets (``(pts, dts, keyframe)``) from ffprobe's csv, then the stream's time base as ``(num, den)``."""
    for line in lines:
        fields = line.decode("ascii", "replace").strip().split(",")
        if len(fields) >= 3:
            yield _signed_float(fields[0]), _signed_float(fields[1]), "K" in fields[2]
        elif len(fields) == 1 and fields[0].count("/") == 1:
            num, _, den = fields[0].partition("/")
            if num.isdigit() and den.isdigit() and int(num) > 0 and int(den) > 0:
                yield int(num), int(den)


def _scan(input_path: str | os.PathLike[str], info: MediaInfo, until: float, cap: int | None) -> VideoIndex:
    end = info.start_time + until + _READ_PAST
    cmd = [ffprobe_binary(), "-hide_banner", "-loglevel", "error", *INPUT_PROTOCOLS, "-select_streams", "V:0"]
    cmd += ["-read_intervals", f"%{fmt_seconds(end)}"]
    cmd += ["-show_entries", "packet=pts_time,dts_time,flags:stream=time_base"]
    cmd += ["-of", "csv=p=0", file_arg(input_path)]
    packets: list[_Packet] = []
    time_base = None
    capped = None
    chunks = stream_ffmpeg(cmd, _CHUNK)
    try:
        for item in _parse(_lines(chunks)):
            if len(item) == 2:
                time_base = item
                continue
            if cap is not None and len(packets) >= cap:
                capped = cap
                break
            packets.append(item)
    except BinaryNotFoundError:
        raise
    except ProcessingError:
        pass
    finally:
        chunks.close()
    return _index(packets, info, until, end, capped, time_base)


def _index(
    packets: Sequence[_Packet],
    info: MediaInfo,
    until: float,
    end: float,
    capped: int | None,
    time_base: tuple[int, int] | None = None,
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
    order = sorted(shown)
    gaps = [b - a for a, b in zip(order, order[1:], strict=False) if b - a > 1e-6]
    interval = statistics.median(gaps) if gaps else frame
    if capped is not None:
        read_to = max(last - _REORDER, -1.0)
    elif latest + origin < end - _READ_PAST / 2:
        read_to = math.inf
    else:
        read_to = until
    forward = bool(info.container_names & FORWARD_SEEKING)
    video_start, audio_start = _stream_starts(info)
    return VideoIndex(
        shown,
        decoded,
        tuple(keys),
        missing,
        frame,
        read_to,
        capped,
        forward,
        interval,
        time_base,
        video_start,
        audio_start,
        info.container_names <= _PLAIN_SEEKING,
    )


def _stream_starts(info: MediaInfo) -> tuple[float, float | None]:
    """When the first video and audio streams start, from the file's start, on :data:`LATER_ORIGIN` demuxers."""
    if not info.container_names & LATER_ORIGIN:
        return 0.0, None
    video = next((s for s in info.streams if s.type == "video" and not s.attached_pic), None)
    audio = next((s for s in info.streams if s.type == "audio"), None)
    video_start = max(video.start_time - info.start_time, 0.0) if video and video.start_time is not None else 0.0
    audio_start = max(audio.start_time - info.start_time, 0.0) if audio and audio.start_time is not None else None
    return video_start, audio_start


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
    origin: float | None = None,
) -> list[str]:
    """
    Decodes the first video stream once, from `decode_from` to the end of the last of `windows`
    (``(start, end)`` in seconds from the file's start, ascending), and writes the frames shown
    inside any window, through `vf`, as raw RGB on stdout, printing each one's time on stderr
    (:data:`~sashimi.core._ffmpeg.FRAME_PROGRESS`), counted from `origin` (where the command's
    timestamps start, :meth:`VideoIndex.origin`; None: `decode_from`): add it to get the time
    from the file's start. Pure.
    """
    seek = ["-ss", fmt_seconds(decode_from)] if decode_from > 0 else []
    base = decode_from if origin is None else origin
    picks = "+".join(
        f"between(t,{fmt_seconds(max(start - base, 0.0))},{fmt_seconds(end - base)})" for start, end in windows
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
    frames: Iterable[tuple[float, bytes]],
    clips: Sequence[tuple[float, float]],
    fps: int,
    held: float | None = None,
) -> Iterator[tuple[int, bytes]]:
    """
    Resamples timed frames (``(seconds from the file's start, frame)``, in display order) to `fps`
    for each clip (``(start, length)``) as ffmpeg's ``fps`` filter does after ``-ss start -t
    length`` into raw video: each tick shows the last frame whose time, rounded to the nearest
    tick, isn't after it, and the output starts at the first frame's tick (ffmpeg doesn't fill
    the ticks before it, so a clip whose first frame starts a tick or more after it runs that
    much early, within one source frame). Yields ``(clip, frame)`` in the order they are settled
    (a clip is settled once a frame after its end, or the end of `frames`, is reached),
    ``round(length * fps)`` frames per clip at most; a clip whose video ends early repeats its
    last frame. Pure.

    With `held` (seconds), a clip whose first frame starts more than `held` after the clip does
    shows the frame still on screen at its start (the last frame given before it) until then,
    and a clip with no frame of its own shows that frame throughout, as a player shows a
    variable-frame-rate recording's held picture; its own frames then keep their ticks.
    """
    counts = [max(1, round(length * fps)) for _, length in clips]
    clips = [(float(fmt_seconds(start)), float(fmt_seconds(length))) for start, length in clips]
    done = [0] * len(clips)
    shown_now: list[bytes | None] = [None] * len(clips)
    before: list[bytes | None] = [None] * len(clips)
    tick: list[int | None] = [None] * len(clips)
    base = [0] * len(clips)

    def emit(slot: int, frame: bytes, copies: int = 1) -> Iterator[tuple[int, bytes]]:
        for _ in range(copies):
            if done[slot] < counts[slot]:
                yield slot, frame
                done[slot] += 1

    def finish(slot: int) -> Iterator[tuple[int, bytes]]:
        last = shown_now[slot]
        if last is None and held is not None:
            last = before[slot]
        if last is None:
            return
        while done[slot] < counts[slot]:
            yield from emit(slot, last)

    for time, data in frames:
        for slot, (start, length) in enumerate(clips):
            offset = time - start
            if done[slot] >= counts[slot]:
                continue
            if offset >= length - 1e-6:
                yield from finish(slot)
                continue
            current = tick[slot]
            if offset < -1e-6:
                if held is not None and current is None:
                    before[slot] = data
                continue
            at = _ticks(offset, fps) - base[slot]
            if current is None:
                carried = before[slot]
                if held is not None and carried is not None and offset > held + 1e-6 and at > 0:
                    yield from emit(slot, carried, at)
                    tick[slot], shown_now[slot] = at, data
                else:
                    tick[slot], shown_now[slot], base[slot] = 0, data, at
                continue
            shown = shown_now[slot]
            assert shown is not None
            while done[slot] < counts[slot] and at > current:
                yield from emit(slot, shown)
                current += 1
            tick[slot], shown_now[slot] = current, data
    for slot in range(len(clips)):
        yield from finish(slot)


def clip_pass(
    input_path: str | os.PathLike[str],
    index: VideoIndex,
    clips: Sequence[tuple[float, float]],
    vf: str,
    fps: int,
    frame_bytes: int,
    threads: int,
    ffmpeg: str = "ffmpeg",
) -> Generator[tuple[int, bytes], None, None]:
    """
    Decodes `clips` (``(start, length)``, ascending) in one pass over the video and yields each
    clip's frames at `fps`, through `vf` (which must not change the frame rate), as
    :func:`clip_frames` settles them: ``(clip, raw RGB frame of frame_bytes)``. A clip that starts
    while a frame from before it is still on screen (:meth:`VideoIndex.held_from`) shows that
    frame until its own first frame. Raises ProcessingError (after the frames it got) if ffmpeg
    fails; closing the generator stops ffmpeg.
    """
    earliest = [min(start, index.frame_for(start)) for start, _ in clips]
    windows = [(early - index.interval, start + length) for early, (start, length) in zip(earliest, clips, strict=True)]
    decode_from = index.decode_from(earliest[0])
    origin = index.origin(decode_from)
    cmd = build_pass_command(input_path, windows, vf, threads, ffmpeg, decode_from=decode_from, origin=origin)
    timed = stream_timed_frames(cmd, frame_bytes)
    try:
        frames = ((time + origin, data) for time, data in timed)
        yield from clip_frames(frames, clips, fps, HELD_FRAMES * index.interval)
    finally:
        timed.close()
