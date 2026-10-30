# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Audio extraction (from video) and audio conversion."""

import logging
from typing import Literal

from sashimi.config import (
    DURATION_TOLERANCE_SECONDS,
    AudioConvertConfig,
    FlacAudioConfig,
    Limits,
    OggAudioConfig,
    WavAudioConfig,
)
from sashimi.config._limits import resolve_limits, too_long_text
from sashimi.core.fidelity import Outcome, media_fidelity
from sashimi.core._atomic import PathLike, atomic_output
from sashimi.core._ffmpeg import (
    INPUT_PROTOCOLS,
    OUTPUT_FLAGS,
    ProgressCallback,
    base_args,
    ffmpeg_binary,
    file_arg,
    fmt_seconds,
    holds_packets,
    run_ffmpeg,
)
from sashimi.core._mediameta import (
    first_stream,
    merged_movflags,
    metadata_args,
    opus_kbps,
    opus_layout_filter,
    quicktime_tag_args,
)
from sashimi.core._resources import resolve_max_workers
from sashimi.core.probe import AUDIO_CONTAINERS, VIDEO_CONTAINERS, MediaInfo, probe
from sashimi.errors import (
    BinaryNotFoundError,
    InvalidOptionsError,
    MediaTooLongError,
    NoAudioTrackError,
    ProcessingError,
    UnsupportedMediaError,
)

__all__ = ["build_convert_command", "convert", "output_channels"]

logger = logging.getLogger("sashimi")

NOT_AUDIO = "This file doesn't look like audio we can read."
NO_AUDIO_TRACK = "This video has no audio track."

_CODECS = {"mp3": "libmp3lame", "m4a": "aac", "ogg": "libopus", "flac": "flac"}
_MUXER_ARGS: dict[str, list[str]] = {
    "mp3": ["-f", "mp3"],
    "m4a": ["-movflags", "+faststart", "-f", "ipod"],
    "ogg": ["-f", "ogg"],
    "wav": ["-f", "wav"],
    "flac": ["-f", "flac"],
}


def _length(config: AudioConvertConfig, duration: float, kind: str, limit: float | None) -> float:
    """The ``-t`` value: the requested length, or the rest of the source plus the tolerance, within `limit`."""
    if config.start_seconds >= duration:
        raise InvalidOptionsError(
            f"The start time ({config.start_seconds:g}s) is at or past the end of the {kind} ({fmt_seconds(duration)}s)."
        )
    if config.duration_seconds is not None:
        return config.duration_seconds
    if limit is not None and duration - config.start_seconds > limit:
        raise MediaTooLongError(
            too_long_text(kind, duration - config.start_seconds, config.start_seconds, limit),
            limit="max_audio_seconds",
        )
    rest = duration - config.start_seconds + DURATION_TOLERANCE_SECONDS
    return rest if limit is None else min(rest, limit)


def _samples_after(input_path: PathLike, start: float, ffmpeg: str) -> bool:
    """Whether the first audio track has any sound at or after `start`; True when that can't be told."""
    cmd = [ffmpeg, *base_args(1), *INPUT_PROTOCOLS, "-ss", fmt_seconds(start), "-i", file_arg(input_path)]
    cmd += ["-map", "0:a:0", "-vn", "-sn", "-dn", "-frames:a", "1", "-f", "s16le", "pipe:1"]
    try:
        return bool(run_ffmpeg(cmd, capture_stdout=True))
    except BinaryNotFoundError:
        raise
    except ProcessingError:
        return True


def _require_audio(output: PathLike, input_path: PathLike, start: float, ffmpeg: str) -> None:
    """
    Raises unless `output` holds audio: InvalidOptionsError when the input's audio ends before
    `start`, else ProcessingError.
    """
    if holds_packets(output, "audio"):
        return
    if not _samples_after(input_path, start, ffmpeg):
        raise InvalidOptionsError(
            f"The start time ({start:g}s) is after the end of the audio, so there's nothing to convert.",
            detail="no audio after the start time",
        )
    raise ProcessingError(f"ffmpeg wrote no audio from {start:g}s")


