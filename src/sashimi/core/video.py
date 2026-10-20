# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Video conversion (GIF/animated WebP/WebM/MP4), lossless or frame-accurate trimming, and rotate/flip."""

import logging
from collections.abc import Sequence
from typing import Literal

from sashimi.config import (
    DURATION_TOLERANCE_SECONDS,
    MAX_VIDEO_SECONDS,
    CopyTrimConfig,
    GifVideoConfig,
    Mp4VideoConfig,
    TransformConfig,
    TrimConfig,
    VideoConvertConfig,
    WebpVideoConfig,
)
from sashimi.config._video import _H264Options, _Vp9Options
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
from sashimi.core.probe import VIDEO_CONTAINERS, MediaInfo, probe
from sashimi.errors import (
    BinaryNotFoundError,
    InvalidOptionsError,
    MediaTooLongError,
    ProcessingError,
    UnsupportedMediaError,
)

__all__ = [
    "VideoRoute",
    "build_convert_command",
    "convert",
    "plan_mp4_route",
    "subtitle_conversions",
    "build_trim_command",
    "check_copy_compatible",
    "trim",
    "build_transform_command",
    "transform",
]

logger = logging.getLogger("sashimi")

NOT_A_VIDEO = "This file doesn't look like a video we can read."
TOO_LONG_VIDEO = (
    f"Videos longer than {MAX_VIDEO_SECONDS // 60} minutes aren't supported yet. "
    "Set a shorter duration to convert part of it."
)

_MUXERS = {"gif": "gif", "webp": "webp", "webm": "webm", "mp4": "mp4", "mkv": "matroska", "mov": "mov"}
_ANY = None
_COPY_ALLOWED: dict[str, dict[str, frozenset[str] | None]] = {
    "mkv": {"video": _ANY, "audio": _ANY, "subtitle": _ANY, "attachment": _ANY},
    "mp4": {"video": _ANY, "audio": _ANY, "subtitle": frozenset({"mov_text"})},
    "mov": {"video": _ANY, "audio": _ANY, "subtitle": frozenset({"mov_text"}), "data": _ANY},
    "webm": {
        "video": frozenset({"vp8", "vp9", "av1"}),
        "audio": frozenset({"opus", "vorbis"}),
        "subtitle": frozenset({"webvtt"}),
        "attachment": _ANY,
    },
}
_COPY_REFUSED: dict[str, frozenset[str]] = {
    "mkv": frozenset({"mov_text"}),
    "mp4": frozenset({"vp8", "prores"}),
    "mov": frozenset({"vp8", "vp9", "av1", "opus", "flac"}),
    "webm": frozenset(),
}
TEXT_SUBTITLES = frozenset({"mov_text", "subrip", "srt", "ass", "ssa", "webvtt", "text"})
"""Subtitle codecs (as ffprobe names them) that a lossless trim converts when the container can't hold them."""
SUBTITLE_ENCODERS = {"mkv": "srt", "mp4": "mov_text", "mov": "mov_text", "webm": "webvtt"}
"""The subtitle format each container gets when a text subtitle has to be converted."""


def _subtitle_fits(codec: str, container: str) -> bool:
    allowed = _COPY_ALLOWED[container].get("subtitle", frozenset())
    return codec not in _COPY_REFUSED[container] and (allowed is None or codec in allowed)


def subtitle_conversions(subtitles: Sequence[str], container: str) -> list[str]:
    """
    ``-c:s:<n> <encoder>`` arguments for the subtitle streams (codecs in stream order) that
    `container` can't hold as they are but that are text, so can be converted. Pure.
    """
    args: list[str] = []
    for index, codec in enumerate(subtitles):
        if not _subtitle_fits(codec, container) and codec in TEXT_SUBTITLES:
            args += [f"-c:s:{index}", SUBTITLE_ENCODERS[container]]
    return args


