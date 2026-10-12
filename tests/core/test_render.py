import sys

import pytest
from PIL import Image

from sashimi.core.sheets import _render


def test_format_duration_and_size():
    assert _render.format_duration(65.5) == "01:05"
    assert _render.format_duration(3665.0) == "1:01:05"
    assert _render.format_duration(None) == "N/A"
    assert _render.format_size(500) == "500 B"
    assert _render.format_size(1048576) == "1.0 MB"
    assert _render.format_size(None) == "N/A"


def test_label_sits_in_bottom_right_corner():
    font = _render.load_font(None, 14)
    label = _render.timestamp_label("01:05", font, (0, 0, 0, 255), (255, 255, 255))
    tile = Image.new("RGB", (200, 100), (0, 255, 0))
    _render.stamp(tile, label)
    assert tile.getpixel((199 - 2, 99 - 2)) == (0, 0, 0)
    assert tile.getpixel((5, 5)) == (0, 255, 0)


def header(emoji_dir=None, text="🎬 holiday.mkv"):
    font = _render.load_font(None, 18)
    style = _render.HeaderStyle(font, 18)
    return _render.render_header(400, [text], style, (0, 0, 0), (255, 255, 255), emoji_dir)


def test_plain_header_does_not_load_pilmoji(monkeypatch):
    monkeypatch.delitem(sys.modules, "pilmoji", raising=False)
    img = header()
    assert img.size[0] == 400 and img.getbbox() is not None
    assert "pilmoji" not in sys.modules


def test_emoji_come_from_local_directory_only(tmp_path):
    pytest.importorskip("pilmoji")
    Image.new("RGBA", (72, 72), (255, 0, 0, 255)).save(tmp_path / "1f3ac.png")
    img = header(str(tmp_path))
    reds = [p for p in img.getdata() if p[0] > 200 and p[1] < 50 and p[2] < 50]
    assert reds, "the local emoji image should have been drawn"


def test_missing_emoji_file_falls_back_quietly(tmp_path):
    pytest.importorskip("pilmoji")
    assert header(str(tmp_path)).getbbox() is not None
