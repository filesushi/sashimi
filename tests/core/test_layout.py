# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pytest

from sashimi.core.sheets._layout import (
    choose_grid,
    clip_starts,
    frame_times,
    grid_for_aspect,
    plan_clips,
    plan_layout,
    sample_window,
)


@pytest.mark.parametrize(
    "ratio, grid",
    [
        (16 / 9, (6, 5)),
        (4 / 3, (6, 4)),
        (2.35, (6, 7)),
        (0.56, (8, 2)),
        (9 / 16, (8, 2)),
        (0.62, (7, 2)),
        (3.0, (6, 8)),
    ],
)
def test_grid_for_aspect(ratio, grid):
    assert grid_for_aspect(ratio) == grid


@pytest.mark.parametrize(
    "ratio, grid", [(16 / 9, (3, 3)), (4 / 3, (3, 3)), (1.0, (3, 3)), (2.35, (3, 3)), (9 / 16, (4, 2)), (0.8, (4, 2))]
)
def test_animated_grid_for_aspect(ratio, grid):
    assert grid_for_aspect(ratio, animated=True) == grid
    assert plan_layout(1280, "auto", "auto", 5, ratio, even=True, animated=True).cells == grid[0] * grid[1]


def test_animated_grid_keeps_explicit_dimensions():
    assert choose_grid(5, "auto", 16 / 9, animated=True) == (5, 3)


def test_single_dimension_override():
    assert choose_grid(3, "auto", 16 / 9) == (3, 5)
    assert choose_grid("auto", 2, 16 / 9) == (6, 2)
    assert choose_grid(4, 4, 16 / 9) == (4, 4)


def test_plan_layout_fits_width_and_follows_aspect():
    layout = plan_layout(1000, 4, 4, 0, 16 / 9)
    assert (layout.thumb_w, layout.thumb_h) == (250, round(250 / (16 / 9)))
    assert layout.grid_width == 1000

    layout = plan_layout(2560, "auto", "auto", 5, 16 / 9)
    assert layout.grid_width <= 2560 and layout.cells == 30
    assert layout.cell_origin(0) == (5, 5)
    assert layout.cell_origin(7) == (5 + layout.thumb_w + 5, 5 + layout.thumb_h + 5)


def test_even_layout_for_video_encoders():
    layout = plan_layout(1001, "auto", "auto", 5, 9 / 16, even=True)
    assert layout.thumb_w % 2 == 0 and layout.thumb_h % 2 == 0
    assert layout.thumb_h > layout.thumb_w


def test_frame_times_match_previous_engine():
    times = frame_times(100.0, 30, 7.0, 7.0)
    assert len(times) == 30
    assert round(times[0], 2) == 7.0 and round(times[-1], 2) == 93.0


def test_frame_times_stay_inside_video():
    times = frame_times(10.0, 5, 0.0, 0.0)
    assert times[0] == 0.01 and times[-1] == pytest.approx(9.99)
    assert frame_times(10.0, 1, 0, 0) == [5.0]


def test_offsets_that_leave_nothing_fall_back_to_whole_video():
    assert sample_window(100.0, 60, 60, 0.0) == (0.0, 100.0)


def test_clip_starts_fall_back_and_fit():
    starts = clip_starts(100.0, 5, 2.0, 60, 60)
    assert starts[0] == pytest.approx(0.0) and starts[-1] == pytest.approx(98.0)
    assert len(set(starts)) == 5
    assert all(s + 2.0 <= 100.0 for s in clip_starts(10.0, 30, 2.0, 0, 0))


@pytest.mark.parametrize("grid", [(3, 3), (4, 2), (2, 2), (1, 1), (6, 1), (5, 4)])
@pytest.mark.parametrize("clip_length", [0.5, 1.0, 2.0, 3.0])
@pytest.mark.parametrize("offsets", [(7.0, 7.0), (0.0, 0.0), (30.0, 10.0), (60.0, 60.0)])
def test_planned_clips_never_overlap_and_agree_with_their_note(grid, clip_length, offsets):
    durations = [0.04, 0.5, 1.27, 1.5, 1.96, 2.0, 2.04, 3.3, 6.0, 7.6, 12.0, 21.0, 23.5, 60.0, 3600.0]
    for duration in durations:
        plan = plan_clips(duration, *grid, clip_length, *offsets)
        count = len(plan.starts)
        assert 1 <= count <= grid[0] * grid[1]
        assert plan.cols * plan.rows >= count
        assert (plan.cols - 1) * plan.rows < count and plan.cols * (plan.rows - 1) < count
        assert plan.clip_length == min(clip_length, duration)
        assert plan.starts == sorted(plan.starts)
        for first, second in zip(plan.starts, plan.starts[1:], strict=False):
            assert second - first >= plan.clip_length - 1e-9, (duration, plan)
        for start in plan.starts:
            assert 0.0 <= start and start + plan.clip_length <= duration + 1e-9
        if duration < clip_length:
            assert count == 1 and plan.starts == [0.0]
            assert plan.note is not None and "one clip of the whole video" in plan.note
            shown = float(plan.note.split("This video is ")[1].split(" second")[0])
            assert shown < clip_length
        elif count < grid[0] * grid[1]:
            assert plan.note is not None
            assert (count == 1 and "shows one clip of" in plan.note) or (
                f"shows {count} clips" in plan.note and f"in a {plan.cols}x{plan.rows} grid" in plan.note
            )
        else:
            assert plan.note is None and (plan.cols, plan.rows) == grid


@pytest.mark.parametrize(
    "duration, count, grid",
    [(7.6, 3, (2, 2)), (12.0, 5, (3, 2)), (21.0, 9, (3, 3)), (1.27, 1, (1, 1)), (1.5, 1, (1, 1))],
)
def test_short_videos_get_fewer_clips_in_a_smaller_grid(duration, count, grid):
    plan = plan_clips(duration, 3, 3, 2.0, 7.0, 7.0)
    assert len(plan.starts) == count and (plan.cols, plan.rows) == grid