def _encoder_args(config: _H264Options | _Vp9Options, *, faststart: bool = True) -> list[str]:
    """Video and audio codec arguments for a VP9/Opus or H.264/AAC encode."""
    crf = str(config.crf)
    if isinstance(config, _Vp9Options):
        video = [
            "-c:v",
            "libvpx-vp9",
            "-crf",
            crf,
            "-b:v",
            "0",
            "-deadline",
            "realtime",
            "-cpu-used",
            str(config.speed),
        ]
        video += ["-row-mt", "1"]
        audio = ["-c:a", "libopus"]
        tail: list[str] = []
    else:
        video = ["-c:v", "libx264", "-preset", config.preset, "-crf", crf]
        audio = ["-c:a", "aac"]
        tail = ["-movflags", "+faststart"] if faststart else []
    if config.mute:
        return [*video, *tail]
    return [*video, *audio, "-b:a", f"{config.audio_bitrate_kbps}k", *tail]


def _stream_args(mute: bool) -> list[str]:
    audio = ["-an"] if mute else ["-map", "0:a:0?"]
    return ["-pix_fmt", "yuv420p", "-map", "0:v:0", *audio, "-sn", "-dn"]


def _probe_video(input_path: PathLike) -> MediaInfo:
    """Probes and checks the input is a video (or animated GIF) with a readable duration."""
    try:
        info = probe(input_path)
    except UnsupportedMediaError as e:
        raise UnsupportedMediaError(NOT_A_VIDEO, detail=e.detail) from e
    if not info.has_video or not info.duration or not (info.container_names & VIDEO_CONTAINERS):
        raise UnsupportedMediaError(NOT_A_VIDEO, detail=f"container={info.container} duration={info.duration}")
    return info


def _check_start(start: float, duration: float) -> None:
    if start >= duration:
        raise InvalidOptionsError(f"The start time ({start:g}s) is past the end of the video ({duration:.0f}s).")


def _length_limit(requested: float | None, start: float, duration: float) -> float:
    """
    The ``-t`` value for a re-encode: the requested length, or else the rest of the probed video
    plus :data:`DURATION_TOLERANCE_SECONDS`, capped at :data:`MAX_VIDEO_SECONDS`. Raises
    MediaTooLongError when the rest of the video is over the limit and no length was requested.
    """
    if requested is not None:
        return requested
    if duration - start > MAX_VIDEO_SECONDS:
        raise MediaTooLongError(TOO_LONG_VIDEO)
    return min(duration - start + DURATION_TOLERANCE_SECONDS, MAX_VIDEO_SECONDS)


def _progress_args() -> list[str]:
    return ["-progress", "pipe:1", "-nostats"]


VideoRoute = Literal["copy", "copy_video", "encode"]
"""
How :func:`convert` makes an MP4: ``copy`` copies the video and audio as they are, ``copy_video``
copies the video and re-encodes the audio, and ``encode`` re-encodes both. Every other target
is always ``encode``.
"""
COPYABLE_PIXEL_FORMATS = frozenset({"yuv420p", "yuvj420p"})
"""H.264 pixel formats that play everywhere an MP4 re-encode does (8-bit 4:2:0)."""
_MP4_DEFAULTS = Mp4VideoConfig()


