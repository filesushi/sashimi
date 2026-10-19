# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Still (JPEG, PNG or WebP) video contact sheets."""

import concurrent.futures
import logging
import os

from PIL import Image

from sashimi.config import (
    MAX_SHEET_HEIGHT,
    MAX_SHEET_PIXELS,
    JpegSheetConfig,
    StillSheetConfig,
    WebpSheetConfig,
)
from sashimi.core._atomic import PathLike, atomic_output
from sashimi.core._ffmpeg import (
    INPUT_PROTOCOLS,
    ProgressCallback,
    base_args,
    ffmpeg_binary,
    file_arg,
    fmt_seconds,
    run_ffmpeg,
)
from sashimi.core._resources import resolve_max_workers
from sashimi.core.probe import VIDEO_CONTAINERS, MediaInfo, probe
from sashimi.core.sheets._layout import SheetLayout, check_tiles, frame_times, plan_layout
from sashimi.core.sheets._render import (
    HeaderStyle,
    format_duration,
    format_size,
    load_font,
    render_header,
    stamp,
    timestamp_label,
)
from sashimi.errors import (
    BinaryNotFoundError,
    MediaTooLargeError,
    MediaTooShortError,
    ProcessingError,
    UnsupportedMediaError,
)

__all__ = ["render_still", "build_frame_command"]

logger = logging.getLogger("sashimi")

NOT_A_VIDEO = "This file doesn't look like a video we can read."
TOO_SHORT_FOR_SHEET = "This video is too short to make a contact sheet."
NO_FRAMES = "We couldn't read any frames from this video. The file may be damaged."
TOO_BIG_SHEET = "These settings would make the contact sheet too large. Use fewer rows or a smaller width."


def optional_path(path: PathLike | None) -> str | None:
    return os.fspath(path) if path is not None else None


def probe_sheet_source(input_path: PathLike, min_duration: float, too_short: str) -> MediaInfo:
    try:
        info = probe(input_path)
    except UnsupportedMediaError as e:
        raise UnsupportedMediaError(NOT_A_VIDEO, detail=e.detail) from e
    if (
        not info.has_video
        or not info.container_names & VIDEO_CONTAINERS
        or not info.duration
        or not info.display_aspect_ratio
    ):
        raise UnsupportedMediaError(NOT_A_VIDEO, detail=f"container={info.container} duration={info.duration}")
    if info.duration < min_duration:
        raise MediaTooShortError(too_short)
    return info


def check_sheet_size(width: int, height: int, message: str = TOO_BIG_SHEET) -> None:
    """Refuses sheets taller than :data:`MAX_SHEET_HEIGHT` or larger than :data:`MAX_SHEET_PIXELS`."""
    if height > MAX_SHEET_HEIGHT or width * height > MAX_SHEET_PIXELS:
        raise MediaTooLargeError(message, detail=f"{width}x{height}")


def header_lines(input_path: PathLike, info: MediaInfo, display_name: str | None) -> list[str]:
    name = display_name or os.path.basename(os.fspath(input_path))
    try:
        size: int | None = os.stat(input_path).st_size
    except OSError:
        size = info.size_bytes
    return [
        name,
        f"File Size: {format_size(size)}",
        f"Duration: {format_duration(info.duration)}",
        f"Dimensions: {info.width or 0}x{info.height or 0}",
    ]


def build_frame_command(
    input_path: PathLike, timestamp: float, width: int, height: int, ffmpeg: str = "ffmpeg"
) -> list[str]:
    """One still frame, scaled to the tile, as raw RGB on stdout. Pure. 'V:0' skips cover art."""
    return [
        ffmpeg,
        *base_args(1),
        *INPUT_PROTOCOLS,
        "-threads",
        "1",
        "-ss",
        fmt_seconds(timestamp),
        "-i",
        file_arg(input_path),
        "-map",
        "0:V:0",
        "-vf",
        f"scale={width}:{height},setsar=1",
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]


def _grab_frame(cmd: list[str], width: int, height: int) -> Image.Image | None:
    try:
        data = run_ffmpeg(cmd, capture_stdout=True)
    except BinaryNotFoundError:
        raise
    except ProcessingError as e:
        logger.debug(f"Frame extraction failed: {e}")
        return None
    if len(data) < width * height * 3:
        return None
    return Image.frombytes("RGB", (width, height), data[: width * height * 3])


def render_still(
    input_path: PathLike,
    output_path: PathLike,
    config: StillSheetConfig,
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
    Renders a still contact sheet; see :func:`sashimi.sheets.render`.

    Runs up to `max_workers` single-threaded ffmpeg processes at once (default min(4, CPUs));
    each uses about 6 threads, so ``max_workers=2`` stays near 15 threads. Frames are piped, so
    nothing is written to disk except the output. A frame that can't be read leaves its tile empty.
    """
    workers = resolve_max_workers(max_workers)
    info = probe_sheet_source(input_path, 1.0, TOO_SHORT_FOR_SHEET)
    assert info.duration is not None and info.display_aspect_ratio
    layout: SheetLayout = plan_layout(config.width, config.cols, config.rows, config.gap, info.display_aspect_ratio)
    check_tiles(layout)
    times = frame_times(info.duration, layout.cells, config.start_offset_percent, config.end_offset_percent)

    header_style = HeaderStyle(load_font(optional_path(header_font), config.header_font_size), config.header_font_size)
    header_h = header_style.height if config.show_header else 0
    ts_font = load_font(optional_path(timestamp_font), config.timestamp_font_size)
    check_sheet_size(config.width, header_h + layout.grid_height)
    sheet = Image.new("RGB", (config.width, header_h + layout.grid_height), config.grid_bg_color)
    if config.show_header:
        lines = header_lines(input_path, info, display_name)
        header = render_header(
            config.width,
            lines,
            header_style,
            config.header_bg_color,
            config.header_text_color,
            optional_path(emoji_dir),
        )
        sheet.paste(header, (0, 0))

    ffmpeg = ffmpeg_binary()
    placed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _grab_frame,
                build_frame_command(input_path, t, layout.thumb_w, layout.thumb_h, ffmpeg),
                layout.thumb_w,
                layout.thumb_h,
            ): slot
            for slot, t in enumerate(times)
        }
        for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            slot = futures[future]
            tile = future.result()
            if tile is not None:
                if config.show_timestamps:
                    label = timestamp_label(
                        format_duration(times[slot]), ts_font, config.timestamp_bg_color, config.timestamp_text_color
                    )
                    stamp(tile, label)
                x, y = layout.cell_origin(slot)
                sheet.paste(tile, (x, header_h + y))
                placed += 1
            if on_progress:
                on_progress(0.9 * done / len(futures), "extracting")

    if not placed:
        raise UnsupportedMediaError(NO_FRAMES)

    with atomic_output(output_path, scratch_dir) as temp:
        if isinstance(config, JpegSheetConfig):
            sheet.save(temp, "JPEG", quality=config.quality, optimize=True, progressive=True)
        elif isinstance(config, WebpSheetConfig):
            sheet.save(temp, "WEBP", quality=config.quality)
        else:
            sheet.save(temp, "PNG", compress_level=6)
    if on_progress:
        on_progress(1.0, "done")