def build_convert_command(
    input_path: PathLike,
    output_path: PathLike,
    config: AudioConvertConfig,
    *,
    duration: float,
    threads: int = 1,
    ffmpeg: str = "ffmpeg",
    source_kind: Literal["audio", "video"] = "audio",
    limits: Limits | None = None,
    source: MediaInfo | None = None,
) -> list[str]:
    """
    Builds the ffmpeg command for :func:`convert`. Pure. Only the first audio track is used.
    Without ``duration_seconds``, ``-t`` is the rest of the probed source (plus
    :data:`~sashimi.config.DURATION_TOLERANCE_SECONDS`, and at most ``limits.max_audio_seconds``),
    so a file whose header understates its length can't be encoded past it. `source` is the
    probed input: its tags carry out ``metadata``, and its channels keep Opus within
    :data:`~sashimi.config.OPUS_MAX_KBPS_PER_CHANNEL` per channel.
    """
    limits = resolve_limits(limits)
    limits.check(config)
    t_value = _length(config, duration, source_kind, limits.max_audio_seconds)
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
    elif isinstance(config, OggAudioConfig) and (layout := opus_layout_filter(output_channels(config, source))):
        cmd += ["-af", layout]
    if config.sample_rate is not None:
        cmd += ["-ar", str(config.sample_rate)]
    if isinstance(config, WavAudioConfig):
        cmd += ["-c:a", f"pcm_s{config.wav_bit_depth}le"]
    else:
        cmd += ["-c:a", _CODECS[config.target]]
    if isinstance(config, FlacAudioConfig):
        cmd += ["-compression_level", str(config.flac_compression_level)]
    elif not isinstance(config, WavAudioConfig):
        kbps = config.bitrate_kbps
        if isinstance(config, OggAudioConfig):
            kbps = opus_kbps(kbps, output_channels(config, source))
        cmd += ["-b:a", f"{kbps}k"]
    cmd += metadata_args(config.metadata, source, [first_stream(source, "audio")])
    muxer = _MUXER_ARGS[config.target][-1]
    cmd += quicktime_tag_args(config.metadata, source, muxer)
    cmd += ["-threads", str(threads), *OUTPUT_FLAGS, *_MUXER_ARGS[config.target], file_arg(output_path)]
    return merged_movflags(cmd)


def output_channels(config: AudioConvertConfig, source: MediaInfo | None) -> int | None:
    """The output's audio channels: 1 with ``mono``, else the source's first audio track's (None if unknown)."""
    if config.mono:
        return 1
    return source.audio_channels if source is not None else None


