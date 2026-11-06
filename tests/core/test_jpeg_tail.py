# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
What follows a JPEG's main picture: every picture ``strip`` keeps there goes through the same
cleaning, listed in MPF or not, with what lists it updated; only what can't be cleaned goes, and it
is named. ``read`` counts what a kept picture holds, and items are matched by where they are.
"""

import io
import random
import struct

import pytest
from PIL import Image, ImageChops
from PIL.TiffImagePlugin import IFDRational

from sashimi import metadata
from sashimi.core import metadata as metadata_core
from sashimi.core._imagemeta import (
    KEEP_VIDEO,
    MAX_TAIL_PICTURES,
    _jpeg_carriers,
    _segment,
    jpeg_pieces,
    motion_video,
    strip_image,
)
from sashimi.core._metaread import directory_items
from tests.core.test_hostile_metadata import directory_packet, jpeg_xmp, mpf_segment, xmp_packet

XMP_PREFIX = b"http://ns.adobe.com/xap/1.0/\0"
NAME = "Jane Doe of 12 Rue X"
VIDEO = b"\0\0\0\x18ftypmp42" + b"\0" * 100
GAIN_XMP = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about="" xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" hdrgm:Version="1.0" '
    b'hdrgm:GainMapMin="0.000000" hdrgm:GainMapMax="2.300448" hdrgm:Gamma="1.000000" hdrgm:OffsetSDR="0.015625" '
    b'hdrgm:OffsetHDR="0.015625" hdrgm:HDRCapacityMin="0.000000" hdrgm:HDRCapacityMax="2.300448" '
    b'hdrgm:BaseRenditionIsHDR="False"/></rdf:RDF></x:xmpmeta>'
)


@pytest.fixture(autouse=True)
def videos_hold_nothing(monkeypatch):
    """The videos here aren't real: what becomes of a real one is tested with FFmpeg, in the integration tests."""
    monkeypatch.setattr(
        metadata_core,
        "_motion_choice",
        lambda data, mode, folder, limits: (KEEP_VIDEO if motion_video(data) is not None else None, []),
    )


def plain(size=(16, 12), seed=1) -> bytes:
    out = io.BytesIO()
    Image.frombytes("L", size, random.Random(seed).randbytes(size[0] * size[1])).save(out, "JPEG")
    return out.getvalue()


def holding(*segments: bytes, size=(16, 12), seed=1) -> bytes:
    data = plain(size, seed)
    return data[:2] + b"".join(segments) + data[2:]


def gps_exif() -> bytes:
    exif = Image.Exif()
    exif[0x013B] = NAME
    exif[0x8825] = {1: "N", 2: (IFDRational(48), IFDRational(51), IFDRational(24))}
    return exif.tobytes()


def photoshop_iptc(dataset: int) -> bytes:
    iptc = b"\x1c\x02" + bytes([dataset]) + struct.pack(">H", len(NAME)) + NAME.encode()
    padded = iptc + b"\0" * (len(iptc) % 2)
    return b"Photoshop 3.0\x008BIM\x04\x04\0\0" + struct.pack(">I", len(iptc)) + padded


CARRIERS = {
    "EXIF": (lambda: _segment(0xE1, gps_exif()), True),
    "XMP name": (
        lambda: _segment(0xE1, XMP_PREFIX + xmp_packet(body=f"<dc:creator>{NAME}</dc:creator>".encode())),
        False,
    ),
    "XMP location": (
        lambda: _segment(0xE1, XMP_PREFIX + xmp_packet(f'exif:GPSLatitude="48,51N" tiff:Artist="{NAME}"'.encode())),
        True,
    ),
    "extended XMP": (lambda: _segment(0xE1, b"http://ns.adobe.com/xmp/extension/\0" + NAME.encode()), False),
    "IPTC": (lambda: _segment(0xED, photoshop_iptc(80)), False),
    "IPTC place": (lambda: _segment(0xED, photoshop_iptc(90)), True),
    "comment": (lambda: _segment(0xFE, NAME.encode()), False),
    "another segment": (lambda: _segment(0xE9, NAME.encode()), False),
}


