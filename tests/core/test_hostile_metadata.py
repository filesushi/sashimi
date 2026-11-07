# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
No file's contents can make ``read``, ``convert``, ``edit`` or ``strip`` raise anything but a
SashimiError, leave an output behind a refusal, or break what a read promises: every text clean
and capped, the whole read bounded, and every counted section's carrier named in ``carriers``.
Hostile metadata is built by hand for every carrier of JPEG, PNG, WebP, TIFF and GIF, then the same
files are damaged at random (seeded) where their metadata lives. Images only (no FFmpeg needed).
"""

import io
import json
import pathlib
import random
import re
import struct
import time
import warnings
import zlib

import pytest
from PIL import Image, TiffImagePlugin

from sashimi import SashimiError, image, metadata
from sashimi.config import IMAGE_CONVERT, IMAGE_EDIT
from sashimi.core import edit as edit_core
from sashimi.core import image as image_core
from sashimi.core import metadata as metadata_core
from tests import metadata_samples as samples

XMP_HEAD = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
XMP_NS = (
    b'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:exif="http://ns.adobe.com/exif/1.0/" '
    b'xmlns:tiff="http://ns.adobe.com/tiff/1.0/" xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" '
    b'xmlns:Container="http://ns.google.com/photos/1.0/container/" '
    b'xmlns:Item="http://ns.google.com/photos/1.0/container/item/" '
    b'xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"'
)
READ_LIMIT = 1 << 20


def xmp_packet(attributes: bytes = b"", body: bytes = b"") -> bytes:
    return (
        XMP_HEAD
        + b'<rdf:Description rdf:about="" '
        + XMP_NS
        + b" "
        + attributes
        + b">"
        + body
        + b"</rdf:Description></rdf:RDF></x:xmpmeta>"
    )


def container_item(attributes: bytes, inner: bytes = b"") -> bytes:
    return (
        b'<Container:Directory><rdf:Seq><rdf:li rdf:parseType="Resource"><Container:Item '
        + attributes
        + b"/>"
        + inner
        + b"</rdf:li></rdf:Seq></Container:Directory>"
    )


GAIN_MAP_ITEMS = (
    b'<Container:Directory><rdf:Seq><rdf:li rdf:parseType="Resource"><Container:Item Item:Semantic="Primary" '
    b'Item:Mime="image/jpeg"/></rdf:li><rdf:li rdf:parseType="Resource"><Container:Item Item:Semantic="GainMap" '
    b'Item:Mime="image/jpeg" Item:Length="1234"/></rdf:li></rdf:Seq></Container:Directory>'
)
NAME_IN_ITEM = (
    b'<Container:Directory><rdf:Seq><rdf:li rdf:parseType="Resource"><Container:Item Item:Semantic="GainMap" '
    b'Item:Mime="image/jpeg"><dc:creator>Jane Doe, 12 Rue X, Paris</dc:creator>'
    b"<exif:GPSLatitude>48,51.5N</exif:GPSLatitude><exif:GPSLongitude>2,17.6E</exif:GPSLongitude></Container:Item>"
    b"</rdf:li></rdf:Seq></Container:Directory>"
)

XMP_PACKETS = {
    "superscript orientation": xmp_packet(b'tiff:Orientation="\xc2\xb2"'),
    "arabic orientation": xmp_packet('tiff:Orientation="٣"'.encode()),
    "huge orientation": xmp_packet(b'tiff:Orientation="' + b"9" * 5000 + b'"'),
    "doctype": b'<!DOCTYPE x [<!ENTITY a "b">]>' + xmp_packet(b'dc:title="&a;"'),
    "not xml": b"<x:xmpmeta \xff\xfe garbage",
    "not utf-8": xmp_packet(b'dc:title="\xff\xfe"'),
    "unclosed": XMP_HEAD,
    "deep": XMP_HEAD + b"<a>" * 60000,
    "deep structs": xmp_packet(body=b"<dc:a>" * 3000 + b"x" + b"</dc:a>" * 3000),
    "c1 controls": xmp_packet('dc:title="\x9b31mred\u202eevil"'.encode()),
    "wide struct": xmp_packet(body=b"<dc:wide>" + b"<dc:c/>" * 20000 + b"</dc:wide>"),
    "long word": b"a" * 500_000 + b":Orientation=",
    "long value": xmp_packet(b'dc:title="' + b"x" * 100_000 + b'"'),
    "gain map with a name": xmp_packet(b'hdrgm:Version="1.0"', NAME_IN_ITEM),
    "only display": xmp_packet(b'hdrgm:Version="1.0"', GAIN_MAP_ITEMS),
    "display with a name in an item": xmp_packet(
        b'hdrgm:Version="1.0"', container_item(b'Item:Semantic="GainMap" Item:Mime="image/jpeg" Item:Label="Jane Doe"')
    ),
    "gps literal in a list": xmp_packet(
        body=b"<exif:GPSLatitude><rdf:Seq><rdf:li>nonsense</rdf:li></rdf:Seq></exif:GPSLatitude>"
    ),
    "empty": b"",
}


def exif_block(**tags) -> bytes:
    block = Image.Exif()
    for tag, value in tags.items():
        block[int(tag.removeprefix("t"), 16)] = value
    return block.tobytes()


def raw_exif(entries: list[tuple[int, int, int, bytes]], tail: bytes = b"", next_ifd: int = 0) -> bytes:
    """A little-endian EXIF block: IFD0's (tag, type, count, 4 bytes of value), its next-IFD pointer, then `tail`."""
    body = struct.pack("<H", len(entries))
    for tag, kind, count, value in entries:
        body += struct.pack("<HHI", tag, kind, count) + value.ljust(4, b"\0")
    return b"II*\0" + struct.pack("<I", 8) + body + struct.pack("<I", next_ifd) + tail


def sub_ifd_exif(pointer: int, entries: list[tuple[int, int, bytes]]) -> bytes:
    """A little-endian EXIF block whose IFD0 points (tag `pointer`) at a directory of (tag, type, value bytes)."""
    start = 8 + 2 + 12 + 4
    tail_at = start + 2 + 12 * len(entries) + 4
    body = struct.pack("<H", len(entries))
    tail = b""
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1}
    for tag, kind, value in entries:
        count = len(value) // sizes[kind]
        if len(value) <= 4:
            body += struct.pack("<HHI", tag, kind, count) + value.ljust(4, b"\0")
        else:
            body += struct.pack("<HHII", tag, kind, count, tail_at + len(tail))
            tail += value
    return raw_exif([(pointer, 4, 1, struct.pack("<I", start))], body + struct.pack("<I", 0) + tail)


def rationals(*pairs: tuple[int, int]) -> bytes:
    return b"".join(struct.pack("<II", *pair) for pair in pairs)


def bomb_thumbnail() -> bytes:
    """A little-endian EXIF block whose IFD1 points at a small JPEG whose header claims 30000x30000."""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, "JPEG")
    thumb = bytearray(buffer.getvalue())
    at = thumb.find(b"\xff\xc0")
    struct.pack_into(">HH", thumb, at + 5, 30000, 30000)
    ifd1_at = 8 + 2 + 12 + 4
    thumb_at = ifd1_at + 2 + 2 * 12 + 4
    ifd1 = struct.pack("<H", 2)
    ifd1 += struct.pack("<HHII", 0x0201, 4, 1, thumb_at)
    ifd1 += struct.pack("<HHII", 0x0202, 4, 1, len(thumb))
    ifd1 += struct.pack("<I", 0)
    return raw_exif([(0x0112, 3, 1, struct.pack("<H", 1))], ifd1 + bytes(thumb), next_ifd=ifd1_at)


