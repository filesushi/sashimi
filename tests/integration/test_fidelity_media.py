# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
What video and audio outputs say they kept, checked against the outputs: a picture and sound
said to be identical decode to the same frames and samples, and what is said to be dropped is
gone. Also: copies keep a hidden lead hidden, JPEG motion photos keep their video when it can be
made to hold what was asked, and short videos' sheets don't repeat tiles.
"""

import pathlib
import struct
import subprocess

import pytest
from PIL import Image

from sashimi import audio, metadata, probe, sheets, video
from sashimi.config import (
    AUDIO_CONVERT,
    AnimatedWebpSheetConfig,
    CopyTrimConfig,
    JpegSheetConfig,
    Mp4VideoConfig,
    VIDEO_CONVERT,
)
from sashimi.core._imagemeta import jpeg_pieces, motion_video
from sashimi.core import video as core_video
from sashimi.core.fidelity import media_inventory
from sashimi.errors import InvalidOptionsError
from tests.conftest import needs_ffmpeg
from tests.core.test_real_world_metadata import _exif_with_gps, _jpeg, _mpf_pictures

pytestmark = needs_ffmpeg


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], check=True)


def _decoded(path: pathlib.Path, kind: str) -> list[str]:
    """The hash of each decoded frame (``v``) or audio frame (``a``) of the first such stream, in order."""
    args = ["-map", f"0:{kind}:0", "-fps_mode", "passthrough"] if kind == "v" else ["-map", "0:a:0"]
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), *args, "-f", "framemd5", "pipe:1"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return [line.split(",")[-1].strip() for line in out.splitlines() if line and not line.startswith("#")]


def _holds(source: list[str], output: list[str]) -> bool:
    """
    Whether `output` decodes to `source`'s frames, in order: a copy into MP4 or MOV may also play
    the AAC encoder's priming frame that Matroska skips, so one extra frame at either end is allowed,
    and FFmpeg 9.0 trims the last AAC frame of such a copy to the MP4 edit list's end, so the last
    frame may differ (the copied packets are the same bytes on 8.1 and 9.0).
    """
    for skip in range(0, 2):
        for tail in range(0, 2):
            kept = output[skip : len(output) - tail]
            if kept == source or (len(kept) == len(source) and kept[:-1] == source[:-1]):
                return True
    return False


@pytest.fixture(scope="module")
def tagged(tmp_path_factory) -> pathlib.Path:
    """2 s of H.264 + AAC in Matroska with tags, chapters, a second audio track and subtitles."""
    folder = tmp_path_factory.mktemp("tagged")
    srt = folder / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    meta = folder / "meta.txt"
    meta.write_text(";FFMETADATA1\ntitle=A title\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\ntitle=One\n")
    path = folder / "t.mkv"
    _ffmpeg(
        *("-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=2", "-f", "lavfi", "-i", "sine=d=2"),
        *("-f", "lavfi", "-i", "sine=f=880:d=2", "-i", str(srt), "-i", str(meta)),
        *("-map", "0", "-map", "1", "-map", "2", "-map", "3", "-map_metadata", "4", "-map_chapters", "4"),
        *("-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-c:a", "aac", "-c:s", "srt"),
        *("-metadata", "location=+51.5000-000.1000/", str(path)),
    )
    return path


def _check_media(source: pathlib.Path, output: pathlib.Path, told) -> None:
    if told.level != "lossy":
        assert _holds(_decoded(source, "v"), _decoded(output, "v")), told
        if "sound" in told.content:
            assert _holds(_decoded(source, "a"), _decoded(output, "a")), told
    before, after = media_inventory(source), media_inventory(output)
    for name in before.items:
        if before.label(name) in told.lost or before.label(name) in told.removed:
            assert name not in after.items, (name, told)
    if told.level == "lossless":
        assert set(before.items) <= set(after.items) | {n for n in before.items if before.label(n) in told.removed}


@pytest.mark.parametrize("container", ["mkv", "mp4", "mov", "webm"])
@pytest.mark.parametrize("mode", ["keep", "remove_location", "remove_all"])
def test_copies_say_truthfully_what_they_kept(tagged, tmp_path, container, mode):
    out = tmp_path / f"o.{container}"
    if container == "webm":
        with pytest.raises(InvalidOptionsError):
            video.trim(tagged, out, CopyTrimConfig(container=container, metadata=mode))
        return
    told = video.trim(tagged, out, CopyTrimConfig(container=container, metadata=mode)).fidelity
    _check_media(tagged, out, told)
    if mode != "keep":
        assert "location" in told.removed
    if container in ("mp4", "mov"):
        assert "the subtitles' SubRip format (converted to MP4 text)" in told.lost


@pytest.mark.parametrize("target", ["mp4", "webm", "gif", "webp"])
def test_reencodes_say_they_are_lossy_and_what_they_dropped(tagged, tmp_path, target):
    out = tmp_path / f"o.{target}"
    told = video.convert(
        tagged, out, VIDEO_CONVERT.from_params({"crf": 30} if target in ("mp4", "webm") else {}, target=target)
    ).fidelity
    assert told.level == "lossy"
    if target in ("mp4", "webm"):
        _check_media(tagged, out, told)
        assert "another audio track" in told.lost


def test_an_mp4_copy_route_that_keeps_one_audio_track_says_so(tagged, tmp_path):
    result = video.convert(tagged, tmp_path / "o.mp4", Mp4VideoConfig())
    assert result.route == "copy" and result.fidelity.level == "content_lossless"
    assert {"another audio track", "subtitles"} <= set(result.fidelity.lost)
    _check_media(tagged, tmp_path / "o.mp4", result.fidelity)


@pytest.fixture(scope="module")
def sounds(tmp_path_factory) -> dict[str, pathlib.Path]:
    folder = tmp_path_factory.mktemp("sounds")
    paths = {}
    for name, codec in (
        ("s16.wav", "pcm_s16le"),
        ("s24.wav", "pcm_s24le"),
        ("f32.wav", "pcm_f32le"),
        ("x.flac", "flac"),
    ):
        paths[name] = folder / name
        _ffmpeg("-f", "lavfi", "-i", "sine=d=1:sample_rate=44100", "-c:a", codec, str(paths[name]))
    paths["x.mp3"] = folder / "x.mp3"
    _ffmpeg("-f", "lavfi", "-i", "sine=d=1", "-c:a", "libmp3lame", str(paths["x.mp3"]))
    return paths


@pytest.mark.parametrize("source", ["s16.wav", "s24.wav", "f32.wav", "x.flac", "x.mp3"])
@pytest.mark.parametrize("params", [{"target": "wav"}, {"target": "wav", "wav_bit_depth": 24}, {"target": "flac"}])
def test_audio_says_identical_only_when_the_samples_are(sounds, tmp_path, source, params):
    params = dict(params)
    out = tmp_path / "o"
    told = audio.convert(sounds[source], out, AUDIO_CONVERT.from_params(params, target=params.pop("target"))).fidelity
    if told.level != "lossy":
        assert _samples(sounds[source]) == _samples(out), told
    else:
        assert told.changed
    written = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=bits_per_raw_sample,bits_per_sample",
            "-of",
            "csv=p=0",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    bits = {int(part) for part in written.replace("\n", ",").split(",") if part.strip().isdigit() and int(part) > 0}
    for change in told.changed:
        if change.endswith(" bits"):
            assert int(change.split()[-2]) in bits, (change, bits)


def _samples(path: pathlib.Path) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "f64le", "pipe:1"],
        capture_output=True,
        check=True,
    ).stdout


def _with_hidden_lead(tmp_path: pathlib.Path) -> pathlib.Path:
    """An MP4 whose edit list hides its first half second, as phone and Dolby Vision files do."""
    full = tmp_path / "full.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=3", "-c:v", "libx264", "-g", "50", "-bf", "0", str(full))
    hidden = tmp_path / "hidden.mp4"
    _ffmpeg("-ss", "0.5", "-i", str(full), "-c", "copy", str(hidden))
    return hidden


def test_a_copy_keeps_a_hidden_lead_hidden(tmp_path):
    source = _with_hidden_lead(tmp_path)
    assert core_video.hidden_lead(source) == pytest.approx(0.5, abs=0.05)
    shown = probe(source).duration
    for container in ("mp4", "mov"):
        out = tmp_path / f"o.{container}"
        result = video.trim(source, out, CopyTrimConfig(container=container))
        assert probe(out).duration == pytest.approx(shown, abs=0.05)
        assert _holds(_decoded(source, "v"), _decoded(out, "v"))
        assert result.fidelity.level != "lossy"
    told = video.trim(source, tmp_path / "o.mkv", CopyTrimConfig(container="mkv")).fidelity
    (changed,) = told.changed
    assert changed.startswith("shows 0.5") and changed.endswith(
        " s of frames the source keeps hidden at its start, which MKV can't hide"
    )


def _mp4(path: pathlib.Path, location: bool) -> bytes:
    args = ("-metadata", "location=+51.5000-000.1000/") if location else ()
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=64x36:r=10:d=1", "-c:v", "libx264", "-preset", "ultrafast", *args, str(path)
    )
    return path.read_bytes()


def _motion_photo(tmp_path: pathlib.Path, location: bool, samsung: bool = False) -> bytes:
    """A Google-layout motion photo (a Samsung trailer after the video with `samsung`) with an Ultra HDR gain map."""
    clip = _mp4(tmp_path / "clip.mp4", location)
    gain = _jpeg(
        Image.new("L", (16, 12), 128),
        xmp=b'<x:xmpmeta><rdf:Description hdrgm:Version="1.0" hdrgm:GainMapMax="2.0"/></x:xmpmeta>',
    )
    padding = b"MotionPhoto_Data" if samsung else b""
    tail = b"\x00" * 8 + b"SEFH" + b"\x00" * 8 + b"SEFT" if samsung else b""
    xmp = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description rdf:about=""'
        b' hdrgm:Version="1.0" GCamera:MotionPhoto="1" GCamera:MotionPhotoVersion="1" exif:GPSLatitude="51,30N">'
        b"<Container:Directory><rdf:Seq>"
        b'<rdf:li rdf:parseType="Resource"><Container:Item Item:Semantic="Primary" Item:Mime="image/jpeg"/></rdf:li>'
        b'<rdf:li rdf:parseType="Resource"><Container:Item Item:Semantic="GainMap" Item:Mime="image/jpeg" Item:Length="'
        + str(len(gain)).encode()
        + b'"/></rdf:li>'
        b'<rdf:li rdf:parseType="Resource"><Container:Item Item:Mime="video/mp4" Item:Semantic="MotionPhoto" Item:Length="'
        + str(len(clip)).encode()
        + b'" Item:Padding="0"/></rdf:li>'
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

    length = len(mpf(0)) + 2
    primary = len(main) + 2 + length
    data = main[:2] + b"\xff\xe2" + struct.pack(">H", length) + mpf(primary - 10) + main[2:]
    return data + gain + padding + clip + tail


def _embedded(data: bytes) -> pathlib.Path | None:
    found = motion_video(data)
    return None if found is None else data[found.start : found.end]


@pytest.mark.parametrize("mode", ["remove_location", "remove_all"])
@pytest.mark.parametrize("location", [False, True])
def test_a_motion_photos_video_is_kept_when_it_can_be_made_to_hold_what_was_asked(tmp_path, mode, location):
    (tmp_path / "m.jpg").write_bytes(_motion_photo(tmp_path, location))
    result = metadata.strip(tmp_path / "m.jpg", tmp_path / "o.jpg", mode)
    out = (tmp_path / "o.jpg").read_bytes()
    clip = _embedded(out)
    assert clip is not None, result.fidelity
    (tmp_path / "kept.mp4").write_bytes(clip)
    kept = media_inventory(tmp_path / "kept.mp4")
    assert not kept.location and probe(tmp_path / "kept.mp4").has_video
    if mode == "remove_all":
        assert not kept.metadata
    if mode == "remove_location" and not location:
        assert clip == (tmp_path / "clip.mp4").read_bytes()
    assert len(_mpf_pictures(out)) == 2 and b"hdrgm:Version" in out and b"GPSLatitude" not in out
    assert not metadata.read(tmp_path / "o.jpg").has_location
    assert "motion photo video" not in result.fidelity.removed


def test_a_samsung_motion_photos_video_with_a_location_is_removed_and_said_so(tmp_path):
    (tmp_path / "m.jpg").write_bytes(_motion_photo(tmp_path, True, samsung=True))
    result = metadata.strip(tmp_path / "m.jpg", tmp_path / "o.jpg", "remove_location")
    out = (tmp_path / "o.jpg").read_bytes()
    assert jpeg_pieces(out)[1] == b"" or b"ftyp" not in out
    assert any("Samsung" in note for note in result.fidelity.notes)
    assert len(_mpf_pictures(out)) == 2


def test_a_sheet_of_a_clip_with_few_frames_shows_each_once_and_says_so(tmp_path):
    clip = tmp_path / "c.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=1.5", "-c:v", "libx264", str(clip))
    result = sheets.render(clip, tmp_path / "s.jpg", JpegSheetConfig(cols=6, rows=4))
    assert result.fidelity.notes == ["This clip has 15 frames, so the sheet shows 15 tiles in a 5x3 grid"]
    long = tmp_path / "l.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=6", "-c:v", "libx264", str(long))
    result = sheets.render(long, tmp_path / "a.webp", AnimatedWebpSheetConfig(cols=3, rows=3, sample_duration=1, fps=4))
    assert result.fidelity.notes == [
        "This video is 6 seconds long, so the sheet shows 5 clips of 1 second in a 3x2 grid, "
        "so that no two clips overlap"
    ]
    assert result.fidelity.changed[0] == "5 clips at 4 fps on an animated sheet"
    tiny = tmp_path / "t.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=1.28", "-c:v", "libx264", str(tiny))
    result = sheets.render(tiny, tmp_path / "t.webp", AnimatedWebpSheetConfig())
    assert result.fidelity.notes == [
        "This video is 1.3 seconds long, shorter than a clip of 2 seconds, so the sheet shows one clip of the whole video"
    ]


def _with_hidden_tail(tmp_path: pathlib.Path) -> pathlib.Path:
    """An MP4 whose edit list ends two frames before its last packets, as some iPhone recordings do."""
    full = tmp_path / "full.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=3", "-c:v", "libx264", "-g", "50", str(full))
    data = bytearray(full.read_bytes())
    at = data.find(b"elst")
    timescale = struct.unpack(">I", data[data.find(b"mvhd") + 16 : data.find(b"mvhd") + 20])[0]
    length = struct.unpack(">I", data[at + 12 : at + 16])[0]
    struct.pack_into(">I", data, at + 12, length - int(0.08 * timescale))
    cut = tmp_path / "tail.mp4"
    cut.write_bytes(bytes(data))
    return cut


def test_a_copy_that_shows_frames_hidden_at_the_end_says_so(tmp_path):
    source = _with_hidden_tail(tmp_path)
    assert core_video.hidden_tail(source, probe(source)) == 2
    for told in (
        video.trim(source, tmp_path / "r.mkv", CopyTrimConfig(container="mkv")).fidelity,
        video.convert(source, tmp_path / "c.mp4", Mp4VideoConfig()).fidelity,
        metadata.strip(source, tmp_path / "s.mp4", "remove_all").fidelity,
    ):
        assert told.level == "lossy" and "shows 2 frames the source keeps hidden at its end" in told.changed
    told = video.trim(source, tmp_path / "part.mp4", CopyTrimConfig(container="mp4", duration_seconds=1)).fidelity
    assert told.level != "lossy"
