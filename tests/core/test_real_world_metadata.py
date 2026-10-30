# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Metadata removal from HEIC and AVIF in place, and JPEG motion photos and Ultra HDR photos, whose
XMP points at data after the picture (regressions from the real-world media benchmark, 2026-10-29).
"""

import io
import re
import struct

import pytest
from PIL import Image, ImageChops

from sashimi.config import IMAGE_CONVERT
from sashimi.core import image as core_image
from sashimi.core import metadata
from sashimi.core._heifmeta import drop_gps_in_place
from sashimi.core._imagemeta import jpeg_pieces, _mpf_entries

pillow_heif = pytest.importorskip("pillow_heif")


def _exif_with_gps() -> Image.Exif:
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    exif[0x0112] = 1
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (51.0, 30.0, 0.0), "W", (0.0, 7.0, 0.0)
    return exif


XMP = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about="" xmlns:exif="http://ns.adobe.com/exif/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"'
    b' exif:GPSLatitude="51,30N" exif:GPSLongitude="0,7W" dc:format="image/heic"/></rdf:RDF></x:xmpmeta>'
)


def _heif(path, fmt: str) -> None:
    pillow_heif.register_heif_opener()
    img = Image.linear_gradient("L").resize((64, 48)).convert("RGB")
    img.save(path, format=fmt, exif=_exif_with_gps().tobytes(), xmp=XMP, quality=90)


@pytest.mark.parametrize("fmt, ext", [("HEIF", "heic"), ("AVIF", "avif")])
@pytest.mark.parametrize("mode", ["remove_location", "remove_all"])
def test_heif_metadata_is_removed_in_place_without_re_encoding(tmp_path, fmt, ext, mode):
    src = tmp_path / f"p.{ext}"
    _heif(src, fmt)
    before = metadata.read(src)
    assert before.has_location and before.camera_make == "TestCam"
    out = tmp_path / f"o.{ext}"
    assert metadata.strip(src, out, mode).format == ext
    after = metadata.read(out)
    assert not after.has_location and after.latitude is None
    assert (after.camera_make == "TestCam") == (mode == "remove_location")
    assert out.stat().st_size == src.stat().st_size
    a, b = src.read_bytes(), out.read_bytes()
    assert sum(x != y for x, y in zip(a, b, strict=True)) < 600
    assert ImageChops.difference(Image.open(src).convert("RGB"), Image.open(out).convert("RGB")).getbbox() is None
    assert b"51,30N" not in b and b"TestCam" not in b if mode == "remove_all" else b"51,30N" not in b


def test_heif_keeps_an_auxiliary_image_s_xmp_under_remove_all():
    from sashimi.core._heifmeta import _new_xmp

    xmp = b'<x:xmpmeta><rdf:Description HDRGainMap:HDRGainMapVersion="65536" exif:GPSLatitude="1N"/></x:xmpmeta>'
    kept = _new_xmp(xmp, "remove_all", keep=True)
    assert b"HDRGainMapVersion" in kept and b"GPSLatitude" not in kept and len(kept) == len(xmp)
    assert _new_xmp(xmp, "remove_all", keep=False).strip() == b'<x:xmpmeta xmlns:x="adobe:ns:meta/"/>'


def test_dropping_gps_in_place_keeps_every_other_offset():
    exif = _exif_with_gps()
    exif[0x9286] = b"ASCII\0\0\0user comment that must keep its offset"
    data = exif.tobytes()[6:] if exif.tobytes().startswith(b"Exif") else exif.tobytes()
    edited = drop_gps_in_place(data)
    assert len(edited) == len(data)
    check = Image.Exif()
    check.load(b"Exif\x00\x00" + edited)
    assert 0x8825 not in check and check[0x010F] == "TestCam"


def _jpeg(img: Image.Image, xmp: bytes | None = None, exif: bytes | None = None) -> bytes:
    buffer = io.BytesIO()
    kwargs = {"quality": 90}
    if exif:
        kwargs["exif"] = exif
    img.save(buffer, "JPEG", **kwargs)
    data = buffer.getvalue()
    if xmp is not None:
        payload = b"http://ns.adobe.com/xap/1.0/\x00" + xmp
        data = data[:2] + b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload + data[2:]
    return data


def _motion_photo(gain_map_gps: bool = False) -> tuple[bytes, int, int]:
    """
    An Ultra HDR motion photo: a JPEG whose XMP lists a gain map and a video after the picture, an
    MPF segment pointing at the gain map, then the gain map JPEG and the video.
    """
    gain = _jpeg(
        Image.new("L", (16, 12), 128),
        xmp=b'<x:xmpmeta><rdf:Description hdrgm:Version="1.0" hdrgm:GainMapMax="2.0"/></x:xmpmeta>',
        exif=_exif_with_gps().tobytes() if gain_map_gps else None,
    )
    video = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100 + b"\xa9xyz+51.5-000.1/"
    xmp = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description rdf:about=""'
        b' hdrgm:Version="1.0" GCamera:MotionPhoto="1" GCamera:MotionPhotoVersion="1"'
        b' GCamera:MotionPhotoPresentationTimestampUs="1000" exif:GPSLatitude="51,30N">'
        b"<Container:Directory><rdf:Seq>"
        b'<rdf:li rdf:parseType="Resource"><Container:Item Item:Semantic="Primary" Item:Mime="image/jpeg"/></rdf:li>'
        b'<rdf:li rdf:parseType="Resource"><Container:Item Item:Semantic="GainMap" Item:Mime="image/jpeg" Item:Length="'
        + str(len(gain)).encode()
        + b'"/></rdf:li>'
        b'<rdf:li rdf:parseType="Resource"><Container:Item Item:Mime="video/mp4" Item:Semantic="MotionPhoto" Item:Length="'
        + str(len(video)).encode()
        + b'"/></rdf:li>'
        b"</rdf:Seq></Container:Directory></rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    main = _jpeg(Image.new("RGB", (32, 24), (200, 50, 50)), xmp=xmp, exif=_exif_with_gps().tobytes())

    def mpf(offset: int) -> bytes:
        entries = struct.pack("<IIIHH", 0x030000, 0, 0, 0, 0) + struct.pack("<IIIHH", 0, len(gain), offset, 0, 0)
        ifd = struct.pack("<H", 3)
        ifd += struct.pack("<HHI4s", 0xB000, 7, 4, b"0100")
        ifd += struct.pack("<HHII", 0xB001, 4, 1, 2)
        ifd += struct.pack("<HHII", 0xB002, 7, 32, 8 + 2 + 36 + 4)
        ifd += struct.pack("<I", 0)
        return b"MPF\x00" + b"II*\x00" + struct.pack("<I", 8) + ifd + entries

    segment_length = len(mpf(0)) + 2
    at = 2 + 4 + len(mpf(0))
    header = 2 + 4 + 4
    primary_length = len(main) + 2 + segment_length
    data = main[:2] + b"\xff\xe2" + struct.pack(">H", segment_length) + mpf(primary_length - header) + main[2:]
    assert len(data) == primary_length and at
    return data + gain + video, len(gain), len(video)


def _mpf_pictures(data: bytes) -> list[tuple[int, int]]:
    pieces, _ = jpeg_pieces(data)
    position = 0
    for marker, segment in pieces:
        if marker == 0xE2 and segment[4:8] == b"MPF\x00":
            return [(size, offset + position + 8 if offset else 0) for size, offset in _mpf_entries(segment)[2]]
        position += len(segment)
    return []


def _xmp(data: bytes) -> bytes:
    return re.search(rb"<x:xmpmeta.*?</x:xmpmeta>", data, re.S).group(0)


def test_remove_location_keeps_the_gain_map_and_drops_the_video_and_what_points_at_it(tmp_path):
    data, gain_length, _ = _motion_photo()
    src = tmp_path / "m.jpg"
    src.write_bytes(data)
    metadata.strip(src, tmp_path / "o.jpg", "remove_location")
    out = (tmp_path / "o.jpg").read_bytes()
    pictures = _mpf_pictures(out)
    size, start = pictures[1]
    assert size == gain_length and out[start : start + 2] == b"\xff\xd8" and start + size == len(out)
    Image.open(io.BytesIO(out[start:])).load()
    xmp = _xmp(out)
    assert b"MotionPhoto" not in xmp and b"GPSLatitude" not in xmp
    assert b'Item:Semantic="GainMap"' in xmp and b"hdrgm:Version" in xmp
    assert b"\xa9xyz" not in out and not metadata.read(tmp_path / "o.jpg").has_location
    assert (
        ImageChops.difference(Image.open(src).convert("RGB"), Image.open(tmp_path / "o.jpg").convert("RGB")).getbbox()
        is None
    )


def test_a_gain_map_with_a_location_is_dropped_too(tmp_path):
    data, _, _ = _motion_photo(gain_map_gps=True)
    (tmp_path / "m.jpg").write_bytes(data)
    metadata.strip(tmp_path / "m.jpg", tmp_path / "o.jpg", "remove_location")
    out = (tmp_path / "o.jpg").read_bytes()
    assert _mpf_pictures(out) == [] and jpeg_pieces(out)[1] == b""
    xmp = _xmp(out)
    assert b"hdrgm" not in xmp and b"Container:Directory" not in xmp and b"MotionPhoto" not in xmp


def test_remove_all_keeps_the_gain_map_and_drops_a_video_it_cannot_read(tmp_path):
    data, gain_length, _ = _motion_photo()
    (tmp_path / "m.jpg").write_bytes(data)
    result = metadata.strip(tmp_path / "m.jpg", tmp_path / "o.jpg", "remove_all")
    out = (tmp_path / "o.jpg").read_bytes()
    assert [size for size, _ in _mpf_pictures(out)][1:] == [gain_length]
    assert b"hdrgm:Version" in out and b"MotionPhoto" not in out and b"GPSLatitude" not in out
    assert b"ftyp" not in out and b"Exif" not in out
    assert any("motion photo's video was removed" in note for note in result.fidelity.notes)


@pytest.mark.parametrize("target", ["jpeg", "webp", "png"])
@pytest.mark.parametrize("mode", ["keep", "remove_location"])
def test_converted_photos_keep_no_xmp_pointing_at_what_they_dont_carry(tmp_path, target, mode):
    data, _, _ = _motion_photo()
    (tmp_path / "m.jpg").write_bytes(data)
    out = tmp_path / f"o.{target}"
    core_image.convert(tmp_path / "m.jpg", out, IMAGE_CONVERT.from_params({"metadata": mode}, target=target))
    written = out.read_bytes()
    assert b"MotionPhoto" not in written and b"hdrgm" not in written and b"Container:Directory" not in written
    if mode == "keep":
        assert b"GPSLatitude" in written