def bomb_thumbnail_png() -> bytes:
    """As :func:`bomb_thumbnail`, with a PNG thumbnail whose header claims 30000x30000."""
    block = bytearray(bomb_thumbnail())
    start = block.find(b"\xff\xd8")
    head = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">IIBBBBB", 30000, 30000, 8, 2, 0, 0, 0) + b"\0\0\0\0"
    block[start : start + len(head)] = head
    return bytes(block)


EXIF_BLOCKS = {
    "bomb thumbnail": bomb_thumbnail(),
    "bomb thumbnail as png": bomb_thumbnail_png(),
    "truncated": bomb_thumbnail()[:20],
    "bad magic": b"XX*\0" + bytes(32),
    "pointer loop": raw_exif([(0x8769, 4, 1, struct.pack("<I", 8))]),
    "gps out of range": raw_exif([(0x8825, 4, 1, struct.pack("<I", 0xFFFFFFF0))]),
    "huge count": raw_exif([(0x010F, 2, 0xFFFFFFFF, struct.pack("<I", 8))]),
    "unknown type": raw_exif([(0x0112, 99, 1, b"\1\0\0\0")]),
    "orientation as text": raw_exif([(0x0112, 2, 2, b"6\0\0\0")]),
    "zero denominators": sub_ifd_exif(
        0x8825,
        [
            (1, 2, b"N\0"),
            (2, 5, rationals((1, 0), (1, 0), (1, 0))),
            (3, 2, b"E\0"),
            (4, 5, rationals((0, 0), (0, 0), (0, 0))),
            (6, 5, rationals((1, 0))),
        ],
    ),
    "gps of the wrong shape": sub_ifd_exif(
        0x8825, [(1, 2, b"N\0"), (2, 2, b"north\0"), (3, 2, b"E\0"), (4, 3, b"\7\0")]
    ),
    "impossible coordinates": sub_ifd_exif(
        0x8825,
        [
            (1, 2, b"N\0"),
            (2, 5, rationals((100000, 1), (0, 1), (0, 1))),
            (3, 2, b"E\0"),
            (4, 5, rationals((999999, 1))),
        ],
    ),
    "details of the wrong shape": sub_ifd_exif(
        0x8769,
        [
            (0x829A, 2, b"fast\0"),
            (0x8827, 2, b"many\0"),
            (0x9003, 2, b"\x1b[31mnot a date\0"),
            (0x9291, 2, "²\0".encode()),
            (0xA434, 2, b"\x1b]0;pwned\x07\0"),
        ],
    ),
    "long text": exif_block(t010f="M" * 100_000, t010e="\x1b[2J" * 1000),
    "maker note of nothing": sub_ifd_exif(0x8769, [(0x927C, 7, b"\0" * 64)]),
}


def icc_profile(tags: dict[bytes, bytes], header: bytes = b"") -> bytes:
    table = struct.pack(">I", len(tags))
    data = b""
    start = 132 + 12 * len(tags)
    for signature, tag in tags.items():
        table += struct.pack(">4sII", signature, start + len(data), len(tag))
        data += tag
    return (header or bytes(128)).ljust(128, b"\0") + table + data


def desc(text: bytes) -> bytes:
    return b"desc\0\0\0\0" + struct.pack(">I", len(text)) + text


ICC_PROFILES = {
    "long description": icc_profile({b"desc": desc(b"\x01" + b"D" * 180_000)}),
    "escapes": icc_profile({b"desc": desc(b"\x1b]0;pwned\x07\x1b[31mDisplay P3\x9b31m")}),
    "only spaces": icc_profile({b"desc": desc(b"   ")}),
    "mluc without records": icc_profile({b"desc": b"mluc\0\0\0\0" + struct.pack(">II", 0xFFFFFFFF, 0)}),
    "mluc beyond its end": icc_profile({b"desc": b"mluc\0\0\0\0" + struct.pack(">II", 1, 12) + b"enUS"}),
    "bad header": b"\xff" * 200,
    "huge tag count": b"\0" * 128 + b"\xff\xff\xff\xff" + b"\0" * 64,
    "out of range date": icc_profile({b"desc": desc(b"x")}, bytes(24) + struct.pack(">6H", 65535, 99, 99, 99, 99, 99)),
    "too short": b"\0\0",
    "text": b"not a profile at all",
}


def iptc(*items: tuple[int, int, bytes]) -> bytes:
    return b"".join(b"\x1c" + bytes([r, d]) + struct.pack(">H", len(v)) + v for r, d, v in items)


IPTC_BLOCKS = {
    "extended length": b"\x1c\x02\x19\x80\x04\xff\xff\xff\xff",
    "truncated": b"\x1c\x02\x19\x00\xff",
    "bad date": iptc((2, 55, b"99999999"), (2, 60, b"\x1b[31m")),
    "controls": iptc((2, 80, b"\x1b]0;pwned\x07"), (2, 90, b"\x9bCity")),
    "not iptc": b"\xff" * 64,
}


def photoshop_block(number: int, value: bytes, size: int | None = None) -> bytes:
    return (
        b"8BIM" + struct.pack(">H", number) + b"\0\0" + struct.pack(">I", len(value) if size is None else size) + value
    )


PHOTOSHOP_BLOCKS = {
    "length past the end": photoshop_block(0x0404, b"x", 0xFFFFFFF0),
    "short resolution": photoshop_block(0x03ED, b"\xff" * 4),
    "zero resolution": photoshop_block(0x03ED, bytes(16)),
    "not photoshop": b"\xff" * 40,
    "iptc inside": photoshop_block(0x0404, IPTC_BLOCKS["extended length"]),
}


def _segment(marker: int, payload: bytes) -> bytes:
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def picture_bytes(fmt: str, **options) -> bytes:
    buffer = io.BytesIO()
    samples.picture("RGB", (16, 12)).save(buffer, fmt, **options)
    return buffer.getvalue()


def jpeg_with(*segments: bytes) -> bytes:
    data = picture_bytes("JPEG")
    return data[:2] + b"".join(segments) + data[2:]


def jpeg_icc(profile: bytes) -> bytes:
    return _segment(0xE2, b"ICC_PROFILE\0\x01\x01" + profile[:60000])


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def png_with(*chunks: bytes) -> bytes:
    data = picture_bytes("PNG")
    return data[:33] + b"".join(chunks) + data[33:]


def png_xmp(packet: bytes) -> bytes:
    return png_chunk(b"iTXt", b"XML:com.adobe.xmp\0\0\0\0\0" + packet)


def webp_with(**options) -> bytes:
    return picture_bytes("WEBP", **options)


def tiff_with(**tags) -> bytes:
    info = TiffImagePlugin.ImageFileDirectory_v2()
    for tag, kind, value in tags.values():
        info[tag] = value
        info.tagtype[tag] = kind
    return picture_bytes("TIFF", tiffinfo=info)


