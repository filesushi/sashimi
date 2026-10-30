# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Video previews: short clips sampled from across a video, joined into one GIF or animated WebP."""

import logging
from collections.abc import Iterator
from typing import Any

from sashimi.config import GifPreviewConfig, Limits, PreviewConfig
from sashimi.config._limits import check_webp_size, resolve_limits
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
    stream_timed_frames,
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
from sashimi.core._hdr import hdr_name, require_tone_mapping, with_tone_map
from sashimi.core.fidelity import Outcome, derived
from sashimi.core._resources import resolve_max_workers
from sashimi.core._seek import (
    VideoIndex,
    clip_pass,
    clip_route,
    decoding_args,
    video_index,
    with_filter,
)
from sashimi.core.sheets._layout import sample_window
from sashimi.core.video import _probe_video
from sashimi.errors import (
    BinaryNotFoundError,
    MediaTooShortError,
    ProcessingError,
    UnsupportedMediaError,
)

__all__ = ["plan_preview", "preview_size", "build_preview_commands", "build_preview_palette_command", "preview"]

logger = logging.getLogger("sashimi")

MIN_PREVIEW_SECONDS = 0.0
"""Videos at least this long get a preview: any video with a frame does (a short one gives itself, whole)."""
TOO_SHORT_FOR_PREVIEW = "This video is too short for a preview."
NO_PREVIEW_FRAMES = "We couldn't read any frames from this video. The file may be damaged."
_CROP = {"9:16": 9 / 16, "1:1": 1.0}
_EPSILON = 1e-9
_PALETTEUSE = "dither=bayer:bayer_scale=4"


def plan_preview(
    duration: float, samples: int, sample_duration: float, start_offset_percent: float, end_offset_percent: float
) -> list[tuple[float, float]]:
    """
    The ``(start, length)`` of each clip, in seconds. Pure.

    Clips are spread evenly across the part of the video the offsets leave (the whole video if
    that part is shorter than one clip), and never overlap: when they don't all fit, fewer are
    taken. A video shorter than one clip gives one clip of the whole video.
    """
    if duration <= MIN_PREVIEW_SECONDS:
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