def plan_mp4_route(info: MediaInfo, config: Mp4VideoConfig) -> VideoRoute:
    """
    The fastest route to the MP4 a re-encode with `config` would give, as far as anyone watching
    can tell. Pure.

    The video is copied when it is 8-bit 4:2:0 H.264 with even dimensions and nothing asks for
    a re-encode: no start time, no length shorter than the rest of the video, no ``max_width``
    below the video's width, and ``crf`` and ``preset`` at their defaults (a value other than the
    default asks for a different encode; the default is taken to mean "an MP4", which the copy
    already is). The audio is copied too when it is AAC and ``audio_bitrate_kbps`` is at its
    default, or when there is none or ``mute`` drops it; otherwise only the audio is re-encoded.
    HEVC, VP9 and everything else are re-encoded, since many browsers can't play them in MP4.
    """
    remaining = (info.duration or 0.0) - config.start_seconds
    reencode = (
        config.start_seconds > 0
        or (config.duration_seconds is not None and config.duration_seconds < remaining)
        or (config.max_width is not None and (info.width is None or config.max_width < info.width))
        or config.crf != _MP4_DEFAULTS.crf
        or config.preset != _MP4_DEFAULTS.preset
        or info.video_codec != "h264"
        or info.pixel_format not in COPYABLE_PIXEL_FORMATS
        or not info.width
        or not info.height
        or info.width % 2
        or info.height % 2
    )
    if reencode:
        return "encode"
    if config.mute or not info.has_audio:
        return "copy"
    if info.audio_codec == "aac" and config.audio_bitrate_kbps == _MP4_DEFAULTS.audio_bitrate_kbps:
        return "copy"
    return "copy_video"


def _copy_command(
    input_path: PathLike,
    output_path: PathLike,
    config: Mp4VideoConfig,
    route: VideoRoute,
    *,
    t_value: float,
    threads: int,
    ffmpeg: str,
) -> list[str]:
    copy_audio = route == "copy"
    cmd = [ffmpeg, *base_args(1 if copy_audio else threads), *_progress_args(), *INPUT_PROTOCOLS]
    cmd += ["-t", fmt_seconds(t_value), "-i", file_arg(input_path)]
    audio = ["-an"] if config.mute else ["-map", "0:a:0?"]
    cmd += ["-map", "0:v:0", *audio, "-sn", "-dn", "-c:v", "copy"]
    if not config.mute:
        cmd += ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", f"{config.audio_bitrate_kbps}k"]
    if not copy_audio:
        cmd += ["-threads", str(threads)]
    cmd += ["-avoid_negative_ts", "make_zero", "-movflags", "+faststart"]
    return [*cmd, *OUTPUT_FLAGS, "-f", "mp4", file_arg(output_path)]


def build_convert_command(
    input_path: PathLike,
    output_path: PathLike,
    config: VideoConvertConfig,
    *,
    duration: float,
    threads: int,
    ffmpeg: str = "ffmpeg",
    route: VideoRoute = "encode",
) -> list[str]:
    """
    Builds the ffmpeg command for :func:`convert`. Pure: no filesystem or process access.

    `duration` is the probed input length in seconds. Raises the same user errors as
    :func:`convert` for out-of-range options. ``-ss`` and ``-t`` are *input* options (before
    ``-i``) so ffmpeg stops reading at the end of the clip instead of decoding the rest of the file.
    Without ``duration_seconds``, ``-t`` is the rest of the probed video (plus
    :data:`~sashimi.config.DURATION_TOLERANCE_SECONDS`, at most
    :data:`~sashimi.config.MAX_VIDEO_SECONDS`), so a file whose header understates its length
    can't be encoded past it.

    `route` is the MP4 route (see :func:`plan_mp4_route`); the copy routes use ``-c:v copy``
    with the same streams, length, ``+faststart`` and length limits as the re-encode.
    """
    _check_start(config.start_seconds, duration)
    t_value = _length_limit(config.duration_seconds, config.start_seconds, duration)
    if route != "encode":
        if not isinstance(config, Mp4VideoConfig) or config.start_seconds > 0:
            raise ValueError(f"route {route!r} is only for MP4 output from the start of the video")
        return _copy_command(input_path, output_path, config, route, t_value=t_value, threads=threads, ffmpeg=ffmpeg)

    cmd = [ffmpeg, *base_args(threads), *_progress_args(), *INPUT_PROTOCOLS, "-threads", str(threads)]
    if config.start_seconds > 0:
        cmd += ["-ss", fmt_seconds(config.start_seconds)]
    cmd += ["-t", fmt_seconds(t_value), "-i", file_arg(input_path)]

    if isinstance(config, GifVideoConfig):
        paletteuse = ["new=1"] if config.palette_mode == "single" else []
        if config.dither == "bayer":
            paletteuse += ["dither=bayer", "bayer_scale=4"]
        else:
            paletteuse.append(f"dither={config.dither}")
        palette = f"[a]palettegen=stats_mode={config.palette_mode}[p];[b][p]paletteuse={':'.join(paletteuse)}"
        cmd += [
            "-vf",
            f"fps={config.fps},scale='min({config.max_width},iw)':-2:flags=lanczos,split[a][b];{palette}",
            "-loop",
            "0" if config.loop else "-1",
            "-an",
        ]
    elif isinstance(config, WebpVideoConfig):
        cmd += [
            "-vf",
            f"fps={config.fps},scale='min({config.max_width},iw)':-2:flags=lanczos",
            "-c:v",
            "libwebp_anim",
            "-lossless",
            "1" if config.lossless else "0",
            "-quality",
            str(config.quality),
            "-loop",
            "0" if config.loop else "1",
            "-an",
        ]
    else:
        width = config.max_width
        scale = f"scale='min({width},iw)':-2:flags=lanczos" if width else "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        cmd += ["-vf", scale, *_stream_args(config.mute), *_encoder_args(config)]
    cmd += ["-threads", str(threads), *OUTPUT_FLAGS, "-f", _MUXERS[config.target], file_arg(output_path)]
    return cmd


