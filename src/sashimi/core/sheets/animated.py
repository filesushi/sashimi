# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Animated (GIF/WebP) video contact sheets, rendered with ffmpeg + Pillow (no moviepy)."""

import concurrent.futures
import logging
import math
from collections.abc import Iterator
from typing import Literal

from PIL import Image

from sashimi.config import MAX_FRAME_BYTES, AnimatedGifSheetConfig, AnimatedSheetConfig
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
from sashimi.core.sheets._layout import SheetLayout, check_tiles, clip_starts, plan_layout
from sashimi.core.sheets._render import HeaderStyle, format_duration, load_font, render_header, stamp, timestamp_label
from sashimi.core.sheets.static import NO_FRAMES, check_sheet_size, header_lines, optional_path, probe_sheet_source
from sashimi.errors import BinaryNotFoundError, InvalidOptionsError, ProcessingError, UnsupportedMediaError

__all__ = ["render_animated", "build_clip_command", "build_encode_command"]

logger = logging.getLogger("sashimi")

TOO_SHORT_FOR_ANIMATED = "This video is too short for an animated contact sheet."
TOO_BIG_TO_RENDER = (
    "These settings would make the animated sheet too large to render. Use a smaller width, a lower "
    "frame rate or shorter clips."
)


def build_clip_command(
    input_path: PathLike, start: float, length: float, fps: int, width: int, height: int, ffmpeg: str = "ffmpeg"
) -> list[str]:
    """One tile's clip as raw RGB frames on stdout, already at the sheet's fps and tile size. Pure."""
    frames = max(1, round(length * fps))
    return [
        ffmpeg,
        *base_args(1),
        *INPUT_PROTOCOLS,
        "-threads",
        "1",
        "-ss",
        fmt_seconds(start),
        "-t",
        fmt_seconds(length),
        "-i",
        file_arg(input_path),
        "-map",
        "0:V:0",
        "-vf",
        f"fps={fps},scale={width}:{height},setsar=1",
        "-frames:v",
        str(frames),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]


def build_encode_command(
    output_path: PathLike, config: AnimatedSheetConfig, width: int, height: int, threads: int, ffmpeg: str = "ffmpeg"
) -> list[str]:
    """Encodes raw RGB frames from stdin into the final GIF or WebP. Pure."""
    cmd = [
        ffmpeg,
        *base_args(threads),
        "-progress",
        "pipe:1",
        "-nostats",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-framerate",
        str(config.fps),
        "-i",
        "pipe:0",
    ]
    if isinstance(config, AnimatedGifSheetConfig):
        cmd += [
            "-vf",
            "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
            "-loop",
            "0" if config.loop else "-1",
            *OUTPUT_FLAGS,
            "-f",
            "gif",
        ]
    else:
        cmd += [
            "-c:v",
            "libwebp_anim",
            "-lossless",
            "0",
            "-quality",
            str(config.quality),
            "-loop",
            "0" if config.loop else "1",
            "-threads",
            str(threads),
            *OUTPUT_FLAGS,
            "-f",
            "webp",
        ]
    return [*cmd, file_arg(output_path)]


def _grab_clip(cmd: list[str], frame_bytes: int) -> list[bytes] | None:
    try:
        data = run_ffmpeg(cmd, capture_stdout=True)
    except BinaryNotFoundError:
        raise
    except ProcessingError as e:
        logger.debug(f"Clip extraction failed: {e}")
        return None
    frames = [data[i : i + frame_bytes] for i in range(0, len(data) - frame_bytes + 1, frame_bytes)]
    return frames or None


