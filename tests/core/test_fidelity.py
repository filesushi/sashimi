# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
What each image output says it kept (``fidelity``), checked against the output itself: a picture
said to be identical decodes to the source's pixels, what is said to be dropped or removed is
gone, and nothing is said to be lossless that isn't. Fixtures are made in the tests.
"""

import io
import itertools
import random

import pytest
from PIL import Image, ImageCms, ImageOps
from PIL.PngImagePlugin import PngInfo

from sashimi import image, metadata
from sashimi.config import IMAGE_CONVERT, IMAGE_EDIT, IMAGE_FRAMES
from sashimi.core.fidelity import (
    Inventory,
    counted,
    differences,
    fidelity,
    image_inventory,
    same_picture,
)
from sashimi.core.sheets._layout import fit_grid


def test_levels_follow_what_changed_was_lost_and_was_checked():
    assert fidelity("picture").level == "lossless"
    assert fidelity("picture", removed=["location"]).level == "lossless"
    assert fidelity("picture", lost=["colour profile"]).level == "content_lossless"
    assert fidelity("picture", checked=False).level == "content_lossless"
    assert fidelity("picture", changed=["scaled to 4x4"], lost=["EXIF"]).level == "lossy"


def test_the_wording_is_short_and_names_what_was_lost_and_removed():
    assert fidelity("picture").text == "lossless"
    assert fidelity("picture", checked=False).text == "picture identical"
    assert (
        fidelity("picture", lost=["TIFF tags", "colour profile"]).text
        == "picture identical; TIFF tags and colour profile dropped"
    )
    assert (
        fidelity("picture and sound", changed=["re-encoded as H.264 (CRF 23)"], removed=["location"]).text
        == "lossy: re-encoded as H.264 (CRF 23); location removed as asked"
    )
    told = fidelity("picture", lost=["GPS track"], notes=["FFmpeg can't copy it into MP4"])
    assert told.text == "picture identical; GPS track dropped. FFmpeg can't copy it into MP4"


def test_differences_tell_what_was_asked_from_what_was_lost():
    source = Inventory()
    for name in ("EXIF", "location", "XMP"):
        source.add(name, metadata=True)
    source.add("colour profile")
    source.location = True
    output = Inventory(items=["EXIF"])
    assert differences(source, output) == (["location", "XMP", "colour profile"], [])
    assert differences(source, output, removed_metadata="remove_location") == (["XMP", "colour profile"], ["location"])
    assert differences(source, Inventory(), removed_metadata="remove_all") == (
        ["colour profile"],
        ["EXIF", "location", "XMP"],
    )
    assert differences(source, output, kept=["XMP"], removed=["colour profile"]) == (["location"], ["colour profile"])


def test_repeated_tracks_are_counted():
    assert counted(["Apple metadata track"] * 3 + ["GoPro telemetry track (GPS)"]) == [
        "3 Apple metadata tracks",
        "GoPro telemetry track (GPS)",
    ]


@pytest.mark.parametrize("count, grid, expected", [(15, (6, 4), (5, 3)), (24, (6, 5), (6, 4)), (1, (6, 4), (1, 1))])
def test_a_smaller_grid_keeps_the_shape_without_empty_cells(count, grid, expected):
    assert fit_grid(count, *grid) == expected


def test_same_picture_compares_what_shows():
    rgba = Image.new("RGBA", (4, 4), (10, 20, 30, 255))
    hidden = rgba.copy()
    rgba.putpixel((0, 0), (1, 2, 3, 0))
    hidden.putpixel((0, 0), (9, 9, 9, 0))
    assert same_picture(rgba, hidden)
    assert same_picture(rgba.convert("P"), rgba.convert("P").convert("RGBA"))
    assert not same_picture(rgba, rgba.convert("RGB"))
    assert not same_picture(Image.new("I;16", (4, 4), 1000), Image.new("L", (4, 4), 4))
    assert same_picture(Image.new("I;16", (4, 4), 1000), Image.new("I", (4, 4), 1000))
    assert not same_picture(Image.new("CMYK", (4, 4)), Image.new("RGB", (4, 4)))


def _source(mode: str) -> Image.Image:
    noise = Image.frombytes("L", (48, 32), random.Random(7).randbytes(48 * 32))
    if mode == "RGBA":
        img = Image.merge("RGBA", [noise, noise.rotate(90, expand=False), noise, noise.point(lambda v: v // 2 * 2)])
    elif mode == "RGB":
        img = Image.merge("RGB", [noise, noise.transpose(Image.Transpose.FLIP_LEFT_RIGHT), noise])
    elif mode == "P":
        img = Image.merge("RGB", [noise, noise, noise]).quantize(64)
    elif mode == "I;16":
        img = noise.convert("I").point(lambda v: v * 257).convert("I;16")
    elif mode == "CMYK":
        img = Image.merge("RGB", [noise, noise, noise]).convert("CMYK")
    else:
        img = noise.convert(mode)
    return img


def _saved(tmp_path, mode: str, fmt: str, *, gps: bool = True, icc: bool = True, orientation: int = 6):
    img = _source(mode)
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010F] = "TestCam"
    if gps:
        exif.get_ifd(0x8825)[1] = "N"
        exif.get_ifd(0x8825)[2] = (51.0, 30.0, 0.0)
    kwargs = {"exif": exif}
    if icc and mode != "CMYK" and fmt in ("PNG", "TIFF", "WEBP", "JPEG"):
        kwargs["icc_profile"] = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    if fmt == "PNG":
        info = PngInfo()
        info.add_text("Comment", "a comment")
        kwargs["pnginfo"] = info
    if fmt == "WEBP":
        kwargs["lossless"] = True
    path = tmp_path / f"src-{mode.replace(';', '')}.{fmt.lower()}"
    img.save(path, format=fmt, **kwargs)
    return path


def _decoded(path, orient: bool = True) -> Image.Image:
    with Image.open(path) as img:
        img.load()
        return ImageOps.exif_transpose(img) if orient else img.copy()


def _check(source, output, told, *, orient: bool = True) -> None:
    """The invariants: a picture said to be identical is; lost and removed things are gone; lossless means nothing went."""
    if told.level != "lossy":
        assert same_picture(_decoded(source, orient), _decoded(output, False)), told
    before, after = image_inventory(source), image_inventory(output)
    for name in before.items:
        label = before.label(name)
        if label in told.lost or label in told.removed:
            assert name not in after.items, (label, told)
    if told.level == "lossless":
        assert told.checked and not told.lost
        assert set(before.items) <= set(after.items) | {
            name for name in before.items if before.label(name) in told.removed
        }, told
    else:
        assert told.changed or told.lost or not told.checked


SOURCES = [
    ("RGB", "PNG"),
    ("RGBA", "PNG"),
    ("P", "PNG"),
    ("L", "PNG"),
    ("I;16", "PNG"),
    ("RGB", "JPEG"),
    ("RGB", "TIFF"),
]
SOURCES += [("CMYK", "TIFF"), ("RGBA", "WEBP"), ("RGB", "BMP"), ("P", "GIF")]
TARGETS = [
    {"target": "png"},
    {"target": "tiff"},
    {"target": "tiff", "tiff_compression": "jpeg"},
    {"target": "bmp"},
    {"target": "gif"},
    {"target": "webp"},
    {"target": "webp", "lossless": True},
    {"target": "jpeg"},
    {"target": "avif"},
    {"target": "apng"},
    {"target": "png", "metadata": "remove_location"},
    {"target": "png", "metadata": "remove_all", "keep_icc": False},
    {"target": "png", "max_width": 20},
    {"target": "png", "preserve_transparency": False},
    {"target": "webp", "lossless": True, "auto_orient": False},
]


@pytest.mark.parametrize("mode, fmt", SOURCES)
@pytest.mark.parametrize("params", TARGETS, ids=lambda p: "-".join(f"{k}={v}" for k, v in p.items()))
def test_every_conversion_says_truthfully_what_it_kept(tmp_path, mode, fmt, params):
    src = _saved(tmp_path, mode, fmt)
    params = dict(params)
    cfg = IMAGE_CONVERT.from_params(params, target=params.pop("target"))
    out = tmp_path / "out"
    told = image.convert(src, out, cfg).fidelity
    _check(src, out, told, orient=getattr(cfg, "auto_orient", True))
    if cfg.target in ("jpeg", "avif") or getattr(cfg, "tiff_compression", None) == "jpeg":
        assert told.level == "lossy"
    if getattr(cfg, "metadata", "keep") != "keep" and metadata.read(src).has_location:
        assert "location" in told.removed and "location" not in told.lost


def test_bmp_to_tiff_is_lossless_and_tiff_to_bmp_says_what_bmp_drops(tmp_path):
    Image.new("RGB", (8, 8), "red").save(tmp_path / "a.bmp", dpi=(0, 0))
    told = image.convert(tmp_path / "a.bmp", tmp_path / "a.tif", IMAGE_CONVERT.from_params({}, target="tiff")).fidelity
    assert told.text in ("lossless", "picture identical; resolution (DPI) dropped")
    src = _saved(tmp_path, "RGB", "TIFF", orientation=1)
    told = image.convert(src, tmp_path / "b.bmp", IMAGE_CONVERT.from_params({}, target="bmp")).fidelity
    assert told.level == "content_lossless"
    assert {"EXIF", "colour profile"} <= set(told.lost)


@pytest.mark.parametrize(
    "params, lossy",
    [
        ({"rotate": 90}, False),
        ({"flip": "horizontal"}, False),
        ({"brightness": 1.5}, True),
        ({"crop_width": 10}, False),
    ],
)
@pytest.mark.parametrize("target", ["png", "webp", "jpeg", "apng"])
def test_edits_say_what_they_changed(tmp_path, params, lossy, target):
    src = _saved(tmp_path, "RGB", "PNG", gps=False)
    extra = {"lossless": True} if target == "webp" else {}
    told = image.edit(src, tmp_path / "out", IMAGE_EDIT.from_params({**params, **extra}, target=target)).fidelity
    assert (told.level == "lossy") == (lossy or target == "jpeg")
    assert ("the area outside the crop" in told.removed) == ("crop_width" in params)


def _animation(path, fmt, frames=5, **kwargs):
    images = [Image.new("RGBA", (24, 16), (i * 50, 255 - i * 40, 80, 255)) for i in range(frames)]
    images[0].save(path, format=fmt, save_all=True, append_images=images[1:], duration=100, loop=0, **kwargs)
    return path


@pytest.mark.parametrize("source_fmt", ["GIF", "WEBP", "PNG"])
@pytest.mark.parametrize("target", ["gif", "webp", "apng", "avif"])
def test_animations_say_whether_every_frame_was_kept(tmp_path, source_fmt, target):
    src = _animation(
        tmp_path / f"a.{source_fmt.lower()}", source_fmt, **({"lossless": True} if source_fmt == "WEBP" else {})
    )
    extra = {"lossless": True} if target == "webp" else {}
    out = tmp_path / f"out.{target}"
    result = image.convert(src, out, IMAGE_CONVERT.from_params(extra, target=target))
    with Image.open(out) as img:
        assert img.n_frames == 5
    assert result.fidelity.level == ("lossy" if target == "avif" else "lossless")


def test_frames_says_what_was_dropped_as_asked(tmp_path):
    src = _animation(tmp_path / "a.gif", "GIF")
    told = image.select_frames(src, tmp_path / "o.gif", IMAGE_FRAMES.from_params({"keep": "0-2"}, target="gif"))
    assert told.fidelity.removed == ["2 of 5 frames"] and told.fidelity.level == "lossless"
    told = image.select_frames(src, tmp_path / "s.gif", IMAGE_FRAMES.from_params({"speed": 2}, target="gif"))
    assert told.fidelity.changed == ["speed ×2 as asked"]


@pytest.mark.parametrize("mode", ["remove_location", "remove_all"])
@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP", "TIFF"])
def test_strip_says_what_it_removed_and_keeps_the_picture(tmp_path, mode, fmt):
    src = _saved(tmp_path, "RGB", fmt, orientation=1)
    out = tmp_path / "out"
    told = metadata.strip(src, out, mode).fidelity
    assert ("location" in told.removed) == metadata.read(src).has_location
    assert not told.lost and told.level == "lossless"
    assert same_picture(_decoded(src), _decoded(out))
    assert not metadata.read(out).has_location


@pytest.mark.parametrize(
    "target, extra", list(itertools.product(["gif", "webp", "avif", "apng"], [{}, {"animated": False}]))
)
def test_avif_and_apng_are_outputs_and_animated_false_gives_the_first_frame(tmp_path, target, extra):
    src = _animation(tmp_path / "a.gif", "GIF")
    out = tmp_path / f"o.{target}"
    result = image.convert(src, out, IMAGE_CONVERT.from_params(extra, target=target))
    with Image.open(out) as img:
        assert getattr(img, "n_frames", 1) == (1 if extra else 5)
        assert img.format == {"gif": "GIF", "webp": "WEBP", "avif": "AVIF", "apng": "PNG"}[target]
    assert ("animation (5 frames)" in result.fidelity.removed) == bool(extra)


def test_an_avif_of_an_animation_that_plays_once_says_it_now_loops(tmp_path):
    images = [Image.new("RGB", (16, 16), (i * 80, 0, 0)) for i in range(3)]
    images[0].save(tmp_path / "a.webp", save_all=True, append_images=images[1:], duration=100, loop=1, lossless=True)
    told = image.convert(tmp_path / "a.webp", tmp_path / "o.avif", IMAGE_CONVERT.from_params({}, target="avif"))
    assert any("loop forever" in item for item in told.fidelity.lost)


def test_an_avif_still_keeps_exif_and_xmp(tmp_path):
    src = _saved(tmp_path, "RGB", "JPEG", orientation=1)
    told = image.convert(src, tmp_path / "o.avif", IMAGE_CONVERT.from_params({}, target="avif")).fidelity
    found = metadata.read(tmp_path / "o.avif")
    assert "EXIF" in found.carriers and found.has_location
    assert "EXIF" not in told.lost and "location" not in told.lost