def convert(
    input_path: PathLike,
    output_path: PathLike,
    config: VideoConvertConfig,
    *,
    max_workers: int | None = None,
    scratch_dir: PathLike | None = None,
    on_progress: ProgressCallback | None = None,
) -> VideoRoute:
    """
    Converts a video (or animated GIF) to GIF, animated WebP, WebM or MP4, as `config` says (see
    :data:`~sashimi.config.VIDEO_CONVERT`), and returns the route taken.

    An MP4 takes the fastest route that gives what a re-encode would (see
    :func:`plan_mp4_route`): ``copy`` when the video is H.264 and the audio AAC (or absent or
    muted), ``copy_video`` when only the audio needs re-encoding, else ``encode``. If a copy
    fails (ffmpeg can't remux the source), it re-encodes instead and returns ``encode``. Every
    other target returns ``encode``.

    `max_workers` is the number of threads each ffmpeg stage may use (default min(4, CPUs)).
    One ffmpeg process runs; with ``max_workers=2`` it uses roughly 15 threads in total.
    The output is written atomically; the input's name and extension are ignored.
    """
    threads = resolve_max_workers(max_workers)
    info = _probe_video(input_path)
    assert info.duration is not None
    remaining = info.duration - config.start_seconds
    expected = min(config.duration_seconds or remaining, remaining)

    def run(route: VideoRoute) -> VideoRoute:
        with atomic_output(output_path, scratch_dir) as temp:
            cmd = build_convert_command(
                input_path,
                temp,
                config,
                duration=info.duration or 0.0,
                threads=threads,
                ffmpeg=ffmpeg_binary(),
                route=route,
            )
            run_ffmpeg(cmd, progress_total=expected, on_progress=on_progress, stage="encoding")
        return route

    planned = plan_mp4_route(info, config) if isinstance(config, Mp4VideoConfig) else "encode"
    try:
        taken = run(planned)
    except BinaryNotFoundError:
        raise
    except ProcessingError as e:
        if planned == "encode":
            raise
        logger.info(f"Copying into MP4 failed, re-encoding instead: {str(e).splitlines()[0]}")
        taken = run("encode")
    if on_progress:
        on_progress(1.0, "done")
    return taken


