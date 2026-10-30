# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Still (JPEG, PNG or WebP) video contact sheets."""

import concurrent.futures
import math
import logging
import os
from collections.abc import Iterable, Iterator, Sequence
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
    stream_timed_frames,
)
from sashimi.core._resources import resolve_max_workers
from sashimi.core._hdr import hdr_name, require_tone_mapping, with_tone_map
from sashimi.core.fidelity import Outcome, derived
from sashimi.core._seek import (
    HELD_FRAMES,
    VideoIndex,
    build_pass_command,
    decoding_args,
    reaches,
    video_index,
    with_filter,
)
from sashimi.core.probe import VIDEO_CONTAINERS, MediaInfo, anamorphic, check_decodable, probe
from sashimi.core.sheets._layout import fit_grid, SheetLayout, check_tiles, frame_times, plan_layout
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

__all__ = ["render_still", "build_frame_command", "pick_tiles", "tile_windows", "sheet_route"]

logger = logging.getLogger("sashimi")

NOT_A_VIDEO = "This file doesn't look like a video we can read."
TOO_SHORT_FOR_SHEET = "This video is too short to make a contact sheet."
MIN_SHEET_SECONDS = 0.0
"""Videos longer than this get a contact sheet: any video with a frame (tiles of a short one share frames)."""
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
    check_decodable(info)
    if info.duration < min_duration or info.duration <= 0:
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
        f"Duration: {format_duration(info.duration, tenths=bool(info.duration and info.duration < 1))}",
        f"Dimensions: {_dimensions(info)}",
    ]


def _dimensions(info: MediaInfo) -> str:
    """The size as shown: a video whose pixels aren't square shows its display size, then its stored one."""
    stored = f"{info.width or 0}x{info.height or 0}"
    if not anamorphic(info):
        return stored
    assert info.height and info.display_aspect_ratio
    return f"{round(info.height * info.display_aspect_ratio)}x{info.height} (stored {stored})"


def build_frame_command(
    input_path: PathLike,
    timestamp: float,
    width: int,
    height: int,
    ffmpeg: str = "ffmpeg",
    *,
    decode_from: float | None = None,
    origin: float | None = None,
    tone_map: str | None = None,
) -> list[str]:
    """
    One still frame, scaled to the tile, as raw RGB on stdout. Pure. 'V:0' skips cover art.
    `tone_map` (:func:`sashimi.core._hdr.tone_map_filter`) follows the scaling, for HDR video.

    `decode_from` is where decoding starts (seconds, from the file's keyframe index; see
    :func:`sashimi.core._seek.VideoIndex.decode_from`): the frames before `timestamp` are then
    decoded and dropped, counting from `origin` (:meth:`~sashimi.core._seek.VideoIndex.origin`;
    None: `decode_from`). None leaves the seek to ffmpeg (``-ss timestamp``).
    """
    tile = with_tone_map(f"scale={width}:{height},setsar=1", tone_map)
    if decode_from is None:
        seek, vf = ["-ss", fmt_seconds(timestamp)], tile
    else:
        decoding = decoding_args(decode_from, timestamp, origin=origin)
        seek, vf = decoding.inputs, with_filter(decoding.video, tile)
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


def pick_tiles(
    frames: Iterable[tuple[float, bytes]],
    times: Sequence[float],
    held: float,
    time_base: tuple[int, int] | None = None,
) -> Iterator[tuple[int, bytes]]:
    """
    Chooses each tile's frame from timed frames (``(seconds from the file's start, frame)``, in
    display order): for each of `times` (ascending, read to the millisecond), the first frame at
    or after it (as ffmpeg's seek rounds to `time_base`; see :func:`~sashimi.core._seek.reaches`)
    when that starts within `held` seconds, else the frame still on screen at it (the last one
    before it). Tiles can share a frame; tiles after the last frame get the last frame. Yields
    ``(slot, frame)`` as each is settled. Pure.
    """
    wanted = [round(t, 3) for t in times]
    slot = 0
    last: bytes | None = None
    for time, data in frames:
        while slot < len(wanted) and reaches(time, wanted[slot], time_base):
            yield slot, data if last is None or time - wanted[slot] <= held + 1e-6 else last
            slot += 1
        if slot >= len(wanted):
            return
        last = data
    if last is not None:
        for rest in range(slot, len(wanted)):
            yield rest, last


def tile_windows(index: VideoIndex, times: Sequence[float]) -> list[tuple[float, float]]:
    """
    The stretches of video (``(start, end)`` seconds, ascending) a one-pass still sheet keeps for
    `times`: around the tile's time and the frame the index says it shows, with half a frame to
    spare on either side, so :func:`pick_tiles` sees that frame and few others. Pure.
    """
    windows = []
    for t in times:
        shown = index.frame_for(t)
        windows.append((min(t, shown) - index.interval / 2, max(t, shown) + index.interval / 2))
    return windows


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


def few_frames(info: MediaInfo, cells: int) -> bool:
    """
    Whether a video may have fewer frames than a sheet has tiles, by its stated frame rate (with
    room to spare, since rates can be averages): only then is it read to the end to count them.
    """
    if not info.duration:
        return False
    rate = info.fps or 0.0
    return not rate or info.duration * rate < 2 * cells


def clip_frames(index: VideoIndex | None, info: MediaInfo) -> list[float] | None:
    """
    When each of a short video's frames is shown (seconds, in the times sheets use, rounded down
    to the millisecond, so a seek to it lands on that frame), from its index, or from its frame
    rate when it has none; None when neither tells.
    """
    if index is not None and len(index.shown):
        shown = index.frame_times(-1e9, 1e9)
    elif info.fps and info.duration:
        shown = [i / info.fps for i in range(max(1, int(info.duration * info.fps + 1e-6)))]
    else:
        return None
    return [math.floor(time * 1000 + 1e-6) / 1000 for time in shown]


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


