# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Image conversion (P0-a), resize (P1-d), ICO and HEIC. Pillow only; no ffmpeg needed."""

import io
import json
import zipfile

import pytest
from PIL import Image

from sashimi import image
from sashimi.config import (
    IMAGE_CONVERT,
    IMAGE_RESIZE,
    FaviconPackConfig,
    IcoConfig,
    JpegImageConfig,
    Limits,
    PngImageConfig,
)
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
    src = blob(tmp_path, Image.new("RGB", (3000, 2000)), "PNG")
    with pytest.raises(MediaTooLargeError) as e:
        image.convert(src, tmp_path / "r", PngImageConfig(), limits=Limits(max_pixels=5_000_000))
    assert e.value.user_message == (
        "This image is too large to process: it has 6 megapixels (3000x2000), and the limit is 5."
    )
    assert e.value.limit == "max_pixels"
    for call in (image.to_ico, image.favicon_pack):
        with pytest.raises(MediaTooLargeError):
            call(src, tmp_path / "r", limits=Limits(max_pixels=5_000_000))
    image.convert(src, tmp_path / "r", PngImageConfig(), limits=Limits(max_pixels=None))
    assert not leftovers(tmp_path)


def _gif_header(path, width, height):
    import struct

    screen = b"GIF89a" + struct.pack("<HHBBB", width, height, 0, 0, 0)
    path.write_bytes(screen + b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0) + b"\x02\x02\x44\x01\x00\x3b")
    return path


def test_limits_above_pillows_own_are_honoured(tmp_path):
    huge = _gif_header(tmp_path / "huge.gif", 20000, 20000)
    before = Image.MAX_IMAGE_PIXELS
    with pytest.raises(MediaTooLargeError) as e:
        image.frames_info(huge)
    assert e.value.limit == "max_pixels"
    assert e.value.user_message == (
        "This image is too large to process: it has more than 179 megapixels, and the limit is 64."
    )
    with pytest.raises(MediaTooLargeError) as e:
        image.frames_info(huge, limits=Limits(max_pixels=300_000_000))
    assert "it has 400 megapixels (20000x20000), and the limit is 300." in e.value.user_message
    assert image.frames_info(huge, limits=Limits(max_pixels=None)).width == 20000
    assert image.frames_info(huge, limits=Limits(max_pixels=400_000_000)).height == 20000
    assert Image.MAX_IMAGE_PIXELS == before


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


def test_ico_stays_transparent_unless_given_a_background(half_transparent, tmp_path):
    image.to_ico(half_transparent, tmp_path / "plain", IcoConfig(sizes=(32,)))
    image.to_ico(half_transparent, tmp_path / "filled", IcoConfig(sizes=(32,), background_color="#ffff00"))
    with Image.open(tmp_path / "plain") as plain, Image.open(tmp_path / "filled") as filled:
        assert plain.convert("RGBA").getpixel((30, 16))[3] == 0
        assert filled.convert("RGBA").getpixel((30, 16)) == (255, 255, 0, 255)
        assert filled.convert("RGBA").getpixel((2, 16)) == (0, 0, 255, 255)


def test_ico_background_is_a_form_field():
    assert IcoConfig.from_params({"background_color": "#000"}).background_color == (0, 0, 0)
    assert IcoConfig.from_params({"background_color": None}).background_color is None
    assert IcoConfig.model_fields["background_color"].default is None


PACK = [
    "favicon.ico",
    "favicon-16x16.png",
    "favicon-32x32.png",
    "favicon-48x48.png",
    "apple-touch-icon.png",
    "icon-192x192.png",
    "icon-512x512.png",
    "site.webmanifest",
    "favicon.html",
]