def _can_copy(info: MediaInfo, container: str) -> bool:
    allowed, refused = _COPY_ALLOWED[container], _COPY_REFUSED[container]
    for stream in info.streams:
        if stream.type == "subtitle" and stream.codec in TEXT_SUBTITLES:
            continue
        if stream.type not in allowed or stream.codec in refused:
            return False
        codecs = allowed[stream.type]
        if codecs is not None and stream.codec not in codecs:
            return False
    return True


def check_copy_compatible(info: MediaInfo, container: str) -> None:
    """
    Raises InvalidOptionsError if the streams of `info` can't be copied into `container` (mkv,
    mp4, webm or mov) without re-encoding, e.g. H.264 into WebM, or a timecode track into MKV.
    Text subtitles always pass: the copy converts any the container can't hold (see
    :func:`subtitle_conversions`).
    """
    if _can_copy(info, container):
        return
    others = [other for other in ("mkv", "mp4", "mov", "webm") if other != container and _can_copy(info, other)]
    hint = f" Try {others[0].upper()} instead." if others else ""
    streams = ", ".join(f"{s.type}:{s.codec}" for s in info.streams)
    raise InvalidOptionsError(
        f"This video can't be trimmed to {container.upper()} without re-encoding.{hint}", detail=streams
    )


def build_trim_command(
    input_path: PathLike,
    output_path: PathLike,
    config: TrimConfig,
    *,
    duration: float,
    threads: int = 1,
    ffmpeg: str = "ffmpeg",
    subtitles: Sequence[str] = (),
) -> list[str]:
    """
    Builds the ffmpeg command for :func:`trim`. Pure.

    A stream copy uses ``-avoid_negative_ts make_zero``, so the frames from the previous keyframe
    are really in the clip rather than hidden behind an MP4 edit list that some players ignore.
    `subtitles` are the source's subtitle codecs in stream order: text subtitles the container
    can't hold are converted (MP4 ``mov_text`` to SRT for MKV, SRT or ASS to ``mov_text`` for
    MP4/MOV, to WebVTT for WebM) while everything else is copied.
    An accurate cut re-encodes with `threads` threads and, like :func:`convert`, is limited to
    :data:`~sashimi.config.MAX_VIDEO_SECONDS`.
    """
    _check_start(config.start_seconds, duration)
    length = config.length

    if isinstance(config, CopyTrimConfig):
        cmd = [ffmpeg, *base_args(1), *_progress_args(), *INPUT_PROTOCOLS]
        if config.start_seconds > 0:
            cmd += ["-ss", fmt_seconds(config.start_seconds)]
        if length is not None:
            cmd += ["-t", fmt_seconds(length)]
        cmd += ["-i", file_arg(input_path), "-map", "0", "-c", "copy"]
        cmd += [*subtitle_conversions(subtitles, config.container), "-avoid_negative_ts", "make_zero"]
        if config.container in ("mp4", "mov"):
            cmd += ["-movflags", "+faststart"]
        cmd += [*OUTPUT_FLAGS, "-f", _MUXERS[config.container], file_arg(output_path)]
        return cmd

    t_value = _length_limit(length, config.start_seconds, duration)
    cmd = [ffmpeg, *base_args(threads), *_progress_args(), *INPUT_PROTOCOLS, "-threads", str(threads)]
    if config.start_seconds > 0:
        cmd += ["-ss", fmt_seconds(config.start_seconds)]
    cmd += ["-t", fmt_seconds(t_value), "-i", file_arg(input_path)]
    cmd += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", *_stream_args(config.mute)]
    cmd += _encoder_args(config, faststart=config.container in ("mp4", "mov"))
    cmd += ["-threads", str(threads), *OUTPUT_FLAGS, "-f", _MUXERS[config.container], file_arg(output_path)]
    return cmd


