# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the image bugs the real-world media benchmark found (2026-10-29)."""

import io
import json
import struct
import zipfile

import pytest
from PIL import Image, ImageChops, ImageStat
from typer.testing import CliRunner

from sashimi import image
from sashimi.cli import settings
from sashimi.cli.main import app
from sashimi.config import (
    IMAGE_CONVERT,
    IMAGE_EDIT,
    IMAGE_RESIZE,
    FaviconPackConfig,
    IcoConfig,
    Limits,
    WEBP_MAX_SIDE,
)
from sashimi.core import edit as core_edit
from sashimi.core import image as core_image
from sashimi.core import metadata as core_metadata
from sashimi.errors import MediaTooLargeError

runner = CliRunner()

FRAMES = 6
COLOURS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255)]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)


def _frames(alpha: bool) -> list[Image.Image]:
    frames = []
    for colour in COLOURS[:FRAMES]:
        frame = Image.new("RGBA", (80, 40), (*colour, 255))
        if alpha:
            frame.paste((0, 0, 0, 0), (0, 0, 20, 40))
        frames.append(frame)
    return frames


def _animation(path, fmt: str, durations: list[int], *, alpha: bool = False, loop: int | None = 0):
    frames = _frames(alpha)
    kwargs = {"save_all": True, "append_images": frames[1:], "duration": durations}
    if loop is not None:
        kwargs["loop"] = loop
    if fmt == "GIF":
        frames = [frame.convert("RGBA") for frame in frames]
        kwargs["append_images"] = frames[1:]
        kwargs["disposal"] = 2
    elif fmt == "AVIF":
        kwargs.pop("loop", None)
    frames[0].save(path, format=fmt, **kwargs)
    return path


SOURCES = {
    "gif": ("GIF", "a.gif"),
    "webp": ("WEBP", "a.webp"),
    "apng": ("PNG", "a.png"),
    "avif": ("AVIF", "a.avif"),
}


def _read(path):
    img = Image.open(path)
    frames, durations = [], []
    for index in range(getattr(img, "n_frames", 1)):
        img.seek(index)
        frames.append(img.convert("RGBA"))
        durations.append(img.info.get("duration", 0))
    return img, frames, durations


def _colour(frame: Image.Image) -> tuple[int, ...]:
    return tuple(round(v) for v in ImageStat.Stat(frame.crop((40, 10, 70, 30)).convert("RGB")).mean)


def _close(a, b, tolerance=12):
    return all(abs(x - y) <= tolerance for x, y in zip(a, b, strict=False))


@pytest.mark.parametrize("source", sorted(SOURCES))
@pytest.mark.parametrize("target", ["gif", "webp", "png"])
@pytest.mark.parametrize("operation", ["convert", "resize"])
def test_animations_stay_animated_with_their_timing_and_transparency(tmp_path, source, target, operation):
    fmt, name = SOURCES[source]
    durations = [100, 50, 200, 100, 70, 300]
    src = _animation(tmp_path / name, fmt, durations, alpha=True)
    out = tmp_path / f"out.{target}"
    if operation == "convert":
        config = IMAGE_CONVERT.from_params({}, target=target)
    else:
        config = IMAGE_RESIZE.from_params({"max_width": 40}, target=target)
    result = core_image.convert(src, out, config)
    assert (result.frames, result.source_frames, result.reason) == (FRAMES, FRAMES, "kept")
    img, frames, written = _read(out)
    assert len(frames) == FRAMES
    assert written == durations
    assert img.info.get("loop") == 0
    for frame, colour in zip(frames, COLOURS, strict=False):
        frame = frame.resize((80, 40)) if frame.size != (80, 40) else frame
        assert _close(_colour(frame), colour), (frame.size, _colour(frame), colour)
        assert frame.getpixel((2, 20))[3] == 0 and frame.getpixel((78, 20))[3] == 255


@pytest.mark.parametrize("source", sorted(SOURCES))
@pytest.mark.parametrize("target", ["jpeg", "bmp", "tiff"])
def test_targets_that_hold_one_picture_give_the_first_frame_and_say_so(tmp_path, source, target):
    fmt, name = SOURCES[source]
    src = _animation(tmp_path / name, fmt, [100] * FRAMES)
    result = core_image.convert(src, tmp_path / "out", IMAGE_CONVERT.from_params({}, target=target))
    assert (result.frames, result.source_frames, result.reason) == (1, FRAMES, "target")
    assert _close(_colour(Image.open(tmp_path / "out").convert("RGBA")), COLOURS[0])


