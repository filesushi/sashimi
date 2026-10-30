# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
The metadata rule for images, pinned on the output's bytes: ``keep`` carries every carrier the
output format holds, ``remove_location`` drops coordinates and place names only, and
``remove_all`` drops everything but what showing the picture needs. Also the lossless removal.
"""

import io
import struct

import pytest
from PIL import Image, ImageChops

from sashimi import image, metadata
from sashimi.config import IMAGE_CONVERT, IMAGE_EDIT, IMAGE_FRAMES, IMAGE_RESIZE, FaviconPackConfig, IcoConfig
from sashimi.core._imagemeta import iptc_without_location, xmp_upright, xmp_without_location
from sashimi.core._tiffmeta import edit_exif, exif_orientation
from sashimi.errors import InvalidOptionsError, UnsupportedMediaError
from tests import metadata_samples as samples

CARRIER_MARKERS = {
    "EXIF": b"SASHIMI-PRIVACY-MARKER-make",
    "XMP": b"SASHIMI-PRIVACY-MARKER-xmp",
    "IPTC": b"SASHIMI-PRIVACY-MARKER-iptc",
    "comment": b"SASHIMI-PRIVACY-MARKER-comment",
    "PNG text": b"SASHIMI-PRIVACY-MARKER-title",
}
SOURCE_CARRIERS = {
    "jpeg": {"EXIF", "XMP", "IPTC", "comment"},
    "png": {"EXIF", "XMP", "comment", "PNG text"},
    "webp": {"EXIF", "XMP"},
    "gif": {"XMP", "comment"},
    "tiff": {"EXIF", "XMP", "IPTC"},
    "tiff-lzw": {"EXIF", "XMP", "IPTC"},
}
TARGET_HOLDS = {
    "jpeg": {"EXIF", "XMP", "IPTC", "comment"},
    "png": {"EXIF", "XMP", "comment", "PNG text"},
    "webp": {"EXIF", "XMP"},
    "gif": {"comment"},
    "bmp": set(),
    "tiff": {"EXIF", "XMP", "IPTC"},
}
"""What each output format can hold of the source's metadata (PNG text only from a PNG)."""
LOCATION_CARRIERS = {"EXIF", "XMP", "IPTC", "PNG text"}


def source(tmp_path, name):
    path = tmp_path / f"source-{name}"
    if not path.exists():
        path.write_bytes(samples.IMAGES[name]())
    return path


def carried(source_name, target):
    held = SOURCE_CARRIERS[source_name] & TARGET_HOLDS[target]
    if source_name not in ("png",):
        held.discard("PNG text")
    return held


def gps_in(path):
    with Image.open(path) as img:
        return 0x8825 in img.getexif()


def check(path, mode, held):
    data = path.read_bytes()
    if mode == "remove_all":
        assert samples.MARKER not in data
    else:
        for carrier in held:
            assert CARRIER_MARKERS[carrier] in data, carrier
    if mode == "keep":
        if held & LOCATION_CARRIERS:
            assert samples.PLACE in data
    else:
        assert samples.PLACE not in data
        assert not any(text in data for text in samples.COORDINATE_BYTES)
        assert not gps_in(path)


MODES = ("keep", "remove_location", "remove_all")


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("target", ["jpeg", "png", "webp", "gif", "bmp", "tiff"])
@pytest.mark.parametrize("source_name", list(samples.IMAGES))
@pytest.mark.parametrize("auto_orient", [True, False])
def test_convert_follows_the_metadata_mode(tmp_path, source_name, target, mode, auto_orient):
    if target == "bmp" and mode != "keep":
        pytest.skip("BMP has no metadata field")
    params = {"auto_orient": auto_orient} if target == "bmp" else {"metadata": mode, "auto_orient": auto_orient}
    out = tmp_path / "out"
    image.convert(source(tmp_path, source_name), out, IMAGE_CONVERT.from_params(params, target=target))
    check(out, mode, carried(source_name, target))
    if target == "bmp":
        assert samples.MARKER not in out.read_bytes()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("source_name", list(samples.IMAGES))
