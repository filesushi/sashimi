"""Regression tests for the shared contact sheet machinery (rotation, grid, slots, atomic writes, headers)."""

import pathlib
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from pydantic import ValidationError

from sashimi.config.schemas.thumbs import AnimatedThumbsConfig, StaticThumbsConfig
from sashimi.file._sheet import atomic_output
from sashimi.file.animated_thumbs import AnimatedContactSheetGenerator
from sashimi.file.thumbs import ContactSheetGenerator, _extract_single_frame_task, _process_file


class DummyTrack:
    def __init__(self, track_type, **kwargs):
        self.track_type = track_type
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getattr__(self, item):
        return None


def _media_info(**video_kwargs):
    info = MagicMock()
    info.tracks = [
        DummyTrack("General", duration="100000.0", file_size=1234),
        DummyTrack("Video", duration="100000.0", **video_kwargs),
    ]
    return info


@pytest.mark.parametrize("rotation", ["90.000", "270.000", "-90"])
@patch("sashimi.file._sheet.MediaInfo.parse")
def test_rotated_video_uses_display_orientation(mock_parse, rotation):
    mock_parse.return_value = _media_info(width=1920, height=1080, display_aspect_ratio="1.778", rotation=rotation)

    gen = ContactSheetGenerator(pathlib.Path("phone.mp4"), StaticThumbsConfig())

    assert (gen.metadata["width"], gen.metadata["height"]) == (1080, 1920)
    assert gen.metadata["display_aspect_ratio"] == "0.562"
    assert (gen.grid_cols, gen.grid_rows) == (7, 2)
    assert gen.thumb_h > gen.thumb_w


@patch("sashimi.file._sheet.MediaInfo.parse")
def test_thumb_height_follows_display_aspect_ratio(mock_parse):
    # Anamorphic: 720x576 storage displayed at 16:9.
    mock_parse.return_value = _media_info(width=720, height=576, display_aspect_ratio="1.778")

    gen = ContactSheetGenerator(pathlib.Path("dvd.mkv"), StaticThumbsConfig(cols=4, rows=4, static_width=1000, gap=0))

    assert gen.thumb_w == 250
    assert gen.thumb_h == round(250 / 1.778)


@patch("sashimi.file._sheet.MediaInfo.parse")
def test_single_grid_dimension_override(mock_parse):
    mock_parse.return_value = _media_info(width=1920, height=1080)

    only_cols = ContactSheetGenerator(pathlib.Path("a.mp4"), StaticThumbsConfig(cols=3))
    only_rows = ContactSheetGenerator(pathlib.Path("a.mp4"), StaticThumbsConfig(rows=2))

    assert (only_cols.grid_cols, only_cols.grid_rows) == (3, 5)
    assert (only_rows.grid_cols, only_rows.grid_rows) == (6, 2)


@pytest.mark.parametrize("value, expected", [("AUTO", "auto"), (" Auto ", "auto"), (None, "auto"), ("4", 4), (7, 7)])
def test_grid_dimension_parsing(value, expected):
    assert StaticThumbsConfig(cols=value).cols == expected


@pytest.mark.parametrize("value", [0, -1, "0", "abc", 2.5, True])
def test_grid_dimension_rejects_invalid(value):
    with pytest.raises(ValidationError):
        StaticThumbsConfig(cols=value)


@patch("sashimi.file._sheet.MediaInfo.parse", side_effect=OSError("libmediainfo not found"))
def test_mediainfo_failure_is_reported_as_error(mock_parse, tmp_path):
    video = tmp_path / "a.mp4"
    video.touch()

    status, message = _process_file(video, StaticThumbsConfig(), force=True)

    assert status == "error"
    assert "libmediainfo not found" in message


