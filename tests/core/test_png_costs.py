# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
A PNG's text and colour profile chunks share one budget for what a read inflates, whatever their
shape, and a PNG Pillow would refuse is opened through a view that holds no copy of the file.
"""

import io
import os
import random
import struct
import tracemalloc
import zlib

import pytest
from PIL import Image

from sashimi import image, metadata
from sashimi.config import IMAGE_CONVERT
from sashimi.core import image as image_core


def chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def plain_png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (32, 24), (9, 200, 9)).save(out, "PNG")
    return out.getvalue()


def png_with(*chunks: bytes) -> bytes:
    data = plain_png()
    return data[:33] + b"".join(chunks) + data[33:]


def profile_chunk(size: int) -> bytes:
    return chunk(b"iCCP", b"p\0\0" + zlib.compress(bytes(size), 9))


def text_chunk(size: int, key: bytes = b"note") -> bytes:
    return chunk(b"zTXt", key + b"\0\0" + zlib.compress(bytes(size), 9))


def broken_stream(size: int, key: bytes) -> bytes:
    """A zTXt whose compressed data inflates `size` bytes, then fails its checksum."""
    stream = bytearray(zlib.compress(bytes(size), 9))
    stream[-1] ^= 0xFF
    return chunk(b"zTXt", key + b"\0\0" + bytes(stream))


@pytest.fixture
def inflated(monkeypatch):
    """Counts every byte zlib inflates while it's on, in sashimi and Pillow alike."""
    real = zlib.decompressobj
    counted = [0]

    class Counting:
        def __init__(self, *args):
            self.inner = real(*args)

        def decompress(self, *args):
            found = self.inner.decompress(*args)
            counted[0] += len(found)
            return found

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(zlib, "decompressobj", Counting)
    return counted


SHAPES = {
    "colour profiles past every limit": lambda: png_with(*[profile_chunk(9 << 20)] * 200),
    "colour profiles under Pillow's limit": lambda: png_with(*[profile_chunk((1 << 20) - 1024)] * 300),
    "text chunks just under Pillow's limit": lambda: png_with(*[text_chunk((1 << 20) - 1024)] * 300),
    "text chunks just past Pillow's limit": lambda: png_with(*[text_chunk((1 << 20) + 1024)] * 300),
    "xmp chunks past every limit": lambda: png_with(*[text_chunk(9 << 20, b"XML:com.adobe.xmp")] * 100),
    "text chunks that inflate to nothing": lambda: png_with(*[text_chunk(0)] * 3000, text_chunk(8 << 20)),
    "damaged text chunks": lambda: png_with(*[broken_stream(8 << 20, b"note")] * 100),
    "raw exif profiles": lambda: png_with(*[text_chunk(8 << 20, b"Raw profile type exif")] * 100),
}
ROUTES = {
    "read": lambda path, out: metadata.read(path),
    "webp": lambda path, out: image.convert(path, out / "o.webp", IMAGE_CONVERT.from_params(target="webp")),
    "strip all": lambda path, out: metadata.strip(path, out / "s.png", "remove_all"),
    "strip location": lambda path, out: metadata.strip(path, out / "s.png", "remove_location"),
}


@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_no_shape_of_chunks_makes_a_small_png_inflate_much_more_than_one_budget(tmp_path, inflated, shape, route):
    path = tmp_path / "in.png"
    path.write_bytes(SHAPES[shape]())
    inflated[0] = 0
    ROUTES[route](path, tmp_path)
    assert inflated[0] <= 12 * (64 << 20)


def test_only_a_pngs_first_colour_profile_is_read(tmp_path, monkeypatch):
    calls = []
    real = image_core.png_profile
    monkeypatch.setattr(image_core, "png_profile", lambda payload: calls.append(payload) or real(payload))
    path = tmp_path / "in.png"
    path.write_bytes(png_with(profile_chunk(2 << 20), profile_chunk(3 << 20), profile_chunk(4 << 20)))
    with image_core._open_image(path, None, load=False) as img:
        assert img.info["icc_profile"] == bytes(2 << 20)
    assert len(calls) == 1


def test_later_colour_profiles_arent_seen_or_carried(tmp_path):
    first, second = profile_chunk(100), profile_chunk(200)
    path = tmp_path / "in.png"
    path.write_bytes(png_with(first, second))
    with image_core._open_image(path, None, load=False) as img:
        assert img.info["icc_profile"] == bytes(100)
    metadata.strip(path, tmp_path / "s.png", "remove_all")
    stripped = (tmp_path / "s.png").read_bytes()
    assert first in stripped and second not in stripped


