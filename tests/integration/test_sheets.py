# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import hashlib
from unittest.mock import patch

import pytest
from PIL import Image

from sashimi import sheets
from sashimi.config import CONTACT_SHEET, AnimatedWebpSheetConfig, JpegSheetConfig, PngSheetConfig
from sashimi.errors import InvalidOptionsError, MediaTooShortError, UnsupportedMediaError

from tests.conftest import leftovers, needs_ffmpeg

pytestmark = needs_ffmpeg


def close(pixel, color, tolerance=25):
    return max(abs(a - b) for a, b in zip(pixel[:3], color, strict=True)) < tolerance


def test_static_sheet(video_blob, tmp_path):
    out = tmp_path / "result.bin"
    progress = []
    config = JpegSheetConfig(width=640, cols=3, rows=2)
    sheets.render(video_blob, out, config, display_name="holiday.mkv", on_progress=lambda f, s: progress.append(f))
    with Image.open(out) as img:
        assert img.format == "JPEG" and img.width == 640
    assert progress[-1] == 1.0 and leftovers(tmp_path) == []


def test_static_sheet_is_deterministic(video_blob, tmp_path):
    config = JpegSheetConfig(width=480, cols=2, rows=2)
    for name in ("a", "b"):
        sheets.render(video_blob, tmp_path / name, config, max_workers=2)
    assert (
        hashlib.sha256((tmp_path / "a").read_bytes()).digest() == hashlib.sha256((tmp_path / "b").read_bytes()).digest()
    )


def test_rotated_video_tiles_fit_their_slots(rotated_video_blob, tmp_path):
    from sashimi.core.sheets._layout import plan_layout
    from sashimi.core.sheets._render import HeaderStyle, load_font

    config = JpegSheetConfig(width=400, cols=2, rows=2, gap=6, start_offset_percent=0, end_offset_percent=0)
    out = tmp_path / "r"
    sheets.render(rotated_video_blob, out, config)
    layout = plan_layout(400, 2, 2, 6, 180 / 320)
    header_h = HeaderStyle(load_font(None, 18), 18).height
    assert layout.thumb_h > layout.thumb_w
    gap_y = header_h + 6 + layout.thumb_h + 3
    with Image.open(out) as img:
        assert img.height == header_h + layout.grid_height
        assert close(img.getpixel((6 + layout.thumb_w // 2, gap_y)), config.grid_bg_color)


def test_failed_frame_leaves_its_tile_empty(video_blob, tmp_path):
    from sashimi.core.sheets import static

    real = static._grab_frame
    calls = []

    def flaky(cmd, w, h):
        calls.append(cmd)
        return None if len(calls) == 1 else real(cmd, w, h)

    config = JpegSheetConfig(width=400, cols=2, rows=1, gap=10)
    with patch.object(static, "_grab_frame", flaky):
        sheets.render(video_blob, tmp_path / "r", config, max_workers=1)
    with Image.open(tmp_path / "r") as img:
        row_y = img.height - 10 - 20
        assert close(img.getpixel((60, row_y)), config.grid_bg_color)
        assert not close(img.getpixel((img.width - 60, row_y)), config.grid_bg_color)


def test_sheet_user_errors(junk_blob, tmp_path):
    with pytest.raises(UnsupportedMediaError):
        sheets.render(junk_blob, tmp_path / "r", JpegSheetConfig())
    with pytest.raises(UnsupportedMediaError):
        sheets.render(junk_blob, tmp_path / "r", AnimatedWebpSheetConfig())


def test_too_short_for_animated(silent_video_blob, tmp_path):
    with pytest.raises(MediaTooShortError):
        sheets.render(silent_video_blob, tmp_path / "r", AnimatedWebpSheetConfig(sample_duration=5))


def test_animated_memory_guard(video_blob, tmp_path):
    config = AnimatedWebpSheetConfig(width=3840, cols=20, rows=20, fps=30, sample_duration=5)
    with pytest.raises(InvalidOptionsError):
        sheets.render(video_blob, tmp_path / "r", config)


@pytest.mark.parametrize(
    "target, loop, expected_loop", [("gif", True, 0), ("gif", False, None), ("webp", True, 0), ("webp", False, 1)]
)
def test_animated_sheet(video_blob, tmp_path, target, loop, expected_loop):
    params = {"animated": True, "loop": loop, "width": 400, "cols": 2, "rows": 2, "sample_duration": 1, "fps": 5}
    config = CONTACT_SHEET.from_params(params, target=target)
    out = tmp_path / "result.bin"
    progress = []
    sheets.render(video_blob, out, config, max_workers=2, on_progress=lambda f, s: progress.append(f))
    with Image.open(out) as img:
        assert img.format == target.upper() and img.n_frames == 5
        assert img.info.get("loop") == expected_loop
    assert progress[-1] == 1.0 and leftovers(tmp_path) == []


def test_sheet_without_header_or_timestamps_as_png(video_blob, tmp_path):
    out = tmp_path / "r"
    config = PngSheetConfig(width=400, cols=2, rows=1, gap=0, show_header=False, show_timestamps=False)
    sheets.render(video_blob, out, config)
    with Image.open(out) as img:
        assert img.format == "PNG"
        assert img.height == round(200 / (320 / 180))
        corner = img.convert("RGB").getpixel((img.width // 2 - 3, img.height - 3))
        assert not close(corner, (0, 0, 0), tolerance=10)


def test_animated_sheet_options(video_blob, tmp_path):
    out = tmp_path / "r"
    config = AnimatedWebpSheetConfig(
        width=320, cols=2, rows=1, fps=4, sample_duration=1, show_header=False, grid_bg_color="#ff0000", gap=10
    )
    sheets.render(video_blob, out, config)
    with Image.open(out) as img:
        assert close(img.convert("RGB").getpixel((2, 2)), (255, 0, 0))


def test_fonts_are_arguments_not_options(video_blob, tmp_path):
    from sashimi.core.sheets import _render

    seen = []
    real = _render.load_font.__wrapped__

    def spy(path, size):
        seen.append(path)
        return real(None, size)

    config = JpegSheetConfig(width=320, cols=2, rows=1)
    with patch("sashimi.core.sheets.static.load_font", spy):
        sheets.render(video_blob, tmp_path / "r", config, header_font=tmp_path / "h.ttf", timestamp_font="t.ttf")
    assert seen == [str(tmp_path / "h.ttf"), "t.ttf"]
