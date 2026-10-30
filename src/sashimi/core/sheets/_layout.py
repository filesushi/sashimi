# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Pure layout maths for contact sheets: grids, tile sizes and sample times. No I/O."""

import math
from dataclasses import dataclass

from sashimi.config import MIN_TILE_SIZE, GridValue
from sashimi.config._limits import TILES_TOO_SMALL
from sashimi.errors import InvalidOptionsError, MediaTooLargeError

BAD_SHAPE = "This video is too tall or too wide to make a contact sheet."


ANIMATED_GRID = (3, 3)
"""The automatic grid of an animated sheet: 3x3, for legible tiles and a quick render."""
ANIMATED_PORTRAIT_GRID = (4, 2)
"""The automatic grid of an animated sheet of a portrait video (aspect ratio under 0.95)."""


def grid_for_aspect(aspect_ratio: float, animated: bool = False) -> tuple[int, int]:
    """
    Columns x rows that suit a video's display aspect ratio (portrait videos get wide, short
    grids). Animated sheets use fewer, larger tiles: 3x3, or 4x2 for portrait videos.
    """
    if animated:
        return ANIMATED_PORTRAIT_GRID if aspect_ratio < 0.95 else ANIMATED_GRID
    table = [
        (0.6, (8, 2)),
        (0.65, (7, 2)),
        (0.75, (6, 2)),
        (0.95, (5, 2)),
        (1.25, (6, 3)),
        (1.55, (6, 4)),
        (1.85, (6, 5)),
        (2.15, (6, 6)),
        (2.55, (6, 7)),
    ]
    for upper, grid in table:
        if aspect_ratio < upper:
            return grid
    return (6, 8)


def choose_grid(cols: GridValue, rows: GridValue, aspect_ratio: float, animated: bool = False) -> tuple[int, int]:
    """Explicit cols/rows win individually; 'auto' dimensions come from the aspect ratio."""
    auto_cols, auto_rows = grid_for_aspect(aspect_ratio, animated)
    return (cols if isinstance(cols, int) else auto_cols, rows if isinstance(rows, int) else auto_rows)


@dataclass(frozen=True)
class SheetLayout:
    cols: int
    rows: int
    thumb_w: int
    thumb_h: int
    gap: int

    @property
    def cells(self) -> int:
        return self.cols * self.rows

    @property
    def grid_width(self) -> int:
        return self.cols * self.thumb_w + (self.cols + 1) * self.gap

    @property
    def grid_height(self) -> int:
        return self.rows * self.thumb_h + (self.rows + 1) * self.gap

    def cell_origin(self, slot: int) -> tuple[int, int]:
        """Top-left of a cell, relative to the top-left of the grid."""
        row, col = divmod(slot, self.cols)
        return (self.gap + col * (self.thumb_w + self.gap), self.gap + row * (self.thumb_h + self.gap))