def test_resize_follows_the_metadata_mode(tmp_path, source_name, mode):
    target = "tiff" if source_name.startswith("tiff") else source_name
    out = tmp_path / "out"
    config = IMAGE_RESIZE.from_params({"max_width": 20, "metadata": mode}, target=target)
    image.convert(source(tmp_path, source_name), out, config)
    check(out, mode, carried(source_name, target))


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("target", ["jpeg", "png", "webp", "gif", "tiff"])
@pytest.mark.parametrize("source_name", ["jpeg", "png", "tiff", "webp"])
def test_edit_follows_the_metadata_mode(tmp_path, source_name, target, mode):
    out = tmp_path / "out"
    config = IMAGE_EDIT.from_params({"rotate": 90, "crop_width": 20, "metadata": mode}, target=target)
    image.edit(source(tmp_path, source_name), out, config)
    check(out, mode, carried(source_name, target))


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("kind", ["gif", "webp"])
def test_animated_edits_and_frame_selection_follow_the_metadata_mode(tmp_path, kind, mode):
    src = tmp_path / "anim"
    src.write_bytes(samples.gif(animated=True) if kind == "gif" else samples.animated_webp())
    held = {"comment"} if kind == "gif" else {"EXIF", "XMP"}
    out = tmp_path / "edited"
    image.edit(src, out, IMAGE_EDIT.from_params({"brightness": 1.2, "metadata": mode}, target=kind))
    with Image.open(out) as img:
        assert img.n_frames == 2
    check(out, mode, held)
    out = tmp_path / "frames"
    image.select_frames(src, out, IMAGE_FRAMES.from_params({"keep": "1", "metadata": mode}, target=kind))
    check(out, mode, held)


def test_frame_strips_icons_and_packs_carry_no_metadata(tmp_path):
    for name in samples.IMAGES:
        src = source(tmp_path, name)
        image.frame_strip(src, tmp_path / "strip")
        image.to_ico(src, tmp_path / "icon", IcoConfig())
        image.favicon_pack(src, tmp_path / "pack", FaviconPackConfig())
        for out in ("strip", "icon", "pack"):
            data = (tmp_path / out).read_bytes()
            assert samples.MARKER not in data and samples.PLACE not in data, (name, out)
            assert b"iCCP" not in data and b"ICCP" not in data, (name, out)


def _with_profile(tmp_path, name, profile):
    path = tmp_path / f"{name}.png"
    Image.new("RGBA", (32, 32), (200, 40, 40, 255)).save(path, "PNG", icc_profile=profile)
    return path


def test_icons_convert_the_colour_profile_to_srgb(tmp_path):
    srgb = _with_profile(tmp_path, "srgb", samples.icc())
    swapped = _with_profile(tmp_path, "swapped", samples.swapped_profile())
    image.to_ico(srgb, tmp_path / "srgb.ico", IcoConfig(sizes=(16,)))
    with Image.open(tmp_path / "srgb.ico") as icon:
        red = icon.convert("RGB").getpixel((8, 8))
    assert all(abs(a - b) <= 2 for a, b in zip(red, (200, 40, 40), strict=True))
    image.to_ico(swapped, tmp_path / "swapped.ico", IcoConfig(sizes=(16,)))
    with Image.open(tmp_path / "swapped.ico") as icon:
        red, green, _ = icon.convert("RGB").getpixel((8, 8))
    assert green > 150 and red < 80
    image.favicon_pack(swapped, tmp_path / "pack.zip", FaviconPackConfig())
    import zipfile

    with zipfile.ZipFile(tmp_path / "pack.zip") as archive:
        icon = Image.open(io.BytesIO(archive.read("favicon-32x32.png")))
        assert icon.convert("RGB").getpixel((16, 16))[1] > 150 and "icc_profile" not in icon.info


