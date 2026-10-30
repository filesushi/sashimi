# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Content-based media probing with ffprobe."""

import collections
import json
import re
import math
import os
import threading
from collections.abc import Container, Iterable, Iterator
from typing import Any

from pydantic import BaseModel, ConfigDict

from sashimi.core._ffmpeg import (
    AUDIO_CONTAINERS,
    INPUT_PROTOCOLS,
    VIDEO_CONTAINERS,
    ffprobe_binary,
    file_arg,
    run_ffmpeg,
    stream_ffmpeg,
)
from sashimi.config._limits import Limits, resolve_limits, rough_length_text
from sashimi.errors import BinaryNotFoundError, MediaTooLargeError, ProcessingError, UnsupportedMediaError

__all__ = ["MediaInfo", "StreamInfo", "anamorphic", "check_decodable", "probe", "VIDEO_CONTAINERS", "AUDIO_CONTAINERS"]

_SEGMENT_TIMESTAMPS = frozenset({"matroska", "webm"})
_PAST_THE_END = "999999999"
_PACKETS_PER_SECOND = 77
"""Packets a second of 30 fps video with 48 kHz AAC audio holds: 30 video and about 47 audio."""
_NO_SCAN = frozenset({"gif"})
_SCAN_CHUNK = 1 << 16
_SCAN_CACHE_SIZE = 64


class StreamInfo(BaseModel):
    """One stream of a file, in stream order."""

    model_config = ConfigDict(frozen=True)

    type: str
    """ffprobe's codec type: ``video``, ``audio``, ``subtitle``, ``data`` or ``attachment``."""
    codec: str
    """ffprobe's codec name, e.g. ``h264``; ``unknown`` when ffprobe has none (e.g. a timecode track)."""
    language: str | None = None
    """The stream's language tag (e.g. ``eng``), if it has one other than ``und``."""
    attached_pic: bool = False
    """True for embedded cover art (a still picture stored as a video stream)."""
    channels: int | None = None
    """Audio channels (1 for mono, 2 for stereo), for audio streams."""
    tags: dict[str, str] = {}
    """The stream's metadata tags as ffprobe shows them (title, handler name, …)."""
    codec_tag: str | None = None
    """ffprobe's codec tag (e.g. ``avc1``; ``text`` for a QuickTime chapter track)."""
    start_time: float | None = None
    """The timestamp the stream starts at, in seconds on the file's clock (compare with ``MediaInfo.start_time``), if ffprobe states one."""

    @property
    def chapter_track(self) -> bool:
        """True for a QuickTime chapter track, which holds the chapter titles (not a stream to copy)."""
        return self.type == "data" and self.codec_tag == "text"


class MediaInfo(BaseModel):
    """What ffprobe found in a file. Dimensions are in display orientation (rotation applied)."""

    model_config = ConfigDict(frozen=True)

    container: str
    """ffprobe's demuxer name list, e.g. ``"matroska,webm"`` or ``"mov,mp4,m4a,3gp,3g2,mj2"``."""
    duration: float | None = None
    """
    Seconds from the first frame, the span ``ffmpeg -ss`` seeks in, or None for a still image.
    Recordings that were never finalized (no stated length) are measured from their packets, up
    to ``Limits.max_scan_packets``.
    """
    start_time: float = 0.0
    """The timestamp the file starts at, in seconds: the time ``ffmpeg -ss`` counts from (often 0; a live capture's or a TS's may be anything)."""
    size_bytes: int | None = None
    bit_rate: int | None = None
    has_video: bool = False
    """True if there is a real video stream; embedded cover art doesn't count."""
    has_audio: bool = False
    width: int | None = None
    height: int | None = None
    rotation: int = 0
    """Display rotation in degrees (0, 90, 180 or 270) that players apply, in either direction (see ``display_turn``)."""
    display_turn: int = 0
    """The clockwise turn (0, 90, 180 or 270 degrees) players apply to show the picture, from its display matrix."""
    display_mirrored: bool = False
    """True when the display matrix also mirrors the picture."""
    dolby_vision: bool = False
    """True when the video carries a Dolby Vision configuration record (``dvcC``/``dvvC``), as iPhone HDR video does."""
    display_aspect_ratio: float | None = None
    fps: float | None = None
    video_codec: str | None = None
    pixel_format: str | None = None
    """The video's pixel format as ffprobe names it, e.g. ``yuv420p`` (8-bit 4:2:0) or ``yuv420p10le``."""
    color_space: str | None = None
    """The video's colour matrix as ffprobe names it, e.g. ``bt709``, ``bt2020nc``, or ``ipt-c2`` for Dolby Vision's own."""
    color_transfer: str | None = None
    """The video's transfer function as ffprobe names it, e.g. ``bt709``, ``smpte2084`` (PQ) or ``arib-std-b67`` (HLG)."""
    color_primaries: str | None = None
    """The video's colour primaries as ffprobe names them, e.g. ``bt709`` or ``bt2020``."""
    audio_codec: str | None = None
    codecs: list[str] = []
    """Codec names of every stream, in stream order."""
    streams: list[StreamInfo] = []
    """Every stream's type and codec, in stream order."""
    audio_channels: int | None = None
    """Channels of the first audio stream (1 for mono, 2 for stereo)."""
    tags: dict[str, str] = {}
    """The file's global metadata tags as ffprobe shows them (title, artist, location, …)."""

    @property
    def container_names(self) -> frozenset[str]:
        return frozenset(self.container.split(","))