def plan_layout(
    width: int,
    cols: GridValue,
    rows: GridValue,
    gap: int,
    aspect_ratio: float,
    even: bool = False,
    animated: bool = False,
) -> SheetLayout:
    """
    Fits the grid into `width` pixels. Tile height follows the display aspect ratio.
    `even` rounds tiles to even sizes, which video encoders require. `animated` picks the
    animated sheets' automatic grid (see :func:`grid_for_aspect`).
    """
    n_cols, n_rows = choose_grid(cols, rows, aspect_ratio, animated)
    thumb_w = max(1, (width - (n_cols + 1) * gap) // n_cols)
    if even:
        thumb_w = max(2, thumb_w // 2 * 2)
        thumb_h = max(2, round(thumb_w / aspect_ratio / 2) * 2)
    else:
        thumb_h = max(1, round(thumb_w / aspect_ratio))
    return SheetLayout(n_cols, n_rows, thumb_w, thumb_h, gap)


def fit_grid(count: int, cols: int, rows: int) -> tuple[int, int]:
    """
    Columns x rows for `count` tiles, as close to a `cols` x `rows` grid's shape as fits: no empty
    cells where a grid without them keeps a reasonable shape (15 tiles in place of 6x4 give 5x3).
    Pure.
    """
    target = math.log(cols / rows)
    best: tuple[float, int, int] | None = None
    for c in range(1, count + 1):
        r = -(-count // c)
        score = (c * r - count) + abs(math.log(c / r) - target)
        if best is None or score <= best[0] + 1e-9:
            best = (score, c, r)
    assert best is not None
    return best[1], best[2]


def check_tiles(layout: SheetLayout) -> None:
    """
    Refuses layouts whose tiles would be smaller than :data:`~sashimi.config.MIN_TILE_SIZE` on
    either side: narrow tiles come from the settings, flat tiles from a very wide video.
    """
    if layout.thumb_w < MIN_TILE_SIZE:
        raise InvalidOptionsError(TILES_TOO_SMALL, detail=f"tile {layout.thumb_w}x{layout.thumb_h}")
    if layout.thumb_h < MIN_TILE_SIZE:
        raise MediaTooLargeError(BAD_SHAPE, detail=f"tile {layout.thumb_w}x{layout.thumb_h}")


def sample_window(
    duration: float, start_offset_percent: float, end_offset_percent: float, min_length: float
) -> tuple[float, float]:
    """The part of the video to sample from; the whole video if the offsets leave less than `min_length`."""
    start = duration * start_offset_percent / 100.0
    end = duration - duration * end_offset_percent / 100.0
    if end - start < min_length or start >= end:
        return 0.0, duration
    return start, end


def frame_times(duration: float, count: int, start_offset_percent: float, end_offset_percent: float) -> list[float]:
    """Evenly spaced still-frame times for a static sheet, kept just inside the video."""
    start, end = sample_window(duration, start_offset_percent, end_offset_percent, 0.0)
    if count == 1:
        times = [start + (end - start) / 2.0]
    else:
        step = (end - start) / (count - 1)
        times = [start + i * step for i in range(count)]
    upper = max(0.01, duration - 0.01)
    return [min(max(t, 0.01), upper) for t in times]


def clip_starts(
    duration: float, count: int, clip_length: float, start_offset_percent: float, end_offset_percent: float
) -> list[float]:
    """Evenly spaced clip start times for an animated sheet; every clip fits inside the video."""
    start, end = sample_window(duration, start_offset_percent, end_offset_percent, clip_length)
    last_start = max(start, end - clip_length)
    if count == 1:
        times = [start + (last_start - start) / 2.0]
    else:
        step = (last_start - start) / (count - 1)
        times = [start + i * step for i in range(count)]
    upper = max(0.0, duration - clip_length)
    return [min(max(t, 0.0), upper) for t in times]


@dataclass(frozen=True)
class ClipPlan:
    """An animated sheet's clips: where each starts, how long each is, and the grid that holds them."""

    starts: list[float]
    clip_length: float
    cols: int
    rows: int
    note: str | None


def _seconds(value: float) -> str:
    return f"{value:g} second{'' if value == 1 else 's'}"


def plan_clips(
    duration: float,
    cols: int,
    rows: int,
    clip_length: float,
    start_offset_percent: float,
    end_offset_percent: float,
) -> ClipPlan:
    """
    The clips of an animated sheet with a `cols` x `rows` grid: as many as fit in the sampled part
    of the video without overlapping, evenly spaced, in a smaller grid with a
    note when fewer than the grid's cells fit. A video shorter than one clip gets one clip of the
    whole video. One computation gives the clips, the grid and the note, so they always agree. Pure.
    """
    if duration < clip_length:
        return ClipPlan(
            [0.0],
            duration,
            1,
            1,
            f"This video is {_rounded(duration, clip_length)} long, shorter than a clip of {_seconds(clip_length)}, "
            "so the sheet shows one clip of the whole video",
        )
    cells = cols * rows
    window_start, window_end = sample_window(duration, start_offset_percent, end_offset_percent, clip_length)
    fitting = max(1, int((window_end - window_start) / clip_length + 1e-9))
    note = None
    if fitting < cells:
        cols, rows = fit_grid(fitting, cols, rows)
        if fitting == 1:
            note = f"This video is {_rounded(duration)} long, so the sheet shows one clip of {_seconds(clip_length)}"
        else:
            note = (
                f"This video is {_rounded(duration)} long, so the sheet shows {fitting} clips of "
                f"{_seconds(clip_length)} in a {cols}x{rows} grid, so that no two clips overlap"
            )
    count = min(fitting, cells)
    starts = clip_starts(duration, count, clip_length, start_offset_percent, end_offset_percent)
    return ClipPlan(starts, clip_length, cols, rows, note)


def _rounded(duration: float, below: float | None = None) -> str:
    places = 1 if duration >= 1 else 2
    shown = round(duration, places)
    while below is not None and shown >= below and places < 3:
        places += 1
        shown = round(duration, places)
    if below is not None and shown >= below:
        shown = math.floor(duration * 1000) / 1000
    return _seconds(shown)
