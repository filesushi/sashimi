# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Video conversion (GIF/animated WebP/WebM/MP4), lossless or frame-accurate trimming, and rotate/flip."""

import logging
from collections.abc import Collection, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from sashimi.config import (
    DURATION_TOLERANCE_SECONDS,
    CopyTrimConfig,
    MetadataMode,
    GifVideoConfig,
    Limits,
    Mp4VideoConfig,
    MovTransformConfig,
    Mp4TransformConfig,
    TransformConfig,
    TrimConfig,
    VideoConvertConfig,
    WebpVideoConfig,
)
from sashimi.config._limits import check_webp_size, duration_text, resolve_limits, too_long_text
from sashimi.config._video import _H264Options, _Vp9Options
from sashimi.core._atomic import PathLike, atomic_output
from sashimi.core._capabilities import require_behaviour
from sashimi.core._hdr import hdr_name, is_hdr, require_tone_mapping, tone_map_filter, with_tone_map
from sashimi.core.fidelity import Fidelity, Outcome, derived, fidelity as make_fidelity, media_fidelity
from sashimi.core._ffmpeg import (
    INPUT_PROTOCOLS,
    OUTPUT_FLAGS,
    ProgressCallback,
    base_args,
    ffmpeg_binary,
    ffprobe_binary,
    file_arg,
    fmt_seconds,
    holds_packets,
    run_ffmpeg,
)
from sashimi.core._mediameta import (
    dropped_streams,
    first_stream,
    merged_movflags,
    metadata_args,
    opus_kbps,
    opus_layout_filter,
    quicktime_tag_args,
)
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
from sashimi.core._seek import FORWARD_SEEKING, SHOWN_SEEKING, VideoIndex, decoding_args, video_index, with_filter
from sashimi.core.probe import (
    DOLBY_VISION_COLOURS,
    VIDEO_CONTAINERS,
    MediaInfo,
    StreamInfo,
    anamorphic,
    check_decodable,
    probe,
    short_gif_frames,
)
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
    "CopyPlan",
    "TrimResult",
    "VideoConversion",
    "plan_copy",
    "trim",
    "build_transform_command",
    "transform",
]

logger = logging.getLogger("sashimi")

NOT_A_VIDEO = "This file doesn't look like a video we can read."


def _too_long(limit: float, length: float, start: float) -> MediaTooLongError:
    return MediaTooLongError(too_long_text("video", length, start, limit), limit="max_video_seconds")


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
_NOT_IN_MP4 = frozenset(
    {
        "vp8", "prores", "prores_raw", "h263", "flv1", "theora", "wmv1", "wmv2", "wmv3", "vp6", "vp6a", "vp6f",
        "msmpeg4v1", "msmpeg4v2", "msmpeg4v3", "rv10", "rv20", "rv30", "rv40", "cinepak",
        "amr_nb", "amr_wb", "wmav1", "wmav2", "wmapro", "wmavoice", "nellymoser", "adpcm_swf", "cook",
    }
)  # fmt: skip
"""Codecs ffmpeg's MP4 muxer can't hold (it has no tag for them), checked against FFmpeg n8.1."""
_COPY_REFUSED: dict[str, frozenset[str]] = {
    "mkv": frozenset({"mov_text"}),
    "mp4": _NOT_IN_MP4,
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
    config: _H264Options | _Vp9Options,
    *,
    faststart: bool = True,
    audio_channels: int | None = None,
    hdr: bool = False,
    container: str = "mp4",
) -> list[str]:
    """
    Video and audio codec arguments for a VP9/Opus or H.264/AAC encode. Opus's bitrate is lowered
    to its ceiling for `audio_channels` (see :data:`~sashimi.config.OPUS_MAX_KBPS_PER_CHANNEL`).
    `hdr` keeps HDR video as HDR (``keep_hdr`` on an HDR source): 10-bit VP9 profile 2, or 10-bit
    HEVC in place of H.264 (tagged ``hvc1`` in MP4 and MOV, as Apple's players need).
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
        if hdr:
            video += ["-profile:v", "2"]
        audio = ["-c:a", "libopus"]
        tail: list[str] = []
    else:
        if hdr:
            video = ["-c:v", "libx265", "-preset", config.preset, "-crf", crf, "-x265-params", "log-level=error"]
            video += ["-tag:v", "hvc1"] if container in ("mp4", "mov") else []
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


def _audio_filters(config: object, source: MediaInfo | None, skip: str | None = None) -> list[str]:
    """
    ``-af`` for a re-encode with sound: `skip` (dropping the sound before a start time) and, for
    Opus, a channel layout it takes (see :func:`~sashimi.core._mediameta.opus_layout_filter`).
    """
    if getattr(config, "mute", False):
        return []
    layout = opus_layout_filter(_channels(source)) if isinstance(config, _Vp9Options) else None
    chain = ",".join(part for part in (skip, layout) if part)
    return ["-af", chain] if chain else []


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


def _stream_args(mute: bool, hdr: bool = False) -> list[str]:
    audio = ["-an"] if mute else ["-map", "0:a:0?"]
    return ["-pix_fmt", "yuv420p10le" if hdr else "yuv420p", "-map", "0:v:0", *audio, "-sn", "-dn"]


def keeps_hdr(config: object, source: MediaInfo | None) -> bool:
    """Whether a re-encode with `config` keeps `source`'s HDR (``keep_hdr`` on HDR video) rather than tone-mapping it. Pure."""
    return bool(getattr(config, "keep_hdr", False)) and is_hdr(source)


def _picture(vf: str, config: object, source: MediaInfo | None) -> str:
    """`vf` followed by tone mapping when `source` is HDR and `config` doesn't keep it (see :func:`keeps_hdr`)."""
    return vf if keeps_hdr(config, source) else with_tone_map(vf, tone_map_filter(source))