def gif_with(*blocks: bytes) -> bytes:
    data = picture_bytes("GIF")
    at = 13 + ((3 << ((data[10] & 7) + 1)) if data[10] & 0x80 else 0)
    return data[:at] + b"".join(blocks) + data[at:]


def gif_xmp(packet: bytes) -> bytes:
    return b"\x21\xff\x0bXMP DataXMP" + packet + b"\x01" + bytes(range(255, -1, -1)) + b"\0"


def gif_comment(text: bytes) -> bytes:
    return b"\x21\xfe" + bytes([min(len(text), 255)]) + text[:255] + b"\0"


def hostile_files() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for name, packet in XMP_PACKETS.items():
        if len(packet) < 60000:
            files[f"jpeg xmp {name}"] = jpeg_with(_segment(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + packet))
            files[f"gif xmp {name}"] = gif_with(gif_xmp(packet))
        files[f"png xmp {name}"] = png_with(png_xmp(packet))
        files[f"webp xmp {name}"] = webp_with(xmp=packet)
        files[f"tiff xmp {name}"] = tiff_with(xmp=(700, 1, packet))
    for name, block in EXIF_BLOCKS.items():
        if len(block) < 60000:
            files[f"jpeg exif {name}"] = jpeg_with(_segment(0xE1, b"Exif\0\0" + block))
        files[f"png exif {name}"] = png_with(png_chunk(b"eXIf", block))
        files[f"webp exif {name}"] = webp_with(exif=block)
    for name, profile in ICC_PROFILES.items():
        files[f"jpeg icc {name}"] = jpeg_with(jpeg_icc(profile))
        compressed = zlib.compress(profile)
        files[f"png icc {name}"] = png_with(png_chunk(b"iCCP", b"profile\0\0" + compressed))
        files[f"webp icc {name}"] = webp_with(icc_profile=profile)
        files[f"tiff icc {name}"] = tiff_with(icc=(34675, 1, profile))
    for name, block in IPTC_BLOCKS.items():
        files[f"tiff iptc {name}"] = tiff_with(iptc=(33723, 1, block))
        wrapped = photoshop_block(0x0404, block)
        files[f"jpeg iptc {name}"] = jpeg_with(_segment(0xED, b"Photoshop 3.0\0" + wrapped))
    for name, block in PHOTOSHOP_BLOCKS.items():
        files[f"jpeg photoshop {name}"] = jpeg_with(_segment(0xED, b"Photoshop 3.0\0" + block))
        files[f"tiff photoshop {name}"] = tiff_with(photoshop=(34377, 1, block))
    extended = b"http://ns.adobe.com/xmp/extension/\0"
    files["jpeg extended xmp short"] = jpeg_with(_segment(0xE1, extended + b"short"))
    files["jpeg extended xmp bad offsets"] = jpeg_with(
        _segment(0xE1, extended + b"0" * 32 + b"\xff" * 8 + b"<x:xmpmeta"),
    )
    files["jpeg extended xmp holding gps"] = jpeg_with(
        _segment(
            0xE1,
            extended + b"0" * 32 + struct.pack(">II", 300, 0) + xmp_packet(b'exif:GPSLatitude="48,51.5N"'),
        ),
    )
    files["jpeg comment binary"] = jpeg_with(_segment(0xFE, bytes(range(256))))
    files["jpeg comment escapes"] = jpeg_with(_segment(0xFE, b"\x1b]0;pwned\x07\x1b[31m"))
    files["gif comment escapes"] = gif_with(gif_comment(b"\x1b]0;pwned\x07"))
    files["gif xmp truncated"] = gif_with(b"\x21\xff\x0bXMP DataXMP<x")
    for name, payload in {
        "bad ztxt": (b"zTXt", b"Comment\0\0not zlib"),
        "bad itxt": (b"iTXt", b"Comment\0\1\0\0\0not zlib"),
        "itxt without fields": (b"iTXt", b"Comment"),
        "no separator": (b"tEXt", b"Comment"),
        "escapes": (b"tEXt", b"Title\0\x1b]0;pwned\x07"),
        "name of escapes": (b"tEXt", b"\x1b[31m\0value"),
        "bad creation time": (b"tEXt", b"Creation Time\0\x9b31m"),
        "raw profile": (b"tEXt", b"Raw profile type exif\0" + b"\xff" * 64),
        "text location": (b"tEXt", b"GPSLatitude\0\xff"),
    }.items():
        files[f"png text {name}"] = png_with(png_chunk(*payload))
    return files


HOSTILE = hostile_files()
SUFFIX = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "tiff": ".tif", "gif": ".gif"}


def write(tmp_path: pathlib.Path, name: str, data: bytes) -> pathlib.Path:
    path = tmp_path / f"input{SUFFIX[name.split()[0]]}"
    path.write_bytes(data)
    return path


def walk_text(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from walk_text(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from walk_text(item)


CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069\ud800-\udfff]")


def assert_keeps_promises(info: metadata.MetadataInfo) -> None:
    from sashimi.core._metaread import MAX_VALUE_CHARS

    dumped = info.model_dump()
    longest = MAX_VALUE_CHARS + len("… (1,000,000,000 characters)")
    for text in walk_text(dumped):
        assert len(text) <= longest, text[:80]
        assert not CONTROLS.search(text), text[:80]
    assert len(info.model_dump_json().encode()) <= READ_LIMIT
    json.loads(info.model_dump_json())
    for found in info.sections:
        if found.counted:
            assert found.carrier in info.carriers, (found.carrier, found.name, info.carriers)
    if info.has_location:
        assert info.carriers


ROUTES = {
    "convert png": lambda p, o: image.convert(p, o, IMAGE_CONVERT.from_params(target="png")),
    "convert jpeg": lambda p, o: image.convert(p, o, IMAGE_CONVERT.from_params(target="jpeg")),
    "convert webp remove_location": lambda p, o: image.convert(
        p, o, IMAGE_CONVERT.from_params(target="webp", metadata="remove_location")
    ),
    "edit": lambda p, o: edit_core.edit(p, o, IMAGE_EDIT.from_params(target="png", rotate=90)),
    "strip": lambda p, o: metadata.strip(p, o, "remove_all"),
    "strip location": lambda p, o: metadata.strip(p, o, "remove_location"),
}


def check_every_route(path: pathlib.Path, folder: pathlib.Path) -> None:
    folder.mkdir(exist_ok=True)
    try:
        assert_keeps_promises(metadata.read(path))
    except SashimiError:
        pass
    for name, route in ROUTES.items():
        output = folder / name.replace(" ", "-")
        try:
            result = route(path, output)
        except SashimiError:
            assert not output.exists(), f"{name} refused, and left its output behind"
        else:
            assert output.exists(), f"{name} succeeded without an output"
            assert result.fidelity is not None
        output.unlink(missing_ok=True)
    assert not list(folder.iterdir()), "a route left a file behind"


@pytest.fixture(autouse=True)
def bomb_warnings_are_errors():
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        yield


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_metadata_never_breaks_a_route(tmp_path, name):
    check_every_route(write(tmp_path, name, HOSTILE[name]), tmp_path / "out")


@pytest.mark.parametrize("name", sorted(set(HOSTILE) - {"gif xmp truncated"}))
def test_a_picture_with_hostile_metadata_is_still_read(tmp_path, name):
    """The picture is sound, so the read answers; a file's metadata alone never turns it into "not an image"."""
    info = metadata.read(write(tmp_path, name, HOSTILE[name]))
    assert info.kind == "image"
    assert_keeps_promises(info)


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_nothing_is_counted_after_remove_all(tmp_path, name):
    path = write(tmp_path, name, HOSTILE[name])
    output = tmp_path / f"out{path.suffix}"
    try:
        metadata.strip(path, output, "remove_all")
    except SashimiError:
        return
    after = metadata.read(output)
    assert after.carriers == [] and not after.has_location


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_location_is_gone_after_remove_location(tmp_path, name):
    path = write(tmp_path, name, HOSTILE[name])
    output = tmp_path / f"out{path.suffix}"
    try:
        metadata.strip(path, output, "remove_location")
    except SashimiError:
        return
    assert not metadata.read(output).has_location


BASES = {
    "jpeg": jpeg_with(
        _segment(0xE1, b"Exif\0\0" + samples.exif(6).tobytes()),
        _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + samples.XMP),
        _segment(0xED, b"Photoshop 3.0\0" + samples.photoshop()),
        jpeg_icc(samples.icc()),
        _segment(0xFE, samples.MARKER),
    ),
    "png": png_with(
        png_chunk(b"eXIf", samples.exif(6).tobytes()),
        png_xmp(samples.XMP),
        png_chunk(b"tEXt", b"Title\0" + samples.MARKER),
        png_chunk(b"zTXt", b"Comment\0\0" + zlib.compress(samples.MARKER)),
        png_chunk(b"iCCP", b"profile\0\0" + zlib.compress(samples.icc())),
    ),
    "webp": webp_with(exif=samples.exif(6).tobytes(), xmp=samples.XMP, icc_profile=samples.icc()),
    "tiff": tiff_with(
        xmp=(700, 1, samples.XMP),
        iptc=(33723, 1, samples.iptc()),
        photoshop=(34377, 1, samples.photoshop()),
        icc=(34675, 1, samples.icc()),
    ),
    "gif": gif_with(gif_comment(samples.MARKER), gif_xmp(samples.XMP)),
}