def primary_item(padding: int | None = None) -> bytes:
    text = b'Item:Semantic="Primary" Item:Mime="image/jpeg"'
    return text if padding is None else text + b' Item:Padding="%d"' % padding


def item(semantic: bytes | None, length: int, *more: bytes) -> bytes:
    parts = [b'Item:Mime="image/jpeg"', b'Item:Length="%d"' % length, *more]
    if semantic is not None:
        parts.insert(0, b'Item:Semantic="%s"' % semantic)
    return b" ".join(parts)


def video_item(video: bytes) -> bytes:
    return b'Item:Semantic="MotionPhoto" Item:Mime="video/mp4" Item:Length="%d" Item:Padding="0"' % len(video)


def with_mpf(main: bytes, listed: list[bytes], kinds: list[int] | None = None) -> bytes:
    sizes = [0, *map(len, listed)]
    types = kinds or [0x030000, *[0] * len(listed)]
    length = len(mpf_segment(sizes, [0] * len(sizes), types)) + 2
    offsets, at = [0], len(main) + 2 + length - 10
    for part in listed:
        offsets.append(at)
        at += len(part)
    segment = mpf_segment(sizes, offsets, types)
    return main[:2] + b"\xff\xe2" + struct.pack(">H", length) + segment + main[2:]


def photo(items: list[bytes], tail: list[bytes], listed: list[bytes] | None = None) -> bytes:
    """A JPEG whose first XMP packet lists `items`, with `tail` after it; `listed` are the parts MPF lists, first in `tail`."""
    main = jpeg_xmp(directory_packet(*items, attributes=b'hdrgm:Version="1.0" GCamera:MotionPhoto="1"'))
    if listed:
        main = with_mpf(main, listed)
    return main + b"".join(tail)


def items_of(data: bytes) -> list[dict[str, str]]:
    return directory_items(_jpeg_carriers(data).xmp or b"")


def walked(data: bytes) -> list[tuple[str | None, bytes]]:
    """Each item a viewer finds by walking back from the end of the file, as (semantic, its first 8 bytes)."""
    found = []
    at = len(data)
    for entry in reversed(items_of(data)[1:]):
        at -= int(entry["Length"]) + int(entry.get("Padding") or 0)
        found.append((entry.get("Semantic"), data[at : at + 8]))
    return found[::-1]


def walks_back_to_the_main_picture(data: bytes) -> bool:
    at = len(data)
    entries = items_of(data)
    for entry in entries[1:]:
        at -= int(entry["Length"]) + int(entry.get("Padding") or 0)
    pieces, _ = jpeg_pieces(data)
    return at - int(entries[0].get("Padding") or 0) == sum(len(segment) for _, segment in pieces)


def same_picture(one: bytes, other: bytes) -> bool:
    first, second = Image.open(io.BytesIO(one)).convert("RGB"), Image.open(io.BytesIO(other)).convert("RGB")
    return first.size == second.size and ImageChops.difference(first, second).getbbox() is None


def write_strip(tmp_path, data: bytes, mode: str):
    source = tmp_path / "photo.jpg"
    source.write_bytes(data)
    result = metadata.strip(source, tmp_path / "out.jpg", mode)
    return result.fidelity, (tmp_path / "out.jpg").read_bytes()


LAYOUTS = ["in MPF", "listed in the directory", "listed nowhere"]


def layout(where: str, depth: bytes, video: bytes | None) -> bytes:
    tail = [depth, *([video] if video else [])]
    if where == "listed nowhere" and not video:
        return holding() + depth
    rows = [primary_item()]
    if where != "listed nowhere":
        rows.append(item(b"Depth", len(depth)))
    if video:
        rows.append(video_item(video))
    return photo(rows, tail, [depth] if where == "in MPF" else None)