@pytest.mark.parametrize("target", ["gif", "webp", "avif", "apng", "png"])
def test_animated_off_keeps_only_the_first_frame(tmp_path, target):
    src = _animation(tmp_path / "a.gif", "GIF", [100] * FRAMES)
    config = IMAGE_CONVERT.from_params({"animated": False}, target=target)
    result = core_image.convert(src, tmp_path / "out", config)
    assert (result.frames, result.reason) == (1, "asked")
    assert getattr(Image.open(tmp_path / "out"), "n_frames", 1) == 1


def test_a_gif_s_short_frames_play_as_browsers_play_them(tmp_path):
    src = _animation(tmp_path / "a.gif", "GIF", [10] * FRAMES)
    core_image.convert(src, tmp_path / "out.webp", IMAGE_CONVERT.from_params({}, target="webp"))
    assert _read(tmp_path / "out.webp")[2] == [100] * FRAMES


def test_an_animation_over_the_memory_budget_is_refused(tmp_path):
    src = _animation(tmp_path / "a.gif", "GIF", [100] * FRAMES)
    with pytest.raises(MediaTooLargeError) as e:
        core_image.convert(
            src,
            tmp_path / "out.webp",
            IMAGE_CONVERT.from_params({}, target="webp"),
            limits=Limits(max_animation_bytes=1000),
        )
    assert e.value.limit == "max_animation_bytes"
    assert "too large to convert" in e.value.user_message
    assert not (tmp_path / "out.webp").exists()


def test_a_multi_page_tiff_gives_its_first_page_and_says_so(tmp_path):
    pages = [Image.new("RGB", (40, 20), colour) for colour in COLOURS[:3]]
    pages[0].save(tmp_path / "p.tif", save_all=True, append_images=pages[1:])
    result = core_image.convert(tmp_path / "p.tif", tmp_path / "out.png", IMAGE_CONVERT.from_params({}, target="png"))
    assert (result.source, result.source_frames, result.reason) == ("pages", 3, "target")


def _cli(*args):
    result = runner.invoke(app, [*args, "--json"], env={"COLUMNS": "1000"})
    return json.loads(result.stdout)["results"], result.exit_code


def test_the_cli_says_when_frames_were_left_out_and_no_animated_gives_the_first_frame(tmp_path):
    _animation(tmp_path / "a.gif", "GIF", [100] * FRAMES)
    (row,), _ = _cli("convert", "a.gif", "jpeg")
    assert (
        row["message"] == "Converted to a.gif.jpeg (lossy: saved as JPEG at quality 85; animation (6 frames) dropped)"
    )
    (row,), _ = _cli("convert", "a.gif", "webp")
    assert row["message"] == "Converted to a.gif.webp (lossy: saved as WebP at quality 85)"
    assert Image.open(tmp_path / "a.gif.webp").n_frames == FRAMES
    (row,), _ = _cli("resize", "a.gif", "-w", "40", "--no-animated")
    assert row["message"] == (
        "Resized to a.gif.resized.gif (lossy: scaled to 40x20; animation (6 frames) removed as asked)"
    )
    assert getattr(Image.open(tmp_path / "a.gif.resized.gif"), "n_frames", 1) == 1
    (row,), _ = _cli("convert", "a.gif", "webp")
    assert row["status"] == "exists"


@pytest.mark.parametrize("section", ["convert", "resize"])
def test_animated_off_can_be_kept_in_sashimi_toml_and_overridden(tmp_path, section):
    _animation(tmp_path / "a.gif", "GIF", [100] * FRAMES)
    assert runner.invoke(app, ["config", "set", f"{section}.animated", "false"]).exit_code == 0
    args = (
        ["convert", "a.gif", "webp"]
        if section == "convert"
        else ["resize", "a.gif", "-w", "40", "--output-format", "webp"]
    )
    _cli(*args)
    out = tmp_path / ("a.gif.webp" if section == "convert" else "a.gif.resized.webp")
    assert getattr(Image.open(out), "n_frames", 1) == 1
    _cli(*args, "-f", "--animated")
    assert Image.open(out).n_frames == FRAMES
    refused = runner.invoke(app, ["config", "set", f"{section}.animated", "yes"])
    assert refused.exit_code != 0 and "true or false" in refused.output


HIGH_BIT = {
    "I;16": lambda: Image.frombytes("I;16", (64, 32), bytes(range(256)) * 16),
    "I;16B": lambda: Image.frombytes("I;16B", (64, 32), bytes(range(256)) * 16),
    "I": lambda: Image.linear_gradient("L").resize((64, 32)).convert("I").point(lambda v: v * 257),
    "F": lambda: Image.linear_gradient("L").resize((64, 32)).convert("F").point(lambda v: v / 255),
}
"""High-bit-depth greyscale pictures whose mean tone, scaled to 8 bits, is about mid-grey."""


