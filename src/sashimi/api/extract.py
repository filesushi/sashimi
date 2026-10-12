import pathlib
import re
from typing import Literal

from sashimi.config import KEYFRAME_NOTE, VIDEO_TRIM
from sashimi.core.probe import probe
from sashimi.core.video import trim
from sashimi.errors import SashimiError, UserInputError

__all__ = ["extract", "parse_time_to_seconds"]

Container = Literal["mkv", "mp4", "webm", "mov"]

_LONGER_THAN_ASKED = 0.5
_ACCURATE_HINT = "Pass --accurate to re-encode for an exact cut."

_CONTAINER_FOR_SUFFIX: dict[str, Container] = {
    ".mkv": "mkv",
    ".mp4": "mp4",
    ".m4v": "mp4",
    ".webm": "webm",
    ".mov": "mov",
}
_CONTAINER_FOR_DEMUXER: dict[str, Container] = {"matroska": "mkv", "webm": "webm", "mov": "mov", "mp4": "mp4"}


def parse_time_to_seconds(time_str: str) -> float:
    """
    Parses flexible human-entered time strings into seconds.

    - Colon notation: '00:24:09', '24:09', '1:22:45.50'
    - Unit suffixes: '42s', '1h22m', '24 min 09 s', '1.5h'
    - Plain numbers: '1250' (seconds)
    """
    clean_str = time_str.strip().lower()
    if not clean_str:
        raise ValueError("Time offset string cannot be empty.")

    if ":" in clean_str:
        parts = clean_str.split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        raise ValueError(f"Unrecognized colon time scheme: '{time_str}'")

    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(hours|hour|h|minutes|minute|min|m|seconds|second|s)", clean_str)
    if matches:
        total_seconds = 0.0
        for value, unit in matches:
            val = float(value)
            if unit in ("h", "hour", "hours"):
                total_seconds += val * 3600
            elif unit in ("m", "min", "minute", "minutes"):
                total_seconds += val * 60
            else:
                total_seconds += val
        return total_seconds

    try:
        return float(clean_str)
    except ValueError as e:
        raise ValueError(f"Could not interpret format of time string: '{time_str}'") from e


def _container_for(input_path: pathlib.Path) -> Container:
    if container := _CONTAINER_FOR_SUFFIX.get(input_path.suffix.lower()):
        return container
    names = probe(input_path).container_names
    for demuxer, container in _CONTAINER_FOR_DEMUXER.items():
        if demuxer in names:
            return container
    return "mkv"


def extract(
    input_path: str | pathlib.Path,
    start: str | float,
    end: str | float,
    output_path: str | pathlib.Path | None = None,
    force: bool = False,
    *,
    accurate: bool = False,
    crf: int | None = None,
    preset: str | None = None,
    speed: int | None = None,
    audio_bitrate_kbps: int | None = None,
    mute: bool = False,
    max_workers: int | None = None,
) -> tuple[str, str, pathlib.Path]:
    """
    Cuts ``start``..``end`` out of a video. By default streams are copied: instant and lossless,
    but the clip starts at the keyframe before ``start``. With ``accurate`` the clip is
    re-encoded and starts exactly on time; the encoder options apply only then (see
    :data:`~sashimi.config.VIDEO_TRIM`; ``preset`` is for H.264 and ``speed`` for WebM). Times are seconds or strings such as ``'1h22m'`` or ``'00:24:09'``. The output
    defaults to ``<stem>_extracted<suffix>`` next to the input. Returns
    ``(status, message, output_path)``; the message gives the clip's real length, and explains
    keyframes when a copied clip came out longer than asked.
    """
    source = pathlib.Path(input_path)
    try:
        start_secs = start if isinstance(start, (int, float)) else parse_time_to_seconds(start)
        end_secs = end if isinstance(end, (int, float)) else parse_time_to_seconds(end)
    except ValueError as err:
        return ("error", f"Time Parsing Failure: {err}", source)
    if start_secs >= end_secs:
        return (
            "error",
            f"Invalid Bounds: Start time ({start_secs}s) cannot meet or exceed End time ({end_secs}s).",
            source,
        )

    target = pathlib.Path(output_path) if output_path else source.with_name(f"{source.stem}_extracted{source.suffix}")
    if target.exists() and not force:
        return ("exists", f"File already exists: {target.name}. Pass -f to overwrite.", target)

    try:
        container = _CONTAINER_FOR_SUFFIX.get(target.suffix.lower()) or _container_for(source)
        encoding = {"crf": crf, "preset": preset, "speed": speed, "audio_bitrate_kbps": audio_bitrate_kbps}
        config = VIDEO_TRIM.from_params(
            encoding,
            container=container,
            start_seconds=start_secs,
            end_seconds=end_secs,
            accurate=accurate,
            mute=mute or None,
        )
        trim(source, target, config, max_workers=max_workers)
    except UserInputError as e:
        return ("error", e.user_message, target)
    except SashimiError as e:
        return ("error", f"FFmpeg execution failed: {str(e).splitlines()[0]}", target)
    requested = end_secs - start_secs
    try:
        actual = probe(target).duration or requested
    except SashimiError:
        actual = requested
    message = f"Extracted {actual:.1f}s segment ➔ {target.name}"
    if actual > requested + _LONGER_THAN_ASKED:
        message += f" ({requested:.1f}s requested). {KEYFRAME_NOTE} {_ACCURATE_HINT}"
    return ("success", message, target)