def damaged(data: bytes, rng: random.Random) -> bytes:
    """`data` with bytes changed, cut or inserted, in the part of the file before the picture's own data."""
    out = bytearray(data)
    span = max(16, len(out) - 256)
    for _ in range(rng.randint(1, 8)):
        at = rng.randrange(2, span)
        kind = rng.randrange(4)
        if kind == 0:
            out[at] = rng.randrange(256)
        elif kind == 1:
            out[at : at + 4] = rng.choice((b"\xff\xff\xff\xff", b"\0\0\0\0", b"\x7f\xff\xff\xff"))
        elif kind == 2:
            del out[at : at + rng.randint(1, 32)]
        else:
            out[at:at] = rng.randbytes(rng.randint(1, 16))
    return bytes(out)


@pytest.mark.parametrize("fmt", sorted(BASES))
def test_damage_anywhere_in_the_metadata_never_breaks_a_route(tmp_path, fmt):
    rng = random.Random(f"sashimi {fmt}")
    path = tmp_path / f"input{SUFFIX[fmt]}"
    for _ in range(40):
        path.write_bytes(damaged(BASES[fmt], rng))
        check_every_route(path, tmp_path / "out")


def test_a_bomb_thumbnail_is_read_as_its_stated_size(tmp_path):
    path = write(tmp_path, "jpeg", jpeg_with(_segment(0xE1, b"Exif\0\0" + bomb_thumbnail())))
    assert metadata.read(path).thumbnail_size == (30000, 30000)


def test_a_superscript_orientation_counts_as_none(tmp_path):
    path = write(tmp_path, "jpeg", HOSTILE["jpeg xmp superscript orientation"])
    info = metadata.read(path)
    assert info.carriers == ["XMP"]
    assert all(detail.name != "orientation" for detail in info.not_counted)


def test_a_name_inside_a_gain_map_item_counts(tmp_path):
    info = metadata.read(write(tmp_path, "jpeg", HOSTILE["jpeg xmp gain map with a name"]))
    assert info.carriers == ["XMP"] and info.has_metadata and info.has_location
    assert all(detail.name != "display XMP" for detail in info.not_counted)
    assert any(found.carrier == "XMP" and found.counted for found in info.sections)


@pytest.mark.parametrize(
    "attributes, body",
    [
        (b'hdrgm:Version="1.0" dc:title="x"', GAIN_MAP_ITEMS),
        (b'hdrgm:Version="Jane Doe"', b""),
        (b'hdrgm:Version="1.0"', container_item(b'Item:Semantic="JaneDoe" Item:Mime="image/jpeg"')),
        (b'hdrgm:Version="1.0"', container_item(b'Item:Semantic="GainMap" Item:Mime="image/JaneDoe"')),
        (b'hdrgm:Version="1.0"', container_item(b'Item:Semantic="GainMap" Item:Mime="image/jpeg" Item:Length="many"')),
        (b'hdrgm:Version="1.0"', container_item(b'Item:Semantic="GainMap" Item:Mime="image/jpeg" Item:Label="x"')),
        (b'hdrgm:Version="1.0"', container_item(b'Item:Semantic="GainMap" Item:Mime="image/jpeg" Item:URI="x"')),
        (b'hdrgm:Version="1.0" GCamera:Note="x"', b""),
        (b'hdrgm:Version="1.0"', b"<!-- Jane Doe -->"),
        (b'hdrgm:Version="1.0"', b"Jane Doe"),
    ],
)
def test_display_xmp_holding_anything_else_counts(tmp_path, attributes, body):
    data = jpeg_with(_segment(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + xmp_packet(attributes, body)))
    info = metadata.read(write(tmp_path, "jpeg", data))
    assert info.carriers == ["XMP"]


def test_a_subject_named_in_display_xmp_counts(tmp_path):
    packet = xmp_packet(b'hdrgm:Version="1.0"').replace(b'rdf:about=""', b'rdf:about="Jane Doe"')
    info = metadata.read(write(tmp_path, "jpeg", jpeg_with(_segment(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + packet))))
    assert info.carriers == ["XMP"]


def test_only_display_xmp_is_not_counted(tmp_path):
    info = metadata.read(write(tmp_path, "jpeg", HOSTILE["jpeg xmp only display"]))
    assert info.carriers == [] and not info.has_metadata
    assert {detail.name: detail.value for detail in info.not_counted}["display XMP"] == "gain map"


def test_a_second_xmp_packet_counts_and_its_location_is_seen(tmp_path):
    display = xmp_packet(b'hdrgm:Version="1.0"')
    second = xmp_packet(b'exif:GPSLatitude="48,51.5N" dc:title="x"')
    marker = b"http://ns.adobe.com/xap/1.0/\0"
    data = jpeg_with(_segment(0xE1, marker + display), _segment(0xE1, marker + second))
    info = metadata.read(write(tmp_path, "jpeg", data))
    assert info.carriers == ["XMP"] and info.has_location