def _ratio(text: Any) -> float | None:
    if not isinstance(text, str) or ":" not in text and "/" not in text:
        return None
    num, _, den = text.replace(":", "/").partition("/")
    try:
        n, d = float(num), float(den)
    except ValueError:
        return None
    return n / d if n > 0 and d > 0 else None


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and result >= 0 else None


def _signed_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _clock_seconds(text: str) -> float | None:
    parts = text.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
    except ValueError:
        return None
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        return None
    result = hours * 3600 + minutes * 60 + seconds
    return result if result > 0 else None


def _tag_duration(tags: Any) -> float | None:
    if not isinstance(tags, dict):
        return None
    for key, value in tags.items():
        name = str(key).upper()
        if (name == "DURATION" or name.startswith("DURATION-")) and (seconds := _clock_seconds(str(value))):
            return seconds
    return None


def _start_time(data: dict[str, Any]) -> float:
    """
    The time ffmpeg counts ``-ss`` from: the container's start time, else the earliest stream's,
    else 0. Pure function over ``ffprobe -show_format -show_streams -of json`` output.
    """
    start = _signed_float(data.get("format", {}).get("start_time"))
    if start is not None:
        return start
    starts = [t for t in (_signed_float(s.get("start_time")) for s in data.get("streams", [])) if t is not None]
    return min(starts) if starts else 0.0


def _rotation(stream: dict[str, Any]) -> int:
    degrees: float | None = None
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            degrees = _float(abs(float(side_data["rotation"])))
            break
    if degrees is None:
        degrees = _float(stream.get("tags", {}).get("rotate"))
    if degrees is None:
        return 0
    return int(round(degrees / 90.0)) * 90 % 360


def _display(stream: dict[str, Any]) -> tuple[int, bool]:
    """The clockwise turn players apply to `stream` (0, 90, 180 or 270), and whether its display matrix mirrors it."""
    for side_data in stream.get("side_data_list", []):
        if "rotation" not in side_data:
            continue
        try:
            counter = float(side_data["rotation"])
        except (TypeError, ValueError):
            return 0, False
        numbers = [int(n) for n in re.findall(r"-?\d+", str(side_data.get("displaymatrix", "")).replace(":", " : "))]
        cells = [n for i, n in enumerate(numbers) if i % 4]
        mirrored = len(cells) >= 5 and cells[0] * cells[4] - cells[1] * cells[3] < 0
        return int(round(-counter / 90.0)) * 90 % 360, mirrored
    tagged = _float(stream.get("tags", {}).get("rotate"))
    return (int(round(tagged / 90.0)) * 90 % 360 if tagged is not None else 0), False


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _tags(entry: dict[str, Any]) -> dict[str, str]:
    """
    A file's or stream's tags. FFmpeg 9.0's ffprobe joins a value stated twice with ``;`` (an MP4's
    ``creation_time`` in its header and as a metadata key): a value repeated that way is shown once.
    """
    tags = entry.get("tags")
    if not isinstance(tags, dict):
        return {}
    shown = {}
    for key, value in tags.items():
        parts = str(value).split(";")
        shown[str(key)] = parts[0] if len(parts) > 1 and len(set(parts)) == 1 else str(value)
    return shown


def _stream(stream: dict[str, Any]) -> StreamInfo:
    tags = _tags(stream)
    language = next((v for k, v in tags.items() if k.lower() == "language"), None)
    return StreamInfo(
        type=stream.get("codec_type", "unknown"),
        codec=stream.get("codec_name", "unknown"),
        language=language if language and language != "und" else None,
        attached_pic=bool(stream.get("disposition", {}).get("attached_pic")),
        channels=_int(stream.get("channels")) if stream.get("codec_type") == "audio" else None,
        tags=tags,
        codec_tag=stream.get("codec_tag_string") or None,
        start_time=_signed_float(stream.get("start_time")),
    )