def _mean(img: Image.Image) -> float:
    return ImageStat.Stat(core_image.eight_bit(img).convert("L")).mean[0]


@pytest.mark.parametrize("mode", sorted(HIGH_BIT))
@pytest.mark.parametrize("target", ["jpeg", "webp", "gif", "bmp", "png", "tiff"])
@pytest.mark.parametrize("operation", ["convert", "edit"])
def test_high_bit_depth_greyscale_keeps_its_tones(tmp_path, mode, target, operation):
    source = HIGH_BIT[mode]()
    expected = _mean(core_image.eight_bit(source))
    assert 100 < expected < 160
    src = tmp_path / "src.tif"
    source.save(src, format="TIFF")
    out = tmp_path / "out"
    if operation == "convert":
        core_image.convert(src, out, IMAGE_CONVERT.from_params({}, target=target))
    else:
        core_edit.edit(src, out, IMAGE_EDIT.from_params({"rotate": 90}, target=target))
    assert abs(_mean(Image.open(out)) - expected) < 6, (mode, target, _mean(Image.open(out)), expected)


def test_a_16_bit_png_makes_mid_grey_icons(tmp_path):
    src = tmp_path / "g.png"
    HIGH_BIT["I;16"]().save(src)
    image.to_ico(src, tmp_path / "f.ico", IcoConfig(sizes=(32,)))
    icon = Image.open(tmp_path / "f.ico").convert("RGBA")
    expected = _mean(core_image.eight_bit(HIGH_BIT["I;16"]()))
    assert abs(ImageStat.Stat(icon.convert("L"), mask=icon.getchannel("A")).mean[0] - expected) < 10


@pytest.mark.parametrize("route", ["convert", "resize", "edit"])
def test_webp_wider_than_webp_allows_is_refused_with_the_size(tmp_path, route):
    src = tmp_path / "wide.png"
    Image.new("RGB", (WEBP_MAX_SIDE + 1, 2), "red").save(src)
    out = tmp_path / "out.webp"
    with pytest.raises(MediaTooLargeError) as e:
        if route == "convert":
            core_image.convert(src, out, IMAGE_CONVERT.from_params({}, target="webp"))
        elif route == "resize":
            core_image.convert(src, out, IMAGE_RESIZE.from_params({"max_height": 2}, target="webp"))
        else:
            core_edit.edit(src, out, IMAGE_EDIT.from_params({}, target="webp"))
    assert e.value.user_message.startswith(f"This picture would be {WEBP_MAX_SIDE + 1}x2 pixels")
    assert "16,383" in e.value.user_message and not out.exists()
    core_image.convert(src, out, IMAGE_RESIZE.from_params({"max_width": WEBP_MAX_SIDE}, target="webp"))
    assert Image.open(out).width == WEBP_MAX_SIDE