def test_tiff_to_tiff_without_auto_orient_drops_every_tag_with_remove_all(tmp_path):
    out = tmp_path / "out"
    config = IMAGE_CONVERT.from_params({"auto_orient": False, "metadata": "remove_all"}, target="tiff")
    image.convert(source(tmp_path, "tiff"), out, config)
    with Image.open(out) as img:
        for tag in (270, 271, 315, 700, 33723, 34377, 34853, 34665):
            assert tag not in img.tag_v2, tag
    assert samples.MARKER not in out.read_bytes()


@pytest.mark.parametrize("target", ["jpeg", "png", "webp", "tiff"])
def test_applying_the_orientation_resets_it_to_1(tmp_path, target):
    src = tmp_path / "rotated.jpg"
    src.write_bytes(samples.jpeg(orientation=6))

    def orientation(path):
        with Image.open(path) as img:
            if target == "tiff":
                return None, img.tag_v2.get(0x0112), None
            return img.size, img.getexif().get(0x0112), dict(img.getexif())

    image.convert(src, tmp_path / "upright", IMAGE_CONVERT.from_params({}, target=target))
    size, tag, _ = orientation(tmp_path / "upright")
    assert size in ((48, 64), None) and tag == 1
    image.convert(src, tmp_path / "asis", IMAGE_CONVERT.from_params({"auto_orient": False}, target=target))
    size, tag, _ = orientation(tmp_path / "asis")
    assert size in ((64, 48), None) and tag == 6
    config = IMAGE_CONVERT.from_params({"auto_orient": False, "metadata": "remove_all"}, target=target)
    image.convert(src, tmp_path / "bare", config)
    size, tag, everything = orientation(tmp_path / "bare")
    assert size in ((64, 48), None) and tag == 6 and everything in (None, {0x0112: 6})
    assert samples.MARKER not in (tmp_path / "bare").read_bytes()


def test_edits_reset_the_orientation_and_drop_the_thumbnail():
    block = Image.Exif()
    block[0x0112] = 6
    block[0x010F] = "cam"
    thumb = b"\xff\xd8thumbnail-bytes\xff\xd9"
    data = bytearray(block.tobytes()[6:])
    order = "<" if data[:2] == b"II" else ">"
    entries = struct.unpack_from(order + "H", data, 8)[0]
    next_at = 8 + 2 + 12 * entries
    struct.pack_into(order + "I", data, next_at, len(data))
    ifd1_offset = len(data)
    thumb_offset = ifd1_offset + 2 + 24 + 4
    data += struct.pack(order + "H", 2)
    data += struct.pack(order + "HHII", 0x0201, 4, 1, thumb_offset)
    data += struct.pack(order + "HHII", 0x0202, 4, 1, len(thumb))
    data += struct.pack(order + "I", 0) + thumb
    edited = edit_exif(bytes(data), drop_thumbnail=True, reset_orientation=True)
    assert thumb not in edited and exif_orientation(edited) == 1
    loaded = Image.Exif()
    loaded.load(b"Exif\x00\x00" + edited)
    assert loaded[0x010F] == "cam"


def test_location_filters_keep_everything_else():
    filtered = xmp_without_location(samples.XMP)
    assert b"GPS" not in filtered and samples.PLACE not in filtered
    assert b"SASHIMI-PRIVACY-MARKER-xmp" in filtered and filtered.endswith(b'<?xpacket end="w"?>')
    assert xmp_upright(b'<x tiff:Orientation="6"/>') == b'<x tiff:Orientation="1"/>'
    assert xmp_upright(b"<tiff:Orientation>8</tiff:Orientation>") == b"<tiff:Orientation>1</tiff:Orientation>"
    iptc = iptc_without_location(samples.iptc())
    assert samples.PLACE not in iptc and b"SASHIMI-PRIVACY-MARKER-iptc" in iptc


def _frames(path):
    with Image.open(path) as img:
        frames = []
        for index in range(getattr(img, "n_frames", 1)):
            img.seek(index)
            frames.append(img.convert("RGBA").tobytes())
        return frames, img.format


