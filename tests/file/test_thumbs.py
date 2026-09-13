import pytest
import pathlib
from unittest.mock import MagicMock, patch

from PIL import Image

from sashimi.config.schemas.thumbs import StaticThumbsConfig
from sashimi.file.thumbs import (
    is_video_file,
    _format_duration,
    _format_size,
    get_grid_dimensions,
    generate_contact_sheets,
    _extract_single_frame_task,
    _process_file,
    ContactSheetGenerator,
)


class DummyTrack:
    """A deterministic stub for pymediainfo tracks to avoid MagicMock side-effects."""

    def __init__(self, track_type, **kwargs):
        self.track_type = track_type
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getattr__(self, item):

        return None


@pytest.fixture
def base_config():
    return StaticThumbsConfig()


def test_get_grid_dimensions():

    assert get_grid_dimensions(1.777) == "6x5"
    assert get_grid_dimensions(1.333) == "6x4"
    assert get_grid_dimensions(2.35) == "6x7"
    assert get_grid_dimensions(0.56) == "7x2"

    assert get_grid_dimensions("1.778") == "6x5"

    assert get_grid_dimensions("16:9") == "6x5"
    assert get_grid_dimensions("4:3") == "6x4"


def test_is_video_file(base_config):
    assert is_video_file(pathlib.Path("movie.mp4"), base_config.video_extensions) is True
    assert is_video_file(pathlib.Path("movie.MKV"), base_config.video_extensions) is True
    assert is_video_file(pathlib.Path("document.txt"), base_config.video_extensions) is False


def test_format_duration():
    assert _format_duration(65.5) == "01:05"
    assert _format_duration(3665.0) == "1:01:05"
    assert _format_duration(None) == "N/A"
    assert _format_duration("invalid") == "N/A"


def test_format_size():
    assert _format_size(500) == "500 B"
    assert _format_size(1048576) == "1.0 MB"
    assert _format_size(None) == "N/A"


@patch("sashimi.file.thumbs.pathlib.Path.exists")
@patch("sashimi.file.thumbs.pathlib.Path.is_dir")
@patch("sashimi.file.thumbs.pathlib.Path.is_file")
def test_generate_contact_sheets_orchestration(mock_is_file, mock_is_dir, mock_exists, base_config):
    p1 = pathlib.Path("test1.mp4")

    mock_exists.return_value = True
    mock_is_dir.return_value = False
    mock_is_file.return_value = True

    generator = generate_contact_sheets([p1], config=base_config, force=True)

    count = next(generator)
    assert count == 1

    with patch("sashimi.file.thumbs._process_file") as mock_process:
        mock_process.return_value = ("success", "Contact sheet created")

        starting_yield = next(generator)
        assert starting_yield[1] == "starting"

        result_yield = next(generator)
        assert result_yield[1] == "success"


@patch("sashimi.file.thumbs.MediaInfo.parse")
def test_contact_sheet_generator_metadata(mock_parse, base_config):

    dummy_track = DummyTrack(track_type="Video", duration="60000.0", width=1920, height=1080)

    mock_media_info = MagicMock()
    mock_media_info.tracks = [dummy_track]
    mock_parse.return_value = mock_media_info

    generator = ContactSheetGenerator(pathlib.Path("dummy.mp4"), config=base_config)

    assert generator.metadata["duration"] == 60.0
    assert generator.metadata["width"] == 1920

    assert generator.metadata["display_aspect_ratio"] == "1.778"

    assert generator.grid_cols == 6
    assert generator.num_samples == 30


@patch("sashimi.file.thumbs.MediaInfo.parse")
def test_calculate_timestamps(mock_parse, base_config):

    dummy_track = DummyTrack(track_type="Video", duration="100000.0", width=1920, height=1080)

    mock_media_info = MagicMock()
    mock_media_info.tracks = [dummy_track]
    mock_parse.return_value = mock_media_info

    generator = ContactSheetGenerator(pathlib.Path("dummy.mp4"), config=base_config)

    timestamps = generator._calculate_timestamps()

    assert len(timestamps) == 30
    assert round(timestamps[0], 2) == 7.0
    assert round(timestamps[-1], 2) == 93.0


@patch("sashimi.file.thumbs.subprocess.run")
def test_extract_single_frame_task(mock_run, fs):

    temp_dir = pathlib.Path("/my_fake_tmp")
    fs.create_dir("/my_fake_tmp")

    expected_out = temp_dir / "frame_1.000.jpg"
    fs.create_file(str(expected_out), contents="dummy_image_data")
    assert _extract_single_frame_task(1.0, temp_dir, pathlib.Path("test.mp4"), 200) == (expected_out, 1.0)

    fs.create_file(str(temp_dir / "frame_2.000.jpg"), contents="")
    assert _extract_single_frame_task(2.0, temp_dir, pathlib.Path("test.mp4"), 200) is None

    mock_run.side_effect = Exception("FFmpeg crash")
    assert _extract_single_frame_task(3.0, temp_dir, pathlib.Path("test.mp4"), 200) is None


@patch("sashimi.file.thumbs.ContactSheetGenerator.generate")
def test_process_file_branches(mock_gen, base_config, fs):
    p = pathlib.Path("/test.mp4")

    fs.create_file("/test.mp4.jpeg")

    assert _process_file(p, base_config, False) == ("exists", "Contact sheet already exists: test.mp4.jpeg")

    assert _process_file(p, base_config, True) == ("success", "Contact sheet created: test.mp4.jpeg")

    mock_gen.side_effect = ValueError("No video track found")
    assert _process_file(p, base_config, True) == ("skipped", "No video track found in container.")

    mock_gen.side_effect = ValueError("duration might be zero")
    assert _process_file(p, base_config, True) == ("skipped", "duration might be zero")

    mock_gen.side_effect = OSError("No valid frames")
    assert _process_file(p, base_config, True) == (
        "error",
        "File appears corrupted or unreadable (0 frames extracted).",
    )

    mock_gen.side_effect = Exception("Boom")
    assert _process_file(p, base_config, True) == ("error", "Failed: Exception: Boom")


@patch("sashimi.file.thumbs.ContactSheetGenerator._calculate_timestamps")
@patch("sashimi.file.thumbs._extract_single_frame_task")
@patch("sashimi.file.thumbs.MediaInfo.parse")
def test_generator_generate(mock_parse, mock_extract, mock_timestamps, base_config, tmp_path):
    class DummyTrack:
        track_type = "Video"
        duration = "60000.0"
        width = 1920
        height = 1080

        def __getattr__(self, item):
            return None

    mock_parse.return_value.tracks = [DummyTrack()]
    gen = ContactSheetGenerator(pathlib.Path("dummy.mp4"), base_config)
    mock_timestamps.return_value = [1.0, 2.0]

    dummy_dir = tmp_path / "dummy_frames"
    dummy_dir.mkdir()
    img1 = dummy_dir / "f1.jpg"
    img2 = dummy_dir / "f2.jpg"

    Image.new("RGB", (10, 10), "red").save(str(img1))
    Image.new("RGB", (10, 10), "blue").save(str(img2))

    mock_extract.side_effect = [(img1, 1.0), (img2, 2.0)]

    out_path = tmp_path / "out.jpeg"
    gen.generate(out_path)

    assert out_path.exists()