def test_an_animated_webp_wider_than_webp_allows_is_refused(tmp_path):
    frames = [Image.new("RGB", (WEBP_MAX_SIDE + 1, 2), colour) for colour in COLOURS[:2]]
    frames[0].save(tmp_path / "w.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    with pytest.raises(MediaTooLargeError):
        core_image.convert(tmp_path / "w.gif", tmp_path / "o.webp", IMAGE_CONVERT.from_params({}, target="webp"))


def test_jfif_is_jpeg(tmp_path):
    Image.new("RGB", (16, 16), "red").save(tmp_path / "comic.jfif", format="JPEG")
    (row,), code = _cli("convert", "comic.jfif", "jpg")
    assert (row["status"], row["message"], code) == ("skipped", "Already JPEG", 0)
    (row,), _ = _cli("convert", "comic.jfif", "png")
    assert row["message"] == "Converted to comic.jfif.png (lossless)"
    (row,), _ = _cli("resize", "comic.jfif", "-w", "8")
    assert row["message"] == "Resized to comic.jfif.resized.jpg (lossy: saved as JPEG at quality 85, scaled to 8x8)"
    for ext in (".jpe", ".jif"):
        Image.new("RGB", (16, 16), "red").save(tmp_path / f"x{ext}", format="JPEG")
        (row,), _ = _cli("convert", f"x{ext}", "png")
        assert row["status"] == "success"


@pytest.mark.parametrize(
    "fmt, ext, expected",
    [("ICO", ".ico", "png"), ("TGA", ".tga", "png"), ("PSD", None, "png"), ("AVIF", ".avif", "avif")],
)
def test_ico_tga_psd_and_avif_are_read(tmp_path, fmt, ext, expected):
    if fmt == "PSD":
        src = tmp_path / "x.psd"
        src.write_bytes(_psd(Image.new("RGB", (20, 10), (200, 10, 10))))
    else:
        src = tmp_path / f"x{ext}"
        img = Image.new("RGB", (64, 64) if fmt == "ICO" else (20, 10), (200, 10, 10))
        img.save(src, format=fmt, **({"sizes": [(16, 16), (64, 64)]} if fmt == "ICO" else {}))
    info = core_edit.frames_info(src)
    assert info.edit_target == expected
    core_image.convert(src, tmp_path / "o.png", IMAGE_CONVERT.from_params({}, target="png"))
    out = Image.open(tmp_path / "o.png")
    assert out.size == ((64, 64) if fmt == "ICO" else (20, 10))
    assert _close(out.convert("RGB").getpixel((5, 5)), (200, 10, 10), 8)
    assert core_metadata.read(src).kind == "image"
    (row,), _ = _cli("convert", src.name, "webp")
    assert row["status"] == "success"


def _psd(img: Image.Image) -> bytes:
    """A minimal flattened 8-bit RGB Photoshop file, raw (uncompressed) image data."""
    width, height = img.size
    header = b"8BPS" + struct.pack(">H6xHIIHH", 1, 3, height, width, 8, 3)
    planes = b"".join(img.getchannel(band).tobytes() for band in "RGB")
    return header + struct.pack(">I", 0) * 3 + struct.pack(">H", 0) + planes


@pytest.mark.parametrize(
    "fmt, reason", [("ICO", "hold no metadata"), ("BMP", "hold no metadata"), ("TGA", "isn't supported")]
)
def test_strip_refuses_formats_with_the_reason(tmp_path, fmt, reason):
    src = tmp_path / f"x.{fmt.lower()}"
    Image.new("RGB", (16, 16), "red").save(src, format=fmt)
    (row,), code = _cli("strip", src.name)
    assert row["status"] == "skipped" and reason in row["message"] and code == 1


def _big_png(path, width, height) -> None:
    """A PNG that says it is `width` x `height` with almost no data (no pixels are ever decoded here)."""
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    text = b"Comment\x00hello"
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"tEXt", text)
        + chunk(b"IDAT", zlib.compress(b"\0" * 9))
        + chunk(b"IEND", b"")
    )


def test_reading_and_stripping_decode_no_pixels_so_no_pixel_limit_applies(tmp_path):
    src = tmp_path / "big.png"
    _big_png(src, 10000, 9000)
    assert core_edit.frames_info(src).width == 10000
    assert core_metadata.read(src).kind == "image"
    core_metadata.strip(src, tmp_path / "s.png")
    assert b"Comment" not in (tmp_path / "s.png").read_bytes()
    with pytest.raises(MediaTooLargeError):
        core_image.convert(src, tmp_path / "o.jpg", IMAGE_CONVERT.from_params({}, target="jpeg"))


def _old_square(img: Image.Image) -> Image.Image:
    """The favicon square as 0.6 made it: padded at full size."""
    from PIL import ImageOps

    oriented = core_image.to_srgb(ImageOps.exif_transpose(img)).convert("RGBA")
    edge = max(oriented.size)
    square = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    square.paste(oriented, ((edge - oriented.width) // 2, (edge - oriented.height) // 2))
    return square


@pytest.mark.parametrize("size", [(300, 100), (2048, 1000), (512, 512)])
def test_favicons_of_ordinary_images_are_unchanged(tmp_path, size):
    img = Image.linear_gradient("L").resize(size).convert("RGB")
    src = tmp_path / "logo.png"
    img.save(src)
    image.favicon_pack(src, tmp_path / "p.zip")
    pack = zipfile.ZipFile(tmp_path / "p.zip")
    square = _old_square(Image.open(src))
    for name, edge in (("favicon-32x32.png", 32), ("icon-512x512.png", 512)):
        expected = square.resize((edge, edge), Image.Resampling.LANCZOS)
        got = Image.open(io.BytesIO(pack.read(name))).convert("RGBA")
        assert ImageChops.difference(got, expected).getbbox() is None, name


def test_a_panorama_s_favicons_are_shrunk_before_they_are_squared(tmp_path, monkeypatch):
    src = tmp_path / "pano.png"
    Image.new("RGB", (20000, 100), "blue").save(src)
    made = []
    original = Image.new

    def new(mode, size, *args, **kwargs):
        made.append(size)
        return original(mode, size, *args, **kwargs)

    monkeypatch.setattr(Image, "new", new)
    image.favicon_pack(src, tmp_path / "p.zip", FaviconPackConfig())
    assert max(max(size) for size in made) <= 4 * 512
    icon = Image.open(io.BytesIO(zipfile.ZipFile(tmp_path / "p.zip").read("icon-512x512.png"))).convert("RGBA")
    assert icon.getpixel((256, 256))[:3] == (0, 0, 255) and icon.getpixel((256, 10))[3] == 0