def _tiles_in_one_pass(
    input_path: PathLike,
    index: VideoIndex,
    times: Sequence[float],
    width: int,
    height: int,
    threads: int,
    ffmpeg: str,
    tone_map: str | None = None,
) -> Iterator[tuple[int, Image.Image]]:
    decode_from = index.decode_from(min(times[0], index.frame_for(times[0])))
    origin = index.origin(decode_from)
    windows = tile_windows(index, times)
    vf = with_tone_map(f"scale={width}:{height},setsar=1", tone_map)
    cmd = build_pass_command(input_path, windows, vf, threads, ffmpeg, decode_from=decode_from, origin=origin)
    timed = stream_timed_frames(cmd, width * height * 3)
    try:
        frames = ((time + origin, data) for time, data in timed)
        for slot, data in pick_tiles(frames, times, HELD_FRAMES * index.interval, index.time_base):
            yield slot, Image.frombytes("RGB", (width, height), data)
    except BinaryNotFoundError:
        raise
    except ProcessingError as e:
        logger.debug(f"Frame extraction failed: {e}")
    finally:
        timed.close()


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
) -> Outcome:
    """
    Renders a still contact sheet; see :func:`sashimi.sheets.render`.

    Runs up to `max_workers` single-threaded ffmpeg processes at once (default min(4, CPUs));
    each uses about 6 threads, so ``max_workers=2`` stays near 15 threads. Frames are piped, so
    nothing is written to disk except the output. A frame that can't be read leaves its tile empty.
    """
    workers = resolve_max_workers(max_workers)
    info = probe_sheet_source(input_path, MIN_SHEET_SECONDS, TOO_SHORT_FOR_SHEET, limits)
    assert info.duration is not None and info.display_aspect_ratio
    layout: SheetLayout = plan_layout(config.width, config.cols, config.rows, config.gap, info.display_aspect_ratio)
    check_tiles(layout)
    times = frame_times(info.duration, layout.cells, config.start_offset_percent, config.end_offset_percent)
    ffmpeg = ffmpeg_binary()
    tone_map = require_tone_mapping(info, ffmpeg)
    few = few_frames(info, layout.cells)
    index = video_index(input_path, info, info.duration if few else times[-1], limits) if times else None
    notes: list[str] = []
    frames = clip_frames(index, info) if few else None
    if frames is not None and 0 < len(frames) < layout.cells:
        cols, rows = fit_grid(len(frames), layout.cols, layout.rows)
        layout = plan_layout(config.width, cols, rows, config.gap, info.display_aspect_ratio)
        check_tiles(layout)
        times = frames
        notes.append(
            f"This clip has {len(frames)} {'frame' if len(frames) == 1 else 'frames'}, so the sheet shows "
            f"{len(frames)} {'tile' if len(frames) == 1 else 'tiles'} in a {cols}x{rows} grid"
        )

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

    placed = 0
    short = info.duration < 1 or bool(notes)

    def place(slot: int, tile: Image.Image) -> None:
        nonlocal placed
        if config.show_timestamps:
            label = timestamp_label(
                format_duration(times[slot], tenths=short),
                ts_font,
                config.timestamp_bg_color,
                config.timestamp_text_color,
            )
            stamp(tile, label)
        x, y = layout.cell_origin(slot)
        sheet.paste(tile, (x, header_h + y))
        placed += 1

    if sheet_route(index, times) == "pass":
        assert index is not None
        for done, (slot, tile) in enumerate(
            _tiles_in_one_pass(input_path, index, times, layout.thumb_w, layout.thumb_h, workers, ffmpeg, tone_map),
            start=1,
        ):
            place(slot, tile)
            if on_progress:
                on_progress(0.9 * done / len(times), "extracting")
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for slot, t in enumerate(times):
                at = index.seek_time(t) if index is not None else t
                begin = index.start_for(at) if index is not None else None
                cmd = build_frame_command(
                    input_path,
                    at,
                    layout.thumb_w,
                    layout.thumb_h,
                    ffmpeg,
                    decode_from=begin,
                    origin=index.origin(begin) if index is not None and begin is not None else None,
                    tone_map=tone_map,
                )
                futures[pool.submit(_grab_frame, cmd, layout.thumb_w, layout.thumb_h)] = slot
            for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                if (grabbed := future.result()) is not None:
                    place(futures[future], grabbed)
                if on_progress:
                    on_progress(0.9 * done / len(futures), "extracting")
    if placed < len(times):
        logger.warning(
            f"{len(times) - placed} of {len(times)} contact sheet frames couldn't be read; their tiles are empty."
        )
    if not placed:
        raise UnsupportedMediaError(NO_FRAMES)

    with atomic_output(output_path, scratch_dir) as temp:
        if isinstance(config, JpegSheetConfig):
            sheet.save(temp, "JPEG", quality=config.quality, optimize=True, progressive=True)
        elif isinstance(config, WebpSheetConfig):
            sheet.save(temp, "WEBP", quality=config.quality)
        else:
            sheet.save(temp, "PNG", compress_level=6)
    changes = [f"{len(times)} {'frame' if len(times) == 1 else 'frames'} on a contact sheet"]
    if tone_map:
        changes.append(f"tone-mapped from {hdr_name(info)}")
    if on_progress:
        on_progress(1.0, "done")
    return Outcome(fidelity=derived(input_path, "picture", changes, media=True, notes=notes))