def test_the_view_of_a_large_png_holds_no_copy_of_the_file(tmp_path):
    random_rows = random.Random(1).randbytes(30_000_000)
    picture = io.BytesIO()
    Image.frombytes("RGB", (5000, 2000), random_rows).save(picture, "PNG", compress_level=0)
    data = picture.getvalue()
    path = tmp_path / "large.png"
    path.write_bytes(data[:33] + text_chunk(2 << 20) + data[33:])
    tracemalloc.start()
    try:
        view, _ = image_core._png_view(path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert view is not None
    view.close()
    assert peak < len(data) / 4


def test_a_picture_read_through_the_view_is_the_same_picture(tmp_path):
    random_rows = random.Random(2).randbytes(600 * 400 * 3)
    source = Image.frombytes("RGB", (600, 400), random_rows)
    plain = io.BytesIO()
    source.save(plain, "PNG", compress_level=1)
    data = plain.getvalue()
    path = tmp_path / "in.png"
    path.write_bytes(data[:33] + text_chunk(2 << 20) + profile_chunk(10) + profile_chunk(20) + data[33:])
    with image_core._open_image(path, None) as img:
        assert img.tobytes() == source.tobytes()


def test_the_view_reads_and_seeks_as_the_file_without_those_ranges(tmp_path):
    rng = random.Random(3)
    data = rng.randbytes(200_000)
    path = tmp_path / "bytes"
    path.write_bytes(data)
    left_out = [(0, 7), (7, 5), (1000, 10), (5000, 4000), (199_990, 10)]
    kept = bytearray()
    at = 0
    for start, length in left_out:
        kept += data[at:start]
        at = start + length
    kept += data[at:]
    flat = [number for pair in left_out for number in pair]
    with io.BufferedReader(image_core._PngWithoutChunks(os.fspath(path), flat)) as view:
        assert view.read() == bytes(kept)
        for _ in range(300):
            where = rng.randrange(len(kept) + 50)
            size = rng.randrange(1, 9000)
            assert view.seek(where) == where
            assert view.read(size) == bytes(kept[where : where + size])
        assert view.seek(-4, os.SEEK_END) == len(kept) - 4
        assert view.read() == bytes(kept[-4:])


XMP = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" dc:format="image/png"/>'
    b"</rdf:RDF></x:xmpmeta>"
)
EMPTY_TEXT = {
    "iTXt": lambda key: chunk(b"iTXt", key + b"\0\0\0\0\0"),
    "compressed iTXt": lambda key: chunk(b"iTXt", key + b"\0\x01\0\0\0" + zlib.compress(b"")),
    "zTXt": lambda key: chunk(b"zTXt", key + b"\0\0" + zlib.compress(b"")),
    "tEXt": lambda key: chunk(b"tEXt", key + b"\0"),
}
XMP_CHUNK = chunk(b"iTXt", b"XML:com.adobe.xmp\0\0\0\0\0" + XMP)


@pytest.mark.parametrize("kind", sorted(EMPTY_TEXT))
def test_blank_text_doesnt_stop_later_text_being_read(tmp_path, kind):
    path = tmp_path / "in.png"
    blanks = [EMPTY_TEXT[kind](b"Blank%d" % index) for index in range(9)]
    path.write_bytes(png_with(*blanks, chunk(b"tEXt", b"Title\0T"), chunk(b"tEXt", b"Author\0A")))
    info = metadata.read(path)
    assert (info.title, info.artist) == ("T", "A")


@pytest.mark.parametrize("kind", sorted(EMPTY_TEXT))
def test_blank_comments_dont_cost_a_png_its_xmp(tmp_path, kind):
    path = tmp_path / "in.png"
    path.write_bytes(png_with(*[EMPTY_TEXT[kind](b"Comment")] * 9, XMP_CHUNK))
    info = metadata.read(path)
    assert not info.has_location
    told = image.convert(path, tmp_path / "o.png", IMAGE_CONVERT.from_params(target="png")).fidelity
    assert told.lost == [] and told.level == "lossless"
    assert b"xmpmeta" in (tmp_path / "o.png").read_bytes()


def test_text_that_wasnt_read_still_reads_as_not_read_and_names_no_location(tmp_path):
    path = tmp_path / "in.png"
    path.write_bytes(png_with(*[broken_stream(1024, b"Comment")] * 9, XMP_CHUNK))
    info = metadata.read(path)
    assert info.has_location, "XMP that wasn't read can't be called clean"
    assert any(section.name == "XMP packets not read" for section in info.sections)
    told = image.convert(path, tmp_path / "o.png", IMAGE_CONVERT.from_params(target="png")).fidelity
    assert "XMP" in told.lost
    assert "location" not in told.lost


def test_an_xmp_packet_too_large_to_search_names_no_location_when_it_is_lost(tmp_path):
    packet = XMP.replace(b"</rdf:RDF>", b" " * (5 << 20) + b"</rdf:RDF>")
    path = tmp_path / "in.png"
    path.write_bytes(png_with(chunk(b"iTXt", b"XML:com.adobe.xmp\0\0\0\0\0" + packet)))
    assert metadata.read(path).has_location
    told = image.convert(path, tmp_path / "o.jpg", IMAGE_CONVERT.from_params(target="jpeg")).fidelity
    assert "XMP" in told.lost
    assert "location" not in told.lost