def _probe_audio_source(
    input_path: PathLike, limits: Limits | None = None
) -> tuple[MediaInfo, Literal["audio", "video"]]:
    try:
        info = probe(input_path, limits=limits)
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
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> Outcome:
    """
    Extracts the first audio track of a video, or converts an audio file, to `config.target`
    (see :data:`~sashimi.config.AUDIO_CONVERT`), and returns what the output kept (``fidelity``).

    The source's tags are kept, as far as the format holds them, unless ``metadata`` says to
    remove location tags or all of them. An OGG (Opus) bitrate above what Opus allows for the
    output's channels (:data:`~sashimi.config.OPUS_MAX_KBPS_PER_CHANNEL` each, so 256 kb/s for
    mono) is lowered to it.

    Raises NoAudioTrackError for a video without audio. A clip longer than
    ``limits.max_audio_seconds`` raises InvalidOptionsError before the file is read, and a whole
    file longer than it MediaTooLongError. A start time after the end of the audio (inside the
    probed length, for example where a video's picture runs on past its sound) raises
    InvalidOptionsError, and nothing is written. One ffmpeg process runs, using at most
    `max_workers` threads per stage (audio encoders are mostly single-threaded).
    """
    limits = resolve_limits(limits)
    limits.check(config)
    threads = resolve_max_workers(max_workers)
    info, kind = _probe_audio_source(input_path, limits)
    assert info.duration is not None
    _length(config, info.duration, kind, limits.max_audio_seconds)
    remaining = info.duration - config.start_seconds
    expected = min(config.duration_seconds or remaining, remaining)
    if isinstance(config, OggAudioConfig):
        channels = output_channels(config, info)
        capped = opus_kbps(config.bitrate_kbps, channels)
        if capped < config.bitrate_kbps:
            logger.info(f"Opus allows at most {capped} kb/s for {channels} channel(s); using {capped} kb/s.")
    ffmpeg = ffmpeg_binary()
    with atomic_output(output_path, scratch_dir) as temp:
        cmd = build_convert_command(
            input_path,
            temp,
            config,
            duration=info.duration,
            threads=threads,
            ffmpeg=ffmpeg,
            source_kind=kind,
            limits=limits,
            source=info,
        )
        run_ffmpeg(cmd, progress_total=expected, on_progress=on_progress, stage="encoding")
        if config.start_seconds > 0:
            _require_audio(temp, input_path, config.start_seconds, ffmpeg)
    told = media_fidelity(
        input_path,
        output_path,
        "sound",
        changed=sound_changes(config, info, sample_format(input_path), sample_format(output_path)[1]),
        removed_metadata=config.metadata,
        kept=["display rotation", "Dolby Vision settings"],
    )
    if on_progress:
        on_progress(1.0, "done")
    return Outcome(fidelity=told)


_LOSSY = {"mp3": "MP3", "m4a": "AAC", "ogg": "Opus"}
_FLOAT_FORMATS = frozenset({"flt", "fltp", "dbl", "dblp"})


def sample_format(input_path: PathLike) -> tuple[str | None, int | None, int | None]:
    """The first audio track's sample format, bits per sample and sample rate, as ffprobe gives them (None when unknown)."""
    from sashimi.core.metadata import _probe_full

    streams = [s for s in _probe_full(input_path).get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        return None, None, None
    stream = streams[0]
    fmt = stream.get("sample_fmt")
    bits = int(stream.get("bits_per_raw_sample") or 0) or int(stream.get("bits_per_sample") or 0) or None
    if bits is None and isinstance(fmt, str):
        bits = {"u8": 8, "u8p": 8, "s16": 16, "s16p": 16, "s32": 32, "s32p": 32, "s64": 64}.get(fmt)
    rate = int(stream.get("sample_rate") or 0) or None
    return fmt if isinstance(fmt, str) else None, bits, rate


def sound_changes(
    config: AudioConvertConfig,
    source: MediaInfo,
    fmt: tuple[str | None, int | None, int | None],
    written_bits: int | None = None,
) -> list[str]:
    """
    How converting `source` with `config` changes its sound, for people: a lossy encoder, mixing
    down to mono, resampling, and samples stored with fewer bits (or as integers, from floating
    point). `written_bits` is what the output stores, as read from it (FLAC's encoder picks 16 or
    24 bits from the source). Pure.
    """
    sample_fmt, bits, rate = fmt
    changes = []
    if config.target in _LOSSY:
        kbps = getattr(config, "bitrate_kbps", None)
        if isinstance(config, OggAudioConfig) and kbps is not None:
            kbps = opus_kbps(kbps, output_channels(config, source))
        changes.append(f"re-encoded as {_LOSSY[config.target]} at {kbps} kb/s")
    if config.mono and (source.audio_channels or 1) > 1:
        changes.append("mixed down to mono")
    if config.sample_rate is not None and config.sample_rate != rate:
        changes.append(f"resampled to {config.sample_rate} Hz")
    if config.target in ("wav", "flac"):
        guess = config.wav_bit_depth if isinstance(config, WavAudioConfig) else (24 if (bits or 16) > 16 else 16)
        depth = written_bits or guess
        if sample_fmt in _FLOAT_FORMATS:
            changes.append(f"floating-point samples rounded to {depth} bits")
        elif bits is not None and bits > depth:
            changes.append(f"reduced to {depth} bits")
    return changes
