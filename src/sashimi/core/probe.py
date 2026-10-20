# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Content-based media probing with ffprobe."""

import json
import os
from typing import Any

from pydantic import BaseModel, ConfigDict

from sashimi.core._ffmpeg import (
    AUDIO_CONTAINERS,
    INPUT_PROTOCOLS,
    VIDEO_CONTAINERS,
    ffprobe_binary,
    file_arg,
    run_ffmpeg,
)
from sashimi.errors import BinaryNotFoundError, ProcessingError, UnsupportedMediaError

__all__ = ["MediaInfo", "StreamInfo", "probe", "VIDEO_CONTAINERS", "AUDIO_CONTAINERS"]


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
    """Seconds, or None when the container doesn't say (e.g. a still image)."""
    size_bytes: int | None = None
    bit_rate: int | None = None
    has_video: bool = False
    """True if there is a real video stream; embedded cover art doesn't count."""
    has_audio: bool = False
    width: int | None = None
    height: int | None = None
    rotation: int = 0
    """Display rotation in degrees (0, 90, 180 or 270) that players apply."""
    display_aspect_ratio: float | None = None
    fps: float | None = None
    video_codec: str | None = None
    pixel_format: str | None = None
    """The video's pixel format as ffprobe names it, e.g. ``yuv420p`` (8-bit 4:2:0) or ``yuv420p10le``."""
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


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _tags(entry: dict[str, Any]) -> dict[str, str]:
    tags = entry.get("tags")
    return {str(k): str(v) for k, v in tags.items()} if isinstance(tags, dict) else {}


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
    )


def parse_ffprobe_json(data: dict[str, Any]) -> MediaInfo:
    """Builds a MediaInfo from ``ffprobe -show_format -show_streams -of json`` output. Pure function."""
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
    audios = [s for s in streams if s.get("codec_type") == "audio"]

    duration = _float(fmt.get("duration"))
    if duration is None:
        durations = [d for d in (_float(s.get("duration")) for s in streams) if d]
        duration = max(durations) if durations else None

    info: dict[str, Any] = {
        "container": fmt.get("format_name", ""),
        "duration": duration,
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
        if rotation in (90, 270):
            width, height = height, width
            dar = 1.0 / dar if dar else None
        info.update(
            width=width or None,
            height=height or None,
            rotation=rotation,
            display_aspect_ratio=round(dar, 6) if dar else None,
            fps=_ratio(video.get("avg_frame_rate")) or _ratio(video.get("r_frame_rate")),
            video_codec=video.get("codec_name"),
            pixel_format=video.get("pix_fmt"),
        )
    return MediaInfo(**info)


def probe(input_path: str | os.PathLike[str]) -> MediaInfo:
    """
    Inspects a file's content with ffprobe. The file name and extension are ignored.

    Raises UnsupportedMediaError if ffprobe can't read the file as media, and
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
    return parse_ffprobe_json(data)