STRIP_SOURCES = {**samples.IMAGES, "gif-animated": lambda: samples.gif(True), "webp-animated": samples.animated_webp}
STRIP_EXTENSIONS = {"jpeg": "jpg", "png": "png", "webp": "webp", "gif": "gif", "tiff": "tif", "tiff-lzw": "tif"}
STRIP_EXTENSIONS |= {"gif-animated": "gif", "webp-animated": "webp"}


@pytest.mark.parametrize("mode", ["remove_location", "remove_all"])
@pytest.mark.parametrize("name", list(STRIP_SOURCES))
def test_strip_removes_metadata_without_re_encoding(tmp_path, name, mode):
    src = tmp_path / "source"
    src.write_bytes(STRIP_SOURCES[name]())
    out = tmp_path / "out"
    extension = metadata.strip(src, out, mode).format
    assert extension == STRIP_EXTENSIONS[name]
    assert _frames(out) == _frames(src)
    held = SOURCE_CARRIERS.get(name, {"comment", "XMP"} if name.startswith("gif") else {"EXIF", "XMP"})
    check(out, mode, held)
    found = metadata.read(out)
    assert not found.has_location
    if mode == "remove_all":
        assert found.carriers == []
        assert found.has_color_profile == (name in ("jpeg", "png", "webp", "tiff", "tiff-lzw"))


def test_strip_keeps_the_jpeg_scan_data_byte_for_byte(tmp_path):
    src = tmp_path / "photo.jpg"
    src.write_bytes(samples.jpeg())
    metadata.strip(src, tmp_path / "out.jpg", "remove_all")
    original, stripped = src.read_bytes(), (tmp_path / "out.jpg").read_bytes()
    scan = original[original.index(b"\xff\xda") :]
    assert stripped.endswith(scan)
    metadata.strip(src, tmp_path / "bare.jpg", "remove_all", keep_icc=False)
    with Image.open(tmp_path / "bare.jpg") as img:
        assert "icc_profile" not in img.info


def test_strip_keeps_the_orientation_alone(tmp_path):
    src = tmp_path / "rotated.jpg"
    src.write_bytes(samples.jpeg(orientation=6))
    metadata.strip(src, tmp_path / "out.jpg", "remove_all")
    with Image.open(tmp_path / "out.jpg") as img:
        assert dict(img.getexif()) == {0x0112: 6}


def test_strip_refuses_keep_and_what_it_cannot_handle(tmp_path):
    src = tmp_path / "photo.jpg"
    src.write_bytes(samples.jpeg())
    with pytest.raises(InvalidOptionsError):
        metadata.strip(src, tmp_path / "out", "keep")  # type: ignore[arg-type]
    bmp = tmp_path / "picture.bmp"
    Image.new("RGB", (4, 4)).save(bmp, "BMP")
    with pytest.raises(UnsupportedMediaError):
        metadata.strip(bmp, tmp_path / "out", "remove_all")
    big = tmp_path / "big.tif"
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, "TIFF", big_tiff=True)
    big.write_bytes(buffer.getvalue())
    with pytest.raises(UnsupportedMediaError):
        metadata.strip(big, tmp_path / "out", "remove_all")


def test_read_reports_what_it_finds(tmp_path):
    found = metadata.read(source(tmp_path, "jpeg"))
    assert found.kind == "image" and found.has_metadata and found.has_location
    assert found.latitude == pytest.approx(48.8584, abs=1e-4) and found.longitude == pytest.approx(2.2945, abs=1e-4)
    assert {"EXIF", "GPS", "XMP", "IPTC", "comment"} <= set(found.carriers)
    assert found.camera_make == "SASHIMI-PRIVACY-MARKER-make" and found.date_taken == "2001:02:03 04:05:06"
    assert found.place == "SASHIMI-LOCATION-MARKER" and found.has_color_profile
    plain = tmp_path / "plain.png"
    Image.new("RGB", (4, 4)).save(plain, "PNG")
    empty = metadata.read(plain)
    assert not empty.has_metadata and not empty.has_location and empty.carriers == []