def render_animated(
    input_path: PathLike,
    output_path: PathLike,
    config: AnimatedSheetConfig,
    *,
    display_name: str | None = None,
    header_font: PathLike | None = None,
    timestamp_font: PathLike | None = None,
    emoji_dir: PathLike | None = None,
    max_workers: int | None = None,
    scratch_dir: PathLike | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    Renders an animated contact sheet; see :func:`sashimi.sheets.render`.

    Clips are decoded by up to `max_workers` single-threaded ffmpeg processes at once (default
    min(4, CPUs)), then composited in Python and encoded by one ffmpeg process using
    `max_workers` threads; ``max_workers=2`` stays near 20 threads in total. Decoded frames are
    held in memory (bounded at 400 MB) and nothing but the output is written to disk.
    """
    workers = resolve_max_workers(max_workers)
    info = probe_sheet_source(input_path, config.sample_duration, TOO_SHORT_FOR_ANIMATED)
    assert info.duration is not None and info.display_aspect_ratio
    layout: SheetLayout = plan_layout(
        config.width, config.cols, config.rows, config.gap, info.display_aspect_ratio, even=True
    )
    check_tiles(layout)
    frames_per_clip = max(1, round(config.sample_duration * config.fps))
    frame_bytes = layout.thumb_w * layout.thumb_h * 3
    if layout.cells * frames_per_clip * frame_bytes > MAX_FRAME_BYTES:
        raise InvalidOptionsError(TOO_BIG_TO_RENDER)
    header_style = HeaderStyle(load_font(optional_path(header_font), config.header_font_size), config.header_font_size)
    header_h = header_style.height if config.show_header else 0
    sheet_w, sheet_h = layout.grid_width, header_h + layout.grid_height
    check_sheet_size(sheet_w, sheet_h, TOO_BIG_TO_RENDER)
    starts = clip_starts(
        info.duration, layout.cells, config.sample_duration, config.start_offset_percent, config.end_offset_percent
    )

    ffmpeg = ffmpeg_binary()
    clips: dict[int, list[bytes]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _grab_clip,
                build_clip_command(
                    input_path, start, config.sample_duration, config.fps, layout.thumb_w, layout.thumb_h, ffmpeg
                ),
                frame_bytes,
            ): slot
            for slot, start in enumerate(starts)
        }
        for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            if (clip_frames := future.result()) is not None:
                clips[futures[future]] = clip_frames
            if on_progress:
                on_progress(0.5 * done / len(futures), "extracting")
    if not clips:
        raise UnsupportedMediaError(NO_FRAMES)

    ts_font = load_font(optional_path(timestamp_font), config.timestamp_font_size)
    base = Image.new("RGB", (sheet_w, sheet_h), config.grid_bg_color)
    if config.show_header:
        base.paste(
            render_header(
                sheet_w,
                header_lines(input_path, info, display_name),
                header_style,
                config.header_bg_color,
                config.header_text_color,
                optional_path(emoji_dir),
            ),
            (0, 0),
        )
    size = (layout.thumb_w, layout.thumb_h)

    def composited_frames() -> Iterator[bytes]:
        for k in range(frames_per_clip):
            canvas = base.copy()
            for slot, clip in clips.items():
                tile = Image.frombytes("RGB", size, clip[min(k, len(clip) - 1)])
                if config.show_timestamps:
                    seconds = starts[slot] + k / config.fps
                    label = timestamp_label(
                        format_duration(math.floor(seconds)),
                        ts_font,
                        config.timestamp_bg_color,
                        config.timestamp_text_color,
                    )
                    stamp(tile, label)
                x, y = layout.cell_origin(slot)
                canvas.paste(tile, (x, header_h + y))
            yield canvas.tobytes()

    def report(fraction: float, stage: str) -> None:
        if on_progress:
            on_progress(0.5 + 0.5 * fraction, stage)

    with atomic_output(output_path, scratch_dir) as temp:
        cmd = build_encode_command(temp, config, sheet_w, sheet_h, workers, ffmpeg)
        run_ffmpeg(
            cmd,
            stdin_chunks=composited_frames(),
            progress_total=frames_per_clip / config.fps,
            on_progress=report if on_progress else None,
            stage="encoding",
        )
    if on_progress:
        on_progress(1.0, "done")