def parse_ffprobe_json(data: dict[str, Any]) -> MediaInfo:
    """Builds a MediaInfo from ``ffprobe -show_format -show_streams -of json`` output. Pure function."""
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
    audios = [s for s in streams if s.get("codec_type") == "audio"]

    duration = _float(fmt.get("duration")) or _tag_duration(fmt.get("tags"))
    if not duration:
        durations = [d for d in (_float(s.get("duration")) or _tag_duration(s.get("tags")) for s in streams) if d]
        duration = max(durations) if durations else None

    info: dict[str, Any] = {
        "container": fmt.get("format_name", ""),
        "duration": duration,
        "start_time": _start_time(data),
        "size_bytes": int(fmt["size"]) if str(fmt.get("size", "")).isdigit() else None,
        "bit_rate": int(fmt["bit_rate"]) if str(fmt.get("bit_rate", "")).isdigit() else None,
        "has_video": bool(videos),
        "has_audio": bool(audios),
        "codecs": [s.get("codec_name", "unknown") for s in streams],
        "streams": [_stream(s) for s in streams],
        "audio_codec": audios[0].get("codec_name") if audios else None,
        "audio_channels": _int(audios[0].get("channels")) if audios else None,
        "tags": _tags(fmt),
    }

    if videos:
        video = videos[0]
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        sar = _ratio(video.get("sample_aspect_ratio")) or 1.0
        dar = width * sar / height if width and height else _ratio(video.get("display_aspect_ratio"))
        rotation = _rotation(video)
        turn, mirrored = _display(video)
        if rotation in (90, 270):
            width, height = height, width
            dar = 1.0 / dar if dar else None
        info.update(
            width=width or None,
            height=height or None,
            rotation=rotation,
            display_turn=turn,
            display_mirrored=mirrored,
            dolby_vision=any(
                str(side.get("side_data_type", "")).startswith("DOVI configuration")
                for side in video.get("side_data_list", [])
            ),
            display_aspect_ratio=round(dar, 6) if dar else None,
            fps=_ratio(video.get("avg_frame_rate")) or _ratio(video.get("r_frame_rate")),
            video_codec=video.get("codec_name"),
            pixel_format=video.get("pix_fmt"),
            color_space=_known(video.get("color_space")),
            color_transfer=_known(video.get("color_transfer")),
            color_primaries=_known(video.get("color_primaries")),
        )
    return MediaInfo(**info)


def anamorphic(source: MediaInfo | None) -> bool:
    """True when `source`'s pixels aren't square: its display shape differs from its stored one (a SAR other than 1)."""
    if source is None or not source.width or not source.height or not source.display_aspect_ratio:
        return False
    return abs(source.display_aspect_ratio - source.width / source.height) > 1e-3 * source.display_aspect_ratio


def _known(value: Any) -> str | None:
    return value if isinstance(value, str) and value and value not in ("unknown", "reserved", "unspecified") else None


DOLBY_VISION_COLOURS = "This is Dolby Vision video whose colours need Dolby's own processing, which sashimi can't do."
PRORES_RAW = (
    "This is ProRes RAW video, which has to be developed like a camera's raw photos; FFmpeg reads it without its "
    "colours, so sashimi can't make pictures from it."
)
LOSSLESS_STILL_WORK = "Lossless copies (remux, a cut that isn't accurate, removing metadata) still work."


def check_decodable(info: MediaInfo) -> None:
    """
    Refuses, with UnsupportedMediaError, video whose pictures FFmpeg can't decode faithfully: Dolby
    Vision whose base layer is in Dolby's IPT colour space (``ipt-c2``: profiles 5 and 20), which
    only Dolby's reshaping turns into viewable colours, and ProRes RAW, which FFmpeg reads as
    undeveloped sensor data. Routes that copy streams without decoding still work on them.
    """
    if info.color_space == "ipt-c2":
        raise UnsupportedMediaError(f"{DOLBY_VISION_COLOURS} {LOSSLESS_STILL_WORK}", detail="color_space=ipt-c2")
    if info.video_codec == "prores_raw":
        raise UnsupportedMediaError(f"{PRORES_RAW} {LOSSLESS_STILL_WORK}", detail="codec=prores_raw")


