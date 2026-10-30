# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Images, videos and audio carrying synthetic metadata in every carrier sashimi handles: a GPS
position (the Eiffel Tower's), a place-name marker, and a marker string in the fields that
aren't about location. Nothing here is anyone's real data.
"""

import io
import pathlib
import struct
import subprocess

from PIL import Image, ImageCms, TiffImagePlugin
from PIL.PngImagePlugin import PngInfo

MARKER = b"SASHIMI-PRIVACY-MARKER"
"""In every carrier's fields that aren't about location (camera, description, title, comment, …)."""
PLACE = b"SASHIMI-LOCATION-MARKER"
"""In place-name fields (EXIF GPS map datum, XMP and IPTC city, PNG location text)."""
XMP_COORDINATES = (b"48,51.504N", b"2,17.67E")
ISO6709 = "+48.8584+002.2945+035.000/"
COORDINATE_BYTES = (b"48.8584", *XMP_COORDINATES)
"""Coordinates as text, which no output may contain once location is removed."""

XMP = (
    b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"'
    b' xmlns:exif="http://ns.adobe.com/exif/1.0/" xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"'
    b' xmlns:tiff="http://ns.adobe.com/tiff/1.0/"'
    b' exif:GPSLatitude="48,51.504N" exif:GPSLongitude="2,17.67E" photoshop:City="SASHIMI-LOCATION-MARKER">'
    b'<dc:title><rdf:Alt><rdf:li xml:lang="x-default">SASHIMI-PRIVACY-MARKER-xmp</rdf:li></rdf:Alt></dc:title>'
    b"<exif:GPSAltitude>35/1</exif:GPSAltitude>"
    b"</rdf:Description></rdf:RDF></x:xmpmeta>"
    b'<?xpacket end="w"?>'
)


def iptc() -> bytes:
    """IPTC IIM: a city (a place) and a caption (not a place)."""

    def dataset(number: int, value: bytes) -> bytes:
        return b"\x1c\x02" + bytes([number]) + struct.pack(">H", len(value)) + value

    return dataset(90, PLACE) + dataset(120, MARKER + b"-iptc")


def photoshop() -> bytes:
    """Photoshop image resources holding the IPTC record."""
    data = iptc()
    return b"8BIM\x04\x04\x00\x00" + struct.pack(">I", len(data)) + data + (b"\0" if len(data) & 1 else b"")


def exif(orientation: int = 1) -> Image.Exif:
    """EXIF with a camera, a description, a date, an orientation and a GPS position."""
    block = Image.Exif()
    block[0x010F] = "SASHIMI-PRIVACY-MARKER-make"
    block[0x010E] = "SASHIMI-PRIVACY-MARKER-description"
    block[0x0112] = orientation
    block[0x8769] = {0x9003: "2001:02:03 04:05:06"}
    rational = TiffImagePlugin.IFDRational
    block[0x8825] = {
        1: "N",
        2: (rational(48), rational(51), rational(3024, 100)),
        3: "E",
        4: (rational(2), rational(17), rational(402, 10)),
        0x12: PLACE.decode(),
    }
    return block


def icc() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _s15(value: float) -> bytes:
    return struct.pack(">i", round(value * 65536))


def _xyz(x: float, y: float, z: float) -> bytes:
    return b"XYZ \0\0\0\0" + _s15(x) + _s15(y) + _s15(z)


def swapped_profile() -> bytes:
    """An RGB colour profile with sRGB's red and green primaries swapped, so converting to sRGB visibly changes colours."""
    curve = b"curv\0\0\0\0" + struct.pack(">IH", 1, round(2.2 * 256)) + b"\0\0"
    tags = [
        (b"wtpt", _xyz(0.9642, 1.0, 0.8249)),
        (b"rXYZ", _xyz(0.3851, 0.7169, 0.0971)),
        (b"gXYZ", _xyz(0.4361, 0.2225, 0.0139)),
        (b"bXYZ", _xyz(0.1431, 0.0606, 0.7141)),
        (b"rTRC", curve),
        (b"gTRC", curve),
        (b"bTRC", curve),
    ]
    table, body = struct.pack(">I", len(tags)), b""
    start = 128 + 4 + 12 * len(tags)
    for signature, data in tags:
        table += signature + struct.pack(">II", start + len(body), len(data))
        body += data
    header = struct.pack(">I", 128 + len(table) + len(body)) + b"\0\0\0\0" + struct.pack(">I", 0x02100000)
    header += b"mntrRGB XYZ " + bytes(12) + b"acsp" + bytes(28) + _s15(0.9642) + _s15(1.0) + _s15(0.8249) + bytes(48)
    return header + table + body


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def picture(mode: str = "RGB", size: tuple[int, int] = (64, 48)) -> Image.Image:
    img = Image.new(mode, size)
    for x in range(size[0]):
        for y in range(size[1]):
            value = (x * 4 % 256, y * 5 % 256, (x + y) * 3 % 256)
            img.putpixel((x, y), value if mode == "RGB" else (*value, 255 if x > 4 else 0))
    return img


def jpeg(orientation: int = 1) -> bytes:
    extra = _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + XMP)
    extra += _segment(0xED, b"Photoshop 3.0\x00" + photoshop())
    out = io.BytesIO()
    picture().save(
        out,
        "JPEG",
        quality=90,
        exif=exif(orientation).tobytes(),
        comment=MARKER + b"-comment",
        extra=extra,
        icc_profile=icc(),
    )
    return out.getvalue()