def _raw_input(out_w: int, out_h: int, fps: int) -> list[str]:
    return ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out_w}x{out_h}", "-framerate", str(fps), "-i", "pipe:0"]


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
    limits: Limits | None = None,
    palette: bytes | None = None,
    index: VideoIndex | None = None,
    tone_map: str | None = None,
) -> tuple[list[list[str]], list[str]]:
    """
    Builds the ffmpeg commands for :func:`preview`: one per clip, then the encoder. Pure.

    `duration` is the probed length and `width` x `height` the display size (rotation and pixel
    aspect applied). Each clip command decodes with one thread (``-ss``/``-t`` before ``-i``) and
    writes exactly its frames, scaled and cropped, as raw RGB on stdout. The encoder reads them all,
    one clip after another, on stdin and writes the GIF or WebP with up to `threads` threads.
    Raises InvalidOptionsError for a config over `limits`.

    `palette` is the PNG that :func:`build_preview_palette_command`'s command writes from the
    same frames: a GIF's encoder then reads it as a second input. Without it, a GIF's encoder
    builds the palette itself, which holds every frame in memory until the end; the GIF is
    byte-identical either way. A WebP ignores it.

    `index` is the video's keyframe index (:func:`sashimi.core._seek.video_index`): each clip then
    decodes from just before the keyframe its start needs and drops the frames before the start.
    Without it, ffmpeg seeks by itself (``-ss start``). :func:`preview` decodes a clip that starts
    while an earlier frame is still on screen (:meth:`~sashimi.core._seek.VideoIndex.held_from`,
    in variable-frame-rate video) with :func:`~sashimi.core._seek.clip_pass` instead of its
    command here, so it shows that frame until its own first frame.

    `tone_map` (:func:`sashimi.core._hdr.tone_map_filter`) follows the scaling and cropping, for HDR video.
    """
    limits = resolve_limits(limits)
    limits.check(config)
    clips = plan_preview(
        duration, config.samples, config.sample_duration, config.start_offset_percent, config.end_offset_percent
    )
    (scale_w, scale_h), (out_w, out_h) = preview_size(width, height, config)

    vf = f"fps={config.fps},scale={scale_w}:{scale_h}:flags=lanczos,setsar=1"
    if (scale_w, scale_h) != (out_w, out_h):
        vf += f",crop={out_w}:{out_h}"
    vf = with_tone_map(vf, tone_map)
    clip_commands = []
    for start, length in clips:
        cmd = [ffmpeg, *base_args(1), *INPUT_PROTOCOLS, "-threads", "1"]
        clip_vf = vf
        if index is None:
            if start > 0:
                cmd += ["-ss", fmt_seconds(start)]
            cmd += ["-t", fmt_seconds(length)]
        else:
            begin = index.start_for(start)
            if begin is None:
                if start > 0:
                    cmd += ["-ss", fmt_seconds(start)]
                cmd += ["-t", fmt_seconds(length)]
            else:
                decoding = decoding_args(begin, start, length, index.origin(begin))
                cmd += decoding.inputs
                clip_vf = with_filter(decoding.video, vf)
        cmd += ["-i", file_arg(input_path), "-map", "0:V:0", "-vf", clip_vf]
        cmd += ["-frames:v", str(_frames(length, config.fps)), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        clip_commands.append(cmd)

    encode = [ffmpeg, *base_args(threads), "-progress", "pipe:1", "-nostats", *_raw_input(out_w, out_h, config.fps)]
    if isinstance(config, GifPreviewConfig):
        if palette is not None:
            encode += [*palette_input(palette), *palette_use("", _PALETTEUSE)]
        else:
            encode += ["-vf", one_pass("", "diff", _PALETTEUSE)]
        encode += ["-loop", "0" if config.loop else "-1", *OUTPUT_FLAGS, "-f", "gif"]
    else:
        encode += ["-c:v", "libwebp_anim", "-lossless", "0", "-quality", str(config.quality)]
        encode += ["-loop", "0" if config.loop else "1", "-threads", str(threads), *OUTPUT_FLAGS, "-f", "webp"]
    return clip_commands, [*encode, file_arg(output_path)]


def build_preview_palette_command(
    config: GifPreviewConfig,
    *,
    width: int,
    height: int,
    threads: int,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """
    Builds the first pass of a GIF preview: it reads the same frames as the encoder of
    :func:`build_preview_commands` on stdin and writes their palette as one PNG on stdout, with
    each frame's time on stderr for progress. Pass its output to :func:`build_preview_commands`
    as `palette`. `width` x `height` is the display size. Pure.
    """
    out_w, out_h = preview_size(width, height, config)[1]
    cmd = [ffmpeg, *base_args(threads), *_raw_input(out_w, out_h, config.fps)]
    return [*cmd, *palette_output("", "diff")]


def preview(
    input_path: PathLike,
    output_path: PathLike,
    config: PreviewConfig,
    *,
    max_workers: int | None = None,
    scratch_dir: PathLike | None = None,
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> Outcome:
    """
    Makes a preview of a video (see :data:`~sashimi.config.VIDEO_PREVIEW`): `config.samples`
    clips of `config.sample_duration` seconds, spread across the video between the offsets,
    played one after another as a GIF or animated WebP, scaled to fit `max_height` (and
    `max_width`) and optionally cropped to `crop`. Short videos get fewer clips; a video shorter
    than one clip becomes a single clip, and one under half a second raises MediaTooShortError.

    The clips are decoded one at a time by a single-threaded ffmpeg and streamed into one encoder
    that uses up to `max_workers` threads (default min(4, CPUs)), so at most two ffmpeg processes
    run, whatever the number of clips, and only one frame at a time is held in Python. A clip that
    can't be read is left out; if none can, UnsupportedMediaError is raised. Previews over
    ``limits.max_gif_seconds`` in all or ``limits.max_preview_clips`` clips are refused before the
    file is read. Written atomically.

    A GIF is made in two passes, so its memory doesn't grow with its length: the clips are decoded
    once to build the palette (written to no file; the first third of the progress, as
    "analysing") and again to encode with it.
    """
    limits = resolve_limits(limits)
    limits.check(config)
    threads = resolve_max_workers(max_workers)
    info = _probe_video(input_path, limits)
    assert info.duration is not None
    height = max(info.height or 0, 2)
    width = max(round(height * info.display_aspect_ratio) if info.display_aspect_ratio else info.width or 0, 2)
    out_w, out_h = preview_size(width, height, config)[1]
    if config.target == "webp":
        check_webp_size(out_w, out_h)
    frame_bytes = out_w * out_h * 3
    ffmpeg = ffmpeg_binary()
    tone_map = require_tone_mapping(info, ffmpeg)
    sent = 0
    planned = plan_preview(
        info.duration, config.samples, config.sample_duration, config.start_offset_percent, config.end_offset_percent
    )
    index = video_index(input_path, info, planned[-1][0] + planned[-1][1], limits)
    one_pass = clip_route(index, planned) == "pass"

    (scale_w, scale_h) = preview_size(width, height, config)[0]
    pass_vf = f"scale={scale_w}:{scale_h}:flags=lanczos,setsar=1"
    if (scale_w, scale_h) != (out_w, out_h):
        pass_vf += f",crop={out_w}:{out_h}"
    pass_vf = with_tone_map(pass_vf, tone_map)

    def frames(clip_commands: list[list[str]]) -> Iterator[bytes]:
        nonlocal sent
        if one_pass:
            assert index is not None
            yield from passed(index, planned, threads)
            return
        for clip, cmd in zip(planned, clip_commands, strict=True):
            if index is not None and index.held_from(clip[0]) is not None:
                yield from passed(index, [clip], 1)
                continue
            try:
                for chunk in stream_ffmpeg(cmd, frame_bytes):
                    if len(chunk) == frame_bytes:
                        sent += 1
                        yield chunk
            except BinaryNotFoundError:
                raise
            except ProcessingError as e:
                logger.debug(f"Preview clip failed: {e}")

    def passed(index: VideoIndex, clips: list[tuple[float, float]], workers: int) -> Iterator[bytes]:
        nonlocal sent
        timed = clip_pass(input_path, index, clips, pass_vf, config.fps, frame_bytes, workers, ffmpeg)
        try:
            for _, frame in timed:
                sent += 1
                yield frame
        except BinaryNotFoundError:
            raise
        except ProcessingError as e:
            logger.debug(f"Preview pass failed: {e}")
        finally:
            timed.close()

    def build(output: PathLike, palette: bytes | None) -> tuple[list[list[str]], list[str]]:
        return build_preview_commands(
            input_path,
            output,
            config,
            duration=info.duration or 0.0,
            width=width,
            height=height,
            threads=threads,
            ffmpeg=ffmpeg,
            limits=limits,
            palette=palette,
            index=index,
            tone_map=tone_map,
        )

    def run(cmd: list[str], clip_commands: list[list[str]], **kwargs: Any) -> bytes:
        try:
            return run_ffmpeg(cmd, stdin_chunks=frames(clip_commands), **kwargs)
        except BinaryNotFoundError:
            raise
        except ProcessingError as e:
            if sent == 0:
                raise UnsupportedMediaError(NO_PREVIEW_FRAMES, detail=str(e)) from e
            raise

    clip_commands = build(output_path, None)[0]
    total = sum(int(cmd[cmd.index("-frames:v") + 1]) for cmd in clip_commands) / config.fps
    palette = None
    report = on_progress
    if isinstance(config, GifPreviewConfig):
        first = build_preview_palette_command(config, width=width, height=height, threads=threads, ffmpeg=ffmpeg)
        palette = run(
            first,
            clip_commands,
            capture_stdout=True,
            progress_total=total,
            on_progress=scaled(on_progress, 0.0, PALETTE_SHARE),
            stage=PALETTE_STAGE,
        )
        report = scaled(on_progress, PALETTE_SHARE, 1 - PALETTE_SHARE)
    with atomic_output(output_path, scratch_dir) as temp:
        clip_commands, encode = build(temp, palette)
        run(encode, clip_commands, progress_total=total, on_progress=report, stage="encoding")
    kind = "GIF (256 colours)" if isinstance(config, GifPreviewConfig) else f"animated WebP, quality {config.quality}"
    changes = [f"{len(planned)} {'clip' if len(planned) == 1 else 'clips'} at {config.fps} fps as {kind}"]
    if (out_w, out_h) != (width, height):
        changes.append(f"scaled to {out_w}x{out_h}")
    if tone_map:
        changes.append(f"tone-mapped from {hdr_name(info)}")
    if on_progress:
        on_progress(1.0, "done")
    return Outcome(fidelity=derived(input_path, "picture", changes, media=True))