def test_favicon_pack_contents(half_transparent, tmp_path):
    progress = []
    image.favicon_pack(half_transparent, tmp_path / "pack", on_progress=lambda f, s: progress.append(f))
    assert progress == [1.0] and leftovers(tmp_path) == []
    with zipfile.ZipFile(tmp_path / "pack") as archive:
        assert archive.namelist() == PACK
        assert {info.date_time for info in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}
        with Image.open(io.BytesIO(archive.read("favicon.ico"))) as ico:
            assert ico.info["sizes"] == {(16, 16), (32, 32), (48, 48)}
        for name, size in [("favicon-16x16.png", 16), ("icon-512x512.png", 512), ("apple-touch-icon.png", 180)]:
            with Image.open(io.BytesIO(archive.read(name))) as png:
                assert png.format == "PNG" and png.size == (size, size)
        with Image.open(io.BytesIO(archive.read("apple-touch-icon.png"))) as touch:
            assert touch.mode == "RGB" and touch.getpixel((170, 90)) == WHITE
        with Image.open(io.BytesIO(archive.read("icon-192x192.png"))) as icon:
            assert icon.getpixel((180, 96))[3] == 0
        manifest = json.loads(archive.read("site.webmanifest"))
        assert manifest == {
            "icons": [
                {"src": "icon-192x192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "icon-512x512.png", "sizes": "512x512", "type": "image/png"},
            ]
        }
        html = archive.read("favicon.html").decode()
        assert '<link rel="apple-touch-icon" sizes="180x180" href="apple-touch-icon.png">' in html
        assert '<link rel="manifest" href="site.webmanifest">' in html
        assert "theme-color" not in html and 'href="/' not in html and "http" not in html


def test_favicon_pack_is_byte_identical_across_runs(half_transparent, tmp_path):
    config = FaviconPackConfig(name="Site", theme_color=(1, 2, 3), fit="crop")
    image.favicon_pack(half_transparent, tmp_path / "a", config)
    image.favicon_pack(half_transparent, tmp_path / "b", config)
    assert (tmp_path / "a").read_bytes() == (tmp_path / "b").read_bytes()


def test_favicon_manifest_and_html_escape_user_text():
    config = FaviconPackConfig(
        name='Tom & "Jerry" </script><b>',
        short_name="T&J",
        theme_color="#336699",
        manifest_background_color="#ffffff",
    )
    manifest = json.loads(image.favicon_manifest(config))
    assert manifest["name"] == 'Tom & "Jerry" </script><b>' and manifest["short_name"] == "T&J"
    assert (manifest["theme_color"], manifest["background_color"]) == ("#336699", "#ffffff")
    html = image.favicon_html(config)
    assert "Tom" not in html and '<meta name="theme-color" content="#336699">' in html


@pytest.mark.parametrize(
    "params, message",
    [
        ({"name": "a\nb"}, "Invalid value for name: must be a single line of text."),
        ({"ico_sizes": "8,16"}, "Invalid value for ico_sizes: icon sizes must be between 16 and 256 pixels."),
        ({"background_color": "teal"}, "Invalid value for background_color: not a colour: 'teal'."),
    ],
)
def test_favicon_pack_options_are_validated(params, message):
    with pytest.raises(InvalidOptionsError) as e:
        FaviconPackConfig.from_params(params)
    assert e.value.user_message == message


def test_favicon_names_have_no_length_limit():
    assert len(FaviconPackConfig.from_params({"name": "x" * 500, "short_name": "y" * 200}).name) == 500


def test_favicon_pack_refuses_non_images(tmp_path):
    src = tmp_path / "original.blob"
    src.write_bytes(b"not an image")
    with pytest.raises(UnsupportedMediaError):
        image.favicon_pack(src, tmp_path / "pack")
    assert not (tmp_path / "pack").exists() and leftovers(tmp_path) == []


@pytest.mark.parametrize("target", ["jpeg", "png", "webp", "tiff", "gif", "bmp"])
@pytest.mark.parametrize("source", ["PNG", "JPEG", "TIFF", "WEBP"])
def test_keep_icc_false_strips_the_profile_in_every_format(tmp_path, source, target):
    from PIL import ImageCms

    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    src = tmp_path / "original.blob"
    Image.new("RGB", (32, 24), (200, 100, 50)).save(src, source, icc_profile=srgb)
    model = IMAGE_CONVERT.model_for(target=target)
    stripped = tmp_path / "stripped"
    kept = tmp_path / "kept"
    has_option = "keep_icc" in model.model_fields
    image.convert(src, stripped, model(**({"keep_icc": False} if has_option else {})))
    image.convert(src, kept, model())
    with Image.open(stripped) as out:
        assert not out.info.get("icc_profile"), target
    with Image.open(kept) as out:
        assert out.info.get("icc_profile") == (srgb if has_option else None), target


@pytest.mark.parametrize("target", ["jpeg", "png", "webp", "tiff"])
def test_a_cmyk_profile_stays_only_on_cmyk_output(tmp_path, target):
    src = tmp_path / "original.blob"
    profile = b"cmyk-profile" * 20
    Image.new("CMYK", (16, 16), (10, 20, 30, 40)).save(src, "JPEG", icc_profile=profile)
    image.convert(src, tmp_path / "out", IMAGE_CONVERT.from_params({}, target=target))
    with Image.open(tmp_path / "out") as out:
        assert out.info.get("icc_profile") == (profile if out.mode == "CMYK" else None)
        assert (out.mode == "CMYK") is (target == "tiff")