def test_icc_text_is_clean_and_capped(tmp_path):
    from sashimi.core._metaread import MAX_VALUE_CHARS

    long = metadata.read(write(tmp_path, "png", HOSTILE["png icc long description"]))
    assert long.color_profile is not None and len(long.color_profile) <= MAX_VALUE_CHARS + 40
    assert "\x01" not in long.color_profile
    assert all(len(detail.value) <= MAX_VALUE_CHARS + 40 for detail in long.not_counted)
    escaped = metadata.read(write(tmp_path, "png", HOSTILE["png icc escapes"]))
    assert escaped.color_profile is not None and "Display P3" in escaped.color_profile
    assert not CONTROLS.search(escaped.color_profile)


def test_strip_fidelity_carries_no_blob(tmp_path):
    path = write(tmp_path, "png", HOSTILE["png icc long description"])
    told = metadata.strip(path, tmp_path / "out.png", "remove_all").fidelity
    assert len(told.text) < 2000
    assert "\x01" not in told.text


def test_a_tiff_iptc_tag_shows_no_photoshop_section(tmp_path):
    info = metadata.read(write(tmp_path, "tiff", tiff_with(iptc=(33723, 1, samples.iptc()))))
    assert info.carriers == ["IPTC"]
    assert [found.carrier for found in info.sections if found.carrier in ("IPTC", "Photoshop")] == ["IPTC"]


def test_a_tiff_with_both_keeps_its_photoshop_section(tmp_path):
    info = metadata.read(
        write(
            tmp_path,
            "tiff",
            tiff_with(iptc=(33723, 1, samples.iptc()), photoshop=(34377, 1, photoshop_block(0x03ED, bytes(16)))),
        )
    )
    assert "Photoshop" in [found.carrier for found in info.sections]


def test_extended_xmp_has_a_section_with_its_size_and_properties(tmp_path):
    info = metadata.read(write(tmp_path, "jpeg", HOSTILE["jpeg extended xmp holding gps"]))
    assert "extended XMP" in info.carriers and info.has_location
    found = next(section for section in info.sections if section.carrier == "extended XMP")
    fields = {field.name: field.value for field in found.fields}
    assert fields["Pieces"] == "1" and "bytes" in fields["Size"] and "exif:GPSLatitude" in fields


def test_a_broken_extended_xmp_still_has_a_section(tmp_path):
    info = metadata.read(write(tmp_path, "jpeg", HOSTILE["jpeg extended xmp short"]))
    assert "extended XMP" in info.carriers
    assert any(section.carrier == "extended XMP" and section.fields for section in info.sections)


def many_chunks(count: int, text: bytes) -> bytes:
    return png_with(*(png_chunk(b"tEXt", b"K%d\0" % number + text) for number in range(count)))


def worst_read_png() -> bytes:
    """As much text as every bound of a read lets through, in values that JSON doesn't shrink."""
    long = "é" * 400
    chunks = [png_chunk(b"iTXt", f"K{n}".encode() + b"\0\0\0\0\0" + long.encode()) for n in range(300)]
    wide = "".join(f"<dc:a{n:04}>{long}</dc:a{n:04}>" for n in range(300)).encode()
    packets = [png_xmp(xmp_packet(body=wide)) for _ in range(150)]
    return png_with(*chunks, *packets)


def test_the_whole_read_is_bounded_and_says_what_it_left_out(tmp_path):
    info = metadata.read(write(tmp_path, "png", worst_read_png()))
    assert len(info.model_dump_json().encode()) <= READ_LIMIT
    assert_keeps_promises(info)
    left_out = info.sections_omitted + sum(section.omitted for section in info.sections)
    assert left_out > 0
    assert any(section.omitted for section in info.sections if not section.fields)


def test_many_chunks_stay_bounded(tmp_path):
    info = metadata.read(write(tmp_path, "png", many_chunks(2000, b"v" * 300)))
    assert len(info.model_dump_json()) <= READ_LIMIT
    png_text = next(section for section in info.sections if section.carrier == "PNG text")
    assert len(png_text.fields) + png_text.omitted == 2000
    assert len(info.tags) + info.tags_omitted == 2000


def test_a_read_of_an_ordinary_file_leaves_nothing_out(tmp_path):
    for fmt, data in samples.IMAGES.items():
        path = tmp_path / f"sample.{fmt}"
        path.write_bytes(data() if callable(data) else data)
        info = metadata.read(path)
        assert info.sections_omitted == 0 and info.not_counted_omitted == 0 and info.tags_omitted == 0
        assert all(section.omitted == 0 for section in info.sections)


def test_a_compressed_text_bomb_is_not_inflated_whole():
    from sashimi.core._imagemeta import MAX_PNG_TEXT, png_text

    bomb = zlib.compress(bytes(MAX_PNG_TEXT * 4), 9)
    assert png_text(b"zTXt", b"Comment\0\0" + bomb) == (b"Comment", None)
    fine = zlib.compress(b"x" * 1000)
    assert png_text(b"zTXt", b"Comment\0\0" + fine) == (b"Comment", b"x" * 1000)


def test_many_compressed_packets_cost_one_bound(tmp_path):
    from sashimi.core._imagemeta import _png_carriers

    packet = xmp_packet(body=b" " * (1 << 22))
    squeezed = zlib.compress(packet, 9)
    chunk = png_chunk(b"iTXt", b"XML:com.adobe.xmp\0\1\0\0\0" + squeezed)
    carriers = _png_carriers(png_with(*[chunk] * 50))
    assert sum(len(extra) for extra in carriers.more_xmp) <= 1 << 23
    assert carriers.xmp_omitted > 0


def test_a_progress_callback_may_stop_the_work(tmp_path):
    class Stop(Exception):
        pass

    def stop(_fraction: float, _stage: str) -> None:
        raise Stop

    path = write(tmp_path, "png", picture_bytes("PNG"))
    with pytest.raises(Stop):
        image.convert(path, tmp_path / "out.webp", IMAGE_CONVERT.from_params(target="webp"), on_progress=stop)


def test_a_check_that_fails_after_writing_keeps_the_output_and_says_so(tmp_path, monkeypatch):
    from sashimi.core import fidelity as fidelity_module

    def broken(*_args, **_kwargs):
        raise ValueError("a parser fell over")

    monkeypatch.setattr(fidelity_module, "image_inventory", broken)
    monkeypatch.setattr(image_core, "image_inventory", broken)
    path = write(tmp_path, "png", picture_bytes("PNG"))
    converted = image.convert(path, tmp_path / "a.webp", IMAGE_CONVERT.from_params(target="webp"))
    assert (tmp_path / "a.webp").exists()
    assert fidelity_module.UNCHECKED in converted.fidelity.text and not converted.fidelity.checked
    stripped = metadata.strip(path, tmp_path / "b.png", "remove_all")
    assert (tmp_path / "b.png").exists()
    assert fidelity_module.UNCHECKED in stripped.fidelity.text and stripped.fidelity.level != "lossless"


def jpeg_xmp(packet: bytes) -> bytes:
    return jpeg_with(_segment(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + packet))


