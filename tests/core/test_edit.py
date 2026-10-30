# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Image editing, frame selection, frame information and frame strips. Pillow only; no ffmpeg needed."""

import random

import pytest
from PIL.PngImagePlugin import PngInfo
from PIL import Image, ImageCms

from sashimi import config, image
from sashimi.config import IMAGE_EDIT, IMAGE_FRAMES, FrameStripConfig, Limits
from sashimi.errors import InvalidOptionsError, MediaTooLargeError, UnsupportedMediaError

from tests.conftest import leftovers, noise

RED, GREEN, BLUE, WHITE = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)


def save(tmp_path, img, fmt, name="original.blob", **kwargs):
    path = tmp_path / name
    img.save(path, format=fmt, **kwargs)
    return path


def quadrants(mode="RGB", size=(40, 20)):
    """Red top-left, green top-right, blue bottom-left, white bottom-right."""
    img = Image.new(mode, size)
    w, h = size
    for box, colour in (((0, 0, w // 2, h // 2), RED), ((w // 2, 0, w, h // 2), GREEN)):
        img.paste(colour, box)
    img.paste(BLUE, (0, h // 2, w // 2, h))
    img.paste(WHITE, (w // 2, h // 2, w, h))
    return img


def run_edit(src, out, target, **params):
    image.edit(src, out, IMAGE_EDIT.from_params(params, target=target))
    result = Image.open(out)
    result.load()
    return result


def css_reference(rgb, brightness=1.0, contrast=1.0, saturation=1.0):
    """CSS brightness(), contrast() and saturate() in that order, from the Filter Effects formulas."""
    channels = [c / 255 for c in rgb]
    channels = [min(1.0, max(0.0, c * brightness)) for c in channels]
    channels = [min(1.0, max(0.0, c * contrast - 0.5 * contrast + 0.5)) for c in channels]
    s = saturation
    matrix = (
        (0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s),
        (0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s),
        (0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s),
    )
    r, g, b = channels
    out = [min(1.0, max(0.0, row[0] * r + row[1] * g + row[2] * b)) for row in matrix]
    return tuple(int(c * 255 + 0.5) for c in out)


def pixels(img):
    data = img.tobytes()
    step = len(img.getbands())
    return [tuple(data[i : i + step]) for i in range(0, len(data), step)]


def test_rotate_then_flip_then_crop(tmp_path):
    src = save(tmp_path, quadrants(), "PNG")
    rotated = run_edit(src, tmp_path / "a", "png", rotate=90)
    assert rotated.size == (20, 40)
    assert rotated.getpixel((0, 0)) == BLUE and rotated.getpixel((19, 0)) == RED
    flipped = run_edit(src, tmp_path / "b", "png", rotate=90, flip="horizontal")
    assert flipped.getpixel((0, 0)) == RED and flipped.getpixel((19, 39)) == WHITE
    cropped = run_edit(src, tmp_path / "c", "png", rotate=90, flip="horizontal", crop_left=10, crop_width=10)
    assert cropped.size == (10, 40)
    assert cropped.getpixel((0, 0)) == BLUE and cropped.getpixel((0, 39)) == WHITE
    vertical = run_edit(src, tmp_path / "d", "png", rotate=180, flip="vertical", crop_top=5, crop_height=5)
    assert vertical.size == (40, 5) and vertical.getpixel((0, 0)) == GREEN


def test_exif_orientation_comes_first(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6
    src = save(tmp_path, quadrants(), "JPEG", exif=exif, quality=100, subsampling=0)
    info = image.frames_info(src)
    assert (info.width, info.height, info.edit_target) == (20, 40, "jpeg")
    out = run_edit(src, tmp_path / "a", "jpeg", crop_top=0, crop_height=20)
    assert out.size == (20, 20)
    assert out.getpixel((15, 5))[0] > 200 and out.getpixel((3, 5))[2] > 200
    assert out.getexif()[0x0112] == 1


@pytest.mark.parametrize(
    "params",
    [
        {"crop_left": 40},
        {"crop_top": 20},
        {"crop_left": 30, "crop_width": 11},
        {"crop_height": 21},
        {"rotate": 90, "crop_width": 21},
    ],
)
def test_crop_outside_the_picture_is_refused(tmp_path, params):
    src = save(tmp_path, quadrants(), "PNG")
    with pytest.raises(InvalidOptionsError) as e:
        image.edit(src, tmp_path / "out", IMAGE_EDIT.from_params(params, target="png"))
    size = "20x40" if params.get("rotate") else "40x20"
    assert e.value.user_message == f"The crop area must fit inside the picture, which is {size} pixels."
    assert not (tmp_path / "out").exists() and leftovers(tmp_path) == []


def test_empty_crop_is_an_invalid_option():
    with pytest.raises(InvalidOptionsError) as e:
        IMAGE_EDIT.from_params({"crop_width": 0}, target="png")
    assert e.value.user_message == "Invalid value for crop_width: input should be greater than or equal to 1."


def test_crop_box_is_pure():
    model = IMAGE_EDIT.model_for(target="png")
    assert image.crop_box(100, 50, model(crop_left=10, crop_top=5)) == (10, 5, 100, 50)
    assert image.crop_box(100, 50, model(crop_width=30, crop_height=20)) == (0, 0, 30, 20)


@pytest.mark.parametrize(
    "aspect, box",
    [
        ("free", (0, 0, 400, 300)),
        ("1:1", (50, 0, 300, 300)),
        ("16:9", (0, 37, 400, 225)),
        ("9:16", (115, 0, 169, 300)),
        ("4:3", (0, 0, 400, 300)),
        ((3, 2), (0, 16, 400, 267)),
    ],
)
def test_aspect_crop(aspect, box):
    assert image.aspect_crop(400, 300, aspect) == box


@pytest.mark.parametrize("aspect", ["16x9", "0:1", "a:b"])
def test_aspect_crop_refuses_other_shapes(aspect):
    with pytest.raises(InvalidOptionsError):
        image.aspect_crop(400, 300, aspect)


def test_crop_aspects_for_uis():
    assert config.CROP_ASPECTS == ("free", "1:1", "4:3", "16:9", "9:16")
    for aspect in config.CROP_ASPECTS:
        image.aspect_crop(10, 10, aspect)


LEVELS = [(v, v, v) for v in range(256)] + [(v, 255 - v, (v * 7) % 256) for v in range(256)]


@pytest.mark.parametrize("brightness", [0.0, 0.5, 0.73, 1.0, 1.25, 2.0])
@pytest.mark.parametrize("contrast", [0.0, 0.6, 1.0, 1.4, 2.0])
def test_brightness_and_contrast_match_css_exactly(brightness, contrast):
    img = Image.new("RGB", (len(LEVELS), 1))
    img.putdata(LEVELS)
    out = image.adjust_colors(img, brightness, contrast)
    assert pixels(out) == [css_reference(p, brightness, contrast) for p in LEVELS]


def worst_and_exact(got, want):
    differences = [max(abs(a - b) for a, b in zip(p, q, strict=True)) for p, q in zip(got, want, strict=True)]
    return max(differences), differences.count(0) / len(differences)


@pytest.mark.parametrize("saturation", [0.0, 0.3, 1.0, 1.5, 2.0])
def test_saturation_matches_css(saturation):
    rng = random.Random(7)
    sample = [tuple(rng.randrange(256) for _ in range(3)) for _ in range(4000)] + LEVELS
    img = Image.new("RGB", (len(sample), 1))
    img.putdata(sample)
    out = image.adjust_colors(img, saturation=saturation)
    worst, exact = worst_and_exact(pixels(out), [css_reference(p, saturation=saturation) for p in sample])
    assert worst <= 1 and exact > 0.99


@pytest.mark.parametrize("settings", [(1.2, 0.8, 1.5), (0.7, 1.6, 0.4), (2.0, 2.0, 2.0), (0.0, 1.0, 2.0)])
def test_all_three_match_css_to_two_levels(settings):
    rng = random.Random(11)
    sample = [tuple(rng.randrange(256) for _ in range(3)) for _ in range(4000)]
    img = Image.new("RGB", (len(sample), 1))
    img.putdata(sample)
    out = pixels(image.adjust_colors(img, *settings))
    worst, exact = worst_and_exact(out, [css_reference(p, *settings) for p in sample])
    assert worst <= 2 and exact > 0.3


def test_css_contrast_is_not_pillows():
    img = Image.new("RGB", (3, 1))
    img.putdata([(0, 0, 0), (100, 100, 100), (255, 255, 255)])
    assert pixels(image.adjust_colors(img, contrast=0.0)) == [(128, 128, 128)] * 3


def test_adjustments_keep_alpha_and_handle_grey(tmp_path):
    rgba = Image.new("RGBA", (2, 1))
    rgba.putdata([(200, 100, 50, 0), (200, 100, 50, 77)])
    out = image.adjust_colors(rgba, 0.5, 1.0, 0.0)
    assert out.mode == "RGBA" and [p[3] for p in pixels(out)] == [0, 77]
    assert pixels(out)[1][:3] == css_reference((200, 100, 50), 0.5, 1.0, 0.0)
    grey = Image.new("LA", (1, 1), (100, 9))
    assert image.adjust_colors(grey, 2.0, 1.0, 0.0).getpixel((0, 0)) == (200, 9)
    assert image.adjust_colors(rgba) is rgba


@pytest.mark.parametrize("target, fmt", [("png", "PNG"), ("webp", "WEBP"), ("gif", "GIF"), ("tiff", "TIFF")])
def test_transparency_survives_every_step(tmp_path, target, fmt):
    img = Image.new("RGBA", (40, 20), (255, 0, 0, 0))
    img.paste((0, 0, 255, 255), (0, 0, 20, 20))
    src = save(tmp_path, img, fmt, lossless=True) if fmt == "WEBP" else save(tmp_path, img, fmt)
    out = run_edit(src, tmp_path / "out", target, rotate=180, crop_left=5, brightness=1.5, saturation=0.5)
    assert out.format == fmt and out.size == (35, 20)
    rgba = out.convert("RGBA")
    assert rgba.getpixel((2, 10))[3] == 0 and rgba.getpixel((30, 10))[3] == 255


@pytest.mark.parametrize("target, fmt", [("jpeg", "JPEG"), ("bmp", "BMP")])
def test_formats_without_alpha_flatten_onto_white(tmp_path, target, fmt):
    img = Image.new("RGBA", (20, 20), (255, 0, 0, 0))
    src = save(tmp_path, img, "PNG")
    out = run_edit(src, tmp_path / "out", target)
    assert out.format == fmt and out.convert("RGB").getpixel((10, 10)) == WHITE


@pytest.mark.parametrize(
    "fmt, target",
    [("PNG", "png"), ("JPEG", "jpeg"), ("WEBP", "webp"), ("GIF", "gif"), ("BMP", "bmp"), ("TIFF", "tiff")],
)
def test_no_op_edit_writes_a_valid_image_in_the_target_format(tmp_path, fmt, target):
    src = save(tmp_path, quadrants(), fmt)
    assert image.frames_info(src).edit_target == target
    out = run_edit(src, tmp_path / "out", target)
    assert out.format == fmt and out.size == (40, 20)
    if fmt in ("PNG", "BMP", "TIFF", "GIF"):
        assert pixels(out.convert("RGB")) == pixels(quadrants())


def test_lossless_edits_are_exact_and_repeatable(tmp_path):
    rng = random.Random(3)
    img = Image.new("RGB", (64, 48))
    img.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(64 * 48)])
    src = save(tmp_path, img, "PNG")
    params = {"rotate": 270, "flip": "vertical", "crop_left": 3, "crop_top": 4, "crop_width": 30, "crop_height": 20}
    out = run_edit(src, tmp_path / "a", "png", **params)
    expected = img.transpose(Image.Transpose.ROTATE_90).transpose(Image.Transpose.FLIP_TOP_BOTTOM).crop((3, 4, 33, 24))
    assert pixels(out) == pixels(expected)
    run_edit(src, tmp_path / "b", "png", **params)
    assert (tmp_path / "a").read_bytes() == (tmp_path / "b").read_bytes()


def test_gif_keeps_its_colours_exactly(tmp_path):
    rng = random.Random(5)
    colours = [tuple(rng.randrange(256) for _ in range(3)) for _ in range(200)] + [(0, 0, 0), (0, 0, 1)]
    img = Image.new("RGB", (60, 40))
    img.putdata([rng.choice(colours) for _ in range(2400)])
    src = save(tmp_path, img.quantize(256, method=Image.Quantize.MAXCOVERAGE, dither=Image.Dither.NONE), "GIF")
    out = run_edit(src, tmp_path / "out", "gif", contrast=1.3)
    expected = image.adjust_colors(Image.open(src).convert("RGB"), contrast=1.3)
    assert pixels(out.convert("RGB")) == pixels(expected)


def test_jpeg_is_resaved_at_high_quality(tmp_path):
    assert config.JpegEditConfig().quality == config.EDIT_QUALITY == 92
    assert config.WebpEditConfig().quality == 92
    noisy = noise((128, 128), 1)
    src = save(tmp_path, noisy, "JPEG", quality=95)
    image.edit(src, tmp_path / "high", config.JpegEditConfig())
    image.edit(src, tmp_path / "low", config.JpegEditConfig(quality=50))
    assert (tmp_path / "low").stat().st_size < (tmp_path / "high").stat().st_size


def test_metadata_is_kept_unless_removed(tmp_path):
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    for fmt, target in (("JPEG", "jpeg"), ("PNG", "png"), ("WEBP", "webp"), ("TIFF", "tiff")):
        src = save(tmp_path, quadrants(), fmt, name=f"in.{target}", exif=exif, icc_profile=srgb)
        plain = run_edit(src, tmp_path / f"plain.{target}", target, keep_icc=False, metadata="remove_all")
        assert not plain.info.get("icc_profile") and 0x010F not in plain.getexif(), fmt
        kept = run_edit(src, tmp_path / f"kept.{target}", target)
        assert kept.info.get("icc_profile") == srgb and kept.getexif().get(0x010F) == "TestCam", fmt


def test_edit_respects_max_pixels_and_refuses_non_images(tmp_path):
    src = save(tmp_path, quadrants(), "PNG")
    with pytest.raises(MediaTooLargeError):
        image.edit(src, tmp_path / "out", config.PngEditConfig(), limits=Limits(max_pixels=100))
    junk = tmp_path / "junk"
    junk.write_bytes(b"not an image")
    with pytest.raises(UnsupportedMediaError):
        image.edit(junk, tmp_path / "out", config.PngEditConfig())
    with pytest.raises(UnsupportedMediaError):
        image.frames_info(junk)
    assert leftovers(tmp_path) == []


def _edit_source(tmp_path, img, name):
    """`img` as a PNG carrying EXIF, an sRGB profile and a comment, so every metadata option has something to change."""
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    comment = PngInfo()
    comment.add_text("Comment", "a comment")
    return save(tmp_path, img, "PNG", name=name, exif=exif, icc_profile=srgb, pnginfo=comment)


def test_every_edit_option_changes_the_output(tmp_path):
    img = noise((40, 30), 2).convert("RGBA")
    img.putpixel((0, 0), (0, 0, 0, 0))
    two_colours = Image.new("RGB", (40, 30), (200, 30, 30))
    two_colours.paste((20, 40, 220), (0, 0, 20, 30))
    alternatives = {
        "rotate": 90, "flip": "vertical", "crop_left": 2, "crop_top": 2, "crop_width": 10, "crop_height": 10,
        "brightness": 1.5, "contrast": 0.5, "saturation": 0.0, "metadata": "remove_all", "keep_icc": False, "quality": 20,
        "optimize": True, "progressive": True, "jpeg_subsampling": "4:4:4", "png_compress_level": 1, "lossless": True,
        "webp_method": 0, "tiff_compression": "none", "avif_speed": 0,
    }  # fmt: skip
    for model in IMAGE_EDIT.models:
        target = model.model_fields["target"].default
        src = _edit_source(tmp_path, img if target not in ("jpeg", "bmp") else img.convert("RGB"), "src.png")
        few = _edit_source(tmp_path, two_colours, "few.png")
        for name in set(model.model_fields) - {"target", "animated"}:
            base = {"tiff_compression": "jpeg"} if target == "tiff" and name == "quality" else {}
            fields = {**base, name: alternatives[name]}
            if name == "optimize" and model.model_fields["optimize"].default:
                fields["optimize"] = False
            source = few if target == "gif" and name == "optimize" else src
            image.edit(source, tmp_path / "ref", model(**base))
            image.edit(source, tmp_path / "alt", model(**fields))
            assert (tmp_path / "alt").read_bytes() != (tmp_path / "ref").read_bytes(), (model.__name__, name)
            if source is few:
                shrunk, full = (Image.open(tmp_path / out).palette for out in ("alt", "ref"))
                assert shrunk is not None and full is not None
                assert len(shrunk.palette) < len(full.palette), "optimize keeps only the GIF palette's used colours"


def animation(tmp_path, fmt, frames=6, size=(24, 16), transparent=False, name="original.blob", **save_kwargs):
    """A colour per frame, a moving white square, 40-140 ms per frame; optionally transparent outside the square."""
    images = []
    for index in range(frames):
        background = (index * 40 % 256, 255 - index * 30 % 256, 80, 0 if transparent else 255)
        frame = Image.new("RGBA", size, background)
        frame.paste((255, 255, 255, 255), (index * 2, 2, index * 2 + 6, 8))
        images.append(frame)
    durations = [40 + 20 * index for index in range(frames)]
    path = tmp_path / name
    kwargs = {"save_all": True, "append_images": images[1:], "duration": durations, "loop": 0, **save_kwargs}
    if fmt == "GIF" and transparent:
        kwargs["disposal"] = 2
    if fmt == "WEBP":
        kwargs.setdefault("lossless", True)
    images[0].save(path, format=fmt, **kwargs)
    return path, images, durations


def frames_of(path):
    with Image.open(path) as img:
        found = []
        for index in range(getattr(img, "n_frames", 1)):
            img.seek(index)
            frame = img.convert("RGBA")
            found.append((frame, img.info.get("duration")))
        return found, img.info.get("loop")


@pytest.mark.parametrize("fmt, target", [("GIF", "gif"), ("WEBP", "webp")])
def test_frames_info(tmp_path, fmt, target):
    path, _, durations = animation(tmp_path, fmt)
    info = image.frames_info(path)
    assert info.model_dump() == {
        "format": target, "edit_target": target, "width": 24, "height": 16, "frames": 6,
        "durations_ms": durations, "loop": 0, "animated": True,
    }  # fmt: skip
    still = image.frames_info(save(tmp_path, quadrants(), "PNG", name="still.png"))
    assert (still.frames, still.durations_ms, still.loop, still.animated) == (1, [0], None, False)


def test_frames_info_of_a_gif_that_plays_once(tmp_path):
    path, _, _ = animation(tmp_path, "GIF")
    with Image.open(path) as img:
        frames = []
        for index in range(img.n_frames):
            img.seek(index)
            frame = img.convert("RGB")
            frame.info = {}
            frames.append(frame)
    frames[0].save(tmp_path / "once.gif", save_all=True, append_images=frames[1:], duration=50)
    info = image.frames_info(tmp_path / "once.gif")
    assert info.loop is None and info.durations_ms == [50] * 6


@pytest.mark.parametrize("fmt, target", [("GIF", "gif"), ("WEBP", "webp")])
@pytest.mark.parametrize("transparent", [False, True])
def test_animated_edits_change_every_frame(tmp_path, fmt, target, transparent):
    path, originals, durations = animation(tmp_path, fmt, transparent=transparent)
    params = {"rotate": 90, "flip": "horizontal", "crop_top": 4, "crop_height": 12, "brightness": 0.5}
    if target == "webp":
        params["lossless"] = True
    out = tmp_path / "out"
    image.edit(path, out, IMAGE_EDIT.from_params(params, target=target))
    found, loop = frames_of(out)
    assert loop == 0 and len(found) == 6
    assert [duration for _, duration in found] == durations
    for (frame, _), original in zip(found, originals, strict=True):
        expected = original.transpose(Image.Transpose.ROTATE_270).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        expected = image.adjust_colors(expected.crop((0, 4, 16, 16)), 0.5)
        assert frame.size == (16, 12)
        got, want = pixels(frame), pixels(expected)
        if transparent:
            assert [p[3] for p in got] == [p[3] for p in want]
            got, want = [p for p in got if p[3]], [p for p in want if p[3]]
        assert got == want


def test_animation_saved_as_a_still_format_gives_the_first_frame(tmp_path):
    path, originals, _ = animation(tmp_path, "GIF")
    out = run_edit(path, tmp_path / "out", "bmp")
    assert getattr(out, "n_frames", 1) == 1 and pixels(out.convert("RGB")) == pixels(originals[0].convert("RGB"))
    asked = run_edit(path, tmp_path / "asked", "png", animated=False)
    assert getattr(asked, "n_frames", 1) == 1 and pixels(asked.convert("RGBA")) == pixels(originals[0])


def test_an_animated_png_is_edited_frame_by_frame_as_apng_and_gives_its_first_frame_when_asked(tmp_path):
    path, _, _ = animation(tmp_path, "PNG")
    assert image.frames_info(path).edit_target == "apng"
    kept = image.edit(path, tmp_path / "anim.apng", config.ApngEditConfig(rotate=90))
    with Image.open(tmp_path / "anim.apng") as out, Image.open(path) as src:
        assert out.n_frames == src.n_frames and out.size == (src.height, src.width)
    assert kept.frames == kept.source_frames and kept.fidelity.level == "lossless"
    png = image.edit(path, tmp_path / "anim.png", config.PngEditConfig(rotate=90))
    assert (tmp_path / "anim.png").read_bytes() == (tmp_path / "anim.apng").read_bytes()
    assert png.frames == png.source_frames and png.fidelity.level == "lossless"
    still = image.edit(path, tmp_path / "still.png", config.PngEditConfig(animated=False))
    assert still.frames == 1 and still.reason == "asked"
    assert still.fidelity.removed == [f"animation ({still.source_frames} frames)"] and not still.fidelity.lost


def test_animation_budget(tmp_path):
    path, _, _ = animation(tmp_path, "GIF")
    tight = Limits(max_animation_bytes=24 * 16 * 4 * 6 - 1)
    for call in (
        lambda limits: image.edit(path, tmp_path / "out", config.GifEditConfig(), limits=limits),
        lambda limits: image.select_frames(path, tmp_path / "out", config.GifFramesConfig(), limits=limits),
        lambda limits: image.frame_strip(path, tmp_path / "out", limits=limits),
    ):
        with pytest.raises(MediaTooLargeError) as e:
            call(tight)
        assert e.value.user_message == (
            "This animation is too large to edit: its frames would take 0 MB of memory, and the limit is 0 MB. "
            "Try one with fewer frames or smaller pictures."
        )
        assert e.value.limit == "max_animation_bytes"
        assert not (tmp_path / "out").exists()
        call(Limits(max_animation_bytes=24 * 16 * 4 * 6))
        call(Limits(max_animation_bytes=None))
        (tmp_path / "out").unlink()


def test_animation_budget_message_states_the_limit(tmp_path):
    frames = [Image.new("RGB", (1024, 1024), (i, 0, 0)) for i in range(3)]
    frames[0].save(tmp_path / "big.gif", save_all=True, append_images=frames[1:], duration=100)
    with pytest.raises(MediaTooLargeError) as e:
        image.edit(
            tmp_path / "big.gif", tmp_path / "out", config.GifEditConfig(), limits=Limits(max_animation_bytes=2**23)
        )
    assert e.value.user_message.startswith(
        "This animation is too large to edit: its frames would take 12 MB of memory, and the limit is 8 MB."
    )


@pytest.mark.parametrize("fmt, target", [("GIF", "gif"), ("WEBP", "webp")])
def test_select_frames_keeps_frames_in_order(tmp_path, fmt, target):
    path, originals, durations = animation(tmp_path, fmt)
    out = tmp_path / "out"
    image.select_frames(
        path, out, IMAGE_FRAMES.from_params({"keep": "4,0-1", "lossless": target == "webp" or None}, target=target)
    )
    found, loop = frames_of(out)
    assert loop == 0
    assert [pixels(frame) for frame, _ in found] == [pixels(originals[i]) for i in (0, 1, 4)]
    assert [duration for _, duration in found] == [durations[i] for i in (0, 1, 4)]


@pytest.mark.parametrize("fmt, target", [("GIF", "gif"), ("WEBP", "webp")])
def test_select_frames_changes_speed(tmp_path, fmt, target):
    path, _, durations = animation(tmp_path, fmt)
    image.select_frames(path, tmp_path / "fast", IMAGE_FRAMES.from_params({"speed": 2}, target=target))
    assert image.frames_info(tmp_path / "fast").durations_ms == [20, 30, 40, 50, 60, 70]
    image.select_frames(path, tmp_path / "slow", IMAGE_FRAMES.from_params({"speed": "0.5"}, target=target))
    assert image.frames_info(tmp_path / "slow").durations_ms == [d * 2 for d in durations]
    image.select_frames(path, tmp_path / "fastest", IMAGE_FRAMES.from_params({"speed": 4}, target=target))
    expected = [20, 20, 20, 30, 30, 40] if target == "gif" else [20, 20, 20, 25, 30, 35]
    assert image.frames_info(tmp_path / "fastest").durations_ms == expected


def test_speed_treats_frames_browsers_slow_down_as_100_ms(tmp_path):
    path, _, _ = animation(tmp_path, "GIF", duration=0)
    assert image.frames_info(path).durations_ms == [0] * 6
    image.select_frames(path, tmp_path / "out", config.GifFramesConfig(speed=2))
    assert image.frames_info(tmp_path / "out").durations_ms == [50] * 6


@pytest.mark.parametrize("fmt, target", [("GIF", "gif"), ("WEBP", "webp")])
def test_select_frames_with_nothing_to_do_is_idempotent(tmp_path, fmt, target):
    path, originals, durations = animation(tmp_path, fmt)
    once, twice = tmp_path / "once", tmp_path / "twice"
    model = IMAGE_FRAMES.model_for(target=target)
    image.select_frames(path, once, model(lossless=True) if target == "webp" else model())
    image.select_frames(once, twice, model(lossless=True) if target == "webp" else model())
    found, _ = frames_of(once)
    assert [pixels(frame) for frame, _ in found] == [pixels(frame) for frame in originals]
    assert [duration for _, duration in found] == durations
    assert once.read_bytes() == twice.read_bytes()


def test_select_frames_validation(tmp_path):
    path, _, _ = animation(tmp_path, "GIF")
    with pytest.raises(InvalidOptionsError) as e:
        image.select_frames(path, tmp_path / "out", config.GifFramesConfig(keep=(0, 6)))
    assert e.value.user_message == "Some of the frames to keep aren't in this animation, which has 6 frames."
    still = save(tmp_path, quadrants(), "GIF", name="still.gif")
    with pytest.raises(UnsupportedMediaError) as e:
        image.select_frames(still, tmp_path / "out", config.GifFramesConfig())
    assert e.value.user_message == "This image isn't animated."
    for keep, message in (
        ("", "Invalid value for keep: keep at least one frame."),
        ("5-2", "Invalid value for keep: 5-2 counts down; write ranges low-high."),
        ("-1", "Invalid value for keep.0: input should be greater than or equal to 0."),
    ):
        with pytest.raises(InvalidOptionsError) as e:
            config.GifFramesConfig.from_params({"keep": keep})
        assert e.value.user_message == message
    assert config.GifFramesConfig.from_params({"keep": [3, 1, 1]}).keep == (1, 3)
    assert config.GifFramesConfig.from_params({"speed": 10}).speed == 10
    for speed in (0, -1, "nan"):
        with pytest.raises(InvalidOptionsError):
            config.GifFramesConfig.from_params({"speed": speed})


@pytest.mark.parametrize("fmt, target, longest", [("GIF", "gif", 655350), ("WEBP", "webp", 0xFFFFFF)])
def test_very_slow_speeds_stop_at_the_longest_frame_the_format_holds(tmp_path, fmt, target, longest):
    path, _, durations = animation(tmp_path, fmt)
    image.select_frames(path, tmp_path / "slow", IMAGE_FRAMES.from_params({"speed": 0.000001}, target=target))
    assert image.frames_info(tmp_path / "slow").durations_ms == [longest] * len(durations)


def test_frame_strip(tmp_path):
    path, originals, _ = animation(tmp_path, "GIF", frames=12, size=(64, 32))
    layout = image.frame_strip(path, tmp_path / "strip", FrameStripConfig(tile_height=16, columns=5))
    assert layout.model_dump() == {"tile_width": 32, "tile_height": 16, "columns": 5, "rows": 3, "frames": 12}
    with Image.open(tmp_path / "strip") as strip:
        assert strip.format == "WEBP" and strip.size == (160, 48)
        tile = strip.convert("RGBA").crop((64, 16, 96, 32))
        expected = originals[7].resize((32, 16), Image.Resampling.LANCZOS)
        differences = [
            abs(a - b) for p, q in zip(pixels(tile), pixels(expected), strict=True) for a, b in zip(p, q, strict=True)
        ]
        assert sum(differences) / len(differences) < 8
    assert image.plan_frame_strip(64, 32, 12, FrameStripConfig(tile_height=16, columns=5)) == layout


def test_frame_strip_layout_fits_webp():
    tall = image.plan_frame_strip(300, 300, 2000, FrameStripConfig(tile_height=256, columns=1))
    assert tall.rows * tall.tile_height <= 16383 and tall.columns == 32
    assert image.plan_frame_strip(100, 50, 3, FrameStripConfig()).model_dump() == {
        "tile_width": 100, "tile_height": 50, "columns": 3, "rows": 1, "frames": 3,
    }  # fmt: skip
    with pytest.raises(MediaTooLargeError):
        image.plan_frame_strip(10000, 16, 50000, FrameStripConfig(tile_height=16))


def test_frame_strip_of_a_still_image(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6
    src = save(tmp_path, quadrants(), "JPEG", exif=exif)
    layout = image.frame_strip(src, tmp_path / "strip", FrameStripConfig(tile_height=20))
    assert (layout.frames, layout.tile_width, layout.tile_height) == (1, 10, 20)
    with Image.open(tmp_path / "strip") as strip:
        assert strip.size == (10, 20)


def test_progress_and_atomic_output(tmp_path):
    path, _, _ = animation(tmp_path, "WEBP")
    seen = []
    image.edit(path, tmp_path / "out", config.WebpEditConfig(), on_progress=lambda f, stage: seen.append((f, stage)))
    assert seen[-1] == (1.0, "done") and len(seen) == 7 and leftovers(tmp_path) == []


def test_edit_models_follow_the_family_rules():
    assert IMAGE_EDIT.targets == ("jpeg", "png", "webp", "gif", "bmp", "tiff", "avif", "apng")
    assert IMAGE_FRAMES.targets == ("gif", "webp", "avif", "apng", "png")
    assert IMAGE_EDIT.model_for(target="jpg") is config.JpegEditConfig
    edit_fields = ["rotate", "flip", "crop_left", "crop_top", "crop_width", "crop_height"]
    edit_fields += ["brightness", "contrast", "saturation"]
    for model in IMAGE_EDIT.models:
        assert list(model.model_fields)[:9] == edit_fields
        assert "max_width" not in model.model_fields and "auto_orient" not in model.model_fields
    assert IMAGE_EDIT.from_params({"rotate": "270", "brightness": "1.5"}, target="png").rotate == 270
    assert IMAGE_EDIT.from_params({"brightness": 2.5, "contrast": 4, "saturation": 10}, target="png").saturation == 10
    with pytest.raises(InvalidOptionsError) as e:
        IMAGE_EDIT.from_params({"brightness": -0.5}, target="png")
    assert e.value.user_message == "Invalid value for brightness: input should be greater than or equal to 0."
    with pytest.raises(InvalidOptionsError) as e:
        IMAGE_EDIT.from_params({"contrast": "inf"}, target="png")
    assert e.value.user_message == "Invalid value for contrast: input should be a finite number."


def test_adjustments_above_two_follow_css(tmp_path):
    src = save(tmp_path, Image.new("RGB", (4, 4), (40, 120, 200)), "PNG")
    out = run_edit(src, tmp_path / "bright.png", "png", brightness=3)
    assert out.getpixel((0, 0)) == (120, 255, 255)
    flat = run_edit(src, tmp_path / "contrast.png", "png", contrast=5)
    assert flat.getpixel((0, 0)) == (0, 90, 255)


def test_cmyk_and_16_bit_sources(tmp_path):
    cmyk = save(tmp_path, quadrants().convert("CMYK"), "JPEG", name="cmyk.jpg")
    out = run_edit(cmyk, tmp_path / "a", "jpeg", saturation=0.0)
    assert out.mode == "RGB"
    deep = Image.new("I;16", (8, 8), 40000)
    path = save(tmp_path, deep, "PNG", name="deep.png")
    kept = run_edit(path, tmp_path / "b", "png", rotate=90)
    assert kept.mode.startswith("I") and kept.getpixel((0, 0)) == 40000
    adjusted = run_edit(path, tmp_path / "c", "png", brightness=0.5)
    assert adjusted.mode == "L" and adjusted.getpixel((0, 0)) == round(40000 / 257 * 0.5)


ANIMATED_TARGETS = ["gif", "webp", "avif", "apng", "png"]


def _animation_routes():
    from sashimi.config import IMAGE_CONVERT, IMAGE_RESIZE

    def convert(src, out, target, **params):
        return image.convert(src, out, IMAGE_CONVERT.from_params(params, target=target))

    def resize(src, out, target, **params):
        return image.convert(src, out, IMAGE_RESIZE.from_params({"max_width": 12, **params}, target=target))

    def edit(src, out, target, **params):
        return image.edit(src, out, IMAGE_EDIT.from_params({"rotate": 90, **params}, target=target))

    def frames(src, out, target, **params):
        return image.select_frames(src, out, IMAGE_FRAMES.from_params({"speed": 2, **params}, target=target))

    return {"convert": convert, "resize": resize, "edit": edit, "frames": frames}


@pytest.mark.parametrize("source", ["GIF", "WEBP", "PNG"])
@pytest.mark.parametrize("target", ANIMATED_TARGETS)
@pytest.mark.parametrize("route", ["convert", "resize", "edit", "frames"])
def test_every_route_keeps_an_animation_by_default_and_gives_one_frame_when_asked(tmp_path, route, target, source):
    path, originals, _ = animation(tmp_path, source)
    run = _animation_routes()[route]
    out = tmp_path / f"out.{target}"
    result = run(path, out, target)
    with Image.open(out) as img:
        assert getattr(img, "n_frames", 1) == len(originals), (route, target)
    assert "timing changed" not in result.fidelity.changed
    assert result.fidelity.level != "lossy" or target in ("gif", "webp", "avif") or route in ("resize", "frames")
    if route == "frames":
        return
    still = run(path, tmp_path / f"still.{target}", target, animated=False)
    with Image.open(tmp_path / f"still.{target}") as img:
        assert getattr(img, "n_frames", 1) == 1
    assert still.frames == 1 and still.reason == "asked"
    assert f"animation ({len(originals)} frames)" in still.fidelity.removed
    assert not any("animation" in item for item in still.fidelity.lost)


def test_edit_saves_an_animated_gif_as_an_animated_png_with_its_timing(tmp_path):
    path, originals, durations = animation(tmp_path, "GIF")
    result = image.edit(path, tmp_path / "out.png", config.PngEditConfig(rotate=90))
    assert result.frames == len(originals) and result.fidelity.level == "lossless"
    found, loop = frames_of(tmp_path / "out.png")
    assert [duration for _, duration in found] == durations and loop == 0
    with Image.open(tmp_path / "out.png") as out:
        assert out.size == (16, 24)
    image.edit(path, tmp_path / "again.png", config.PngEditConfig(rotate=90))
    assert (tmp_path / "again.png").read_bytes() == (tmp_path / "out.png").read_bytes()


def test_an_animated_edit_to_png_is_bounded_by_the_animation_budget(tmp_path):
    path, _, _ = animation(tmp_path, "GIF", frames=20, size=(64, 64))
    with pytest.raises(MediaTooLargeError):
        image.edit(path, tmp_path / "out.png", config.PngEditConfig(), limits=Limits(max_animation_bytes=64 * 64 * 4))
    image.edit(path, tmp_path / "still.png", config.PngEditConfig(animated=False), limits=Limits(max_animation_bytes=1))