def probe(input_path: str | os.PathLike[str], *, limits: Limits | None = None) -> MediaInfo:
    """
    Inspects a file's content with ffprobe. The file name and extension are ignored.

    A video or audio file that doesn't state its length, such as a live-stream recording that was
    never finalized, is read to its end to measure it from its packets' timestamps. That costs
    CPU time in proportion to its packets (about 20 microseconds each; a 30 fps video with AAC
    audio has about 280,000 an hour), so ``limits.max_scan_packets`` caps it. The measurement is
    remembered for the file while its size and modification time stay the same, so probing it
    again in the same process doesn't read it again.

    Raises UnsupportedMediaError if ffprobe can't read the file as media, MediaTooLargeError
    (``limit="max_scan_packets"``) if measuring it would read more packets than the limit, and
    BinaryNotFoundError if ffprobe itself is missing or can't be run.
    """
    cmd = [
        ffprobe_binary(),
        "-hide_banner",
        "-loglevel",
        "error",
        *INPUT_PROTOCOLS,
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        file_arg(input_path),
    ]
    try:
        output = run_ffmpeg(cmd, capture_stdout=True)
        data = json.loads(output or b"{}")
    except BinaryNotFoundError:
        raise
    except ProcessingError as e:
        raise UnsupportedMediaError("This file doesn't look like media we can read.", detail=str(e)) from e
    except ValueError as e:
        raise ProcessingError(f"ffprobe returned invalid JSON for {os.fspath(input_path)}: {e}") from e
    if not data.get("format"):
        raise UnsupportedMediaError("This file doesn't look like media we can read.")
    info = parse_ffprobe_json(data)
    if "gif" in info.container_names and info.duration and short_gif_frames(input_path):
        from sashimi.core._animation import gif_shown_seconds

        info = info.model_copy(update={"duration": gif_shown_seconds(input_path)})
    if info.duration is None and _needs_packet_scan(info):
        cap = resolve_limits(limits).max_scan_packets
        info = info.model_copy(update={"duration": _measured_duration(input_path, data, cap)})
    elif info.duration and info.start_time > 0 and info.container_names & _SEGMENT_TIMESTAMPS:
        cap = resolve_limits(limits).max_scan_packets
        if (span := _late_span(input_path, data, cap)) is not None:
            info = info.model_copy(update={"duration": span})
    return info


def short_gif_frames(input_path: str | os.PathLike[str]) -> bool:
    """
    True for a GIF with frames stored as shorter than 20 ms, which browsers play for 100 ms: its
    length (``MediaInfo.duration``) and every video made from it follow the browsers' timing.
    """
    from sashimi.core._animation import gif_has_short_frames

    try:
        return gif_has_short_frames(input_path)
    except OSError:
        return False


def _needs_packet_scan(info: MediaInfo) -> bool:
    """
    Whether a file without a stated duration is a video or audio container whose length must be
    read from its packets (an unfinalized recording). Still images and GIFs never are.
    """
    names = info.container_names - _NO_SCAN
    return bool((info.has_video and names & VIDEO_CONTAINERS) or (info.has_audio and names & AUDIO_CONTAINERS))


def _packet_end(line: bytes, stream_indexes: Container[int]) -> float | None:
    """
    When a packet of `stream_indexes` ends (``pts_time``, else ``dts_time``, plus ``duration_time``),
    from one line of ``ffprobe -show_entries packet=stream_index,pts_time,dts_time,duration_time
    -of csv=p=0``. None for other streams and packets without a time. Pure function.
    """
    fields = line.decode("ascii", "replace").strip().split(",")
    if len(fields) < 4:
        return None
    try:
        index = int(fields[0])
    except ValueError:
        return None
    if index not in stream_indexes:
        return None
    time = _signed_float(fields[1])
    if time is None:
        time = _signed_float(fields[2])
    if time is None:
        return None
    return time + (_float(fields[3]) or 0.0)


def _lines(chunks: Iterable[bytes]) -> Iterator[bytes]:
    rest = b""
    for chunk in chunks:
        *complete, rest = (rest + chunk).split(b"\n")
        yield from complete
    if rest:
        yield rest


class _Scan:
    """One packet scan's result: the end it found, how many packets it read, and whether it reached the end."""

    __slots__ = ("end", "packets", "complete")

    def __init__(self, end: float | None, packets: int, complete: bool):
        self.end, self.packets, self.complete = end, packets, complete


_scans: collections.OrderedDict[tuple[int, int, int, int], _Scan] = collections.OrderedDict()
_scans_lock = threading.Lock()