@pytest.mark.parametrize(
    "packet",
    [
        xmp_packet(b'hdrgm:Version="1.0"').replace(
            b"xmlns:dc=", b'xmlns:junk="urn:Jane Doe, 12 Rue X, Paris" xmlns:dc='
        ),
        xmp_packet(b'hdrgm:Version="1.0"').replace(
            b"<rdf:Description ", b'<rdf:Description xml:lang="Jane Doe, 12 Rue X" '
        ),
        xmp_packet(b'hdrgm:Version="1.0"').replace(b"<x:xmpmeta ", b'<x:xmpmeta x:xmptk="Jane Doe, 12 Rue X" '),
        xmp_packet(b'GCamera:MotionPhoto="1"', b'<dc:x rdf:parseType="Jane Doe"><dc:y>1</dc:y></dc:x>'),
        b'<?xpacket begin="" id="Jane Doe"?>' + xmp_packet(b'hdrgm:Version="1.0"') + b'<?xpacket end="w"?>',
        xmp_packet(b'hdrgm:Version="1.0"').replace(b"xmlns:dc=", b"xmlns:JaneDoe="),
        xmp_packet(b'GCamera:MotionPhotoPhone="4915551234567"'),
        xmp_packet(b'GCamera:MotionPhotoVersion="4915551234567"'),
        xmp_packet(b'GCamera:MicroVideoPresentationTimestampUs="4915551234567"'),
        xmp_packet(b'hdrgm:Version="1"', container_item(b'Item:Semantic="Primary" Item:Length="4915551234567"')),
    ],
    ids=["namespace", "language", "toolkit", "parse type", "xpacket", "prefix", "name", "version", "time", "length"],
)
def test_free_text_anywhere_in_a_display_packet_counts(tmp_path, packet):
    info = metadata.read(write(tmp_path, "jpeg", jpeg_xmp(packet)))
    assert "XMP" in info.carriers and info.has_metadata


@pytest.mark.parametrize(
    "packet",
    [
        xmp_packet(b'hdrgm:Version="1.0"'),
        b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        + xmp_packet(b'hdrgm:Version="1.0"').replace(b"<x:xmpmeta ", b'<x:xmpmeta x:xmptk="Adobe XMP Core 5.1.2" ')
        + b'<?xpacket end="w"?>',
        xmp_packet(
            b'GCamera:MotionPhoto="1" GCamera:MotionPhotoVersion="1" GCamera:MotionPhotoPresentationTimestampUs="-1"'
        ),
        xmp_packet(
            b'hdrgm:Version="1.0"', container_item(b'Item:Semantic="Depth" Item:Mime="image/jpeg" Item:Length="5"')
        ),
    ],
    ids=["plain", "xpacket and toolkit", "motion", "depth item"],
)
def test_what_only_shows_a_photo_still_reads_as_nothing_counted(tmp_path, packet):
    info = metadata.read(write(tmp_path, "jpeg", jpeg_xmp(packet)))
    assert "XMP" not in info.carriers


@pytest.mark.parametrize(
    "toolkit",
    [
        "XMP Core 6.0.0",
        "XMP Core 4.4.0-Exiv2",
        "Adobe XMP Core 5.1.0-jc003",
        "Adobe XMP Core Test.SNAPSHOT",
        "Adobe XMP Core 5.6-c148 79.164036, 2019/08/13-01:06:57        ",
        "Image::ExifTool 12.76",
    ],
)
def test_real_toolkit_strings_say_nothing(tmp_path, toolkit):
    packet = xmp_packet(b'hdrgm:Version="1.0"').replace(b"<x:xmpmeta ", f'<x:xmpmeta x:xmptk="{toolkit}" '.encode())
    assert "XMP" not in metadata.read(write(tmp_path, "jpeg", jpeg_xmp(packet))).carriers


def directory_packet(*items: bytes, attributes: bytes = b'hdrgm:Version="1.0" GCamera:MotionPhoto="1"') -> bytes:
    listed = b"".join(b'<rdf:li rdf:parseType="Resource"><Container:Item ' + item + b"/></rdf:li>" for item in items)
    return xmp_packet(attributes, b"<Container:Directory><rdf:Seq>" + listed + b"</rdf:Seq></Container:Directory>")


def mpf_segment(sizes: list[int], offsets: list[int], kinds: list[int]) -> bytes:
    entries = b"".join(
        struct.pack("<IIIHH", kind, size, offset, 0, 0)
        for kind, size, offset in zip(kinds, sizes, offsets, strict=True)
    )
    ifd = struct.pack("<H", 3)
    ifd += struct.pack("<HHI4s", 0xB000, 7, 4, b"0100")
    ifd += struct.pack("<HHII", 0xB001, 4, 1, len(sizes))
    ifd += struct.pack("<HHII", 0xB002, 7, len(entries), 8 + 2 + 36 + 4)
    ifd += struct.pack("<I", 0)
    return b"MPF\x00II*\x00" + struct.pack("<I", 8) + ifd + entries


def plain_jpeg(size: tuple[int, int] = (16, 12)) -> bytes:
    out = io.BytesIO()
    Image.frombytes("L", size, random.Random(size[0]).randbytes(size[0] * size[1])).save(out, "JPEG")
    return out.getvalue()


def layered_photo(
    *, depth_listed_in_mpf: bool = False, label: bytes = b'Item:Label="clip"', video: bytes | None = None
) -> tuple[bytes, bytes, bytes]:
    """A photo with a gain map (in MPF), Depth and Confidence items, and a video after them: what's in the file, in order."""
    gain, depth, confidence = plain_jpeg((48, 48)), plain_jpeg((32, 32)), plain_jpeg((16, 16))
    video = video or b"\0\0\0\x18ftypmp42" + b"\0" * 100
    items = (
        b'Item:Semantic="Primary" Item:Mime="image/jpeg"',
        b'Item:Semantic="GainMap" Item:Mime="image/jpeg" Item:Length="%d"' % len(gain),
        b'Item:Semantic="Depth" Item:Mime="image/jpeg" Item:Length="%d" %s Item:URI="depth/Jane Doe.jpg"'
        % (len(depth), label),
        b'Item:Semantic="Confidence" Item:Mime="image/jpeg" Item:Length="%d"' % len(confidence),
        b'Item:Semantic="MotionPhoto" Item:Mime="video/mp4" Item:Length="%d" Item:Padding="0"' % len(video),
    )
    main = jpeg_xmp(directory_packet(*items))
    listed = [gain, depth] if depth_listed_in_mpf else [gain]
    sizes = [0, *[len(part) for part in listed]]
    length = len(mpf_segment(sizes, [0] * len(sizes), [0x030000] + [0] * len(listed))) + 2
    primary = len(main) + 2 + length
    offsets, at = [0], primary - 10
    for part in listed:
        offsets.append(at)
        at += len(part)
    segment = mpf_segment(sizes, offsets, [0x030000] + [0] * len(listed))
    head = main[:2] + b"\xff\xe2" + struct.pack(">H", length) + segment + main[2:]
    rest = [part for part in (gain, depth, confidence) if part not in listed]
    return head + b"".join(listed) + b"".join(rest) + video, video, gain


def items_of(data: bytes) -> list[dict[str, str]]:
    from sashimi.core._imagemeta import _jpeg_carriers
    from sashimi.core._metaread import directory_items

    return directory_items(_jpeg_carriers(data).xmp or b"")