def _probe_video(input_path: PathLike, limits: Limits | None = None, *, decoding: bool = True) -> MediaInfo:
    """
    Probes and checks the input is a video (or animated GIF) with a readable duration, and, when
    its pictures will be decoded, that they can be (see :func:`~sashimi.core.probe.check_decodable`).
    """
    try:
        info = probe(input_path, limits=limits)
    except UnsupportedMediaError as e:
        raise UnsupportedMediaError(NOT_A_VIDEO, detail=e.detail) from e
    if not info.has_video or not info.duration or not (info.container_names & VIDEO_CONTAINERS):
        raise UnsupportedMediaError(NOT_A_VIDEO, detail=f"container={info.container} duration={info.duration}")
    if decoding:
        check_decodable(info)
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
    input_path: PathLike,
    start: float,
    ffmpeg: str,
    *,
    copied: bool = False,
    fps: int | None = None,
    index: VideoIndex | None = None,
) -> bool:
    """
    Whether ffmpeg, seeking to `start`, finds any video frame; True when that can't be told.
    Frames are decoded, as a re-encode sees them (through ``fps=`` `fps` when given, as a GIF or
    animated WebP does), or with `copied` only demuxed, as a stream copy sees them (no decoding,
    so it costs the same whatever the keyframe interval).
    """
    cmd = [ffmpeg, *base_args(1), *INPUT_PROTOCOLS, "-threads", "1"]
    scale = f"fps={fps},scale=2:2" if fps else "scale=2:2"
    begin = index.start_for(start) if index is not None and not copied else None
    if begin is None:
        cmd += ["-ss", fmt_seconds(start)]
    else:
        decoding = decoding_args(begin, start, origin=index.origin(begin) if index is not None else None)
        cmd += decoding.inputs
        scale = with_filter(decoding.video, scale)
    cmd += ["-i", file_arg(input_path)]
    if copied:
        cmd += ["-map", "0:v:0", "-c", "copy", "-copypriorss", "0", "-copyinkf", "-frames:v", "1", "-f", "data"]
    else:
        cmd += ["-an", "-sn", "-dn", "-vf", scale, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray"]
    cmd.append("pipe:1")
    try:
        return bool(run_ffmpeg(cmd, capture_stdout=True))
    except BinaryNotFoundError:
        raise
    except ProcessingError:
        return True


def _nothing_from(
    input_path: PathLike, start: float, ffmpeg: str, action: str, index: VideoIndex | None = None
) -> InvalidOptionsError | None:
    """
    Why ffmpeg, seeking to `start`, decodes no video frame: there is none after it, or there are
    some it can't decode from there (it found no keyframe to start from). None when it decodes one.
    """
    if _frames_after(input_path, start, ffmpeg, index=index):
        return None
    if _frames_after(input_path, start, ffmpeg, copied=True):
        return InvalidOptionsError(
            f"No frame of the video could be read from the start time ({start:g}s), so there's nothing to "
            f"{action}. Try an earlier start.",
            detail="frames after the start time can't be decoded from there",
        )
    return _after_last_frame(start, action)


def _require_video(
    output: PathLike, input_path: PathLike, start: float, ffmpeg: str, action: str, index: VideoIndex | None = None
) -> None:
    """
    Raises unless `output` holds a video frame: InvalidOptionsError when ffmpeg decodes no frame
    of the input from `start` (see :func:`_nothing_from`), else ProcessingError.
    """
    if holds_packets(output, "video"):
        return
    if refusal := _nothing_from(input_path, start, ffmpeg, action, index):
        raise refusal
    raise ProcessingError(f"ffmpeg wrote no video frames from {start:g}s")


def _start_index(input_path: PathLike, info: MediaInfo, start: float, limits: Limits | None) -> VideoIndex | None:
    """The keyframe index up to `start` when decoding starts there (inside the video), else None."""
    if start <= 0 or start >= (info.duration or 0.0):
        return None
    return video_index(input_path, info, start, limits)


def _length_limit(requested: float | None, start: float, duration: float, limit: float | None) -> float:
    """
    The ``-t`` value for a re-encode: the requested length, or else the rest of the probed video
    plus :data:`DURATION_TOLERANCE_SECONDS`, capped at `limit` (``Limits.max_video_seconds``).
    Raises MediaTooLongError when the rest of the video is over the limit and no length was requested.
    """
    if requested is not None:
        return requested
    if limit is not None and duration - start > limit:
        raise _too_long(limit, duration - start, start)
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


class VideoConversion(Outcome):
    """What :func:`convert` wrote: the route it took, and what the output kept of the source (``fidelity``)."""

    route: VideoRoute
    """``copy``, ``copy_video`` or ``encode`` (see :data:`VideoRoute`)."""


COPYABLE_PIXEL_FORMATS = frozenset({"yuv420p", "yuvj420p"})
"""H.264 pixel formats that play everywhere an MP4 re-encode does (8-bit 4:2:0)."""
HDR_COPYABLE_PIXEL_FORMAT = "yuv420p10le"
"""The pixel format of HDR HEVC that an MP4 with ``keep_hdr`` copies: what its re-encode would write."""
_MP4_DEFAULTS = Mp4VideoConfig()


def _copyable_video(info: MediaInfo, config: Mp4VideoConfig) -> bool:
    if is_hdr(info):
        return config.keep_hdr and info.video_codec == "hevc" and info.pixel_format == HDR_COPYABLE_PIXEL_FORMAT
    return info.video_codec == "h264" and info.pixel_format in COPYABLE_PIXEL_FORMATS


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
    HEVC, VP9 and everything else are re-encoded, since many browsers can't play them in MP4,
    with two exceptions for HDR: HDR video is re-encoded (tone-mapped) even when it is H.264, and
    with ``keep_hdr`` 10-bit 4:2:0 HEVC HDR video is copied, since a re-encode would keep it as
    10-bit HEVC anyway.
    """
    remaining = (info.duration or 0.0) - config.start_seconds
    reencode = (
        config.start_seconds > 0
        or (config.duration_seconds is not None and config.duration_seconds < remaining)
        or (config.max_width is not None and (info.width is None or config.max_width < info.width))
        or config.crf != _MP4_DEFAULTS.crf
        or config.preset != _MP4_DEFAULTS.preset
        or not _copyable_video(info, config)
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


GENERATED_PTS = frozenset({"mpeg"})
"""Demuxers whose packets can lack a pts (MPEG-PS B-frames), which a stream copy must generate (``-fflags +genpts``)."""


_LEAD_PACKETS = 256


def hidden_lead(input_path: PathLike) -> float:
    """
    How many seconds of video a file keeps hidden before its start: packets an MP4 or MOV edit list
    shows before time 0 or marks to discard (a phone's pre-roll, Dolby Vision's leading GOP). A
    stream copy with ``-avoid_negative_ts make_zero`` would show them. Reads the first packets only.
    """
    cmd = [ffprobe_binary(), "-hide_banner", "-loglevel", "error", *INPUT_PROTOCOLS, "-select_streams", "V:0"]
    cmd += ["-read_intervals", f"%+#{_LEAD_PACKETS}", "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0"]
    try:
        listing = run_ffmpeg([*cmd, file_arg(input_path)], capture_stdout=True).decode("ascii", "replace")
    except BinaryNotFoundError:
        raise
    except ProcessingError:
        return 0.0
    earliest = 0.0
    for line in listing.splitlines():
        fields = line.strip().split(",")
        try:
            time = float(fields[0])
        except (ValueError, IndexError):
            continue
        if time < earliest and (time < -1e-3 or "D" in (fields[1] if len(fields) > 1 else "")):
            earliest = time
    return round(-earliest, 6)


def hidden_tail(input_path: PathLike, info: MediaInfo) -> int:
    """
    How many video frames an MP4 or MOV file keeps hidden after its end: packets marked to discard
    in its last seconds (some iPhone recordings). A stream copy shows them, since no container
    sashimi writes can hide frames after the end. 0 for other containers. Reads the last packets only.
    """
    if not info.container_names & {"mov", "mp4"} or not info.duration:
        return 0
    cmd = [ffprobe_binary(), "-hide_banner", "-loglevel", "error", *INPUT_PROTOCOLS, "-select_streams", "V:0"]
    cmd += ["-read_intervals", f"{max(0.0, info.duration - _TAIL_SECONDS):.3f}%"]
    cmd += ["-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", file_arg(input_path)]
    try:
        listing = run_ffmpeg(cmd, capture_stdout=True).decode("ascii", "replace")
    except BinaryNotFoundError:
        raise
    except ProcessingError:
        return 0
    hidden = 0
    for line in listing.splitlines():
        fields = line.strip().split(",")
        try:
            time = float(fields[0])
        except (ValueError, IndexError):
            continue
        if time >= 0 and "D" in (fields[1] if len(fields) > 1 else ""):
            hidden += 1
    return hidden


_TAIL_SECONDS = 3.0


def tail_text(frames: int) -> str:
    """``shows 2 frames the source keeps hidden at its end``. Pure."""
    return f"shows {frames} {'frame' if frames == 1 else 'frames'} the source keeps hidden at its end"


EDIT_LIST_CONTAINERS = frozenset({"mp4", "mov"})
"""Containers whose edit list can keep a source's hidden lead hidden in a copy."""


def _negative_ts(container: str, hidden: float) -> list[str]:
    """
    ``-avoid_negative_ts make_zero`` (the frames before a cut's start are really in the clip), but
    not for an MP4 or MOV copy of a source with a hidden lead (:func:`hidden_lead`) from its start:
    its edit list then keeps those frames hidden, as the source's does.
    """
    return [] if hidden > 0 and container in EDIT_LIST_CONTAINERS else ["-avoid_negative_ts", "make_zero"]


def revealed_text(seconds: float) -> str:
    """``shows 1.75 s of frames the source keeps hidden at its start``. Pure."""
    return f"shows {seconds:.2f} s of frames the source keeps hidden at its start"


def _copy_input(source: MediaInfo | None) -> list[str]:
    """Input options for a stream copy of `source`: generated pts for MPEG program streams (DVD .vob, .mpg)."""
    return ["-fflags", "+genpts"] if source is not None and source.container_names & GENERATED_PTS else []


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
    hidden: float = 0.0,
) -> list[str]:
    copy_audio = route == "copy"
    cmd = [ffmpeg, *base_args(1 if copy_audio else threads), *_progress_args(), *INPUT_PROTOCOLS, *_copy_input(source)]
    cmd += ["-t", fmt_seconds(t_value), "-i", file_arg(input_path)]
    audio = ["-an"] if config.mute else ["-map", "0:a:0?"]
    cmd += ["-map", "0:v:0", *audio, "-sn", "-dn", "-c:v", "copy"]
    if source is not None and source.video_codec == "hevc":
        cmd += ["-tag:v", "hvc1", "-strict", "unofficial"]
    if not config.mute:
        cmd += ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", f"{config.audio_bitrate_kbps}k"]
    if not copy_audio:
        cmd += ["-threads", str(threads)]
    cmd += [*_negative_ts("mp4", hidden), "-movflags", "+faststart"]
    cmd += metadata_args(config.metadata, source, _first_streams(source, config.mute))
    cmd += quicktime_tag_args(config.metadata, source, "mp4")
    return merged_movflags([*cmd, *OUTPUT_FLAGS, "-f", "mp4", file_arg(output_path)])


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
    decode_from: float | None = None,
    origin: float | None = None,
    browser_timing: bool = False,
    hidden: float = 0.0,
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

    `decode_from` is where decoding starts for a start time (seconds, from the file's keyframe
    index; see :func:`sashimi.core._seek.VideoIndex.decode_from`): the frames before the start
    are decoded and dropped (with ``trim`` before a GIF's or WebP's ``fps``, else with an output
    ``-ss``), counting from `origin` (:meth:`~sashimi.core._seek.VideoIndex.origin`; None:
    `decode_from`). None leaves the seek to ffmpeg (an input ``-ss``).

    `browser_timing` retimes an animated GIF's frames as browsers play them: a frame stored as
    shorter than :data:`~sashimi.config.MIN_FRAME_MS` lasts 100 ms (:data:`BROWSER_TIMING`).

    `hidden` is the source's hidden lead (:func:`hidden_lead`): an MP4 copy then keeps it hidden.
    """
    limits = resolve_limits(limits)
    limits.check(config)
    _check_start(config.start_seconds, duration)
    t_value = _length_limit(config.duration_seconds, config.start_seconds, duration, limits.max_video_seconds)
    if route != "encode":
        if not isinstance(config, Mp4VideoConfig) or config.start_seconds > 0:
            raise ValueError(f"route {route!r} is only for MP4 output from the start of the video")
        return _copy_command(
            input_path,
            output_path,
            config,
            route,
            t_value=t_value,
            threads=threads,
            ffmpeg=ffmpeg,
            source=source,
            hidden=hidden,
        )

    inputs, skip, audio_skip = _input_args(input_path, config, t_value, threads, decode_from, origin)
    if browser_timing:
        skip = f"{BROWSER_TIMING},{skip}" if skip else BROWSER_TIMING
    cmd = [ffmpeg, *base_args(threads), *_progress_args(), *inputs]
    if isinstance(config, GifVideoConfig):
        filters = with_filter(skip, _gif_filters(config, source))
        if palette is not None and two_pass(config):
            cmd += [*palette_input(palette), *palette_use(filters, _paletteuse(config))]
        else:
            cmd += ["-vf", one_pass(filters, config.palette_mode, _paletteuse(config))]
        cmd += ["-loop", "0" if config.loop else "-1", "-an"]
    elif isinstance(config, WebpVideoConfig):
        cmd += [
            "-vf",
            with_filter(
                skip,
                with_tone_map(f"fps={config.fps},{_animated_scale(config.max_width, source)}", tone_map_filter(source)),
            ),
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
        hdr = keeps_hdr(config, source)
        cmd += _audio_filters(config, source, audio_skip)
        cmd += [
            "-vf",
            with_filter(skip, _picture(scale, config, source)),
            *_stream_args(config.mute, hdr),
            *_encoder_args(config, audio_channels=_channels(source), hdr=hdr, container=config.target),
        ]
        cmd += metadata_args(config.metadata, source, _first_streams(source, config.mute))
        cmd += quicktime_tag_args(config.metadata, source, _MUXERS[config.target])
    cmd += ["-threads", str(threads), *OUTPUT_FLAGS, "-f", _MUXERS[config.target], file_arg(output_path)]
    return merged_movflags(cmd)


BROWSER_TIMING = "setpts='if(eq(N,0),PTS,PREV_OUTPTS+if(lt((PTS-PREV_INPTS)*TB,0.02),0.1/TB,PTS-PREV_INPTS))'"
"""
A filter that gives each frame the time browsers show it: a frame stored as shorter than 20 ms
(a GIF's 0 or 10 ms, "as fast as possible") lasts 100 ms, as every browser plays it.
"""


def two_pass(config: GifVideoConfig) -> bool:
    """
    Whether :func:`convert` builds this GIF's palette in a first pass of its own. Pure.

    A palette built from every frame ("diff", "full") is: building it in the same pass would hold
    every frame in memory until the end. A palette per frame ("single") streams in one pass.
    """
    return config.palette_mode != "single"


def _animated_scale(max_width: int, source: MediaInfo | None) -> str:
    """
    The scale filter of a GIF or animated WebP: at most `max_width` wide, keeping the shape. GIF
    and WebP have square pixels, so a video whose pixels aren't is scaled to its display shape.
    """
    if anamorphic(source):
        return f"scale='min({max_width},iw*sar)':'round(ow/dar/2)*2':flags=lanczos,setsar=1"
    return f"scale='min({max_width},iw)':-2:flags=lanczos"


def animated_size(source: MediaInfo, max_width: int) -> tuple[int, int]:
    """The size a GIF or animated WebP of `source` comes out at, at most `max_width` wide, in its display shape."""
    height = source.height or 0
    width = round(height * source.display_aspect_ratio) if source.display_aspect_ratio else source.width or 0
    if not width or not height:
        return width, height
    out_w = min(max_width, width)
    return out_w, max(2, round(out_w * height / width / 2) * 2)


def _gif_filters(config: GifVideoConfig, source: MediaInfo | None = None) -> str:
    return with_tone_map(f"fps={config.fps},{_animated_scale(config.max_width, source)}", tone_map_filter(source))


def _paletteuse(config: GifVideoConfig) -> str:
    options = ["new=1"] if config.palette_mode == "single" else []
    if config.dither == "bayer":
        options += ["dither=bayer", "bayer_scale=4"]
    else:
        options.append(f"dither={config.dither}")
    return ":".join(options)


def _input_args(
    input_path: PathLike,
    config: VideoConvertConfig,
    t_value: float,
    threads: int,
    decode_from: float | None,
    origin: float | None = None,
) -> tuple[list[str], str | None, str | None]:
    cmd = [*INPUT_PROTOCOLS, "-threads", str(threads)]
    if decode_from is None or config.start_seconds <= 0:
        if config.start_seconds > 0:
            cmd += ["-ss", fmt_seconds(config.start_seconds)]
        return [*cmd, "-t", fmt_seconds(t_value), "-i", file_arg(input_path)], None, None
    decoding = decoding_args(decode_from, config.start_seconds, t_value, origin)
    return [*cmd, *decoding.inputs, "-i", file_arg(input_path)], decoding.video, decoding.audio


def build_palette_command(
    input_path: PathLike,
    config: GifVideoConfig,
    *,
    duration: float,
    threads: int,
    ffmpeg: str = "ffmpeg",
    limits: Limits | None = None,
    decode_from: float | None = None,
    origin: float | None = None,
    source: MediaInfo | None = None,
    browser_timing: bool = False,
) -> list[str]:
    """
    Builds the first pass of a two-pass GIF (see :func:`two_pass`): the same frames as
    :func:`build_convert_command`'s, through ``palettegen``, written as one PNG on stdout, with
    each frame's time on stderr for progress. Pass its output to :func:`build_convert_command`
    as `palette`. Pure; raises the same user errors as :func:`build_convert_command`. `source`
    is the probed input, which :func:`build_convert_command` takes too (for the frames' shape).
    """
    limits = resolve_limits(limits)
    limits.check(config)
    _check_start(config.start_seconds, duration)
    t_value = _length_limit(config.duration_seconds, config.start_seconds, duration, limits.max_video_seconds)
    inputs, skip, _ = _input_args(input_path, config, t_value, threads, decode_from, origin)
    if browser_timing:
        skip = f"{BROWSER_TIMING},{skip}" if skip else BROWSER_TIMING
    cmd = [ffmpeg, *base_args(threads), *inputs]
    return [*cmd, "-an", *palette_output(with_filter(skip, _gif_filters(config, source)), config.palette_mode)]


def convert(
    input_path: PathLike,
    output_path: PathLike,
    config: VideoConvertConfig,
    *,
    max_workers: int | None = None,
    scratch_dir: PathLike | None = None,
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> VideoConversion:
    """
    Converts a video (or animated GIF) to GIF, animated WebP, WebM or MP4, as `config` says (see
    :data:`~sashimi.config.VIDEO_CONVERT`), and returns the route taken and what the output kept
    (:class:`VideoConversion`).

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
    info = _probe_video(input_path, limits)
    assert info.duration is not None
    if isinstance(config, WebpVideoConfig):
        check_webp_size(*animated_size(info, config.max_width))
    remaining = info.duration - config.start_seconds
    expected = min(config.duration_seconds or remaining, remaining)
    ffmpeg = ffmpeg_binary()
    if config.start_seconds > 0:
        require_behaviour("start times", ffmpeg)
    if not keeps_hdr(config, info):
        require_tone_mapping(info, ffmpeg)
    animated = isinstance(config, (GifVideoConfig, WebpVideoConfig))
    index = _start_index(input_path, info, config.start_seconds, limits)
    decode_from = index.start_for(config.start_seconds) if index is not None else None
    with_audio = not animated and not getattr(config, "mute", False)
    retime = "gif" in info.container_names and short_gif_frames(input_path)
    origin = index.origin(decode_from, audio=with_audio) if index is not None and decode_from is not None else None

    def run(route: VideoRoute) -> VideoRoute:
        palette = None
        report = on_progress
        if isinstance(config, GifVideoConfig) and two_pass(config):
            first = build_palette_command(
                input_path,
                config,
                duration=info.duration or 0.0,
                threads=threads,
                ffmpeg=ffmpeg,
                limits=limits,
                decode_from=decode_from,
                origin=origin,
                source=info,
                browser_timing=retime,
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
                decode_from=decode_from,
                origin=origin,
                browser_timing=retime,
                hidden=hidden_lead(input_path) if route != "encode" else 0.0,
            )
            run_ffmpeg(cmd, progress_total=expected, on_progress=report, stage="encoding")
            if config.start_seconds > 0 and not animated:
                _require_video(temp, input_path, config.start_seconds, ffmpeg, "convert", index)
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
            if refusal := _nothing_from(input_path, config.start_seconds, ffmpeg, "convert", index):
                raise refusal from e
            if not _frames_after(input_path, config.start_seconds, ffmpeg, fps=config.fps, index=index):
                raise _too_close_to_the_end(config.start_seconds, config.fps) from e
        if planned == "encode":
            raise
        logger.info(f"Copying into MP4 failed, re-encoding instead: {str(e).splitlines()[0]}")
        taken = run("encode")
    told = _conversion_fidelity(input_path, output_path, config, info, taken)
    if on_progress:
        on_progress(1.0, "done")
    return VideoConversion(route=taken, fidelity=told)


def _conversion_fidelity(
    input_path: PathLike, output_path: PathLike, config: VideoConvertConfig, info: MediaInfo, route: VideoRoute
) -> Fidelity:
    if isinstance(config, (GifVideoConfig, WebpVideoConfig)):
        return _animated_fidelity(input_path, config, info)
    if route == "encode" or not isinstance(config, Mp4VideoConfig):
        return _reencode_fidelity(input_path, output_path, config, info)
    changed = [] if route == "copy" else _encoded(config, info)[-1:]
    if config.duration_seconds is None and (tail := hidden_tail(input_path, info)):
        changed.append(tail_text(tail))
    data = [index for index, stream in enumerate(info.streams) if stream.type == "data"]
    return _copy_fidelity(
        input_path,
        output_path,
        info,
        config.metadata,
        CopyPlan(),
        "mp4",
        changed=changed,
        muted=config.mute,
        forced=data,
    )


def _unfit(info: MediaInfo, container: str, skip: frozenset[int] = frozenset()) -> list[int]:
    """The streams of `info` (but those in `skip`) that `container` can't hold as they are."""
    allowed, refused = _COPY_ALLOWED[container], _COPY_REFUSED[container]
    unfit = []
    for index, stream in enumerate(info.streams):
        if index in skip:
            continue
        if stream.type == "subtitle" and stream.codec in TEXT_SUBTITLES:
            continue
        if container == "mov" and _unwritable_data(stream):
            unfit.append(index)
            continue
        codecs = allowed.get(stream.type, frozenset())
        if stream.type not in allowed or stream.codec in refused or (codecs is not None and stream.codec not in codecs):
            unfit.append(index)
    return unfit


_UNKNOWN_CODECS = frozenset({"unknown", "none", ""})


def _unwritable_data(stream: StreamInfo) -> bool:
    """
    A data track FFmpeg can't write into MOV as it is: one whose codec it doesn't know (Apple's
    ``mebx`` metadata tracks, a GoPro's ``fdsc``), which it would write under a made-up sample
    description that no reader can use. Timecode tracks are rebuilt by the muxer, so they pass.
    """
    tag = (stream.codec_tag or "").strip().lower()
    return stream.type == "data" and stream.codec in _UNKNOWN_CODECS and tag != "tmcd"


def _can_copy(info: MediaInfo, container: str, skip: frozenset[int] = frozenset()) -> bool:
    return not _unfit(info, container, skip)


_DATA_TRACKS = {
    "gpmd": "the GoPro telemetry track (GPS)",
    "tmcd": "the timecode track",
    "fdsc": "the GoPro file-description track",
    "mebx": "the Apple metadata track",
    "camm": "the camera-motion track",
    "rtmd": "the Sony real-time metadata track",
}


def _track_name(stream: StreamInfo) -> str:
    """A data track, named for people: ``the GoPro telemetry track (GPS)``."""
    tag = (stream.codec_tag or "").strip().lower()
    if tag in _DATA_TRACKS:
        return _DATA_TRACKS[tag]
    handler = next((value for key, value in stream.tags.items() if key.lower() == "handler_name"), "").strip()
    return f"a data track ({handler or tag or stream.codec})"


def _and(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def dropped_tracks(names: Sequence[str]) -> str:
    """``the timecode track and the GoPro telemetry track (GPS)``, with each name counted once: ``3 Apple metadata tracks``."""
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    words = []
    for name, count in counts.items():
        if count == 1:
            words.append(name)
        else:
            plain = name.removeprefix("the ").removeprefix("a ")
            head, _, rest = plain.partition(" track")
            words.append(f"{count} {head} tracks{rest}")
    return _and(words)


class CopyPlan(BaseModel):
    """What a lossless copy into a container leaves out (see :func:`plan_copy`)."""

    model_config = ConfigDict(frozen=True)

    dropped: list[int] = []
    """Source streams left out because the container can't hold them: data tracks only."""
    dropped_names: list[str] = []
    """
    Those streams, named for people (``the GoPro telemetry track (GPS)``), in stream order. A
    timecode track isn't named when the video stream states its timecode, which the copy keeps
    (MP4 and MOV rebuild the track from it; MKV keeps it as a tag).
    """

    def note(self, container: str) -> str | None:
        """
        Why the copy dropped tracks, or None when it dropped none: ``FFmpeg can't copy the GoPro
        telemetry track (GPS) into MP4``. The source may well be an MP4 holding the track: what
        can't carry it is FFmpeg's stream copy into that container. MOV keeps the data tracks
        whose codec FFmpeg knows (a GoPro's telemetry) and timecodes; others (Apple's metadata
        tracks) FFmpeg could only write under a made-up sample description, so they are left out.
        """
        if not self.dropped_names:
            return None
        return f"FFmpeg can't copy {dropped_tracks(self.dropped_names)} into {container.upper()}"


class TrimResult(CopyPlan, Outcome):
    """What :func:`trim` wrote: what a copy left out (:class:`CopyPlan`), and what the clip kept (``fidelity``)."""


def _mode_removals(info: MediaInfo, mode: MetadataMode, forced: Collection[int] = ()) -> list[str]:
    """
    The tracks a ``metadata`` mode leaves out of a copy, named as
    :func:`~sashimi.core.fidelity.media_inventory` names them, but those in `forced`: tracks the
    container can't hold, which are dropped whatever was asked (see :class:`CopyPlan`).
    """
    names = []
    for index in dropped_streams(info, mode):
        if index in forced:
            continue
        stream = info.streams[index]
        if stream.attached_pic:
            names.append("cover art")
        elif stream.type == "data" and not stream.chapter_track:
            names.append(_track_name(stream).removeprefix("the "))
    return names


def _timecode_kept(info: MediaInfo) -> list[str]:
    """The timecode track, when the video's tags keep the timecode (MP4 and MOV rebuild the track; MKV keeps the tag)."""
    tagged = any(key.lower() == "timecode" for s in info.streams if s.type == "video" for key in s.tags)
    return ["timecode track"] if tagged else []


_CODEC_NAMES = {"aac": "AAC", "libopus": "Opus"}


def _encoded(config: _H264Options | _Vp9Options, info: MediaInfo, *, audio: bool = True) -> list[str]:
    """How a re-encode with `config` changes `info`'s picture and sound, for people."""
    hdr = keeps_hdr(config, info)
    if isinstance(config, _Vp9Options):
        codec = "10-bit VP9" if hdr else "VP9"
    else:
        codec = "10-bit HEVC" if hdr else "H.264"
    changes = [f"re-encoded as {codec} (CRF {config.crf})"]
    if is_hdr(info) and not hdr:
        changes.append(f"tone-mapped from {hdr_name(info)}")
    width = getattr(config, "max_width", None)
    if width is not None and info.width and width < info.width:
        changes.append(f"scaled to {width} px wide")
    if audio and not config.mute and info.has_audio:
        kbps = opus_kbps(config.audio_bitrate_kbps, info.audio_channels) if isinstance(config, _Vp9Options) else None
        name = "Opus" if isinstance(config, _Vp9Options) else "AAC"
        changes.append(f"sound re-encoded as {name} at {kbps or config.audio_bitrate_kbps} kb/s")
    return changes


def _content(config: object, info: MediaInfo) -> Literal["picture", "picture and sound"]:
    return "picture and sound" if info.has_audio and not getattr(config, "mute", False) else "picture"


def _reencode_fidelity(
    input_path: PathLike, output_path: PathLike, config: _H264Options | _Vp9Options, info: MediaInfo
) -> Fidelity:
    """What a re-encoded video (convert, accurate cut, rotate) kept: the encode, then the tracks and tags it kept."""
    muted = ["sound"] if config.mute and info.has_audio else []
    return media_fidelity(
        input_path,
        output_path,
        _content(config, info),
        changed=_encoded(config, info),
        removed_metadata=config.metadata,
        removed=muted,
        kept=["display rotation", *_timecode_kept(info)],
    )


def _animated_fidelity(input_path: PathLike, config: GifVideoConfig | WebpVideoConfig, info: MediaInfo) -> Fidelity:
    """What a GIF or animated WebP made from a video kept: a new picture, so always lossy."""
    if isinstance(config, GifVideoConfig):
        changes = [f"made into a GIF at {config.fps} fps (256 colours)"]
    elif config.lossless:
        changes = [f"made into an animated WebP at {config.fps} fps"]
    else:
        changes = [f"made into an animated WebP at {config.fps} fps, quality {config.quality}"]
    width, height = animated_size(info, config.max_width)
    if info.height and height < info.height:
        changes.append(f"scaled to {width}x{height}")
    if is_hdr(info):
        changes.append(f"tone-mapped from {hdr_name(info)}")
    told = derived(input_path, "picture", changes, media=True)
    if info.has_audio:
        told = make_fidelity("picture", changed=told.changed, lost=[*told.lost, "sound"])
    return told


def _copy_fidelity(
    input_path: PathLike,
    output_path: PathLike,
    info: MediaInfo,
    mode: MetadataMode,
    plan: CopyPlan,
    container: str,
    *,
    changed: Sequence[str] = (),
    muted: bool = False,
    adjusted: Sequence[str] = (),
    forced: Collection[int] = (),
) -> Fidelity:
    """
    What a stream copy kept: the picture and sound as they were, then the tracks and tags (see
    :class:`CopyPlan`). `adjusted` says what was set as asked without changing them (a display
    rotation); `forced` lists streams the copy leaves out whatever was asked (a conversion keeps
    only the first video and audio), which are never said to be removed as asked.
    """
    note = plan.note(container)
    codecs = [stream.codec for stream in info.streams if stream.type == "subtitle"]
    converted = sorted({c for c in codecs if not _subtitle_fits(c, container) and c in TEXT_SUBTITLES})
    names = {
        "ass": "ASS",
        "ssa": "SSA",
        "subrip": "SubRip",
        "srt": "SubRip",
        "mov_text": "MP4 text",
        "webvtt": "WebVTT",
    }
    target = names.get(SUBTITLE_ENCODERS[container], SUBTITLE_ENCODERS[container])
    shown = sorted({names.get(codec, codec) for codec in converted})
    lost = [f"the subtitles' {_and(shown)} format (converted to {target})"] if converted else []
    return media_fidelity(
        input_path,
        output_path,
        "picture" if muted or not info.has_audio else "picture and sound",
        changed=changed,
        removed_metadata=mode,
        removed=[
            *_mode_removals(info, mode, [*plan.dropped, *forced]),
            *(["sound"] if muted and info.has_audio else []),
        ],
        kept=[*_timecode_kept(info), *(["display rotation"] if adjusted else [])],
        notes=[note] if note else [],
        lost=lost,
        adjusted=adjusted,
    )


def _what(stream: StreamInfo) -> str:
    return (
        f"{stream.codec.upper()} {stream.type}"
        if stream.type in ("video", "audio")
        else f"{stream.codec} {stream.type}"
    )


def plan_copy(info: MediaInfo, container: str, metadata: MetadataMode = "keep", *, action: str = "copied") -> CopyPlan:
    """
    How the streams of `info` are copied into `container` (mkv, mp4, webm or mov) without
    re-encoding: data tracks the container can't hold (a timecode, a GoPro's telemetry and GPS,
    Apple's metadata tracks) are left out and named, and anything else it can't hold (video,
    audio or subtitles, e.g. H.264 into WebM) raises InvalidOptionsError naming the stream and
    a container that holds it. Text subtitles always pass: the copy converts any the container
    can't hold (see :func:`subtitle_conversions`). Streams a `metadata` mode drops (data streams
    unless ``keep``, cover art for ``remove_all``) don't count, except that data tracks the
    container can't hold are named as dropped under ``remove_location`` too: only the location
    was asked for, so they are dropped because of the container, not as asked. `action` words the refusal:
    ``trimmed`` for a cut, ``copied`` for a whole-file remux. Pure.
    """
    if container in ("mkv", "webm") and info.color_space == "ipt-c2":
        raise InvalidOptionsError(
            f"{DOLBY_VISION_COLOURS} {container.upper()} can't carry the Dolby Vision settings that processing needs, "
            "so copy it into MP4 or MOV instead.",
            detail="color_space=ipt-c2",
        )
    skip = frozenset(dropped_streams(info, metadata))
    asked = frozenset(dropped_streams(info, "keep")) if metadata == "remove_location" else skip
    unfit = _unfit(info, container, skip)
    data = [index for index in _unfit(info, container, asked) if info.streams[index].type == "data"]
    blocking = [index for index in unfit if index not in data]
    if blocking:
        everything = skip | frozenset(data)
        others = [c for c in ("mkv", "mp4", "mov", "webm") if c != container and _can_copy(info, c, everything)]
        streams = _and([f"its {_what(info.streams[index])}" for index in blocking])
        hint = (
            f" Try {others[0].upper()} instead, or convert it (which re-encodes)."
            if others
            else (" Convert it instead (which re-encodes).")
        )
        raise InvalidOptionsError(
            f"This video can't be {action} into {container.upper()} without re-encoding: {container.upper()} can't "
            f"hold {streams}.{hint}",
            detail=", ".join(f"{s.type}:{s.codec}" for s in info.streams),
        )
    kept_timecode = any(
        key.lower() == "timecode" for stream in info.streams if stream.type == "video" for key in stream.tags
    )
    names = [
        _track_name(info.streams[index])
        for index in data
        if not (kept_timecode and (info.streams[index].codec_tag or "").lower() == "tmcd")
    ]
    return CopyPlan(dropped=data, dropped_names=names)


def check_copy_compatible(info: MediaInfo, container: str, metadata: MetadataMode = "keep") -> None:
    """
    Raises InvalidOptionsError if the streams of `info` can't be copied into `container` (mkv,
    mp4, webm or mov) without re-encoding, e.g. H.264 into WebM. Data tracks the container can't
    hold don't count: a copy leaves them out (see :func:`plan_copy`).
    """
    plan_copy(info, container, metadata)


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
    decode_from: float | None = None,
    origin: float | None = None,
    drop: Sequence[int] = (),
    hidden: float = 0.0,
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

    `decode_from` is where reading starts for a start time (seconds, from the file's keyframe
    index): for an accurate cut, decoding starts there and the frames before the start are
    dropped with an output ``-ss``; a copy starts there, just before the keyframe it keeps, and
    its length is counted from the start time. None leaves the seek to ffmpeg. `origin` is where an
    accurate cut's timestamps start (:meth:`~sashimi.core._seek.VideoIndex.origin`; None: `decode_from`).
    `drop` are source streams a copy leaves out besides those `metadata` drops: the data tracks
    the container can't hold (:attr:`CopyPlan.dropped`). `hidden` is the source's hidden lead
    (:func:`hidden_lead`) when the copy starts at the file's start: an MP4 or MOV copy then keeps
    it hidden behind an edit list, as the source does (no ``make_zero``). HEVC copied into MP4 or
    MOV gets ``-strict unofficial``, so an MP4 keeps its Dolby Vision configuration (``dvcC``);
    FFmpeg's MOV muxer (8.1 and 9.0) doesn't write one, and the fidelity says it was dropped.
    """
    limits = resolve_limits(limits)
    limits.check(config)
    _check_start(config.start_seconds, duration)
    length = config.length
    start = config.start_seconds
    early = start - decode_from if decode_from is not None and start > 0 else 0.0

    if isinstance(config, CopyTrimConfig):
        cmd = [ffmpeg, *base_args(1), *_progress_args(), *INPUT_PROTOCOLS, *_copy_input(source)]
        if decode_from is not None and start > 0:
            if decode_from > 0:
                cmd += ["-ss", fmt_seconds(decode_from)]
        elif start > 0:
            cmd += ["-ss", fmt_seconds(start)]
        if length is not None:
            cmd += ["-t", fmt_seconds(length + early)]
        cmd += ["-i", file_arg(input_path), "-map", "0"]
        dropped = sorted(set(dropped_streams(source, config.metadata)) | set(drop))
        for index in dropped:
            cmd += ["-map", f"-0:{index}"]
        cmd += ["-c", "copy"]
        cmd += [*subtitle_conversions(subtitles, config.container), *_negative_ts(config.container, hidden)]
        if config.container in ("mp4", "mov"):
            cmd += ["-movflags", "+faststart"]
            if source is not None and source.video_codec == "hevc":
                cmd += ["-strict", "unofficial"]
        kept = [index for index in range(len(source.streams)) if index not in dropped] if source else []
        cmd += metadata_args(config.metadata, source, kept)
        cmd += quicktime_tag_args(config.metadata, source, _MUXERS[config.container])
        cmd += [*OUTPUT_FLAGS, "-f", _MUXERS[config.container], file_arg(output_path)]
        return merged_movflags(cmd)

    t_value = _length_limit(length, config.start_seconds, duration, limits.max_video_seconds)
    cmd = [ffmpeg, *base_args(threads), *_progress_args(), *INPUT_PROTOCOLS, "-threads", str(threads)]
    scale = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    if decode_from is not None and start > 0:
        decoding = decoding_args(decode_from, start, t_value, origin)
        cmd += [*decoding.inputs, "-i", file_arg(input_path)]
        cmd += _audio_filters(config, source, decoding.audio)
        scale = with_filter(decoding.video, scale)
    else:
        if start > 0:
            cmd += ["-ss", fmt_seconds(start)]
        cmd += ["-t", fmt_seconds(t_value), "-i", file_arg(input_path)]
        cmd += _audio_filters(config, source)
    hdr = keeps_hdr(config, source)
    cmd += ["-vf", _picture(scale, config, source), *_stream_args(config.mute, hdr)]
    cmd += _encoder_args(
        config,
        faststart=config.container in ("mp4", "mov"),
        audio_channels=_channels(source),
        hdr=hdr,
        container=config.container,
    )
    cmd += metadata_args(config.metadata, source, _first_streams(source, config.mute))
    cmd += quicktime_tag_args(config.metadata, source, _MUXERS[config.container])
    cmd += ["-threads", str(threads), *OUTPUT_FLAGS, "-f", _MUXERS[config.container], file_arg(output_path)]
    return merged_movflags(cmd)


def trim(
    input_path: PathLike,
    output_path: PathLike,
    config: TrimConfig,
    *,
    max_workers: int | None = None,
    scratch_dir: PathLike | None = None,
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> TrimResult:
    """
    Cuts a section of a video (see :data:`~sashimi.config.VIDEO_TRIM`), and returns what a copy
    left out (:class:`CopyPlan`; nothing for an accurate cut) and what the clip kept (``fidelity``).

    A :class:`~sashimi.config.CopyTrimConfig` copies streams: fast and lossless, but the cut starts
    at the keyframe at or before `start_seconds`, so the clip can begin early
    (:data:`~sashimi.config.KEYFRAME_NOTE` explains this for end users). The source's streams must
    fit the container, or InvalidOptionsError is raised before ffmpeg runs; data tracks it can't
    hold (a timecode, a GoPro's telemetry and GPS, Apple's metadata tracks) are left out instead,
    and the result names them (see :func:`plan_copy`). An accurate config
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
    copy = isinstance(config, CopyTrimConfig)
    info = _probe_video(input_path, limits, decoding=not copy)
    assert info.duration is not None
    _check_start(config.start_seconds, info.duration)
    plan = CopyPlan()
    if copy:
        whole = config.start_seconds <= 0 and config.length is None
        plan = plan_copy(info, config.container, config.metadata, action="copied" if whole else "trimmed")
    ffmpeg = ffmpeg_binary()
    start = config.start_seconds
    if copy and (start > 0 or config.length is not None):
        require_behaviour("lossless cuts", ffmpeg)
    elif not copy and start > 0:
        require_behaviour("start times", ffmpeg)
    if copy and start > 0 and not _frames_after(input_path, start, ffmpeg, copied=True):
        raise _after_last_frame(start, "cut")
    index = None
    decode_from = None
    origin = None
    names = info.container_names
    if not copy or names & (FORWARD_SEEKING | SHOWN_SEEKING):
        index = _start_index(input_path, info, start, limits)
    if index is not None:
        if copy:
            decode_from = index.copy_from(start, at_shown=not names & FORWARD_SEEKING)
        else:
            decode_from = index.start_for(start)
            if decode_from is not None:
                origin = index.origin(decode_from, audio=not getattr(config, "mute", False))
    threads = 1 if copy else resolve_max_workers(max_workers)
    hidden = hidden_lead(input_path) if copy else 0.0
    if not copy and not keeps_hdr(config, info):
        require_tone_mapping(info, ffmpeg)
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
            decode_from=decode_from,
            origin=origin,
            drop=plan.dropped,
            hidden=hidden,
        )
        if copy:
            run_ffmpeg(cmd)
            if start > 0 and decode_from is None and not holds_packets(temp, "video"):
                retry = _seek_to_keyframe(input_path, info, start, limits)
                if retry is not None:
                    decode_from = retry
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
                        decode_from=decode_from,
                        drop=plan.dropped,
                        hidden=hidden,
                    )
                    run_ffmpeg(cmd)
        else:
            remaining = info.duration - start
            expected = min(config.length or remaining, remaining)
            run_ffmpeg(cmd, progress_total=expected, on_progress=on_progress, stage="encoding")
        if start > 0:
            _require_video(temp, input_path, start, ffmpeg, "cut", None if copy else index)
    if isinstance(config, CopyTrimConfig):
        from_start = start <= 0 or decode_from == 0.0
        shown = hidden if from_start and config.container not in EDIT_LIST_CONTAINERS else 0.0
        changed = [f"{revealed_text(shown)}, which {config.container.upper()} can't hide"] if shown else []
        to_end = config.length is None or start + config.length >= info.duration
        if to_end and (tail := hidden_tail(input_path, info)):
            changed.append(tail_text(tail))
        told = _copy_fidelity(input_path, output_path, info, config.metadata, plan, config.container, changed=changed)
    else:
        told = _reencode_fidelity(input_path, output_path, config, info)
    if on_progress:
        on_progress(1.0, "done")
    return TrimResult(dropped=plan.dropped, dropped_names=plan.dropped_names, fidelity=told)


def _seek_to_keyframe(input_path: PathLike, info: MediaInfo, start: float, limits: Limits | None) -> float | None:
    """
    Where a stream copy from `start` should seek when ffmpeg's own seek left it without a video
    frame (a WebM with a single keyframe, whose packets after the seek ffmpeg drops as they aren't
    keyframes): the keyframe at or before `start`, from the file's index. None when there is none.
    """
    index = _start_index(input_path, info, start, limits)
    if index is None or not index.keys:
        return None
    return index.copy_from(start, at_shown=True)


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
    vf = _picture(",".join([*_transform_filters(config), "scale=trunc(iw/2)*2:trunc(ih/2)*2"]), config, source)
    hdr = keeps_hdr(config, source)
    return merged_movflags(
        [
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
            *_audio_filters(config, source),
            "-vf",
            vf,
            *_stream_args(config.mute, hdr),
            *_encoder_args(config, audio_channels=_channels(source), hdr=hdr, container=config.target),
            *metadata_args(config.metadata, source, _first_streams(source, config.mute)),
            *quicktime_tag_args(config.metadata, source, _MUXERS[config.target]),
            "-threads",
            str(threads),
            *OUTPUT_FLAGS,
            "-f",
            _MUXERS[config.target],
            file_arg(output_path),
        ]
    )


RotationRoute = Literal["display", "encode"]
"""
How :func:`transform` rotates a video: ``display`` copies every stream and sets the display
rotation (lossless; the picture's pixels are unchanged and players turn it), ``encode``
re-encodes the turned picture.
"""
DISPLAY_ROTATION_CODECS = frozenset({"h264", "hevc", "av1", "vp9"})
"""Video codecs a lossless rotation copies into MP4: those MP4 players play, so the result plays wherever the source did."""
DISPLAY_ROTATION_AUDIO = frozenset({"aac", "mp3", "opus", "flac", "alac", "ac3", "eac3"})
"""
Audio a lossless rotation copies into MP4; other audio is re-encoded as AAC, as browsers play
it, except PCM that was already in an MP4 (``ipcm``/``fpcm``), which is copied as it is.
"""
MOV_ROTATION_CODECS = frozenset({"h264", "hevc", "prores"})
"""Video codecs a lossless rotation copies into MOV: those QuickTime plays, so the result plays wherever the source did."""
_ISO_PCM = frozenset({"ipcm", "fpcm"})
_DISPLAY_CONFIGS = (Mp4TransformConfig, MovTransformConfig)
DisplayConfig = Mp4TransformConfig | MovTransformConfig
_TRANSFORM_DEFAULTS = Mp4TransformConfig(rotate=90)


def plan_rotation(info: MediaInfo, config: TransformConfig) -> RotationRoute:
    """
    How :func:`transform` turns `info`'s video for `config` (see :data:`RotationRoute`). Pure.

    ``display`` when the output is MP4 or MOV, the config asks only for a rotation (no flip, no
    ``reencode``, ``crf`` and ``preset`` at their defaults, which a re-encode would use), the
    source isn't mirrored by its display matrix, its video is one the container plays as it is
    (MP4: H.264, HEVC, AV1 or VP9; MOV: H.264, HEVC or ProRes), and every other stream fits the
    container (data tracks it can't hold are left out and named, as remux does; sound it can't
    hold is re-encoded as AAC).
    """
    if not isinstance(config, _DISPLAY_CONFIGS) or config.reencode or config.flip != "none":
        return "encode"
    if config.crf != _TRANSFORM_DEFAULTS.crf or config.preset != _TRANSFORM_DEFAULTS.preset:
        return "encode"
    codecs = MOV_ROTATION_CODECS if config.target == "mov" else DISPLAY_ROTATION_CODECS
    if info.display_mirrored or info.video_codec not in codecs or not info.width:
        return "encode"
    audio = frozenset(index for index, stream in enumerate(info.streams) if stream.type == "audio")
    skip = frozenset(dropped_streams(info, config.metadata)) | audio
    unfit = [index for index in _unfit(info, config.target, skip) if info.streams[index].type != "data"]
    return "encode" if unfit else "display"


def rotation_container(info: MediaInfo, mp4: Mp4TransformConfig, mov: MovTransformConfig) -> Literal["mp4", "mov"]:
    """
    Which container keeps more of a MOV source `info` on rotation, given the same options as `mp4`
    and `mov` (pure): MOV, the source's own, unless only MP4 rotates it losslessly, or both do and
    MP4 keeps something MOV can't while copying the sound as it is: FFmpeg writes a video's Dolby
    Vision configuration into MP4 but not MOV, so an iPhone HDR clip with AAC sound stays whole in
    MP4. Sound MP4 would re-encode (PCM) outweighs the Dolby Vision settings: MOV keeps the
    picture and sound identical either way.
    """
    into_mov, into_mp4 = plan_rotation(info, mov) == "display", plan_rotation(info, mp4) == "display"
    if into_mp4 and not into_mov:
        return "mp4"
    if into_mp4 and into_mov and info.dolby_vision and not _reencoded_audio(info, mp4):
        return "mp4"
    return "mov"


def turned(info: MediaInfo, rotate: int) -> int:
    """The clockwise display rotation after turning `info`'s video `rotate` degrees clockwise. Pure."""
    return (info.display_turn + rotate) % 360


def _copied_audio(stream: StreamInfo, container: str) -> bool:
    """Whether a lossless rotation into `container` copies this audio stream as it is."""
    if container == "mov":
        return stream.codec not in _COPY_REFUSED["mov"]
    return stream.codec in DISPLAY_ROTATION_AUDIO or (
        stream.codec.startswith("pcm_") and (stream.codec_tag or "").strip().lower() in _ISO_PCM
    )


def _reencoded_audio(info: MediaInfo, config: DisplayConfig) -> bool:
    """
    Whether a lossless rotation re-encodes the sound: audio the container can't hold as it is
    (see :func:`_copied_audio`), or a bitrate other than the default.
    """
    if config.mute:
        return False
    audio = [stream for stream in info.streams if stream.type == "audio"]
    return bool(audio) and (
        any(not _copied_audio(stream, config.target) for stream in audio)
        or config.audio_bitrate_kbps != _TRANSFORM_DEFAULTS.audio_bitrate_kbps
    )


def build_rotation_command(
    input_path: PathLike,
    output_path: PathLike,
    config: DisplayConfig,
    *,
    source: MediaInfo,
    ffmpeg: str = "ffmpeg",
    drop: Sequence[int] = (),
    hidden: float = 0.0,
) -> list[str]:
    """
    The command for :func:`transform`'s ``display`` route: every stream copied into MP4 or MOV
    (``config.target``) with its display rotation set to :func:`turned` (``-display_rotation``
    counts counter-clockwise), the sound re-encoded as AAC only when :func:`_reencoded_audio`
    says so, subtitles converted to MP4 text, data tracks in `drop` left out, a hidden lead kept
    hidden (`hidden`, see :func:`hidden_lead`). Pure.
    """
    container = config.target
    turn = turned(source, config.rotate)
    audio = _reencoded_audio(source, config)
    cmd = [ffmpeg, *base_args(1), *_progress_args(), *INPUT_PROTOCOLS, *_copy_input(source)]
    cmd += ["-display_rotation", str((360 - turn) % 360), "-i", file_arg(input_path), "-map", "0"]
    dropped = sorted(set(dropped_streams(source, config.metadata)) | set(drop))
    for index in dropped:
        cmd += ["-map", f"-0:{index}"]
    if config.mute:
        cmd += ["-map", "-0:a"]
    cmd += ["-c", "copy"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", f"{config.audio_bitrate_kbps}k"]
    subtitles = [stream.codec for stream in source.streams if stream.type == "subtitle"]
    cmd += [*subtitle_conversions(subtitles, container), *_negative_ts(container, hidden), "-movflags", "+faststart"]
    if source.video_codec == "hevc":
        cmd += ["-tag:v", "hvc1", "-strict", "unofficial"]
    kept = [
        index
        for index, stream in enumerate(source.streams)
        if index not in dropped and not (config.mute and stream.type == "audio")
    ]
    cmd += metadata_args(config.metadata, source, kept)
    cmd += quicktime_tag_args(config.metadata, source, _MUXERS[container])
    return merged_movflags([*cmd, *OUTPUT_FLAGS, "-f", _MUXERS[container], file_arg(output_path)])


def _rotation_text(before: int, after: int) -> str:
    if not before:
        return f"display rotation set to {after}° clockwise"
    if not after:
        return f"display rotation of {before}° removed"
    return f"display rotation changed from {before}° to {after}° clockwise"


class TransformResult(Outcome):
    """What :func:`transform` wrote: the route it took, and what the output kept of the source (``fidelity``)."""

    route: RotationRoute
    """``display`` (lossless, display rotation set) or ``encode`` (see :data:`RotationRoute`)."""


def transform(
    input_path: PathLike,
    output_path: PathLike,
    config: TransformConfig,
    *,
    max_workers: int | None = None,
    scratch_dir: PathLike | None = None,
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> TransformResult:
    """
    Rotates and/or flips a video (see :data:`~sashimi.config.VIDEO_TRANSFORM`), the route chosen
    by :func:`plan_rotation`. A rotation alone into MP4 of H.264, HEVC, AV1 or VP9 video, or into
    MOV of H.264, HEVC or ProRes video, is lossless: every stream is copied, as :func:`trim`
    remuxes, and only the display rotation is set, composed with the source's (an iPhone portrait
    clip turned 90° shows at 180°; turned back to upright, the rotation is removed); sound MP4
    players don't all play (PCM from a MOV) is re-encoded as AAC in an MP4 and copied in a MOV,
    and HDR stays HDR, as in any copy. It has no length limit. Everything else (a flip,
    ``reencode``, WebM, other codecs) re-encodes the turned picture as `config.target`, the
    source's display rotation applied first; such a video longer than ``limits.max_video_seconds``
    raises MediaTooLongError. Tags follow ``metadata``, as in :func:`convert`.
    """
    threads = resolve_max_workers(max_workers)
    info = _probe_video(input_path, limits, decoding=False)
    assert info.duration is not None
    ffmpeg = ffmpeg_binary()
    route = plan_rotation(info, config)
    if route == "display":
        assert isinstance(config, _DISPLAY_CONFIGS)
        plan = plan_copy(info, config.target, config.metadata)
        hidden = hidden_lead(input_path)
        with atomic_output(output_path, scratch_dir) as temp:
            cmd = build_rotation_command(
                input_path, temp, config, source=info, ffmpeg=ffmpeg, drop=plan.dropped, hidden=hidden
            )
            run_ffmpeg(cmd, progress_total=info.duration, on_progress=on_progress, stage="copying")
        changed = _encoded(config, info)[-1:] if _reencoded_audio(info, config) else []
        if tail := hidden_tail(input_path, info):
            changed.append(tail_text(tail))
        told = _copy_fidelity(
            input_path,
            output_path,
            info,
            config.metadata,
            plan,
            config.target,
            changed=changed,
            muted=config.mute,
            adjusted=[_rotation_text(info.display_turn, turned(info, config.rotate))],
        )
    else:
        check_decodable(info)
        if not keeps_hdr(config, info):
            require_tone_mapping(info, ffmpeg)
        _log_opus_cap(config, info)
        with atomic_output(output_path, scratch_dir) as temp:
            cmd = build_transform_command(
                input_path,
                temp,
                config,
                duration=info.duration,
                threads=threads,
                ffmpeg=ffmpeg,
                limits=limits,
                source=info,
            )
            run_ffmpeg(cmd, progress_total=info.duration, on_progress=on_progress, stage="encoding")
        told = _reencode_fidelity(input_path, output_path, config, info)
    if on_progress:
        on_progress(1.0, "done")
    return TransformResult(route=route, fidelity=told)
