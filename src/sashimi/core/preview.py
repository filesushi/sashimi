# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Video previews: short clips sampled from across a video, joined into one GIF or animated WebP."""

import logging
from collections.abc import Iterator

from sashimi.config import MAX_FRAME_BYTES, GifPreviewConfig, PreviewConfig
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
    stream_ffmpeg,
)
from sashimi.core._resources import resolve_max_workers
from sashimi.core.sheets._layout import sample_window
from sashimi.core.video import _probe_video
from sashimi.errors import (
    BinaryNotFoundError,
    InvalidOptionsError,
    MediaTooShortError,
    ProcessingError,
    UnsupportedMediaError,
)

__all__ = ["plan_preview", "preview_size", "build_preview_commands", "preview"]

logger = logging.getLogger("sashimi")

MIN_PREVIEW_SECONDS = 0.5
TOO_SHORT_FOR_PREVIEW = "This video is too short for a preview."
TOO_BIG_PREVIEW = (
    "These settings would make the preview too large to render as a GIF. Use a smaller size, a "
    "lower frame rate, or fewer or shorter clips."
)
NO_PREVIEW_FRAMES = "We couldn't read any frames from this video. The file may be damaged."
_CROP = {"9:16": 9 / 16, "1:1": 1.0}
_EPSILON = 1e-9


def plan_preview(
    duration: float, samples: int, sample_duration: float, start_offset_percent: float, end_offset_percent: float
) -> list[tuple[float, float]]:
    """
    The ``(start, length)`` of each clip, in seconds. Pure.

    Clips are spread evenly across the part of the video the offsets leave (the whole video if
    that part is shorter than one clip), and never overlap: when they don't all fit, fewer are
    taken. A video shorter than one clip gives one clip of the whole video.
    """
    if duration < MIN_PREVIEW_SECONDS:
        raise MediaTooShortError(TOO_SHORT_FOR_PREVIEW, detail=f"duration={duration}")
    if duration <= sample_duration:
        return [(0.0, duration)]
    start, end = sample_window(duration, start_offset_percent, end_offset_percent, sample_duration)
    count = max(1, min(samples, int((end - start) / sample_duration + _EPSILON)))
    last = end - sample_duration
    if count == 1:
        return [(start + (last - start) / 2.0, sample_duration)]
    step = (last - start) / (count - 1)
    return [(start + i * step, sample_duration) for i in range(count)]


def _even(value: float) -> int:
    return max(2, round(value / 2) * 2)


def preview_size(width: int, height: int, config: PreviewConfig) -> tuple[tuple[int, int], tuple[int, int]]:
    """
    The size to scale the whole frame to, and the size of the centre crop taken from it (the
    output size). `width` and `height` are the source's display size. Never upscales. Pure.
    """
    crop_w, crop_h = float(width), float(height)
    ratio = _CROP.get(config.crop)
    if ratio is not None:
        if width / height > ratio:
            crop_w = height * ratio
        else:
            crop_h = width / ratio
    limits = [1.0, config.max_height / crop_h]
    if config.max_width is not None:
        limits.append(config.max_width / crop_w)
    scale = min(limits)
    out_w, out_h = _even(crop_w * scale), _even(crop_h * scale)
    if ratio is None:
        return (out_w, out_h), (out_w, out_h)
    return (max(out_w, _even(width * scale)), max(out_h, _even(height * scale))), (out_w, out_h)


def _frames(length: float, fps: int) -> int:
    return max(1, round(length * fps))