def test_strip_keeps_the_directory_of_a_photo_with_depth_when_everything_stays():
    from sashimi.core._imagemeta import KEEP_VIDEO, motion_video, strip_image

    data, video, gain = layered_photo()
    stripped = strip_image(data, "JPEG", "remove_all", True, KEEP_VIDEO)
    found = motion_video(stripped)
    assert found is not None and stripped[found.start : found.end] == video
    assert [item["Semantic"] for item in items_of(stripped)] == [
        "Primary",
        "GainMap",
        "Depth",
        "Confidence",
        "MotionPhoto",
    ]
    assert b"Jane Doe" not in stripped[: stripped.find(b"\xff\xd9")] and b"clip" not in stripped
    assert all(set(item) <= {"Semantic", "Mime", "Length", "Padding"} for item in items_of(stripped))
    assert gain in stripped


def test_strip_lists_only_what_stays_when_the_video_is_replaced():
    from sashimi.core._imagemeta import motion_video, strip_image

    data, _, gain = layered_photo()
    new = b"\0\0\0\x18ftypmp42" + b"\1" * 60
    stripped = strip_image(data, "JPEG", "remove_all", True, new)
    found = motion_video(stripped)
    assert found is not None and stripped[found.start : found.end] == new
    assert [item["Semantic"] for item in items_of(stripped)] == [
        "Primary",
        "GainMap",
        "Depth",
        "Confidence",
        "MotionPhoto",
    ]
    assert gain in stripped


def test_strip_keeps_a_kept_picture_of_another_kind_in_the_list():
    from sashimi.core._imagemeta import strip_image

    data, _, _ = layered_photo(depth_listed_in_mpf=True)
    stripped = strip_image(data, "JPEG", "remove_all", True, b"\0\0\0\x18ftypmp42" + b"\1" * 60)
    assert [item["Semantic"] for item in items_of(stripped)] == [
        "Primary",
        "GainMap",
        "Depth",
        "Confidence",
        "MotionPhoto",
    ]


def test_removing_location_lists_only_what_stays_when_the_video_is_replaced():
    from sashimi.core._imagemeta import motion_video, strip_image

    data, _, gain = layered_photo()
    new = b"\0\0\0\x18ftypmp42" + b"\1" * 60
    stripped = strip_image(data, "JPEG", "remove_location", True, new)
    found = motion_video(stripped)
    assert found is not None and stripped[found.start : found.end] == new and gain in stripped
    assert [item["Semantic"] for item in items_of(stripped)] == [
        "Primary",
        "GainMap",
        "Depth",
        "Confidence",
        "MotionPhoto",
    ]


def test_removing_location_keeps_the_list_when_everything_stays():
    from sashimi.core._imagemeta import KEEP_VIDEO, strip_image

    data, _, _ = layered_photo()
    stripped = strip_image(data, "JPEG", "remove_location", True, KEEP_VIDEO)
    assert [item["Semantic"] for item in items_of(stripped)] == [
        "Primary",
        "GainMap",
        "Depth",
        "Confidence",
        "MotionPhoto",
    ]


def test_removing_location_lists_only_what_stays_when_the_video_goes():
    from sashimi.core._imagemeta import strip_image

    data, _, gain = layered_photo()
    stripped = strip_image(data, "JPEG", "remove_location", True, None)
    assert gain in stripped
    assert [item["Semantic"] for item in items_of(stripped)] == ["Primary", "GainMap", "Depth", "Confidence"]


def test_a_stripped_photo_with_depth_reads_as_nothing_counted(tmp_path):
    from sashimi.core._imagemeta import KEEP_VIDEO, strip_image

    data, _, _ = layered_photo()
    path = tmp_path / "out.jpg"
    path.write_bytes(strip_image(data, "JPEG", "remove_all", True, KEEP_VIDEO))
    assert "XMP" not in metadata.read(path).carriers


def test_an_item_of_unknown_kind_keeps_its_place_but_not_its_words():
    from sashimi.core._imagemeta import display_xmp

    shown = display_xmp(
        directory_packet(
            b'Item:Semantic="Primary" Item:Mime="image/jpeg"',
            b'Item:Semantic="Jane Doe" Item:Mime="x/Jane Doe" Item:Length="9"',
            b'Item:Semantic="MotionPhoto" Item:Mime="video/mp4" Item:Length="5"',
        ),
        gain_map=False,
        video=True,
    )
    assert shown is not None and b"Jane" not in shown and b'Item:Length="9"' in shown


def test_reading_and_keeping_an_item_with_a_label_stay_different(tmp_path):
    from sashimi.core._imagemeta import display_xmp

    labelled = directory_packet(
        b'Item:Semantic="Primary" Item:Mime="image/jpeg"',
        b'Item:Semantic="MotionPhoto" Item:Mime="video/mp4" Item:Length="5" Item:Label="clip"',
    )
    assert "XMP" in metadata.read(write(tmp_path, "jpeg", jpeg_xmp(labelled))).carriers
    shown = display_xmp(labelled, gain_map=False, video=True)
    assert shown is not None and b"Label" not in shown and b"MotionPhoto" in shown


GPS_ELEMENT = b"<exif:GPSLatitude>"
QUADRATIC_SHAPES = {
    "location openers": GPS_ELEMENT,
    "distinct location openers": None,
    "attachment openers": b"<GCamera:MotionPhoto>",
    "directory openers": b"<Container:Directory>",
    "open items": b'<Container:Item Item:Semantic="MotionPhoto" ',
    "open list items": b"<rdf:li ",
    "open directory entries": b"<rdf:li><Container:Item ",
    "open items of primary": b'<Container:Item Item:Semantic="Primary" ',
    "attributes with no value": b" exif:GPSLatitude   ",
    "unclosed attribute": b" a:b='",
    "unclosed starts": b"<a:b ",
    "long word": b"a",
    "spaces": b" ",
    "colons": b":",
    "angle brackets": b"<",
    "xpacket": b"<?xpacket ",
    "closed then open": b"</Container:Directory><Container:Directory>",
}


