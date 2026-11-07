# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
``metadata.read`` lists every field it read, by carrier, as bounded readable text, beside its
highlights; says what it looked for and what is still in the file but not counted; and keeps a
capture date apart from a file's own dates. Images only (no FFmpeg needed).
"""

import io
import struct

import pytest
from PIL import Image, ImageCms, TiffImagePlugin

from sashimi import metadata
from sashimi.core._metaread import MAX_FIELDS, MAX_VALUE_CHARS, readable, xmp_pairs
from sashimi.core.metadata import offset_of
from tests import metadata_samples as samples

rational = TiffImagePlugin.IFDRational
CAPTURE_FIELDS = {"DateTimeOriginal", "exif:DateTimeOriginal", "photoshop:DateCreated", "DateCreated", "Creation Time"}


def camera_exif(**extra) -> Image.Exif:
    """EXIF as a phone writes it: camera, lens, exposure, capture date with offset and sub-seconds, GPS with direction."""
    block = Image.Exif()
    block[0x010F] = "Maker"
    block[0x0110] = "Phone 1"
    block[0x0112] = 6
    block[0x011A] = rational(72)
    block[0x011B] = rational(72)
    block[0x0128] = 2
    block[0x0131] = "1.2.3"
    block[0x0132] = "2025:01:02 03:04:05"
    block[0x8769] = {
        0x829A: rational(1, 33),
        0x829D: rational(19, 10),
        0x8827: 250,
        0x9003: "2024:05:18 04:04:36",
        0x9011: "+03:00",
        0x9291: "586",
        0x920A: rational(269, 100),
        0xA405: 30,
        0xA433: "Maker",
        0xA434: "Phone 1 front camera 2.69mm f/1.9",
        0x927C: b"Apple iOS\x00\x00\x01MM" + bytes(200),
        0xBEEF: 3,
    }
    block[0x8825] = {
        1: "N",
        2: (rational(48), rational(51), rational(3024, 100)),
        3: "E",
        4: (rational(2), rational(17), rational(402, 10)),
        0x10: "T",
        0x11: rational(11250, 100),
    }
    for tag, value in extra.items():
        block[int(tag, 16)] = value
    return block


def jpeg_with(exif: Image.Exif | None = None, **save) -> bytes:
    out = io.BytesIO()
    samples.picture().save(out, "JPEG", **({"exif": exif.tobytes()} if exif is not None else {}), **save)
    return out.getvalue()


def write(tmp_path, data: bytes, name: str = "photo.blob"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def fields_of(info, name):
    found = next(s for s in info.sections if s.name == name)
    return {field.name: field.value for field in found.fields}


def check_bounded(info):
    for found in info.sections:
        assert len(found.fields) <= MAX_FIELDS
        for field in found.fields:
            assert isinstance(field.value, str) and isinstance(field.name, str)
            assert len(field.value) <= MAX_VALUE_CHARS + 40
            assert not any(ord(c) < 32 and c not in "\t\n" for c in field.value), field
            assert "\x00" not in field.name


def test_every_exif_directory_is_a_section_with_standard_names(tmp_path):
    info = metadata.read(write(tmp_path, jpeg_with(camera_exif())))
    names = [s.name for s in info.sections]
    assert {"EXIF IFD0", "EXIF Exif IFD", "EXIF maker note", "EXIF GPS IFD"} <= set(names)
    ifd0, details, gps = fields_of(info, "EXIF IFD0"), fields_of(info, "EXIF Exif IFD"), fields_of(info, "EXIF GPS IFD")
    assert ifd0["Make"] == "Maker" and ifd0["Orientation"] == "6" and ifd0["XResolution"] == "72"
    assert (
        details["ExposureTime"] == "1/33" and details["FNumber"] == "1.9" and details["OffsetTimeOriginal"] == "+03:00"
    )
    assert details["0xBEEF"] == "3"
    assert "MakerNote" not in details and fields_of(info, "EXIF maker note")["Maker"] == "Apple"
    assert gps["GPSLatitude"] == "48, 51, 30.24" and gps["GPSImgDirection"] == "112.5"
    assert {s.carrier for s in info.sections} >= {"EXIF", "GPS"}
    check_bounded(info)


def test_highlights_name_lens_exposure_direction_and_capture_offset(tmp_path):
    info = metadata.read(write(tmp_path, jpeg_with(camera_exif())))
    assert info.lens_make == "Maker" and info.lens_model == "Phone 1 front camera 2.69mm f/1.9"
    assert info.exposure_time == pytest.approx(1 / 33) and info.f_number == pytest.approx(1.9) and info.iso == 250
    assert info.focal_length == pytest.approx(2.69) and info.focal_length_35mm == 30
    assert info.image_direction == pytest.approx(112.5) and info.image_direction_ref == "true"
    assert info.date_taken == "2024:05:18 04:04:36"
    assert info.date_taken_offset == "+03:00" and info.date_taken_subseconds == "586"
    assert info.maker_note == "Apple"


def test_date_taken_is_never_the_files_own_date(tmp_path):
    block = Image.Exif()
    block[0x010F] = "Maker"
    block[0x0132] = "2025:01:02 03:04:05"
    info = metadata.read(write(tmp_path, jpeg_with(block)))
    assert info.date_taken is None and info.date_taken_offset is None
    assert fields_of(info, "EXIF IFD0")["DateTime"] == "2025:01:02 03:04:05"


def test_an_xmp_create_date_is_not_a_capture_date(tmp_path):
    xmp = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:CreateDate="2020-01-01T00:00:00+01:00"/>'
        b"</rdf:RDF></x:xmpmeta>"
    )
    path = write(tmp_path, jpeg_with(xmp=xmp))
    info = metadata.read(path)
    assert info.date_taken is None
    assert fields_of(info, "XMP")["xmp:CreateDate"] == "2020-01-01T00:00:00+01:00"


def test_a_photoshop_capture_date_keeps_its_offset(tmp_path):
    xmp = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"'
        b' photoshop:DateCreated="2020-01-01T10:00:00-05:00"/></rdf:RDF></x:xmpmeta>'
    )
    info = metadata.read(write(tmp_path, jpeg_with(xmp=xmp)))
    assert info.date_taken == "2020-01-01T10:00:00-05:00" and info.date_taken_offset == "-05:00"


def test_the_exif_thumbnail_and_colour_profile_are_described(tmp_path):
    thumb = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(thumb, "JPEG")
    block = camera_exif()
    tiff = bytearray(block.tobytes()[6:] if block.tobytes().startswith(b"Exif") else block.tobytes())
    ifd1_at = len(tiff) + (len(tiff) & 1)
    tiff += b"\0" * (ifd1_at - len(tiff))
    data_at = ifd1_at + 2 + 2 * 12 + 4
    order = "<" if tiff[:2] == b"II" else ">"
    entries = struct.pack(order + "H", 2)
    entries += struct.pack(order + "HHII", 0x0201, 4, 1, data_at)
    entries += struct.pack(order + "HHII", 0x0202, 4, 1, len(thumb.getvalue()))
    tiff += entries + struct.pack(order + "I", 0) + thumb.getvalue()
    first = struct.unpack_from(order + "I", tiff, 4)[0]
    count = struct.unpack_from(order + "H", tiff, first)[0]
    struct.pack_into(order + "I", tiff, first + 2 + 12 * count, ifd1_at)
    out = io.BytesIO()
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    samples.picture().save(out, "JPEG", exif=b"Exif\x00\x00" + bytes(tiff), icc_profile=profile)
    info = metadata.read(write(tmp_path, out.getvalue()))
    assert info.thumbnail_size == (32, 24)
    assert fields_of(info, "EXIF thumbnail (IFD1)")["Image size"] == "32x24"
    assert info.color_profile and "sRGB" in info.color_profile
    icc = fields_of(info, "ICC profile")
    assert icc["Colour space"] == "RGB" and icc["Size"] == f"{len(profile):,} bytes"
    assert next(s for s in info.sections if s.name == "ICC profile").counted is False


def test_values_are_readable_text_and_bounded(tmp_path):
    block = camera_exif()
    block[0x010E] = "x" * 5000
    block[0x8769] = {
        **block[0x8769],
        0x9286: b"ASCII\x00\x00\x00a comment",
        0xA420: "\x01\x02id\x7f",
        0xA300: bytes(range(256)) * 4,
    }
    info = metadata.read(write(tmp_path, jpeg_with(block)))
    ifd0, details = fields_of(info, "EXIF IFD0"), fields_of(info, "EXIF Exif IFD")
    assert ifd0["ImageDescription"].startswith("x" * MAX_VALUE_CHARS) and "5,000 characters" in ifd0["ImageDescription"]
    assert details["UserComment"] == "a comment"
    assert details["FileSource"] == "(1,024 bytes)"
    check_bounded(info)


def test_a_section_lists_at_most_max_fields_and_counts_the_rest():
    properties = b"".join(b' p:f%d="%d"' % (n, n) for n in range(MAX_FIELDS + 50))
    xmp = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description xmlns:p="urn:p"' + properties + b"/></rdf:RDF></x:xmpmeta>"
    )
    from sashimi.core._metaread import xmp_section

    found = xmp_section(xmp)
    assert len(found.fields) == MAX_FIELDS and found.omitted == 50


def test_xmp_with_entities_is_not_parsed():
    xmp = b'<!DOCTYPE x [<!ENTITY a "aaaaaaaaaa">]><x:xmpmeta xmlns:x="adobe:ns:meta/">&a;</x:xmpmeta>'
    assert xmp_pairs(xmp) is None


def test_xmp_lists_and_structs_are_flattened():
    pairs = dict(xmp_pairs(samples.XMP) or [])
    assert pairs["dc:title"] == "SASHIMI-PRIVACY-MARKER-xmp"
    assert pairs["exif:GPSLatitude"] == "48,51.504N" and pairs["exif:GPSAltitude"] == "35/1"


def test_iptc_and_photoshop_sections_name_their_fields(tmp_path):
    info = metadata.read(write(tmp_path, samples.jpeg()))
    iptc = fields_of(info, "IPTC IIM")
    assert iptc["City"] == "SASHIMI-LOCATION-MARKER" and iptc["Caption-Abstract"] == "SASHIMI-PRIVACY-MARKER-iptc"
    assert "IPTC-NAA record" in fields_of(info, "Photoshop image resources")
    assert fields_of(info, "Comments")["Comment"] == "SASHIMI-PRIVACY-MARKER-comment"


def test_png_text_is_a_section(tmp_path):
    info = metadata.read(write(tmp_path, samples.png()))
    text = fields_of(info, "PNG text")
    assert text["Title"] == "SASHIMI-PRIVACY-MARKER-title" and text["Location"] == "SASHIMI-LOCATION-MARKER"
    assert not any(s.name == "Comments" for s in info.sections)


@pytest.mark.parametrize(
    ("name", "looked_for"),
    [
        ("jpeg", {"EXIF", "GPS", "XMP", "IPTC", "Photoshop", "comment"}),
        ("png", {"EXIF", "GPS", "XMP", "PNG text", "comment"}),
        ("webp", {"EXIF", "GPS", "XMP"}),
        ("gif", {"XMP", "comment"}),
        ("tiff", {"EXIF", "GPS", "XMP", "IPTC", "Photoshop"}),
    ],
)
def test_read_says_what_it_looked_for(tmp_path, name, looked_for):
    info = metadata.read(write(tmp_path, samples.IMAGES[name]()))
    assert looked_for <= set(info.looked_for)
    assert set(info.carriers) <= set(info.looked_for)


def test_a_stripped_photo_says_what_is_still_in_it(tmp_path):
    source = write(tmp_path, jpeg_with(camera_exif(), icc_profile=samples.icc()))
    out = tmp_path / "out.jpg"
    result = metadata.strip(source, out, "remove_all")
    info = metadata.read(out)
    assert info.carriers == [] and not info.has_metadata
    assert [(d.name, d.value) for d in info.not_counted][0] == ("orientation", "6 (turned 90° clockwise)")
    assert "colour profile" in [d.name for d in info.not_counted]
    exif = next(s for s in info.sections if s.carrier == "EXIF")
    assert exif.counted is False and [f.name for f in exif.fields] == ["Orientation"]
    assert all(not s.counted for s in info.sections)
    assert "EXIF (all but the orientation)" in result.fidelity.removed
    assert "EXIF" not in result.fidelity.removed
    assert result.fidelity.kept[0] == "orientation (turned 90° clockwise)"
    assert any(item.startswith("colour profile (") for item in result.fidelity.kept)
    assert "kept: orientation (turned 90° clockwise)" in result.fidelity.text


def test_removing_only_location_names_nothing_kept(tmp_path):
    source = write(tmp_path, jpeg_with(camera_exif()))
    result = metadata.strip(source, tmp_path / "out.jpg", "remove_location")
    assert result.fidelity.kept == []
    info = metadata.read(tmp_path / "out.jpg")
    assert not info.has_location and not any(s.carrier == "GPS" for s in info.sections)
    assert info.image_direction is None


def test_resolution_is_listed_as_not_counted(tmp_path):
    out = io.BytesIO()
    samples.picture().save(out, "PNG", dpi=(300, 300))
    info = metadata.read(write(tmp_path, out.getvalue()))
    assert info.carriers == [] and ("resolution", "300 dpi") in [(d.name, d.value) for d in info.not_counted]


@pytest.mark.parametrize("name", sorted(samples.IMAGES))
def test_every_image_kind_reads_bounded_sections_that_agree_with_its_carriers(tmp_path, name):
    info = metadata.read(write(tmp_path, samples.IMAGES[name]()))
    check_bounded(info)
    counted = {s.carrier for s in info.sections if s.counted}
    assert counted <= set(info.carriers)
    assert {"EXIF", "XMP"} & set(info.carriers) <= {s.carrier for s in info.sections}
    info.model_validate_json(info.model_dump_json())
    if info.date_taken is not None:
        stored = {f.value for s in info.sections for f in s.fields if f.name in CAPTURE_FIELDS}
        assert info.date_taken in stored


@pytest.mark.parametrize("name", sorted(samples.IMAGES))
def test_after_strip_nothing_counted_is_left_and_no_marker_survives(tmp_path, name):
    source = write(tmp_path, samples.IMAGES[name]())
    out = tmp_path / "out"
    metadata.strip(source, out, "remove_all")
    info = metadata.read(out)
    assert info.carriers == [] and all(not s.counted for s in info.sections)
    assert samples.MARKER not in out.read_bytes() and samples.PLACE not in out.read_bytes()
    check_bounded(info)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (rational(1, 33), "1/33"),
        (rational(19, 10), "1.9"),
        (rational(72, 1), "72"),
        (rational(0, 0), "undefined"),
        (b"0232", "0232"),
        (b"\x01\x02\x03\x00", "01 02 03 00"),
        (bytes(100), "(100 bytes)"),
        ((rational(1), rational(2, 4)), "1, 0.5"),
        ("a\x00b\x07c", "a b c"),
        (float("nan"), "nan"),
    ],
)
def test_readable_values(value, text):
    assert readable(value) == text


@pytest.mark.parametrize(
    ("text", "offset"),
    [
        ("+03:00", "+03:00"),
        ("2024-09-06T18:08:07+0200", "+02:00"),
        ("2024-09-06T18:08:07Z", "+00:00"),
        ("2024-09-06T18:08:07-07:00", "-07:00"),
        ("2024:09:06 18:08:07", None),
        ("2024-09-06", None),
        (None, None),
    ],
)
def test_offset_of(text, offset):
    assert offset_of(text) == offset


def test_xmp_that_only_says_how_to_show_a_gain_map_is_not_counted(tmp_path):
    xmp = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description rdf:about="" xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" hdrgm:Version="1.0"/>'
        b"</rdf:RDF></x:xmpmeta>"
    )
    info = metadata.read(write(tmp_path, jpeg_with(xmp=xmp)))
    assert "XMP" not in info.carriers and not info.has_metadata
    assert ("display XMP", "gain map") in [(d.name, d.value) for d in info.not_counted]
    assert next(s for s in info.sections if s.carrier == "XMP").counted is False


def test_xmp_with_anything_else_is_counted(tmp_path):
    xmp = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description rdf:about="" xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" hdrgm:Version="1.0"'
        b' xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:CreatorTool="Editor"/></rdf:RDF></x:xmpmeta>'
    )
    info = metadata.read(write(tmp_path, jpeg_with(xmp=xmp)))
    assert "XMP" in info.carriers and info.software == "Editor"


def test_a_hand_built_probe_result_is_decodable_by_default():
    from sashimi.core.probe import MediaInfo, check_decodable

    check_decodable(MediaInfo(container="mov", has_video=True, duration=1.0))


def test_deeply_nested_xmp_is_named_not_parsed():
    depth = 5000
    body = b"".join(b'<p:a%d xmlns:p="urn:p">' % n for n in range(depth)) + b"".join(
        b"</p:a%d>" % n for n in reversed(range(depth))
    )
    xmp = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description rdf:about="">' + body + b"</rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    from sashimi.core._metaread import xmp_section

    assert xmp_section(xmp).name == "XMP (unreadable)"
