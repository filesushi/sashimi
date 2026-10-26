# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Still (JPEG, PNG or WebP) video contact sheets."""

import concurrent.futures
import logging
import os
from collections.abc import Sequence
from typing import Literal

from PIL import Image

from sashimi.config import (
    GIF_MAX_SIDE,
    JPEG_MAX_SIDE,
    WEBP_MAX_SIDE,
    JpegSheetConfig,
    Limits,
    StillSheetConfig,
    WebpSheetConfig,
)
from sashimi.config._limits import megapixels, resolve_limits
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
from sashimi.core._seek import VideoIndex, decoding_args, video_index, with_filter
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

__all__ = ["render_still", "build_frame_command", "build_frames_command", "sheet_route"]

logger = logging.getLogger("sashimi")

NOT_A_VIDEO = "This file doesn't look like a video we can read."
TOO_SHORT_FOR_SHEET = "This video is too short to make a contact sheet."
NO_FRAMES = "We couldn't read any frames from this video. The file may be damaged."
TOO_BIG_SHEET = "These settings would make the contact sheet too large"
FORMAT_MAX_SIDE = {"jpeg": JPEG_MAX_SIDE, "webp": WEBP_MAX_SIDE, "gif": GIF_MAX_SIDE}
"""The widest and tallest image each sheet format can hold, in pixels (PNG has no practical limit)."""
_FORMAT_NAMES = {"jpeg": "JPEG", "webp": "WebP", "gif": "GIF"}


def optional_path(path: PathLike | None) -> str | None:
    return os.fspath(path) if path is not None else None


def probe_sheet_source(
    input_path: PathLike, min_duration: float, too_short: str, limits: Limits | None = None
) -> MediaInfo:
    try:
        info = probe(input_path, limits=limits)
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


def check_sheet_size(width: int, height: int, target: str, limits: Limits | None, too_big: str = TOO_BIG_SHEET) -> None:
    """
    Refuses sheets wider or taller than `target`'s format allows (:data:`FORMAT_MAX_SIDE`), or
    larger than ``limits.max_sheet_pixels``, with MediaTooLargeError. `too_big` starts the message.
    """
    side = FORMAT_MAX_SIDE.get(target)
    if side is not None and max(width, height) > side:
        raise MediaTooLargeError(
            f"{too_big} for {_FORMAT_NAMES[target]}, which can be at most {side} pixels on a side ({width}x{height}). "
            "Use fewer rows or a smaller width.",
            detail=f"{width}x{height}",
        )
    budget = resolve_limits(limits).max_sheet_pixels
    if budget is not None and width * height > budget:
        raise MediaTooLargeError(
            f"{too_big}: it would have {megapixels(width * height)} megapixels ({width}x{height}), and the limit "
            f"is {megapixels(budget)} megapixels. Use fewer rows or a smaller width.",
            detail=f"{width}x{height}",
            limit="max_sheet_pixels",
        )


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
    input_path: PathLike,
    timestamp: float,
    width: int,
    height: int,
    ffmpeg: str = "ffmpeg",
    *,
    decode_from: float | None = None,
) -> list[str]:
    """
    One still frame, scaled to the tile, as raw RGB on stdout. Pure. 'V:0' skips cover art.

    `decode_from` is where decoding starts (seconds, from the file's keyframe index; see
    :func:`sashimi.core._seek.VideoIndex.decode_from`): the frames before `timestamp` are then
    decoded and dropped. None leaves the seek to ffmpeg (``-ss timestamp``).
    """
    if decode_from is None:
        seek, vf = ["-ss", fmt_seconds(timestamp)], f"scale={width}:{height},setsar=1"
    else:
        decoding = decoding_args(decode_from, timestamp)
        seek, vf = decoding.inputs, with_filter(decoding.video, f"scale={width}:{height},setsar=1")
    return [
        ffmpeg,
        *base_args(1),
        *INPUT_PROTOCOLS,
        "-threads",
        "1",
        *seek,
        "-i",
        file_arg(input_path),
        "-map",
        "0:V:0",
        "-vf",
        vf,
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]