def trim(
    input_path: PathLike,
    output_path: PathLike,
    config: TrimConfig,
    *,
    max_workers: int | None = None,
    scratch_dir: PathLike | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    Cuts a section of a video (see :data:`~sashimi.config.VIDEO_TRIM`).

    A :class:`~sashimi.config.CopyTrimConfig` copies streams: fast and lossless, but the cut starts
    at the keyframe at or before `start_seconds`, so the clip can begin early
    (:data:`~sashimi.config.KEYFRAME_NOTE` explains this for end users). The source's streams must
    fit the container, or InvalidOptionsError is raised before ffmpeg runs. An accurate config
    re-encodes (with up to `max_workers` threads) and starts exactly at `start_seconds`.

    With no end and no length, a copy takes the whole file: a lossless remux into another
    container. Copies convert text subtitles the container can't hold (for example MP4
    ``mov_text`` into MKV becomes SRT); an accurate cut drops subtitles.
    """
    info = _probe_video(input_path)
    assert info.duration is not None
    copy = isinstance(config, CopyTrimConfig)
    if copy:
        check_copy_compatible(info, config.container)
    threads = 1 if copy else resolve_max_workers(max_workers)
    subtitles = [stream.codec for stream in info.streams if stream.type == "subtitle"]
    with atomic_output(output_path, scratch_dir) as temp:
        cmd = build_trim_command(
            input_path,
            temp,
            config,
            duration=info.duration,
            threads=threads,
            ffmpeg=ffmpeg_binary(),
            subtitles=subtitles,
        )
        if copy:
            run_ffmpeg(cmd)
        else:
            remaining = info.duration - config.start_seconds
            expected = min(config.length or remaining, remaining)
            run_ffmpeg(cmd, progress_total=expected, on_progress=on_progress, stage="encoding")
    if on_progress:
        on_progress(1.0, "done")


def _transform_filters(config: TransformConfig) -> list[str]:
    filters = {90: ["transpose=1"], 180: ["hflip", "vflip"], 270: ["transpose=2"]}.get(config.rotate, [])
    if config.flip == "horizontal":
        filters.append("hflip")
    elif config.flip == "vertical":
        filters.append("vflip")
    return filters


def build_transform_command(
    input_path: PathLike,
    output_path: PathLike,
    config: TransformConfig,
    *,
    duration: float,
    threads: int,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """
    Builds the ffmpeg command for :func:`transform`. Pure. Uses the same encoders as :func:`convert`.
    ``-t`` is the probed length (plus the tolerance, at most
    :data:`~sashimi.config.MAX_VIDEO_SECONDS`), so a file whose header understates its length
    can't run past it.
    """
    t_value = _length_limit(None, 0.0, duration)
    vf = ",".join([*_transform_filters(config), "scale=trunc(iw/2)*2:trunc(ih/2)*2"])
    return [
        ffmpeg,
        *base_args(threads),
        *_progress_args(),
        *INPUT_PROTOCOLS,
        "-threads",
        str(threads),
        "-t",
        fmt_seconds(t_value),
        "-i",
        file_arg(input_path),
        "-vf",
        vf,
        *_stream_args(config.mute),
        *_encoder_args(config),
        "-threads",
        str(threads),
        *OUTPUT_FLAGS,
        "-f",
        _MUXERS[config.target],
        file_arg(output_path),
    ]


def transform(
    input_path: PathLike,
    output_path: PathLike,
    config: TransformConfig,
    *,
    max_workers: int | None = None,
    scratch_dir: PathLike | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    Rotates and/or flips a video, re-encoding it as `config.target` (see
    :data:`~sashimi.config.VIDEO_TRANSFORM`). Display-rotation metadata is applied first.
    """
    threads = resolve_max_workers(max_workers)
    info = _probe_video(input_path)
    assert info.duration is not None
    with atomic_output(output_path, scratch_dir) as temp:
        cmd = build_transform_command(
            input_path, temp, config, duration=info.duration, threads=threads, ffmpeg=ffmpeg_binary()
        )
        run_ffmpeg(cmd, progress_total=info.duration, on_progress=on_progress, stage="encoding")
    if on_progress:
        on_progress(1.0, "done")