@pytest.mark.parametrize("mode", ["remove_all", "remove_location"])
@pytest.mark.parametrize("video", [None, VIDEO], ids=["no video", "video"])
@pytest.mark.parametrize("where", LAYOUTS)
@pytest.mark.parametrize("carrier", sorted(CARRIERS))
def test_a_kept_picture_goes_through_the_same_cleaning_as_the_main_one(tmp_path, carrier, where, video, mode):
    segment, location = CARRIERS[carrier]
    depth = holding(segment(), seed=2)
    data = layout(where, depth, video)
    source = tmp_path / "source.jpg"
    source.write_bytes(data)
    before = metadata.read(source)
    if carrier != "another segment":
        assert before.has_metadata, "a read calls the file clean while a kept picture holds metadata"
    if location:
        assert before.has_location

    told, out = write_strip(tmp_path, data, mode)
    after = metadata.read(tmp_path / "out.jpg")
    assert not after.has_location
    assert b"48,51N" not in out
    scan = jpeg_pieces(plain(seed=2))[0][-2][1]
    assert scan in out, "the picture itself went"
    if mode == "remove_all":
        assert NAME.encode() not in out
        assert "EXIF" not in after.carriers and "IPTC" not in after.carriers and after.carriers in ([], ["XMP"])
    if location and mode == "remove_location":
        assert "GPS" not in after.carriers
    if video:
        found = motion_video(out)
        assert found is not None and out[found.start : found.end] == video
    named_when_removed = {"extended XMP": "extended XMP", "IPTC place": "IPTC"}
    assert told.lost == (
        [named_when_removed[carrier]] if mode == "remove_location" and carrier in named_when_removed else []
    )


@pytest.mark.parametrize("mode", ["remove_all", "remove_location"])
def test_a_replaced_video_keeps_every_picture_with_its_new_length(tmp_path, mode):
    depth = holding(_segment(0xE1, gps_exif()), seed=2)
    gain = holding(_segment(0xE1, XMP_PREFIX + GAIN_XMP), seed=3)
    rows = [primary_item(), item(b"GainMap", len(gain)), item(b"Depth", len(depth)), video_item(VIDEO)]
    data = photo(rows, [gain, depth, VIDEO], [gain])
    new_video = b"\0\0\0\x18ftypmp42" + b"\1" * 60
    out = strip_image(data, "JPEG", mode, True, new_video)
    assert [entry["Semantic"] for entry in items_of(out)] == ["Primary", "GainMap", "Depth", "MotionPhoto"]
    assert walks_back_to_the_main_picture(out)
    assert [start[4:8] if name == "MotionPhoto" else start[:2] for name, start in walked(out)] == [
        b"\xff\xd8",
        b"\xff\xd8",
        b"ftyp",
    ]
    assert gain in out, "a gain map holding only how to apply it stays byte for byte"
    assert NAME.encode() not in out or mode == "remove_location"
    assert "GPS" not in _jpeg_carriers(out[out.index(gain) + len(gain) :]).found
    found = motion_video(out)
    assert found is not None and out[found.start : found.end] == new_video


@pytest.mark.parametrize("mode", ["remove_all", "remove_location"])
def test_a_kept_pictures_offset_and_length_follow_it_in_mpf(tmp_path, mode):
    gain = holding(_segment(0xE1, gps_exif()), _segment(0xE1, XMP_PREFIX + GAIN_XMP), seed=3)
    data = photo([primary_item(), item(b"GainMap", len(gain))], [gain], [gain])
    _, out = write_strip(tmp_path, data, mode)
    from sashimi.core._imagemeta import _mpf_entries

    position = 0
    for marker, segment in jpeg_pieces(out)[0]:
        if marker == 0xE2 and segment[4:8] == b"MPF\0":
            size, offset = _mpf_entries(segment)[2][1]
            start = position + 8 + offset
            assert out[start : start + 3] == b"\xff\xd8\xff" and start + size == len(out)
            assert b"hdrgm:GainMapMax" in out[start:] and "GPS" not in _jpeg_carriers(out[start:]).found
            assert int(items_of(out)[1]["Length"]) == size
            break
        position += len(segment)
    else:
        pytest.fail("the MPF segment went")