def png() -> bytes:
    info = PngInfo()
    info.add_text("Comment", (MARKER + b"-comment").decode())
    info.add_text("Title", (MARKER + b"-title").decode())
    info.add_text("Location", PLACE.decode())
    info.add_itxt("XML:com.adobe.xmp", XMP.decode())
    out = io.BytesIO()
    picture("RGBA").save(out, "PNG", pnginfo=info, exif=exif().tobytes(), icc_profile=icc())
    return out.getvalue()


def webp() -> bytes:
    out = io.BytesIO()
    picture().save(out, "WEBP", quality=90, exif=exif().tobytes(), xmp=XMP, icc_profile=icc())
    return out.getvalue()


def animated_webp() -> bytes:
    frames = [picture(), picture().rotate(180)]
    out = io.BytesIO()
    frames[0].save(out, "WEBP", save_all=True, append_images=frames[1:], duration=100, exif=exif().tobytes(), xmp=XMP)
    return out.getvalue()


def _gif_with_xmp(data: bytes) -> bytes:
    header = 13 + ((3 << ((data[10] & 7) + 1)) if data[10] & 0x80 else 0)
    trailer = b"\x01" + bytes(range(255, -1, -1)) + b"\x00"
    return data[:header] + b"\x21\xff\x0bXMP DataXMP" + XMP + trailer + data[header:]


def gif(animated: bool = False) -> bytes:
    frames = [picture().convert("P"), picture().rotate(180).convert("P")]
    out = io.BytesIO()
    if animated:
        frames[0].save(out, "GIF", save_all=True, append_images=frames[1:], duration=100, comment=MARKER + b"-comment")
    else:
        frames[0].save(out, "GIF", comment=MARKER + b"-comment")
    return _gif_with_xmp(out.getvalue())


def tiff(compression: str = "raw") -> bytes:
    """
    A TIFF with EXIF tags in its first directory, XMP, IPTC and Photoshop tags, and (uncompressed
    only, since Pillow can't write them with compression) EXIF and GPS directories.
    """
    tags = Image.Exif()
    tags.load(exif().tobytes())
    if compression != "raw":
        del tags[0x8769]
        del tags[0x8825]
    tags[315] = "SASHIMI-PRIVACY-MARKER-artist"
    tags[700] = XMP
    tags[33723] = iptc()
    tags[34377] = photoshop()
    out = io.BytesIO()
    picture().save(out, "TIFF", exif=tags, compression=None if compression == "raw" else compression, icc_profile=icc())
    return out.getvalue()


