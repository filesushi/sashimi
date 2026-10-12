import pytest

from sashimi.core.sheets._layout import (
    choose_grid,
    clip_starts,
    frame_times,
    grid_for_aspect,
    plan_layout,
    sample_window,
)


@pytest.mark.parametrize(
    "ratio, grid", [(16 / 9, (6, 5)), (4 / 3, (6, 4)), (2.35, (6, 7)), (0.56, (7, 2)), (9 / 16, (7, 2)), (3.0, (6, 8))]
)
def test_grid_for_aspect(ratio, grid):
    assert grid_for_aspect(ratio) == grid


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
