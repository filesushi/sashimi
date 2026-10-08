import pytest
import tempfile
import pathlib
import ffmpeg  # type: ignore
from PIL import Image

from sashimi.config.schemas.thumbs import StaticThumbsConfig
from sashimi.file.thumbs import ContactSheetGenerator, generate_contact_sheets


@pytest.fixture(scope="module")
def real_video():
    """
    Generates a real 2-second video file using FFmpeg's built-in testsrc.
    This requires FFmpeg to be installed on the host system.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        vid_path = pathlib.Path(tmpdir) / "synthetic_test_video.mp4"
        try:
            (
                ffmpeg.input("testsrc=duration=2:size=640x360:rate=10", f="lavfi")
                .output(str(vid_path), vcodec="libx264")
                .global_args("-nostdin")
                .run(capture_stdout=True, capture_stderr=True, quiet=True)
            )
        except ffmpeg.Error as e:
            pytest.skip(f"FFmpeg failed to generate test asset: {e.stderr.decode('utf-8')}")
        except FileNotFoundError:
            pytest.skip("FFmpeg is not installed or not in PATH.")

        yield vid_path


def test_real_contact_sheet_pipeline(real_video):
    """
    End-to-end integration test.
    No mocks. Real FFmpeg extraction, real Pillow rendering, real JPEG output.
    """

    config = StaticThumbsConfig(
        static_width=800, cols=2, rows=2, gap=5, start_offset_percent=0, end_offset_percent=0, jpeg_quality=50
    )

    generator = ContactSheetGenerator(real_video, config)
    out_path = real_video.parent / "test_output.jpeg"
    generator.generate(out_path)

    assert out_path.exists()
    assert out_path.stat().st_size > 0

    with Image.open(out_path) as img:
        assert img.format == "JPEG"
        assert img.width == 800

        assert img.mode == "RGB"


def test_real_orchestrator_integration(real_video):
    """Verifies the public generator yields the correct physical files."""
    config = StaticThumbsConfig()

    gen = generate_contact_sheets([real_video.parent], config, recursive=False, force=True)

    assert next(gen) == 1

    file_path, status, _ = next(gen)
    assert status == "starting"
    assert file_path.name == real_video.name

    file_path, status, msg = next(gen)
    assert status == "success"
    assert "Contact sheet created" in msg
    assert (real_video.parent / f"{real_video.name}.jpeg").exists()


@pytest.fixture(scope="module")
def rotated_video(real_video):
    """The synthetic video re-muxed with a 90 degree display rotation, like phone footage."""
    rot_path = real_video.parent / "synthetic_rotated.mp4"
    try:
        (
            ffmpeg.input(str(real_video), display_rotation=90)
            .output(str(rot_path), c="copy")
            .global_args("-nostdin")
            .run(capture_stdout=True, capture_stderr=True, quiet=True)
        )
    except ffmpeg.Error as e:
        pytest.skip(f"FFmpeg could not apply display rotation: {e.stderr.decode('utf-8')}")
    return rot_path


def test_rotated_video_tiles_fit_their_slots(rotated_video):
    config = StaticThumbsConfig(static_width=400, cols=2, rows=2, gap=6, start_offset_percent=0, end_offset_percent=0)
    generator = ContactSheetGenerator(rotated_video, config)
    out_path = rotated_video.parent / "rotated.jpeg"
    generator.generate(out_path)

    assert generator.thumb_h > generator.thumb_w

    # The horizontal gap between the two rows must still be background, not overlapping frames.
    gap_y = generator.header_h + config.gap + generator.thumb_h + config.gap // 2
    with Image.open(out_path) as img:
        assert img.size == (400, generator.total_height)
        for x in (config.gap + generator.thumb_w // 2, img.width - config.gap - generator.thumb_w // 2):
            pixel = img.getpixel((x, gap_y))
            assert max(abs(a - b) for a, b in zip(pixel, config.grid_bg_color, strict=True)) < 25


@pytest.mark.parametrize(
    "output_format, loop, expected_loop",
    [("gif", True, 0), ("gif", False, None), ("webp", True, 0), ("webp", False, 1)],
)
def test_animated_loop_setting(real_video, output_format, loop, expected_loop):
    from sashimi.config.schemas.thumbs import AnimatedThumbsConfig
    from sashimi.file.animated_thumbs import AnimatedContactSheetGenerator

    config = AnimatedThumbsConfig(
        animated_width=200,
        cols=2,
        rows=1,
        sample_duration=0.5,
        output_fps=5,
        output_format=output_format,
        loop=loop,
    )
    out_path = real_video.parent / f"loop_{loop}.{output_format}"
    AnimatedContactSheetGenerator(real_video, config).generate(out_path)

    with Image.open(out_path) as img:
        assert img.info.get("loop") == expected_loop
    assert not list(real_video.parent.glob(".*.partial*"))