def test_a_gain_maps_free_text_goes_and_how_to_apply_it_stays(tmp_path):
    packet = GAIN_XMP.replace(b"</rdf:Description>", b"").replace(
        b'hdrgm:BaseRenditionIsHDR="False"/>',
        b'hdrgm:BaseRenditionIsHDR="False"><dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">Jane</dc:creator></rdf:Description>',
    )
    gain = holding(_segment(0xE1, XMP_PREFIX + packet), seed=3)
    data = photo([primary_item(), item(b"GainMap", len(gain))], [gain], [gain])
    told, out = write_strip(tmp_path, data, "remove_all")
    assert b"Jane" not in out and b'hdrgm:GainMapMax="2.300448"' in out
    assert not told.lost and metadata.read(tmp_path / "out.jpg").carriers == []


def test_iso_gain_map_data_stays_in_both_pictures(tmp_path):
    iso = _segment(0xE2, b"urn:iso:std:iso:ts:21496:-1\0\0\0\0\0")
    gain = holding(iso, seed=3)
    main = with_mpf(holding(iso), [gain])
    told, out = write_strip(tmp_path, main + gain, "remove_all")
    assert out.count(iso) == 2 and not told.lost


def test_a_gain_map_with_several_channels_keeps_them(tmp_path):
    seq = b"<rdf:Seq><rdf:li>0.1</rdf:li><rdf:li>0.2</rdf:li><rdf:li>0.3</rdf:li></rdf:Seq>"
    packet = xmp_packet(
        b'hdrgm:Version="1.0"',
        b"<hdrgm:GainMapMax>" + seq + b"</hdrgm:GainMapMax><dc:creator>Jane</dc:creator>",
    )
    gain = holding(_segment(0xE1, XMP_PREFIX + packet), seed=3)
    data = photo([primary_item(), item(b"GainMap", len(gain))], [gain], [gain])
    _, out = write_strip(tmp_path, data, "remove_all")
    assert b"Jane" not in out and seq in out


@pytest.mark.parametrize("mode", ["remove_all", "remove_location"])
def test_a_stereo_pair_keeps_its_other_eye_without_its_metadata(tmp_path, mode):
    left = holding(_segment(0xE1, gps_exif()), size=(32, 24), seed=4)
    right = holding(_segment(0xE1, gps_exif()), size=(32, 24), seed=5)
    data = with_mpf(left, [right], [0x020002, 0x020002]) + right
    source = tmp_path / "pair.mpo"
    source.write_bytes(data)
    assert metadata.read(source).has_location
    out_path = tmp_path / "out.mpo"
    told = metadata.strip(source, out_path, mode).fidelity
    assert told.lost == []
    with Image.open(source) as before, Image.open(out_path) as after:
        assert after.n_frames == before.n_frames == 2
        for frame in range(2):
            before.seek(frame)
            after.seek(frame)
            assert ImageChops.difference(before.convert("RGB"), after.convert("RGB")).getbbox() is None
    assert not metadata.read(out_path).has_location
    if mode == "remove_all":
        assert NAME.encode() not in out_path.read_bytes()


@pytest.mark.parametrize("mode", ["remove_all", "remove_location"])
def test_what_isnt_a_picture_goes_and_is_named(tmp_path, mode):
    other = b"not a picture: " + NAME.encode() + b"\1" * 100
    rows = [primary_item(), item(b"Sidecar", len(other)), video_item(VIDEO)]
    data = photo(rows, [other, VIDEO])
    told, out = write_strip(tmp_path, data, mode)
    assert NAME.encode() not in out
    assert "another item the photo listed" in told.lost
    assert [entry.get("Semantic") for entry in items_of(out)] == ["Primary", "MotionPhoto"]
    found = motion_video(out)
    assert found is not None and out[found.start : found.end] == VIDEO


def test_a_picture_past_an_item_that_goes_is_still_kept(tmp_path):
    depth = holding(_segment(0xE1, gps_exif()), seed=2)
    other = b"not a picture" + b"\1" * 100
    rows = [primary_item(), item(b"Sidecar", len(other)), item(b"Depth", len(depth)), video_item(VIDEO)]
    told, out = write_strip(tmp_path, photo(rows, [other, depth, VIDEO]), "remove_all")
    assert [entry.get("Semantic") for entry in items_of(out)] == ["Primary", "Depth", "MotionPhoto"]
    assert told.lost == ["another item the photo listed"] and walks_back_to_the_main_picture(out)


