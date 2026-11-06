# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""A re-encoded output never keeps a picture hidden in the metadata; the rest of the metadata stays, as the mode says."""

import io
import struct
import zlib

import pytest
from PIL import Image

from sashimi import image, metadata
from sashimi.config import IMAGE_CONVERT, IMAGE_EDIT
from sashimi.core._imagemeta import EXIF_PREFIX, _jpeg_carriers, _segment

MARK = b"HIDDEN-PICTURE-BYTES"
MAKE = "CameraMaker"


def small_jpeg() -> bytes:
    written = io.BytesIO()
    Image.new("RGB", (16, 12), "red").save(written, "JPEG", comment=MARK)
    return written.getvalue()


THUMB_JPEG = small_jpeg()


def exif_block(thumbnail: str = "jpeg", order: str = "<") -> bytes:
    """An EXIF block whose first directory names a camera, with a thumbnail's directory after it, in JPEG or as a strip of pixels."""
    make = MAKE.encode() + b"\0"
    head = (b"II" if order == "<" else b"MM") + struct.pack(order + "HI", 42, 8)
    ifd0_size = 2 + 12 + 4
    make_at = 8 + ifd0_size
    ifd1_at = make_at + len(make) + (len(make) & 1)
    ifd0 = struct.pack(order + "HHHII", 1, 0x010F, 2, len(make), make_at) + struct.pack(order + "I", ifd1_at)
    tags = (0x0201, 0x0202) if thumbnail == "jpeg" else (0x0111, 0x0117)
    data_at = ifd1_at + 2 + 2 * 12 + 4
    body = THUMB_JPEG if thumbnail == "jpeg" else MARK * 4
    ifd1 = struct.pack(order + "H", 2)
    ifd1 += struct.pack(order + "HHII", tags[0], 4, 1, data_at)
    ifd1 += struct.pack(order + "HHII", tags[1], 4, 1, len(body))
    ifd1 += struct.pack(order + "I", 0)
    return head + ifd0 + make + b"\0" * (len(make) & 1) + ifd1 + body


def picture() -> Image.Image:
    return Image.linear_gradient("L").resize((48, 32)).convert("RGB")


def source(kind: str, tmp_path, exif: bytes):
    path = tmp_path / f"in.{kind}"
    picture().save(path, kind.upper() if kind != "jpg" else "JPEG", exif=EXIF_PREFIX + exif)
    return path


SOURCES = ["jpg", "png", "webp", "avif"]
TARGETS = ["jpeg", "png", "webp", "avif", "tiff"]
MODES = ["keep", "remove_location"]


def assert_gone_and_the_rest_kept(path, told) -> None:
    data = path.read_bytes()
    assert MARK not in data
    info = metadata.read(path)
    assert info.thumbnail_size is None
    assert info.camera_make == MAKE
    assert told.lost == ["embedded thumbnail"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("kind", SOURCES)
def test_a_conversion_drops_the_exif_thumbnail_and_keeps_the_rest(tmp_path, kind, target, mode):
    path = source(kind, tmp_path, exif_block())
    assert MARK in path.read_bytes() or kind == "avif"
    if kind != "avif":
        assert metadata.read(path).has_metadata
    out = tmp_path / f"out.{target}"
    told = image.convert(path, out, IMAGE_CONVERT.from_params(target=target, metadata=mode)).fidelity
    assert_gone_and_the_rest_kept(out, told)


@pytest.mark.parametrize("target", ["gif", "bmp"])
def test_outputs_that_cant_hold_it_dont_say_they_lost_it(tmp_path, target):
    path = source("jpg", tmp_path, exif_block())
    out = tmp_path / f"out.{target}"
    told = image.convert(path, out, IMAGE_CONVERT.from_params(target=target)).fidelity
    assert MARK not in out.read_bytes()
    assert "embedded thumbnail" not in told.lost


@pytest.mark.parametrize("mode", MODES)
def test_resizing_and_editing_drop_it_too(tmp_path, mode):
    path = source("jpg", tmp_path, exif_block())
    out = tmp_path / "out.jpg"
    told = image.convert(path, out, IMAGE_CONVERT.from_params(target="jpeg", max_width=24, metadata=mode)).fidelity
    assert_gone_and_the_rest_kept(out, told)
    told = image.edit(path, out, IMAGE_EDIT.from_params(target="jpeg", rotate=90, metadata=mode)).fidelity
    assert_gone_and_the_rest_kept(out, told)


@pytest.mark.parametrize("mode", MODES)
def test_an_animation_drops_it_too(tmp_path, mode):
    path = tmp_path / "in.webp"
    frames = [picture(), picture().rotate(180)]
    frames[0].save(path, "WEBP", save_all=True, append_images=frames[1:], exif=EXIF_PREFIX + exif_block())
    out = tmp_path / "out.webp"
    told = image.convert(path, out, IMAGE_CONVERT.from_params(target="webp", metadata=mode)).fidelity
    assert_gone_and_the_rest_kept(out, told)


@pytest.mark.parametrize("order", ["<", ">"], ids=["little-endian", "big-endian"])
@pytest.mark.parametrize("thumbnail", ["jpeg", "strip"])
def test_any_kind_of_exif_thumbnail_goes(tmp_path, thumbnail, order):
    path = source("jpg", tmp_path, exif_block(thumbnail, order))
    assert MARK in path.read_bytes()
    out = tmp_path / "out.webp"
    told = image.convert(path, out, IMAGE_CONVERT.from_params(target="webp")).fidelity
    assert_gone_and_the_rest_kept(out, told)


def test_the_thumbnail_leaves_no_padding_behind(tmp_path):
    path = source("jpg", tmp_path, exif_block())
    out = tmp_path / "out.jpg"
    image.convert(path, out, IMAGE_CONVERT.from_params({}, target="jpeg"))
    kept = _jpeg_carriers(out.read_bytes()).exif
    assert kept is not None and len(kept) < len(exif_block()) - len(THUMB_JPEG)


def test_remove_all_says_exif_went_not_the_thumbnail(tmp_path):
    path = source("jpg", tmp_path, exif_block())
    out = tmp_path / "out.webp"
    told = image.convert(path, out, IMAGE_CONVERT.from_params(target="webp", metadata="remove_all")).fidelity
    assert MARK not in out.read_bytes()
    assert "embedded thumbnail" not in told.lost and "EXIF" in told.removed


def test_a_strip_copies_the_thumbnail_rather_than_re_encoding(tmp_path):
    path = source("jpg", tmp_path, exif_block())
    out = tmp_path / "out.jpg"
    told = metadata.strip(path, out, "remove_location").fidelity
    assert MARK in out.read_bytes() and told.lost == []
    assert metadata.read(out).thumbnail_size is not None


def photoshop_thumbnail() -> bytes:
    body = THUMB_JPEG
    return b"Photoshop 3.0\0" + b"8BIM\x04\x0c\0\0" + struct.pack(">I", len(body)) + body + b"\0" * (len(body) & 1)


XMP_THUMBNAIL = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
    b'xmlns:xmpGImg="http://ns.adobe.com/xap/1.0/g/img/" dc:format="image/jpeg"><xmp:Thumbnails><rdf:Alt><rdf:li '
    b'rdf:parseType="Resource"><xmpGImg:image>' + MARK + b"</xmpGImg:image></rdf:li></rdf:Alt></xmp:Thumbnails>"
    b"</rdf:Description></rdf:RDF></x:xmpmeta>"
)


