# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pathlib
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any, Literal

from sashimi.api._batch import VIDEO_EXTENSIONS, FileProgress, PathInput, YieldType, output_name, run_batch
from sashimi.api._consume import consume_generator
from sashimi.config import KEYFRAME_NOTE, VIDEO_TRIM, CopyTrimConfig, Limits, MetadataMode, TrimConfig
from sashimi.core.probe import probe
from sashimi.core.video import trim
from sashimi.errors import InvalidOptionsError, SashimiError

__all__ = ["extract", "extract_generator", "parse_time_to_seconds"]

Container = Literal["mkv", "mp4", "webm", "mov"]

_LONGER_THAN_ASKED = 0.5
_ACCURATE_HINT = "Use an accurate cut to start exactly on time."

_CONTAINER_FOR_SUFFIX: dict[str, Container] = {
    ".mkv": "mkv",
    ".mp4": "mp4",
    ".m4v": "mp4",
    ".webm": "webm",
    ".mov": "mov",
}
_TIME_HELP = "Use seconds (90), units (1m30s, 1h22m) or a clock time (00:01:30)."


def parse_time_to_seconds(time_str: str) -> float:
    """
    Parses flexible human-entered time strings into seconds.

    - Colon notation: '00:24:09', '24:09', '1:22:45.50'
    - Unit suffixes: '42s', '1h22m', '24 min 09 s', '1.5h'
    - Plain numbers: '1250' (seconds)

    Raises :class:`~sashimi.errors.InvalidOptionsError` for anything else.
    """
    clean_str = time_str.strip().lower()
    if not clean_str:
        raise InvalidOptionsError(f"A time is missing. {_TIME_HELP}")
    invalid = InvalidOptionsError(f"{time_str.strip()!r} isn't a time. {_TIME_HELP}")

    if ":" in clean_str:
        parts = clean_str.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
        except ValueError:
            raise invalid from None
        raise invalid

    unit = r"(\d+(?:\.\d+)?)\s*(hours|hour|h|minutes|minute|min|m|seconds|second|s)"
    if re.fullmatch(rf"(?:{unit}\s*)+", clean_str):
        total_seconds = 0.0
        for value, name in re.findall(unit, clean_str):
            scale = 3600 if name.startswith("h") else 60 if name.startswith("m") else 1
            total_seconds += float(value) * scale
        return total_seconds

    try:
        return float(clean_str)
    except ValueError:
        raise invalid from None


def _seconds(value: str | float | None) -> float | None:
    if value is None or isinstance(value, (int, float)):
        return value
    return parse_time_to_seconds(value)


def _clip_message(config: TrimConfig, source: pathlib.Path, output: pathlib.Path) -> str:
    """The success message: the clip's real length, with the keyframe note when a copy came out long."""
    requested = config.length
    if not isinstance(config, CopyTrimConfig):
        if requested is None:
            return f"Extracted from {config.start_seconds:g}s to the end: {{name}}"
        return f"Extracted {requested:.1f}s to {{name}}"
    try:
        actual = probe(output).duration
        if requested is None and config.start_seconds > 0:
            source_length = probe(source).duration
            requested = source_length - config.start_seconds if source_length else None
    except SashimiError:
        actual = None
    if actual is None:
        return "Extracted to {name}"
    message = f"Extracted {actual:.1f}s to {{name}}"
    if requested is not None and actual > requested + _LONGER_THAN_ASKED:
        message += f" ({requested:.1f}s requested). {KEYFRAME_NOTE} {_ACCURATE_HINT}"
    return message