def _file_key(input_path: str | os.PathLike[str]) -> tuple[int, int, int, int] | None:
    try:
        st = os.stat(input_path)
    except OSError:
        return None
    if not st.st_ino:
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _too_many_packets(cap: int) -> MediaTooLargeError:
    return MediaTooLargeError(
        "This recording doesn't state its length, so it would have to be read through to measure it, "
        f"and it has more than {cap:,} audio and video packets (about {rough_length_text(cap / _PACKETS_PER_SECOND)} "
        f"of 30 fps video with sound); the limit is {cap:,} packets.",
        detail=f"more than {cap} packets",
        limit="max_scan_packets",
    )


_ends: collections.OrderedDict[tuple[int, int, int, int], _Scan] = collections.OrderedDict()


def _late_span(input_path: str | os.PathLike[str], data: dict[str, Any], cap: int | None) -> float | None:
    """
    How long a Matroska or WebM file whose timestamps start late really is, measured from its
    last packets, or None when that can't be told within `cap` packets.

    Muxers disagree about the stated length of such a file: libavformat writes the time its last
    frame ends, mkvmerge the span from the first frame, and a remux can keep the old muxer's
    ``ENCODER`` tag. So the end is read from the file: ffprobe seeks to its last keyframe (from the
    Cues) and reads the packets after it; without Cues, or with keyframes far apart, it reads more,
    up to `cap` packets. The stated length is kept when the end isn't reached, so it is never
    shortened by guesswork (a length that is too short cuts every re-encode short).
    """
    key = _file_key(input_path)
    known = None
    if key is not None:
        with _scans_lock:
            known = _ends.get(key)
    if known is None or not (known.complete or (cap is not None and known.packets > cap)):
        known = _scan_packets(input_path, data, cap, start=_PAST_THE_END)
        if key is not None and key == _file_key(input_path):
            with _scans_lock:
                _ends[key] = known
                while len(_ends) > _SCAN_CACHE_SIZE:
                    _ends.popitem(last=False)
    if not known.complete or known.end is None or (cap is not None and known.packets > cap):
        return None
    span = round(known.end - _start_time(data), 6)
    return span if span > 0 else None


def _measured_duration(input_path: str | os.PathLike[str], data: dict[str, Any], cap: int | None) -> float | None:
    key = _file_key(input_path)
    known = None
    if key is not None:
        with _scans_lock:
            known = _scans.get(key)
            if known is not None:
                _scans.move_to_end(key)
    if known is not None and (known.complete or (cap is not None and known.packets > cap)):
        if cap is not None and known.packets > cap:
            raise _too_many_packets(cap)
        scan = known
    else:
        scan = _scan_packets(input_path, data, cap)
        if key is not None and key == _file_key(input_path):
            with _scans_lock:
                _scans[key] = scan
                _scans.move_to_end(key)
                while len(_scans) > _SCAN_CACHE_SIZE:
                    _scans.popitem(last=False)
        if not scan.complete:
            assert cap is not None
            raise _too_many_packets(cap)
    if scan.end is None:
        return None
    duration = round(scan.end - _start_time(data), 6)
    return duration if duration > 0 else None


def _scan_packets(
    input_path: str | os.PathLike[str], data: dict[str, Any], cap: int | None, *, start: str | None = None
) -> _Scan:
    indexes = {
        s["index"]
        for s in data.get("streams", [])
        if isinstance(s.get("index"), int)
        and (
            s.get("codec_type") == "audio"
            or s.get("codec_type") == "video"
            and not s.get("disposition", {}).get("attached_pic")
        )
    }
    cmd = [ffprobe_binary(), "-hide_banner", "-loglevel", "error", *INPUT_PROTOCOLS]
    if start is not None:
        cmd += ["-read_intervals", f"{start}%"]
    cmd += ["-show_entries", "packet=stream_index,pts_time,dts_time,duration_time", "-of", "csv=p=0"]
    cmd.append(file_arg(input_path))
    end: float | None = None
    packets = 0
    chunks = stream_ffmpeg(cmd, _SCAN_CHUNK)
    try:
        for line in _lines(chunks):
            if not line.strip():
                continue
            packets += 1
            if cap is not None and packets > cap:
                return _Scan(end, packets, False)
            time = _packet_end(line, indexes)
            if time is not None and (end is None or time > end):
                end = time
    except BinaryNotFoundError:
        raise
    except ProcessingError:
        pass
    finally:
        chunks.close()
    return _Scan(end, packets, True)
