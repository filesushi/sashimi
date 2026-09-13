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
        output_width=800, cols=2, rows=2, gap=5, start_offset_percent=0, end_offset_percent=0, jpeg_quality=50
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