def build_frames_command(
    input_path: PathLike,
    timestamps: Sequence[float],
    width: int,
    height: int,
    threads: int,
    ffmpeg: str = "ffmpeg",
    *,
    decode_from: float = 0.0,
) -> list[str]:
    """
    Every tile's frame from one decoding pass, in order, as raw RGB on stdout: for each of
    `timestamps` (ascending), the first frame shown at or after it. Pure. Decoding starts at
    `decode_from` (seconds; see :func:`build_frame_command`) and stops after the last timestamp,
    so a video with keyframes far apart is decoded once instead of once per tile.
    """
    seek = ["-ss", fmt_seconds(decode_from)] if decode_from > 0 else []
    picks = "0"
    for slot in reversed(range(len(timestamps))):
        at = fmt_seconds(max(timestamps[slot] - decode_from, 0.0))
        picks = f"if(eq(selected_n,{slot}),gte(t,{at}),{picks})"
    reach = timestamps[-1] - decode_from + 1.0
    return [
        ffmpeg,
        *base_args(threads),
        *INPUT_PROTOCOLS,
        "-threads",
        str(threads),
        *seek,
        "-t",
        fmt_seconds(reach),
        "-i",
        file_arg(input_path),
        "-map",
        "0:V:0",
        "-vf",
        f"select='{picks}',scale={width}:{height},setsar=1",
        "-fps_mode",
        "passthrough",
        "-frames:v",
        str(len(timestamps)),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]


def sheet_route(index: VideoIndex | None, times: Sequence[float]) -> Literal["seek", "pass"]:
    """
    Whether a still sheet's frames are cheaper to get with one seek per tile (``seek``) or one
    decoding pass (``pass``), counted in frames decoded from the keyframe `index`. Pure.
    """
    if index is None or not times:
        return "seek"
    per_tile = sum(index.decoded_before(t) - index.decoded_before(index.decode_from(t)) + 1 for t in times)
    one_pass = index.decoded_before(times[-1]) - index.decoded_before(index.decode_from(times[0])) + 1
    return "pass" if one_pass < per_tile else "seek"


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


def _grab_frames(cmd: list[str], width: int, height: int, count: int) -> list[Image.Image]:
    size = width * height * 3
    try:
        data = run_ffmpeg(cmd, capture_stdout=True)
    except BinaryNotFoundError:
        raise
    except ProcessingError as e:
        logger.debug(f"Frame extraction failed: {e}")
        return []
    return [
        Image.frombytes("RGB", (width, height), data[i : i + size])
        for i in range(0, min(len(data), count * size) - size + 1, size)
    ]


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
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    Renders a still contact sheet; see :func:`sashimi.sheets.render`.

    Runs up to `max_workers` single-threaded ffmpeg processes at once (default min(4, CPUs));
    each uses about 6 threads, so ``max_workers=2`` stays near 15 threads. Frames are piped, so
    nothing is written to disk except the output. A frame that can't be read leaves its tile empty.
    """
    workers = resolve_max_workers(max_workers)
    info = probe_sheet_source(input_path, 1.0, TOO_SHORT_FOR_SHEET, limits)
    assert info.duration is not None and info.display_aspect_ratio
    layout: SheetLayout = plan_layout(config.width, config.cols, config.rows, config.gap, info.display_aspect_ratio)
    check_tiles(layout)
    times = frame_times(info.duration, layout.cells, config.start_offset_percent, config.end_offset_percent)

    header_style = HeaderStyle(load_font(optional_path(header_font), config.header_font_size), config.header_font_size)
    header_h = header_style.height if config.show_header else 0
    ts_font = load_font(optional_path(timestamp_font), config.timestamp_font_size)
    check_sheet_size(config.width, header_h + layout.grid_height, config.target, limits)
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
    index = video_index(input_path, info, times[-1], limits) if times else None
    tiles: dict[int, Image.Image] = {}
    if sheet_route(index, times) == "pass":
        assert index is not None
        cmd = build_frames_command(
            input_path, times, layout.thumb_w, layout.thumb_h, workers, ffmpeg, decode_from=index.decode_from(times[0])
        )
        for slot, tile in enumerate(_grab_frames(cmd, layout.thumb_w, layout.thumb_h, len(times))):
            tiles[slot] = tile
            if on_progress:
                on_progress(0.9 * (slot + 1) / len(times), "extracting")
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _grab_frame,
                    build_frame_command(
                        input_path,
                        t,
                        layout.thumb_w,
                        layout.thumb_h,
                        ffmpeg,
                        decode_from=index.start_for(t) if index is not None else None,
                    ),
                    layout.thumb_w,
                    layout.thumb_h,
                ): slot
                for slot, t in enumerate(times)
            }
            for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                if (grabbed := future.result()) is not None:
                    tiles[futures[future]] = grabbed
                if on_progress:
                    on_progress(0.9 * done / len(futures), "extracting")
    placed = 0
    for slot, tile in sorted(tiles.items()):
        if config.show_timestamps:
            label = timestamp_label(
                format_duration(times[slot]), ts_font, config.timestamp_bg_color, config.timestamp_text_color
            )
            stamp(tile, label)
        x, y = layout.cell_origin(slot)
        sheet.paste(tile, (x, header_h + y))
        placed += 1

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