def build_preview_commands(
    input_path: PathLike,
    output_path: PathLike,
    config: PreviewConfig,
    *,
    duration: float,
    width: int,
    height: int,
    threads: int,
    ffmpeg: str = "ffmpeg",
) -> tuple[list[list[str]], list[str]]:
    """
    Builds the ffmpeg commands for :func:`preview`: one per clip, then the encoder. Pure.

    `duration` is the probed length and `width` x `height` the display size (rotation and pixel
    aspect applied). Each clip command decodes with one thread (``-ss``/``-t`` before ``-i``) and
    writes exactly its frames, scaled and cropped, as raw RGB on stdout. The encoder reads them all,
    one clip after another, on stdin and writes the GIF or WebP with up to `threads` threads.
    Raises InvalidOptionsError when a GIF would need more than
    :data:`~sashimi.config.MAX_FRAME_BYTES` of frames in memory (its palette is built from every frame).
    """
    clips = plan_preview(
        duration, config.samples, config.sample_duration, config.start_offset_percent, config.end_offset_percent
    )
    (scale_w, scale_h), (out_w, out_h) = preview_size(width, height, config)
    total_frames = sum(_frames(length, config.fps) for _, length in clips)
    if isinstance(config, GifPreviewConfig) and total_frames * out_w * out_h * 3 > MAX_FRAME_BYTES:
        raise InvalidOptionsError(TOO_BIG_PREVIEW, detail=f"{total_frames} frames of {out_w}x{out_h}")

    vf = f"fps={config.fps},scale={scale_w}:{scale_h}:flags=lanczos,setsar=1"
    if (scale_w, scale_h) != (out_w, out_h):
        vf += f",crop={out_w}:{out_h}"
    clip_commands = []
    for start, length in clips:
        cmd = [ffmpeg, *base_args(1), *INPUT_PROTOCOLS, "-threads", "1"]
        if start > 0:
            cmd += ["-ss", fmt_seconds(start)]
        cmd += ["-t", fmt_seconds(length), "-i", file_arg(input_path), "-map", "0:V:0", "-vf", vf]
        cmd += ["-frames:v", str(_frames(length, config.fps)), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        clip_commands.append(cmd)

    encode = [ffmpeg, *base_args(threads), "-progress", "pipe:1", "-nostats", "-f", "rawvideo", "-pix_fmt", "rgb24"]
    encode += ["-s", f"{out_w}x{out_h}", "-framerate", str(config.fps), "-i", "pipe:0"]
    if isinstance(config, GifPreviewConfig):
        encode += ["-vf", "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4"]
        encode += ["-loop", "0" if config.loop else "-1", *OUTPUT_FLAGS, "-f", "gif"]
    else:
        encode += ["-c:v", "libwebp_anim", "-lossless", "0", "-quality", str(config.quality)]
        encode += ["-loop", "0" if config.loop else "1", "-threads", str(threads), *OUTPUT_FLAGS, "-f", "webp"]
    return clip_commands, [*encode, file_arg(output_path)]


def preview(
    input_path: PathLike,
    output_path: PathLike,
    config: PreviewConfig,
    *,
    max_workers: int | None = None,
    scratch_dir: PathLike | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    Makes a preview of a video (see :data:`~sashimi.config.VIDEO_PREVIEW`): `config.samples`
    clips of `config.sample_duration` seconds, spread across the video between the offsets,
    played one after another as a GIF or animated WebP, scaled to fit `max_height` (and
    `max_width`) and optionally cropped to `crop`. Short videos get fewer clips; a video shorter
    than one clip becomes a single clip, and one under half a second raises MediaTooShortError.

    The clips are decoded one at a time by a single-threaded ffmpeg and streamed into one encoder
    that uses up to `max_workers` threads (default min(4, CPUs)), so at most two ffmpeg processes
    run, whatever the number of clips, and only one frame at a time is held in Python. A clip that
    can't be read is left out; if none can, UnsupportedMediaError is raised. A GIF's encoder keeps
    every frame until the palette is built, so GIFs over :data:`~sashimi.config.MAX_FRAME_BYTES`
    of frames are refused. Written atomically.
    """
    threads = resolve_max_workers(max_workers)
    info = _probe_video(input_path)
    assert info.duration is not None
    height = max(info.height or 0, 2)
    width = max(round(height * info.display_aspect_ratio) if info.display_aspect_ratio else info.width or 0, 2)
    out_w, out_h = preview_size(width, height, config)[1]
    frame_bytes = out_w * out_h * 3
    ffmpeg = ffmpeg_binary()
    sent = 0

    def frames(clip_commands: list[list[str]]) -> Iterator[bytes]:
        nonlocal sent
        for cmd in clip_commands:
            try:
                for chunk in stream_ffmpeg(cmd, frame_bytes):
                    if len(chunk) == frame_bytes:
                        sent += 1
                        yield chunk
            except BinaryNotFoundError:
                raise
            except ProcessingError as e:
                logger.debug(f"Preview clip failed: {e}")

    with atomic_output(output_path, scratch_dir) as temp:
        clip_commands, encode = build_preview_commands(
            input_path, temp, config, duration=info.duration, width=width, height=height, threads=threads, ffmpeg=ffmpeg
        )
        total = sum(int(cmd[cmd.index("-frames:v") + 1]) for cmd in clip_commands) / config.fps
        try:
            run_ffmpeg(
                encode,
                stdin_chunks=frames(clip_commands),
                progress_total=total,
                on_progress=on_progress,
                stage="encoding",
            )
        except BinaryNotFoundError:
            raise
        except ProcessingError as e:
            if sent == 0:
                raise UnsupportedMediaError(NO_PREVIEW_FRAMES, detail=str(e)) from e
            raise
    if on_progress:
        on_progress(1.0, "done")