def shaped(shape: bytes | None, size: int) -> bytes:
    if shape is None:
        names = (b"<exif:GPS%x>" % number for number in range(size // 8))
        return b"".join(names)[:size]
    return shape * (size // len(shape))


def cost(call, *args) -> float:
    start = time.process_time()
    call(*args)
    return time.process_time() - start


@pytest.mark.parametrize("shape", sorted(QUADRATIC_SHAPES))
def test_every_xmp_scan_takes_time_in_proportion_to_the_packet(shape):
    from sashimi.core._imagemeta import xmp_has_location, xmp_without_attachments, xmp_without_motion
    from sashimi.core._metaread import XmpRead

    small, large = shaped(QUADRATIC_SHAPES[shape], 200_000), shaped(QUADRATIC_SHAPES[shape], 1_600_000)

    def everything(packet: bytes) -> None:
        xmp_has_location(packet)
        xmp_without_attachments(packet)
        xmp_without_motion(packet, 100)
        XmpRead(packet).display_parts()
        metadata_core._xmp_value(packet, "GPSLatitude")

    took = cost(everything, large)
    assert took < 2 or took < 24 * max(cost(everything, small), 0.01)


WITH_XMP = {
    "png": lambda packet: png_with(png_xmp(packet)),
    "webp": lambda packet: webp_with(xmp=packet),
    "tiff": lambda packet: tiff_with(xmp=(700, 1, packet)),
    "gif": lambda packet: gif_with(gif_xmp(packet)),
}


@pytest.mark.parametrize("fmt", sorted(WITH_XMP))
def test_a_packet_past_the_scan_limit_counts_as_a_location_and_removal_takes_it(tmp_path, fmt):
    from sashimi.core._imagemeta import MAX_XMP_SCAN

    packet = xmp_packet(body=b" " * (MAX_XMP_SCAN + 10))
    path = write(tmp_path, fmt, WITH_XMP[fmt](packet))
    info = metadata.read(path)
    assert info.has_location and "XMP" in info.carriers
    start = time.process_time()
    out = tmp_path / f"out{path.suffix}"
    done = metadata.strip(path, out, "remove_location")
    assert time.process_time() - start < 30
    after = metadata.read(out)
    assert not after.has_location and "XMP" not in after.carriers
    assert "XMP" in done.fidelity.lost


def test_a_packet_past_the_scan_limit_is_not_carried_when_location_goes(tmp_path):
    from sashimi.core._imagemeta import MAX_XMP_SCAN

    packet = xmp_packet(b'exif:GPSLatitude="48,51.5N"', b" " * MAX_XMP_SCAN)
    path = write(tmp_path, "png", WITH_XMP["png"](packet))
    out = tmp_path / "a.png"
    image.convert(path, out, IMAGE_CONVERT.from_params(target="png", metadata="remove_location"))
    assert b"GPSLatitude" not in out.read_bytes()


def large_text_png(
    tmp_path: pathlib.Path, inflated: int, *, key: bytes = b"Comment", after_pixels: bool = False
) -> pathlib.Path:
    squeezed = zlib.compress(b"x" * inflated, 9)
    chunk = png_chunk(b"zTXt", key + b"\0\0" + squeezed)
    data = picture_bytes("PNG")
    at = data.index(b"IEND") - 4 if after_pixels else data.index(b"IDAT") - 4
    path = tmp_path / "large.png"
    path.write_bytes(data[:at] + chunk + data[at:])
    return path


@pytest.mark.parametrize("after_pixels", [False, True], ids=["before the pixels", "after the pixels"])
@pytest.mark.parametrize("inflated", [(1 << 20) - 1, 1 << 20, (1 << 20) + 1, 2 << 20, 7 << 20], ids=str)
def test_a_png_with_a_large_text_chunk_is_an_ordinary_file(tmp_path, inflated, after_pixels):
    path = large_text_png(tmp_path, inflated, after_pixels=after_pixels)
    converted = image.convert(path, tmp_path / "a.webp", IMAGE_CONVERT.from_params(target="webp"))
    assert (tmp_path / "a.webp").exists() and converted.fidelity.checked
    info = metadata.read(path)
    assert "PNG text" in info.carriers
    edited = image.edit(path, tmp_path / "b.png", IMAGE_EDIT.from_params(target="png", rotate=90))
    assert edited.fidelity is not None
    assert metadata.strip(path, tmp_path / "c.png", "remove_all").fidelity is not None
    assert "PNG text" not in metadata.read(tmp_path / "c.png").carriers
    kept = image.convert(path, tmp_path / "d.png", IMAGE_CONVERT.from_params(target="png"))
    assert "PNG text" in metadata.read(tmp_path / "d.png").carriers and "PNG text" not in kept.fidelity.lost


def test_pillows_settings_stay_as_they_were(tmp_path):
    from PIL import ImageFile, PngImagePlugin

    before = (PngImagePlugin.MAX_TEXT_CHUNK, PngImagePlugin.MAX_TEXT_MEMORY, ImageFile.LOAD_TRUNCATED_IMAGES)
    metadata.read(large_text_png(tmp_path, 2 << 20))
    assert (PngImagePlugin.MAX_TEXT_CHUNK, PngImagePlugin.MAX_TEXT_MEMORY, ImageFile.LOAD_TRUNCATED_IMAGES) == before


def test_text_past_the_limit_reads_as_not_read_and_its_size_is_stated(tmp_path):
    from sashimi.core._imagemeta import MAX_PNG_TEXT

    info = metadata.read(large_text_png(tmp_path, MAX_PNG_TEXT + 1))
    fields = next(section for section in info.sections if section.carrier == "PNG text").fields
    assert [(field.name, field.value) for field in fields] == [("Comment", "(not read)")]


def test_xmp_past_the_limit_counts_as_a_location_and_is_not_kept(tmp_path):
    from sashimi.core._imagemeta import MAX_PNG_TEXT

    squeezed = zlib.compress(xmp_packet(b'exif:GPSLatitude="48,51.5N"', b" " * (MAX_PNG_TEXT + 1)), 9)
    chunk = png_chunk(b"iTXt", b"XML:com.adobe.xmp\0\1\0\0\0" + squeezed)
    data = picture_bytes("PNG")
    at = data.index(b"IDAT") - 4
    path = tmp_path / "xmp.png"
    path.write_bytes(data[:at] + chunk + data[at:])
    info = metadata.read(path)
    assert info.has_location and "XMP" in info.carriers
    assert any(section.name == "XMP packets not read" for section in info.sections)
    done = metadata.strip(path, tmp_path / "out.png", "remove_location")
    assert not metadata.read(tmp_path / "out.png").has_location and "XMP" in done.fidelity.lost


def test_a_large_colour_profile_stays_with_the_picture(tmp_path):
    profile = samples.icc() + bytes(2 << 20)
    data = picture_bytes("PNG")
    chunk = png_chunk(b"iCCP", b"large\0\0" + zlib.compress(profile, 9))
    at = data.index(b"IDAT") - 4
    path = tmp_path / "icc.png"
    path.write_bytes(data[:at] + chunk + data[at:])
    assert metadata.read(path).has_color_profile
    converted = image.convert(path, tmp_path / "a.png", IMAGE_CONVERT.from_params(target="png"))
    assert metadata.read(tmp_path / "a.png").has_color_profile
    assert "colour profile" not in converted.fidelity.lost


def test_clean_text_turns_lone_surrogates_into_spaces():
    from sashimi.core._metaread import clean_text

    assert clean_text("a\ud800b\udfffc") == "a b c"


HOSTILE_TEXT = "\x1b]0;pwned\x07\x1b[31m\x7f\u202e\ud800\n\r\t" + "H" * 70_000


@pytest.mark.parametrize("field", ["changed", "lost", "removed", "adjusted", "kept", "notes"])
def test_every_fidelity_entry_is_clean_on_one_line_and_capped(field):
    from sashimi.core.fidelity import MAX_ENTRIES, MAX_ENTRY, Fidelity

    told = Fidelity(level="lossy", content="picture", **{field: [HOSTILE_TEXT, *[f"{n}" for n in range(100)]]})
    entries = getattr(told, field)
    assert len(entries) == MAX_ENTRIES + 1 and entries[-1] == "81 more"
    assert all(len(entry) <= MAX_ENTRY for entry in entries)
    for entry in (*entries, told.text):
        assert not re.search("[\x00-\x1f\x7f-\x9f\u202a-\u202e\ud800-\udfff]", entry)