def extract_generator(
    paths: PathInput | Sequence[PathInput],
    start: str | float | None = None,
    end: str | float | None = None,
    *,
    duration: str | float | None = None,
    container: Container | None = None,
    accurate: bool = False,
    crf: int | None = None,
    preset: str | None = None,
    speed: int | None = None,
    audio_bitrate_kbps: int | None = None,
    mute: bool = False,
    metadata: MetadataMode = "keep",
    recursive: bool = False,
    force: bool = False,
    max_workers: int | None = None,
    extensions: Iterable[str] = VIDEO_EXTENSIONS,
    on_file_progress: FileProgress | None = None,
    output: PathInput | None = None,
    output_dir: PathInput | None = None,
    dry_run: bool = False,
    limits: Limits | None = None,
) -> Iterator[YieldType]:
    """
    Cuts a section out of every video found (see :func:`sashimi.video.trim`), from `start`
    (default the beginning) to `end`, or for `duration`; with neither, to the end of the video.
    Times are seconds or strings such as ``'1h22m'`` or ``'00:24:09'``.

    By default streams are copied: instant and lossless, but the clip starts at the keyframe at
    or before `start`, and the success message explains when it came out longer than asked. With
    `accurate` the clip is re-encoded and starts exactly on time; the encoder options apply only
    then (``preset`` is for H.264 and ``speed`` for WebM; see :data:`~sashimi.config.VIDEO_TRIM`).

    `metadata` is the clip's ``metadata`` field: keep the source's tags (the default), or remove
    location tags or all of them (see :data:`~sashimi.config.VIDEO_TRIM`).

    The container is `container`, or else `output`'s extension if it names one, or else the
    input's (MKV for anything else). Each clip is written as ``<stem>.extracted.<ext>`` next to
    its video, under `output_dir`, or to `output` for a single video. Invalid times raise
    :class:`~sashimi.errors.InvalidOptionsError` before anything runs.
    """
    start_s, end_s, duration_s = _seconds(start), _seconds(end), _seconds(duration)
    CopyTrimConfig.from_params(
        container="mkv", start_seconds=start_s, end_seconds=end_s, duration_seconds=duration_s, metadata=metadata
    )
    encoding = {"crf": crf, "preset": preset, "speed": speed, "audio_bitrate_kbps": audio_bitrate_kbps}
    if not accurate and (
        used := [key for key, value in {**encoding, "mute": mute or None}.items() if value is not None]
    ):
        raise InvalidOptionsError(
            f"{', '.join(used)} only {'applies' if len(used) == 1 else 'apply'} to an accurate cut."
        )
    output_container = _CONTAINER_FOR_SUFFIX.get(pathlib.Path(output).suffix.lower()) if output is not None else None

    def container_for(source: pathlib.Path) -> Container:
        return container or output_container or _CONTAINER_FOR_SUFFIX.get(source.suffix.lower(), "mkv")

    def config_for(source: pathlib.Path) -> TrimConfig:
        return VIDEO_TRIM.from_params(
            encoding,
            container=container_for(source),
            start_seconds=start_s,
            end_seconds=end_s,
            duration_seconds=duration_s,
            accurate=accurate,
            mute=(mute or None) if accurate else None,
            metadata=metadata,
        )

    def name_for(source: pathlib.Path, stem: str) -> str:
        chosen = container_for(source)
        keep = _CONTAINER_FOR_SUFFIX.get(source.suffix.lower()) == chosen
        return output_name(stem, source.suffix if keep else chosen, "extracted")

    def process(source: pathlib.Path, target: pathlib.Path, progress: Callable[[float, str], None] | None) -> str:
        config = config_for(source)
        trim(source, target, config, max_workers=max_workers, limits=limits, on_progress=progress)
        return _clip_message(config, source, target)

    return run_batch(
        paths,
        extensions=extensions,
        recursive=recursive,
        force=force,
        name_for=name_for,
        process=process,
        success_message="Extracted to {name}",
        on_file_progress=on_file_progress,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
    )


def extract(
    paths: PathInput | Sequence[PathInput],
    start: str | float | None = None,
    end: str | float | None = None,
    *,
    show_progress: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Like :func:`extract_generator`, but runs to completion and returns a summary."""
    return consume_generator(
        extract_generator(paths, start, end, **kwargs),
        show_progress,
        "Extracting",
        "extract",
        dry_run=kwargs.get("dry_run", False),
    )