def jpeg_holding(*segments: bytes) -> bytes:
    written = io.BytesIO()
    picture().save(written, "JPEG")
    data = written.getvalue()
    return data[:2] + b"".join(segments) + data[2:]


def test_properties_that_merely_look_alike_stay(tmp_path):
    packet = XMP_THUMBNAIL.replace(b"<xmp:Thumbnails>", b"<xmp:Other>").replace(b"</xmp:Thumbnails>", b"</xmp:Other>")
    packet = packet.replace(b'dc:format="image/jpeg"', b'dc:format="image/jpeg" dc:Confidence="high" dc:Data="kept"')
    path = tmp_path / "in.jpg"
    path.write_bytes(jpeg_holding(_segment(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + packet)))
    out = tmp_path / "out.jpg"
    told = image.convert(path, out, IMAGE_CONVERT.from_params({}, target="jpeg")).fidelity
    kept = _jpeg_carriers(out.read_bytes()).xmp
    assert kept == packet
    assert "embedded thumbnail" not in told.lost


@pytest.mark.parametrize(
    "segment",
    [
        _segment(0xED, photoshop_thumbnail()),
        _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + XMP_THUMBNAIL),
    ],
    ids=["Photoshop", "XMP"],
)
@pytest.mark.parametrize("target", ["jpeg", "png", "webp"])
def test_pictures_hidden_elsewhere_in_the_metadata_go(tmp_path, segment, target):
    path = tmp_path / "in.jpg"
    path.write_bytes(jpeg_holding(segment))
    out = tmp_path / f"out.{target}"
    told = image.convert(path, out, IMAGE_CONVERT.from_params(target=target)).fidelity
    assert MARK not in out.read_bytes()
    if b"xmpmeta" in segment:
        assert b'dc:format="image/jpeg"' in _xmp_of(out, target)
        assert told.lost == ["embedded thumbnail"]


def _xmp_of(path, target: str) -> bytes:
    data = path.read_bytes()
    if target == "jpeg":
        return _jpeg_carriers(data).xmp or b""
    return data


def raw_profile_chunk(exif: bytes) -> bytes:
    body = b"\nexif\n%8d\n" % len(EXIF_PREFIX + exif) + (EXIF_PREFIX + exif).hex().encode() + b"\n"
    payload = b"Raw profile type exif\0\0" + zlib.compress(body)
    return struct.pack(">I", len(payload)) + b"zTXt" + payload + struct.pack(">I", zlib.crc32(b"zTXt" + payload))


def test_an_exif_profile_written_as_text_loses_its_thumbnail_and_nothing_else(tmp_path):
    written = io.BytesIO()
    picture().save(written, "PNG")
    data = written.getvalue()
    path = tmp_path / "in.png"
    path.write_bytes(data[:33] + raw_profile_chunk(exif_block()) + data[33:])
    out = tmp_path / "out.png"
    told = image.convert(path, out, IMAGE_CONVERT.from_params(target="png")).fidelity
    output = out.read_bytes()
    at = output.index(b"Raw profile type exif")
    length = struct.unpack(">I", output[at - 8 : at - 4])[0]
    text = zlib.decompress(output[at + len(b"Raw profile type exif") + 2 : at + length])
    block = bytes.fromhex(text.split(b"\n", 3)[3].decode())
    assert MARK not in block and MAKE.encode() in block and block.startswith(EXIF_PREFIX)
    assert told.lost == ["embedded thumbnail"]
