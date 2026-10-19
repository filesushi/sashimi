# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Audio extraction (from video) and audio conversion."""

from typing import Literal

from sashimi.config import (
    DURATION_TOLERANCE_SECONDS,
    MAX_AUDIO_SECONDS,
    AudioConvertConfig,
    FlacAudioConfig,
    WavAudioConfig,
)
from sashimi.core._atomic import PathLike, atomic_output
from sashimi.core._ffmpeg import (
    INPUT_PROTOCOLS,
    OUTPUT_FLAGS,
    ProgressCallback,
    base_args,
    ffmpeg_binary,
    file_arg,
    fmt_seconds,
    run_ffmpeg,
)
from sashimi.core._resources import resolve_max_workers
from sashimi.core.probe import AUDIO_CONTAINERS, VIDEO_CONTAINERS, MediaInfo, probe
from sashimi.errors import InvalidOptionsError, MediaTooLongError, NoAudioTrackError, UnsupportedMediaError

__all__ = ["build_convert_command", "convert"]

NOT_AUDIO = "This file doesn't look like audio we can read."
NO_AUDIO_TRACK = "This video has no audio track."
TOO_LONG_AUDIO = (
    f"Audio longer than {MAX_AUDIO_SECONDS // 60} minutes isn't supported yet. "
    "Set a shorter duration to convert part of it."
)

_CODECS = {"mp3": "libmp3lame", "m4a": "aac", "ogg": "libopus", "flac": "flac"}
_MUXER_ARGS: dict[str, list[str]] = {
    "mp3": ["-f", "mp3"],
    "m4a": ["-movflags", "+faststart", "-f", "ipod"],
    "ogg": ["-f", "ogg"],
    "wav": ["-f", "wav"],
    "flac": ["-f", "flac"],
}


def _length(config: AudioConvertConfig, duration: float, kind: str) -> float:
    """The ``-t`` value: the requested length, or the rest of the source plus the tolerance, within the limit."""
    if config.start_seconds >= duration:
        raise InvalidOptionsError(
            f"The start time ({config.start_seconds:g}s) is past the end of the {kind} ({duration:.0f}s)."
        )
    if config.duration_seconds is not None:
        return config.duration_seconds
    if duration - config.start_seconds > MAX_AUDIO_SECONDS:
        raise MediaTooLongError(TOO_LONG_AUDIO)
    return min(duration - config.start_seconds + DURATION_TOLERANCE_SECONDS, MAX_AUDIO_SECONDS)


def build_convert_command(
    input_path: PathLike,
    output_path: PathLike,
    config: AudioConvertConfig,
    *,
    duration: float,
    threads: int = 1,
    ffmpeg: str = "ffmpeg",
    source_kind: Literal["audio", "video"] = "audio",
) -> list[str]:
    """
    Builds the ffmpeg command for :func:`convert`. Pure. Only the first audio track is used.
    Without ``duration_seconds``, ``-t`` is the rest of the probed source (plus
    :data:`~sashimi.config.DURATION_TOLERANCE_SECONDS`, at most
    :data:`~sashimi.config.MAX_AUDIO_SECONDS`), so a file whose header understates its length
    can't be encoded past it.
    """
    t_value = _length(config, duration, source_kind)
    cmd = [ffmpeg, *base_args(threads), "-progress", "pipe:1", "-nostats", *INPUT_PROTOCOLS]
    if config.start_seconds > 0:
        cmd += ["-ss", fmt_seconds(config.start_seconds)]
    cmd += [
        "-t",
        fmt_seconds(t_value),
        "-i",
        file_arg(input_path),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
    ]
    if config.mono:
        cmd += ["-ac", "1"]
    if config.sample_rate is not None:
        cmd += ["-ar", str(config.sample_rate)]
    if isinstance(config, WavAudioConfig):
        cmd += ["-c:a", f"pcm_s{config.wav_bit_depth}le"]
    else:
        cmd += ["-c:a", _CODECS[config.target]]
    if isinstance(config, FlacAudioConfig):
        cmd += ["-compression_level", str(config.flac_compression_level)]
    elif not isinstance(config, WavAudioConfig):
        cmd += ["-b:a", f"{config.bitrate_kbps}k"]
    cmd += ["-threads", str(threads), *OUTPUT_FLAGS, *_MUXER_ARGS[config.target], file_arg(output_path)]
    return cmd


def _probe_audio_source(input_path: PathLike) -> tuple[MediaInfo, Literal["audio", "video"]]:
    try:
        info = probe(input_path)
    except UnsupportedMediaError as e:
        raise UnsupportedMediaError(NOT_AUDIO, detail=e.detail) from e
    names = info.container_names
    if info.has_video and names & VIDEO_CONTAINERS and info.duration:
        if not info.has_audio:
            raise NoAudioTrackError(NO_AUDIO_TRACK)
        return info, "video"
    if not info.has_audio or not info.duration or not names & AUDIO_CONTAINERS:
        raise UnsupportedMediaError(NOT_AUDIO, detail=f"container={info.container} duration={info.duration}")
    return info, "audio"


def convert(
    input_path: PathLike,
    output_path: PathLike,
    config: AudioConvertConfig,
    *,
    max_workers: int | None = None,
    scratch_dir: PathLike | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    Extracts the first audio track of a video, or converts an audio file, to `config.target`
    (see :data:`~sashimi.config.AUDIO_CONVERT`).

    Raises NoAudioTrackError for a video without audio. One ffmpeg process runs, using at most
    `max_workers` threads per stage (audio encoders are mostly single-threaded).
    """
    threads = resolve_max_workers(max_workers)
    info, kind = _probe_audio_source(input_path)
    assert info.duration is not None
    _length(config, info.duration, kind)
    remaining = info.duration - config.start_seconds
    expected = min(config.duration_seconds or remaining, remaining)
    with atomic_output(output_path, scratch_dir) as temp:
        cmd = build_convert_command(
            input_path,
            temp,
            config,
            duration=info.duration,
            threads=threads,
            ffmpeg=ffmpeg_binary(),
            source_kind=kind,
        )
        run_ffmpeg(cmd, progress_total=expected, on_progress=on_progress, stage="encoding")
    if on_progress:
        on_progress(1.0, "done")