IMAGES = {
    "jpeg": jpeg,
    "png": png,
    "webp": webp,
    "gif": gif,
    "tiff": tiff,
    "tiff-lzw": lambda: tiff("tiff_lzw"),
}


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], check=True)


CREATED = "2024-05-01T12:34:56.000000Z"
"""The creation time :func:`tagged_mov` records."""


def tagged_mov(directory: pathlib.Path) -> pathlib.Path:
    """H.264 + AAC MOV with QuickTime location, global and stream tags, a chapter and a 90° display rotation."""
    meta = directory / "meta.txt"
    meta.write_text(
        ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\ntitle=SASHIMI-PRIVACY-MARKER-chapter\n"
    )
    plain = directory / "plain.mov"
    _ffmpeg(
        *("-f", "lavfi", "-i", "testsrc2=s=320x240:d=2:r=25", "-f", "lavfi", "-i", "sine=d=2", "-i", str(meta)),
        *("-map", "0", "-map", "1", "-map_metadata", "2", "-map_chapters", "2"),
        *("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"),
        *("-metadata", "title=SASHIMI-PRIVACY-MARKER-title", "-metadata", "artist=SASHIMI-PRIVACY-MARKER-artist"),
        *("-metadata", "comment=SASHIMI-PRIVACY-MARKER-comment", "-metadata", f"location={ISO6709}"),
        *("-metadata", f"com.apple.quicktime.location.ISO6709={ISO6709}"),
        *("-metadata", "com.apple.quicktime.make=SASHIMI-PRIVACY-MARKER-make", "-metadata", "firmware=HD8.01"),
        *("-metadata", f"creation_time={CREATED}"),
        *("-metadata:s:v:0", "handler_name=SASHIMI-PRIVACY-MARKER-vh", "-metadata:s:a:0", "language=fra"),
        *("-movflags", "use_metadata_tags", "-f", "mov", str(plain)),
    )
    path = directory / "original.blob"
    _ffmpeg(
        *("-display_rotation", "90", "-i", str(plain), "-map", "0:v", "-map", "0:a", "-c", "copy"),
        *("-metadata", f"creation_time={CREATED}", "-movflags", "use_metadata_tags", "-f", "mov", str(path)),
    )
    return path


def tagged_mkv(directory: pathlib.Path, mov: pathlib.Path) -> pathlib.Path:
    """The MOV's streams in Matroska with Opus audio, Matroska tags (location included) and a chapter."""
    path = directory / "original.blob"
    _ffmpeg(
        *("-i", str(mov), "-map", "0:v", "-map", "0:a", "-map_metadata", "0", "-c:v", "copy", "-c:a", "libopus"),
        *("-metadata:s:a:0", "title=SASHIMI-PRIVACY-MARKER-stream", "-metadata", "ENCODED_BY=SASHIMI-PRIVACY-MARKER"),
        *("-f", "matroska", str(path)),
    )
    return path


def tagged_mp3(directory: pathlib.Path) -> pathlib.Path:
    """An MP3 with ID3 title, artist, comment and a location tag (TXXX)."""
    path = directory / "original.blob"
    _ffmpeg(
        *("-f", "lavfi", "-i", "sine=d=2", "-metadata", "title=SASHIMI-PRIVACY-MARKER-title"),
        *("-metadata", "artist=SASHIMI-PRIVACY-MARKER-artist", "-metadata", f"location={ISO6709}"),
        *("-id3v2_version", "3", "-c:a", "libmp3lame", "-f", "mp3", str(path)),
    )
    return path


def tagged_flac(directory: pathlib.Path) -> pathlib.Path:
    """A FLAC with Vorbis comments: a title and a location."""
    path = directory / "original.blob"
    _ffmpeg(
        *("-f", "lavfi", "-i", "sine=d=2", "-metadata", "title=SASHIMI-PRIVACY-MARKER-title"),
        *("-metadata", f"location={ISO6709}", "-c:a", "flac", "-f", "flac", str(path)),
    )
    return path
