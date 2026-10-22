# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Video conversion (GIF/animated WebP/WebM/MP4), lossless or frame-accurate trimming, and rotate/flip."""

import logging
from collections.abc import Sequence
from typing import Literal

from sashimi.config import (
    DURATION_TOLERANCE_SECONDS,
    CopyTrimConfig,
    MetadataMode,
    GifVideoConfig,
    Limits,
    Mp4VideoConfig,
    TransformConfig,
    TrimConfig,
    VideoConvertConfig,
    WebpVideoConfig,
)
from sashimi.config._limits import duration_text, resolve_limits
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
    holds_packets,
    run_ffmpeg,
)
from sashimi.core._mediameta import dropped_streams, first_stream, metadata_args, opus_kbps
from sashimi.core._palette import (
    PALETTE_SHARE,
    PALETTE_STAGE,
    one_pass,
    palette_input,
    palette_output,
    palette_use,
    scaled,
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
    "build_palette_command",
    "convert",
    "two_pass",
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


def _too_long(limit: float) -> MediaTooLongError:
    return MediaTooLongError(
        f"Videos longer than {duration_text(limit)} aren't supported yet. Set a shorter duration to convert part of it.",
        limit="max_video_seconds",
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


def _encoder_args(
    config: _H264Options | _Vp9Options, *, faststart: bool = True, audio_channels: int | None = None
) -> list[str]:
    """
    Video and audio codec arguments for a VP9/Opus or H.264/AAC encode. Opus's bitrate is lowered
    to its ceiling for `audio_channels` (see :data:`~sashimi.config.OPUS_MAX_KBPS_PER_CHANNEL`).
    """
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
    kbps = config.audio_bitrate_kbps
    if isinstance(config, _Vp9Options):
        kbps = opus_kbps(kbps, audio_channels)
    return [*video, *audio, "-b:a", f"{kbps}k", *tail]


def _first_streams(source: MediaInfo | None, mute: bool) -> list[int | None]:
    """The source streams a re-encode or MP4 copy writes: the first video, then the first audio unless muted."""
    outputs = [first_stream(source, "video")]
    if not mute and first_stream(source, "audio") is not None:
        outputs.append(first_stream(source, "audio"))
    return outputs


def _channels(source: MediaInfo | None) -> int | None:
    return source.audio_channels if source is not None else None


def _log_opus_cap(config: object, info: MediaInfo) -> None:
    if isinstance(config, _Vp9Options) and not config.mute:
        capped = opus_kbps(config.audio_bitrate_kbps, info.audio_channels)
        if capped < config.audio_bitrate_kbps:
            logger.info(f"Opus allows at most {capped} kb/s for {info.audio_channels} channel(s); using {capped} kb/s.")


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
        raise InvalidOptionsError(
            f"The start time ({start:g}s) is at or past the end of the video ({fmt_seconds(duration)}s)."
        )


def _after_last_frame(start: float, action: str) -> InvalidOptionsError:
    return InvalidOptionsError(
        f"The start time ({start:g}s) is after the video's last frame, so there's nothing to {action}.",
        detail="no frames after the start time",
    )


def _too_close_to_the_end(start: float, fps: int) -> InvalidOptionsError:
    return InvalidOptionsError(
        f"The start time ({start:g}s) is too close to the end of the video: it leaves less than one frame "
        f"at {fps} fps, so there's nothing to convert.",
        detail="no frames after the start time at this frame rate",
    )


def _frames_after(
    input_path: PathLike, start: float, ffmpeg: str, *, copied: bool = False, fps: int | None = None
) -> bool:
    """
    Whether ffmpeg, seeking to `start`, finds any video frame; True when that can't be told.
    Frames are decoded, as a re-encode sees them (through ``fps=`` `fps` when given, as a GIF or
    animated WebP does), or with `copied` only demuxed, as a stream copy sees them (no decoding,
    so it costs the same whatever the keyframe interval).
    """
    cmd = [ffmpeg, *base_args(1), *INPUT_PROTOCOLS, "-threads", "1", "-ss", fmt_seconds(start)]
    cmd += ["-i", file_arg(input_path)]
    if copied:
        cmd += ["-map", "0:v:0", "-c", "copy", "-copypriorss", "0", "-copyinkf", "-frames:v", "1", "-f", "data"]
    else:
        scale = f"fps={fps},scale=2:2" if fps else "scale=2:2"
        cmd += ["-an", "-sn", "-dn", "-vf", scale, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray"]
    cmd.append("pipe:1")
    try:
        return bool(run_ffmpeg(cmd, capture_stdout=True))
    except BinaryNotFoundError:
        raise
    except ProcessingError:
        return True


def _nothing_from(input_path: PathLike, start: float, ffmpeg: str, action: str) -> InvalidOptionsError | None:
    """
    Why ffmpeg, seeking to `start`, decodes no video frame: there is none after it, or there are
    some it can't decode from there (it found no keyframe to start from). None when it decodes one.
    """
    if _frames_after(input_path, start, ffmpeg):
        return None
    if _frames_after(input_path, start, ffmpeg, copied=True):
        return InvalidOptionsError(
            f"No frame of the video could be read from the start time ({start:g}s), so there's nothing to "
            f"{action}. Try an earlier start.",
            detail="frames after the start time can't be decoded from there",
        )
    return _after_last_frame(start, action)


def _require_video(output: PathLike, input_path: PathLike, start: float, ffmpeg: str, action: str) -> None:
    """
    Raises unless `output` holds a video frame: InvalidOptionsError when ffmpeg decodes no frame
    of the input from `start` (see :func:`_nothing_from`), else ProcessingError.
    """
    if holds_packets(output, "video"):
        return
    if refusal := _nothing_from(input_path, start, ffmpeg, action):
        raise refusal
    raise ProcessingError(f"ffmpeg wrote no video frames from {start:g}s")


def _length_limit(requested: float | None, start: float, duration: float, limit: float | None) -> float:
    """
    The ``-t`` value for a re-encode: the requested length, or else the rest of the probed video
    plus :data:`DURATION_TOLERANCE_SECONDS`, capped at `limit` (``Limits.max_video_seconds``).
    Raises MediaTooLongError when the rest of the video is over the limit and no length was requested.
    """
    if requested is not None:
        return requested
    if limit is not None and duration - start > limit:
        raise _too_long(limit)
    rest = duration - start + DURATION_TOLERANCE_SECONDS
    return rest if limit is None else min(rest, limit)


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
    source: MediaInfo | None,
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
    cmd += metadata_args(config.metadata, source, _first_streams(source, config.mute))
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
    limits: Limits | None = None,
    source: MediaInfo | None = None,
    palette: bytes | None = None,
) -> list[str]:
    """
    Builds the ffmpeg command for :func:`convert`. Pure: no filesystem or process access.

    `duration` is the probed input length in seconds. Raises the same user errors as
    :func:`convert` for out-of-range options and `limits`. ``-ss`` and ``-t`` are *input* options
    (before ``-i``) so ffmpeg stops reading at the end of the clip instead of decoding the rest of
    the file. Without ``duration_seconds``, ``-t`` is the rest of the probed video (plus
    :data:`~sashimi.config.DURATION_TOLERANCE_SECONDS`, and at most ``limits.max_video_seconds``),
    so a file whose header understates its length can't be encoded past it.

    `route` is the MP4 route (see :func:`plan_mp4_route`); the copy routes use ``-c:v copy``
    with the same streams, length, ``+faststart`` and length limits as the re-encode.

    `source` is the probed input, used to carry out ``metadata`` (stream tags and languages) and
    to keep Opus within its per-channel bitrate; without it, tags named by the mode's fixed list
    are removed and the bitrate is passed as asked.

    `palette` is the PNG that :func:`build_palette_command`'s command writes, for a GIF that
    :func:`two_pass` says is built in two passes: the command then reads it as a second input
    instead of building the palette itself. Without it, a GIF's command builds the palette in the
    same pass, which holds every frame in memory until the end; the GIF is byte-identical
    either way. Other targets ignore it.
    """
    limits = resolve_limits(limits)
    limits.check(config)
    _check_start(config.start_seconds, duration)
    t_value = _length_limit(config.duration_seconds, config.start_seconds, duration, limits.max_video_seconds)
    if route != "encode":
        if not isinstance(config, Mp4VideoConfig) or config.start_seconds > 0:
            raise ValueError(f"route {route!r} is only for MP4 output from the start of the video")
        return _copy_command(
            input_path, output_path, config, route, t_value=t_value, threads=threads, ffmpeg=ffmpeg, source=source
        )

    cmd = [ffmpeg, *base_args(threads), *_progress_args(), *_input_args(input_path, config, t_value, threads)]
    if isinstance(config, GifVideoConfig):
        filters = _gif_filters(config)
        if palette is not None and two_pass(config):
            cmd += [*palette_input(palette), *palette_use(filters, _paletteuse(config))]
        else:
            cmd += ["-vf", one_pass(filters, config.palette_mode, _paletteuse(config))]
        cmd += ["-loop", "0" if config.loop else "-1", "-an"]
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
        cmd += ["-vf", scale, *_stream_args(config.mute), *_encoder_args(config, audio_channels=_channels(source))]
        cmd += metadata_args(config.metadata, source, _first_streams(source, config.mute))
    cmd += ["-threads", str(threads), *OUTPUT_FLAGS, "-f", _MUXERS[config.target], file_arg(output_path)]
    return cmd


def two_pass(config: GifVideoConfig) -> bool:
    """
    Whether :func:`convert` builds this GIF's palette in a first pass of its own. Pure.

    A palette built from every frame ("diff", "full") is: building it in the same pass would hold
    every frame in memory until the end. A palette per frame ("single") streams in one pass.
    """
    return config.palette_mode != "single"


def _gif_filters(config: GifVideoConfig) -> str:
    return f"fps={config.fps},scale='min({config.max_width},iw)':-2:flags=lanczos"


def _paletteuse(config: GifVideoConfig) -> str:
    options = ["new=1"] if config.palette_mode == "single" else []
    if config.dither == "bayer":
        options += ["dither=bayer", "bayer_scale=4"]
    else:
        options.append(f"dither={config.dither}")
    return ":".join(options)


def _input_args(input_path: PathLike, config: VideoConvertConfig, t_value: float, threads: int) -> list[str]:
    cmd = [*INPUT_PROTOCOLS, "-threads", str(threads)]
    if config.start_seconds > 0:
        cmd += ["-ss", fmt_seconds(config.start_seconds)]
    return [*cmd, "-t", fmt_seconds(t_value), "-i", file_arg(input_path)]


def build_palette_command(
    input_path: PathLike,
    config: GifVideoConfig,
    *,
    duration: float,
    threads: int,
    ffmpeg: str = "ffmpeg",
    limits: Limits | None = None,
) -> list[str]:
    """
    Builds the first pass of a two-pass GIF (see :func:`two_pass`): the same frames as
    :func:`build_convert_command`'s, through ``palettegen``, written as one PNG on stdout, with
    each frame's time on stderr for progress. Pass its output to :func:`build_convert_command`
    as `palette`. Pure; raises the same user errors as :func:`build_convert_command`.
    """
    limits = resolve_limits(limits)
    limits.check(config)
    _check_start(config.start_seconds, duration)
    t_value = _length_limit(config.duration_seconds, config.start_seconds, duration, limits.max_video_seconds)
    cmd = [ffmpeg, *base_args(threads), *_input_args(input_path, config, t_value, threads)]
    return [*cmd, "-an", *palette_output(_gif_filters(config), config.palette_mode)]


def convert(
    input_path: PathLike,
    output_path: PathLike,
    config: VideoConvertConfig,
    *,
    max_workers: int | None = None,
    scratch_dir: PathLike | None = None,
    limits: Limits | None = None,
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

    `limits` (default ``Limits()``): a GIF or animated WebP longer than ``max_gif_seconds`` and a
    WebM or MP4 clip longer than ``max_video_seconds`` raise InvalidOptionsError before the file
    is read; a whole video longer than ``max_video_seconds`` raises MediaTooLongError. A start
    time after the video's last frame (inside the probed length, for example where the audio runs
    on past the picture) raises InvalidOptionsError, and nothing is written.

    A GIF's memory doesn't grow with its length: a palette built from every frame is built in a
    first pass, written to no file, then used in a second (see :func:`two_pass`), so the video is
    decoded twice. The first pass is the first third of the progress, as "analysing".

    WebM and MP4 keep the source's tags and chapters as far as the container holds them, unless
    ``metadata`` says to remove location tags or all of them; GIFs and animated WebPs hold none.
    An Opus bitrate above what Opus allows for the audio's channels is lowered to it.

    `max_workers` is the number of threads each ffmpeg stage may use (default min(4, CPUs)).
    One ffmpeg process runs at a time; with ``max_workers=2`` it uses roughly 15 threads in total.
    The output is written atomically; the input's name and extension are ignored.
    """
    limits = resolve_limits(limits)
    limits.check(config)
    threads = resolve_max_workers(max_workers)
    info = _probe_video(input_path)
    assert info.duration is not None
    remaining = info.duration - config.start_seconds
    expected = min(config.duration_seconds or remaining, remaining)
    ffmpeg = ffmpeg_binary()
    animated = isinstance(config, (GifVideoConfig, WebpVideoConfig))

    def run(route: VideoRoute) -> VideoRoute:
        palette = None
        report = on_progress
        if isinstance(config, GifVideoConfig) and two_pass(config):
            first = build_palette_command(
                input_path, config, duration=info.duration or 0.0, threads=threads, ffmpeg=ffmpeg, limits=limits
            )
            palette = run_ffmpeg(
                first,
                capture_stdout=True,
                progress_total=expected,
                on_progress=scaled(on_progress, 0.0, PALETTE_SHARE),
                stage=PALETTE_STAGE,
            )
            if not palette:
                raise ProcessingError("ffmpeg built no palette: no frames were decoded")
            report = scaled(on_progress, PALETTE_SHARE, 1 - PALETTE_SHARE)
        with atomic_output(output_path, scratch_dir) as temp:
            cmd = build_convert_command(
                input_path,
                temp,
                config,
                duration=info.duration or 0.0,
                threads=threads,
                ffmpeg=ffmpeg,
                route=route,
                limits=limits,
                source=info,
                palette=palette,
            )
            run_ffmpeg(cmd, progress_total=expected, on_progress=report, stage="encoding")
            if config.start_seconds > 0 and not animated:
                _require_video(temp, input_path, config.start_seconds, ffmpeg, "convert")
        return route

    _log_opus_cap(config, info)
    planned = plan_mp4_route(info, config) if isinstance(config, Mp4VideoConfig) else "encode"
    try:
        taken = run(planned)
    except BinaryNotFoundError:
        raise
    except ProcessingError as e:
        if animated and config.start_seconds > 0:
            assert isinstance(config, (GifVideoConfig, WebpVideoConfig))
            if refusal := _nothing_from(input_path, config.start_seconds, ffmpeg, "convert"):
                raise refusal from e
            if not _frames_after(input_path, config.start_seconds, ffmpeg, fps=config.fps):
                raise _too_close_to_the_end(config.start_seconds, config.fps) from e
        if planned == "encode":
            raise
        logger.info(f"Copying into MP4 failed, re-encoding instead: {str(e).splitlines()[0]}")
        taken = run("encode")
    if on_progress:
        on_progress(1.0, "done")
    return taken


def _can_copy(info: MediaInfo, container: str, skip: frozenset[int] = frozenset()) -> bool:
    allowed, refused = _COPY_ALLOWED[container], _COPY_REFUSED[container]
    for index, stream in enumerate(info.streams):
        if index in skip:
            continue
        if stream.type == "subtitle" and stream.codec in TEXT_SUBTITLES:
            continue
        if stream.type not in allowed or stream.codec in refused:
            return False
        codecs = allowed[stream.type]
        if codecs is not None and stream.codec not in codecs:
            return False
    return True


def check_copy_compatible(info: MediaInfo, container: str, metadata: MetadataMode = "keep") -> None:
    """
    Raises InvalidOptionsError if the streams of `info` can't be copied into `container` (mkv,
    mp4, webm or mov) without re-encoding, e.g. H.264 into WebM, or a timecode track into MKV.
    Text subtitles always pass: the copy converts any the container can't hold (see
    :func:`subtitle_conversions`). Streams a `metadata` mode drops (data streams unless
    ``keep``, cover art for ``remove_all``) don't count.
    """
    skip = frozenset(dropped_streams(info, metadata))
    if _can_copy(info, container, skip):
        return
    others = [other for other in ("mkv", "mp4", "mov", "webm") if other != container and _can_copy(info, other, skip)]
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
    limits: Limits | None = None,
    source: MediaInfo | None = None,
) -> list[str]:
    """
    Builds the ffmpeg command for :func:`trim`. Pure.

    A stream copy uses ``-avoid_negative_ts make_zero``, so the frames from the previous keyframe
    are really in the clip rather than hidden behind an MP4 edit list that some players ignore.
    `subtitles` are the source's subtitle codecs in stream order: text subtitles the container
    can't hold are converted (MP4 ``mov_text`` to SRT for MKV, SRT or ASS to ``mov_text`` for
    MP4/MOV, to WebVTT for WebM) while everything else is copied.
    An accurate cut re-encodes with `threads` threads and, like :func:`convert`, is limited to
    ``limits.max_video_seconds``; a stream copy has no length limit. `source` is the probed input,
    used as in :func:`build_convert_command`; a copy that removes metadata also leaves out timed
    metadata (data streams, such as GPS tracks), and cover art for ``remove_all``.
    """
    limits = resolve_limits(limits)
    limits.check(config)
    _check_start(config.start_seconds, duration)
    length = config.length

    if isinstance(config, CopyTrimConfig):
        cmd = [ffmpeg, *base_args(1), *_progress_args(), *INPUT_PROTOCOLS]
        if config.start_seconds > 0:
            cmd += ["-ss", fmt_seconds(config.start_seconds)]
        if length is not None:
            cmd += ["-t", fmt_seconds(length)]
        cmd += ["-i", file_arg(input_path), "-map", "0"]
        dropped = dropped_streams(source, config.metadata)
        for index in dropped:
            cmd += ["-map", f"-0:{index}"]
        cmd += ["-c", "copy"]
        cmd += [*subtitle_conversions(subtitles, config.container), "-avoid_negative_ts", "make_zero"]
        if config.container in ("mp4", "mov"):
            cmd += ["-movflags", "+faststart"]
        kept = [index for index in range(len(source.streams)) if index not in dropped] if source else []
        cmd += metadata_args(config.metadata, source, kept)
        cmd += [*OUTPUT_FLAGS, "-f", _MUXERS[config.container], file_arg(output_path)]
        return cmd

    t_value = _length_limit(length, config.start_seconds, duration, limits.max_video_seconds)
    cmd = [ffmpeg, *base_args(threads), *_progress_args(), *INPUT_PROTOCOLS, "-threads", str(threads)]
    if config.start_seconds > 0:
        cmd += ["-ss", fmt_seconds(config.start_seconds)]
    cmd += ["-t", fmt_seconds(t_value), "-i", file_arg(input_path)]
    cmd += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", *_stream_args(config.mute)]
    cmd += _encoder_args(config, faststart=config.container in ("mp4", "mov"), audio_channels=_channels(source))
    cmd += metadata_args(config.metadata, source, _first_streams(source, config.mute))
    cmd += ["-threads", str(threads), *OUTPUT_FLAGS, "-f", _MUXERS[config.container], file_arg(output_path)]
    return cmd


def trim(
    input_path: PathLike,
    output_path: PathLike,
    config: TrimConfig,
    *,
    max_workers: int | None = None,
    scratch_dir: PathLike | None = None,
    limits: Limits | None = None,
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
    ``mov_text`` into MKV becomes SRT); an accurate cut drops subtitles. Tags and chapters are
    kept unless ``metadata`` says otherwise; a copy that removes metadata also leaves out timed
    metadata tracks (such as GPS tracks), and cover art for ``remove_all``.

    An accurate cut longer than ``limits.max_video_seconds`` raises InvalidOptionsError, or
    MediaTooLongError when it runs to the end of a longer video. Copies have no length limit.
    A start time after the video's last frame (inside the probed length, for example where the
    audio runs on past the picture) raises InvalidOptionsError, and nothing is written; a copy
    would otherwise hold only the frames before it, from the keyframe.
    """
    limits = resolve_limits(limits)
    limits.check(config)
    info = _probe_video(input_path)
    assert info.duration is not None
    _check_start(config.start_seconds, info.duration)
    copy = isinstance(config, CopyTrimConfig)
    if copy:
        check_copy_compatible(info, config.container, config.metadata)
    ffmpeg = ffmpeg_binary()
    start = config.start_seconds
    if copy and start > 0 and not _frames_after(input_path, start, ffmpeg, copied=True):
        raise _after_last_frame(start, "cut")
    threads = 1 if copy else resolve_max_workers(max_workers)
    _log_opus_cap(config, info)
    subtitles = [stream.codec for stream in info.streams if stream.type == "subtitle"]
    with atomic_output(output_path, scratch_dir) as temp:
        cmd = build_trim_command(
            input_path,
            temp,
            config,
            duration=info.duration,
            threads=threads,
            ffmpeg=ffmpeg,
            subtitles=subtitles,
            limits=limits,
            source=info,
        )
        if copy:
            run_ffmpeg(cmd)
        else:
            remaining = info.duration - start
            expected = min(config.length or remaining, remaining)
            run_ffmpeg(cmd, progress_total=expected, on_progress=on_progress, stage="encoding")
        if start > 0:
            _require_video(temp, input_path, start, ffmpeg, "cut")
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
    limits: Limits | None = None,
    source: MediaInfo | None = None,
) -> list[str]:
    """
    Builds the ffmpeg command for :func:`transform`. Pure. Uses the same encoders as :func:`convert`,
    and `source` as it does.
    ``-t`` is the probed length (plus the tolerance, and at most ``limits.max_video_seconds``),
    so a file whose header understates its length can't run past it.
    """
    t_value = _length_limit(None, 0.0, duration, resolve_limits(limits).max_video_seconds)
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
        *_encoder_args(config, audio_channels=_channels(source)),
        *metadata_args(config.metadata, source, _first_streams(source, config.mute)),
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
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    Rotates and/or flips a video, re-encoding it as `config.target` (see
    :data:`~sashimi.config.VIDEO_TRANSFORM`). Display-rotation metadata is applied first. Tags
    follow ``metadata``, as in :func:`convert`. A video longer than ``limits.max_video_seconds``
    raises MediaTooLongError.
    """
    threads = resolve_max_workers(max_workers)
    info = _probe_video(input_path)
    assert info.duration is not None
    _log_opus_cap(config, info)
    with atomic_output(output_path, scratch_dir) as temp:
        cmd = build_transform_command(
            input_path,
            temp,
            config,
            duration=info.duration,
            threads=threads,
            ffmpeg=ffmpeg_binary(),
            limits=limits,
            source=info,
        )
        run_ffmpeg(cmd, progress_total=info.duration, on_progress=on_progress, stage="encoding")
    if on_progress:
        on_progress(1.0, "done")
