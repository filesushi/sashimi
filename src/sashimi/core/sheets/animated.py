# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Animated (GIF/WebP) video contact sheets, rendered with ffmpeg + Pillow (no moviepy)."""

import concurrent.futures
import logging
import math
from collections.abc import Iterator
from PIL import Image

from sashimi.config import AnimatedGifSheetConfig, AnimatedSheetConfig, Limits
from sashimi.config._limits import megabytes, resolve_limits
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
from sashimi.core.sheets._layout import SheetLayout, check_tiles, clip_starts, plan_layout
from sashimi.core.sheets._render import HeaderStyle, format_duration, load_font, padded_label, render_header, stamp
from sashimi.core.sheets.static import NO_FRAMES, check_sheet_size, header_lines, optional_path, probe_sheet_source
from sashimi.errors import BinaryNotFoundError, InvalidOptionsError, ProcessingError, UnsupportedMediaError

__all__ = ["render_animated", "build_clip_command", "build_encode_command", "build_palette_command"]

logger = logging.getLogger("sashimi")

TOO_SHORT_FOR_ANIMATED = "This video is too short for an animated contact sheet."
MIN_ANIMATED_SECONDS = 1.0
LABEL_PADDING = 4
TOO_BIG_TO_RENDER = "These settings would make the animated sheet too large to render"
_PALETTEUSE = "dither=bayer:bayer_scale=4"


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


def _raw_input(width: int, height: int, fps: int) -> list[str]:
    return ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-framerate", str(fps), "-i", "pipe:0"]


def build_encode_command(
    output_path: PathLike,
    config: AnimatedSheetConfig,
    width: int,
    height: int,
    threads: int,
    ffmpeg: str = "ffmpeg",
    palette: bytes | None = None,
) -> list[str]:
    """
    Encodes raw RGB frames from stdin into the final GIF or WebP. Pure. A GIF reads `palette`,
    the PNG :func:`build_palette_command`'s command writes, as a second input; without it, it
    builds the palette itself, holding every frame until the end.
    """
    cmd = [ffmpeg, *base_args(threads), "-progress", "pipe:1", "-nostats", *_raw_input(width, height, config.fps)]
    if isinstance(config, AnimatedGifSheetConfig):
        if palette is not None:
            cmd += [*palette_input(palette), *palette_use("", _PALETTEUSE)]
        else:
            cmd += ["-vf", one_pass("", "diff", _PALETTEUSE)]
        cmd += ["-loop", "0" if config.loop else "-1", *OUTPUT_FLAGS, "-f", "gif"]
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


def build_palette_command(
    config: AnimatedGifSheetConfig, width: int, height: int, threads: int, ffmpeg: str = "ffmpeg"
) -> list[str]:
    """A GIF sheet's first pass: the palette of the frames on stdin, as one PNG on stdout. Pure."""
    cmd = [ffmpeg, *base_args(threads), *_raw_input(width, height, config.fps)]
    return [*cmd, *palette_output("", "diff")]


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
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    Renders an animated contact sheet; see :func:`sashimi.sheets.render`.

    Clips are decoded by up to `max_workers` single-threaded ffmpeg processes at once (default
    min(4, CPUs)), then composited in Python and encoded by one ffmpeg process using
    `max_workers` threads; ``max_workers=2`` stays near 20 threads in total. The clips' decoded
    frames are held in memory (at most ``limits.max_frame_bytes``) and nothing but the output is
    written to disk. A GIF is encoded in two passes over the same frames, the first building the
    palette, so the encoder never holds every frame as well.

    A video shorter than one clip (but at least a second long) gets a single tile of the whole video.
    """
    workers = resolve_max_workers(max_workers)
    info = probe_sheet_source(input_path, min(config.sample_duration, MIN_ANIMATED_SECONDS), TOO_SHORT_FOR_ANIMATED)
    assert info.duration is not None and info.display_aspect_ratio
    single = info.duration < config.sample_duration
    clip_length = info.duration if single else config.sample_duration
    cols, rows = (1, 1) if single else (config.cols, config.rows)
    layout: SheetLayout = plan_layout(
        config.width, cols, rows, config.gap, info.display_aspect_ratio, even=True, animated=True
    )
    check_tiles(layout)
    frames_per_clip = max(1, round(clip_length * config.fps))
    frame_bytes = layout.thumb_w * layout.thumb_h * 3
    needed = layout.cells * frames_per_clip * frame_bytes
    budget = resolve_limits(limits).max_frame_bytes
    if budget is not None and needed > budget:
        raise InvalidOptionsError(
            f"{TOO_BIG_TO_RENDER}: its frames would take {megabytes(needed)} of memory, and the limit is "
            f"{megabytes(budget)}. Use a smaller width, a lower frame rate or shorter clips.",
            detail=f"{layout.cells} clips of {frames_per_clip} frames of {layout.thumb_w}x{layout.thumb_h}",
            limit="max_frame_bytes",
        )
    header_style = HeaderStyle(load_font(optional_path(header_font), config.header_font_size), config.header_font_size)
    header_h = header_style.height if config.show_header else 0
    sheet_w, sheet_h = layout.grid_width, header_h + layout.grid_height
    check_sheet_size(sheet_w, sheet_h, config.target, limits, TOO_BIG_TO_RENDER)
    starts = (
        [0.0]
        if single
        else clip_starts(
            info.duration, layout.cells, clip_length, config.start_offset_percent, config.end_offset_percent
        )
    )

    ffmpeg = ffmpeg_binary()
    clips: dict[int, list[bytes]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _grab_clip,
                build_clip_command(input_path, start, clip_length, config.fps, layout.thumb_w, layout.thumb_h, ffmpeg),
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
                    label = padded_label(
                        format_duration(math.floor(seconds)),
                        ts_font,
                        config.timestamp_bg_color,
                        config.timestamp_text_color,
                        LABEL_PADDING,
                    )
                    stamp(tile, label, LABEL_PADDING)
                x, y = layout.cell_origin(slot)
                canvas.paste(tile, (x, header_h + y))
            yield canvas.tobytes()

    total = frames_per_clip / config.fps
    palette = None
    encode_start = 0.5
    if isinstance(config, AnimatedGifSheetConfig):
        palette = run_ffmpeg(
            build_palette_command(config, sheet_w, sheet_h, workers, ffmpeg),
            stdin_chunks=composited_frames(),
            capture_stdout=True,
            progress_total=total,
            on_progress=scaled(on_progress, 0.5, 0.5 * PALETTE_SHARE),
            stage=PALETTE_STAGE,
        )
        encode_start += 0.5 * PALETTE_SHARE

    with atomic_output(output_path, scratch_dir) as temp:
        cmd = build_encode_command(temp, config, sheet_w, sheet_h, workers, ffmpeg, palette)
        run_ffmpeg(
            cmd,
            stdin_chunks=composited_frames(),
            progress_total=total,
            on_progress=scaled(on_progress, encode_start, 1.0 - encode_start),
            stage="encoding",
        )
    if on_progress:
        on_progress(1.0, "done")