@pytest.mark.parametrize("mode", ["remove_all", "remove_location"])
@pytest.mark.parametrize(
    "semantic", [b"Sidecar", None, b"Unknown"], ids=["unknown semantic", "no semantic", "stated unknown"]
)
def test_no_loss_is_reported_for_an_item_of_unknown_kind_that_stays(tmp_path, semantic, mode):
    picture = plain(seed=2)
    rows = [primary_item(), item(semantic, len(picture)), video_item(VIDEO)]
    told, out = write_strip(tmp_path, photo(rows, [picture, VIDEO]), mode)
    assert told.lost == [] and picture in out
    assert len(items_of(out)) == 3


@pytest.mark.parametrize("mode", ["remove_all", "remove_location"])
def test_items_of_equal_length_are_told_apart_by_where_they_are(tmp_path, mode):
    picture = plain(seed=2)
    other = b"\1" * len(picture)
    rows = [
        primary_item(),
        item(b"GainMap", len(picture)),
        item(b"Depth", len(other)),
        item(b"Confidence", len(picture)),
        video_item(VIDEO),
    ]
    data = photo(rows, [picture, other, picture, VIDEO], [picture])
    told, out = write_strip(tmp_path, data, mode)
    assert [entry.get("Semantic") for entry in items_of(out)] == ["Primary", "GainMap", "Confidence", "MotionPhoto"]
    assert told.lost == ["the photo's Depth item"]
    assert walks_back_to_the_main_picture(out)
    assert out.count(picture) == 2 and b"\1" * 50 not in out


def test_a_replaced_video_keeps_items_of_equal_length_apart():
    picture = plain(seed=2)
    rows = [
        primary_item(),
        item(b"GainMap", len(picture)),
        item(b"Depth", len(picture)),
        item(b"Confidence", len(picture)),
        video_item(VIDEO),
    ]
    data = photo(rows, [picture, picture, picture, VIDEO], [picture])
    out = strip_image(data, "JPEG", "remove_all", True, b"\0\0\0\x18ftypmp42" + b"\1" * 40)
    assert len(items_of(out)) == 5 and out.count(picture) == 3 and walks_back_to_the_main_picture(out)


def test_a_directory_that_doesnt_add_up_lists_nothing_it_cant_locate(tmp_path):
    picture = plain(seed=2)
    rows = [primary_item(), item(b"GainMap", len(picture)), item(b"Depth", len(picture) + 9)]
    told, out = write_strip(tmp_path, photo(rows, [picture]), "remove_all")
    assert [entry.get("Semantic") for entry in items_of(out)] == []
    assert picture in out
    assert "the photo's Depth item" in told.lost


def test_pictures_past_the_limit_go_and_are_named(tmp_path):
    pictures = [plain(seed=2)] * (MAX_TAIL_PICTURES + 3)
    told, out = write_strip(tmp_path, holding() + b"".join(pictures), "remove_all")
    assert out.count(plain(seed=2)) == MAX_TAIL_PICTURES
    assert any("data after the picture" in name for name in told.removed)


def test_a_samsung_trailer_stays_with_a_video_kept_as_it_is():
    trailer = b"\0\0" + b"Image_UTC_Data1234SEFHMotionPhoto_Data"
    video = b"\0\0\0\x18ftypmp42" + b"\0" * 40 + b"SEFT"
    rows = [
        b'Item:Semantic="Primary" Item:Mime="image/jpeg" Item:Length="0" Item:Padding="%d"' % len(trailer),
        video_item(video),
    ]
    data = photo(rows, [trailer, video])
    out = strip_image(data, "JPEG", "remove_all", True, KEEP_VIDEO)
    assert out.endswith(trailer + video)
    assert motion_video(out) is not None


def test_a_read_names_the_picture_that_holds_what_it_found(tmp_path):
    depth = holding(_segment(0xE1, gps_exif()), seed=2)
    source = tmp_path / "source.jpg"
    source.write_bytes(holding() + depth)
    info = metadata.read(source)
    assert info.has_location and "EXIF" in info.carriers and "GPS" in info.carriers
    assert any(section.name.startswith("Picture 2: ") and section.counted for section in info.sections)
    assert info.camera_make is None and info.artist is None