@patch("sashimi.file.thumbs.ContactSheetGenerator._calculate_timestamps", return_value=[1.0, 2.0])
@patch("sashimi.file.thumbs._extract_single_frame_task")
@patch("sashimi.file._sheet.MediaInfo.parse")
def test_failed_frame_leaves_gap_instead_of_shifting(mock_parse, mock_extract, mock_ts, tmp_path):
    mock_parse.return_value = _media_info(width=1920, height=1080)
    red = tmp_path / "red.jpg"
    Image.new("RGB", (10, 10), (255, 0, 0)).save(red)
    mock_extract.side_effect = lambda ts, *args: None if ts == 1.0 else (red, ts)

    config = StaticThumbsConfig(cols=2, rows=1, static_width=400, gap=10)
    gen = ContactSheetGenerator(pathlib.Path("a.mp4"), config)
    out = tmp_path / "out.jpeg"
    gen.generate(out)

    centre_y = gen.header_h + config.gap + gen.thumb_h // 2
    with Image.open(out) as img:
        slot0 = img.getpixel((config.gap + gen.thumb_w // 2, centre_y))
        slot1 = img.getpixel((2 * config.gap + gen.thumb_w + gen.thumb_w // 2, centre_y))
    assert max(abs(a - b) for a, b in zip(slot0, config.grid_bg_color, strict=True)) < 10
    assert slot1[0] > 200 and slot1[1] < 60


@patch("sashimi.file.thumbs.subprocess.run")
def test_frame_extraction_skips_cover_art_and_forces_size(mock_run, tmp_path):
    _extract_single_frame_task(1.0, tmp_path, pathlib.Path("a.mp4"), 200, 112)

    cmd = " ".join(mock_run.call_args.args[0])
    assert "[0:V:0]" in cmd
    assert "scale=200:112" in cmd


@patch("sashimi.file._sheet.MediaInfo.parse")
def test_animated_offsets_fall_back_to_full_duration(mock_parse):
    mock_parse.return_value = _media_info(width=1920, height=1080)
    config = AnimatedThumbsConfig(cols=5, rows=1, sample_duration=2.0, start_offset_percent=60, end_offset_percent=60)

    timestamps = AnimatedContactSheetGenerator(pathlib.Path("a.mp4"), config)._calculate_timestamps()

    assert timestamps[0] == pytest.approx(0.0)
    assert timestamps[-1] == pytest.approx(98.0)
    assert len(set(timestamps)) == 5


@patch("sashimi.file._sheet.MediaInfo.parse")
def test_animated_thumbs_have_even_dimensions(mock_parse):
    mock_parse.return_value = _media_info(width=1920, height=1080, rotation="90")

    gen = AnimatedContactSheetGenerator(pathlib.Path("a.mp4"), AnimatedThumbsConfig(animated_width=1001))

    assert gen.thumb_w % 2 == 0 and gen.thumb_h % 2 == 0
    assert gen.thumb_h > gen.thumb_w


@patch("sashimi.file._sheet.Pilmoji", side_effect=OSError("offline"))
@patch("sashimi.file._sheet.MediaInfo.parse")
def test_header_falls_back_when_emoji_rendering_fails(mock_parse, mock_pilmoji):
    mock_parse.return_value = _media_info(width=1920, height=1080)
    gen = ContactSheetGenerator(pathlib.Path("🎬 movie.mp4"), StaticThumbsConfig())

    header = gen._render_header(300)

    assert header.size == (300, gen.header_h)
    assert header.getbbox() is not None  # text was drawn


def test_atomic_output_moves_into_place_on_success(tmp_path):
    out = tmp_path / "a.mp4.jpeg"
    with atomic_output(out) as temp:
        assert temp.suffix == ".jpeg" and temp.parent == tmp_path
        temp.write_bytes(b"data")

    assert out.read_bytes() == b"data"
    assert list(tmp_path.iterdir()) == [out]


@pytest.mark.parametrize("exc", [RuntimeError("encode failed"), KeyboardInterrupt()])
def test_atomic_output_leaves_nothing_on_failure(tmp_path, exc):
    out = tmp_path / "a.mp4.webp"
    with pytest.raises(type(exc)):
        with atomic_output(out) as temp:
            temp.write_bytes(b"partial")
            raise exc

    assert list(tmp_path.iterdir()) == []
