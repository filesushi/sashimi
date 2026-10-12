"""Image conversion (P0-a), resize (P1-d), ICO and HEIC. Pillow only; no ffmpeg needed."""

import io

import pytest
from PIL import Image

from sashimi import image
from sashimi.config import IMAGE_CONVERT, IMAGE_RESIZE, JpegImageConfig, PngImageConfig
from sashimi.errors import InvalidOptionsError, MediaTooLargeError, UnsupportedMediaError

from tests.conftest import leftovers

WHITE = (255, 255, 255)


def blob(tmp_path, img: Image.Image, fmt: str, **save) -> object:
    path = tmp_path / "original.blob"
    img.save(path, format=fmt, **save)
    return path


@pytest.fixture
def half_transparent(tmp_path):
    """64x64 RGBA: left half opaque blue, right half fully transparent red."""
    img = Image.new("RGBA", (64, 64), (255, 0, 0, 0))
    img.paste((0, 0, 255, 255), (0, 0, 32, 64))
    return blob(tmp_path, img, "PNG")


def convert(src, out, target, **params):
    image.convert(src, out, IMAGE_CONVERT.from_params(params, target=target))
    result = Image.open(out)
    result.load()
    return result


def test_jpeg_always_flattens_alpha_onto_white(half_transparent, tmp_path):
    out = convert(half_transparent, tmp_path / "result.jpg", "jpeg")
    assert out.format == "JPEG" and out.mode == "RGB"
    assert out.getpixel((60, 10)) == WHITE
    assert out.getpixel((5, 10))[2] > 240


@pytest.mark.parametrize("target, fmt", [("png", "PNG"), ("webp", "WEBP"), ("gif", "GIF")])
def test_alpha_kept_by_default_and_flattened_on_request(half_transparent, tmp_path, target, fmt):
    kept = convert(half_transparent, tmp_path / "a", target).convert("RGBA")
    assert kept.getpixel((60, 10))[3] == 0
    flat = convert(half_transparent, tmp_path / "b", target, preserve_transparency=False)
    assert flat.format == fmt
    assert flat.convert("RGBA").getpixel((60, 10))[:3] == pytest.approx(WHITE, abs=4)
    assert flat.convert("RGBA").getpixel((60, 10))[3] == 255


def test_output_format_comes_from_target_not_suffix(half_transparent, tmp_path):
    assert convert(half_transparent, tmp_path / "result.jpg", "png").format == "PNG"


def test_max_width_scales_down_and_never_up(tmp_path):
    src = blob(tmp_path, Image.new("RGB", (400, 200), "green"), "JPEG")
    assert convert(src, tmp_path / "a", "png", max_width=100).size == (100, 50)
    assert convert(src, tmp_path / "b", "png", max_width=1000).size == (400, 200)


def test_quality_and_lossless(tmp_path):
    noisy = Image.effect_noise((256, 256), 64).convert("RGB")
    src = blob(tmp_path, noisy, "PNG")
    low, high = tmp_path / "low", tmp_path / "high"
    image.convert(src, low, JpegImageConfig(quality=10))
    image.convert(src, high, JpegImageConfig(quality=95))
    assert low.stat().st_size < high.stat().st_size

    lossless = convert(src, tmp_path / "ll", "webp", lossless=True)
    assert list(lossless.convert("RGB").getdata()) == list(noisy.getdata())


def test_first_frame_of_animated_gif(tmp_path):
    frames = [Image.new("RGB", (32, 32), c) for c in ("red", "blue")]
    src = tmp_path / "original.blob"
    frames[0].save(src, format="GIF", save_all=True, append_images=frames[1:], duration=100)
    out = convert(src, tmp_path / "r", "png")
    assert out.convert("RGB").getpixel((5, 5)) == (255, 0, 0)


@pytest.mark.parametrize("fmt", ["BMP", "TIFF", "WEBP"])
def test_other_input_formats(tmp_path, fmt):
    src = blob(tmp_path, Image.new("RGB", (20, 10), "red"), fmt)
    assert convert(src, tmp_path / "r", "jpeg").size == (20, 10)


def test_exif_orientation_is_applied_by_default(tmp_path):
    img = Image.new("RGB", (40, 20), "red")
    exif = Image.Exif()
    exif[0x0112] = 6
    src = blob(tmp_path, img, "JPEG", exif=exif)
    assert convert(src, tmp_path / "a", "png").size == (20, 40)
    assert convert(src, tmp_path / "b", "png", auto_orient=False).size == (40, 20)


def test_non_images_are_user_errors(tmp_path):
    src = tmp_path / "original.blob"
    src.write_bytes(b"not an image")
    with pytest.raises(UnsupportedMediaError) as e:
        image.convert(src, tmp_path / "r", PngImageConfig())
    assert e.value.user_message == "This file isn't an image we can convert."
    assert leftovers(tmp_path) == []


def test_truncated_image_is_a_user_error(tmp_path):
    buf = io.BytesIO()
    Image.effect_noise((256, 256), 64).convert("RGB").save(buf, "PNG")
    src = tmp_path / "original.blob"
    src.write_bytes(buf.getvalue()[:2000])
    with pytest.raises(UnsupportedMediaError):
        image.convert(src, tmp_path / "r", PngImageConfig())


def test_oversized_images_are_refused(tmp_path):
    src = blob(tmp_path, Image.new("RGB", (100, 100)), "PNG")
    with pytest.raises(MediaTooLargeError):
        image.convert(src, tmp_path / "r", PngImageConfig(), max_pixels=5000)


def test_resize_configs_need_a_size(tmp_path):
    src = blob(tmp_path, Image.new("RGB", (300, 600), "red"), "WEBP")
    image.convert(src, tmp_path / "r", IMAGE_RESIZE.from_params({"max_width": 100, "max_height": 100}, target="webp"))
    out = Image.open(tmp_path / "r")
    assert out.format == "WEBP" and out.size == (50, 100)
    image.convert(src, tmp_path / "j", IMAGE_RESIZE.from_params({"max_height": "60"}, target="jpg"))
    assert Image.open(tmp_path / "j").format == "JPEG"
    with pytest.raises(InvalidOptionsError) as e:
        IMAGE_RESIZE.from_params({}, target="png")
    assert e.value.user_message == "Set a maximum width or height to resize to."


def test_ico_has_standard_square_sizes(tmp_path):
    src = blob(tmp_path, Image.new("RGBA", (300, 100), "red"), "PNG")
    image.to_ico(src, tmp_path / "favicon")
    ico = Image.open(tmp_path / "favicon")
    assert ico.format == "ICO"
    assert ico.info["sizes"] == {(s, s) for s in (16, 32, 48, 64, 128, 256)}


@pytest.mark.skipif(not image.heif_available(), reason="pillow-heif not installed")
def test_heic_to_jpeg(tmp_path):
    import pillow_heif

    heif = pillow_heif.from_pillow(Image.new("RGB", (64, 48), "red"))
    src = tmp_path / "original.blob"
    heif.save(src, quality=90)
    out = convert(src, tmp_path / "r", "jpeg")
    assert out.size == (64, 48) and out.getpixel((10, 10))[0] > 200
