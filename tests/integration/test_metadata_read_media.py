# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
``metadata.read`` on video and audio: every tag by where ffprobe found it (the file, each
stream, chapters), what isn't counted (encoder and handler tags), a capture date kept apart from
the container's own date; and video whose codec FFmpeg can't decode, refused with its own error
by every route that decodes the picture while copies still work.
"""

import subprocess

import pytest

import sashimi
from sashimi import audio, metadata, sheets, video
from sashimi.config import AUDIO_CONVERT, VIDEO_CONVERT, VIDEO_PREVIEW, VIDEO_TRANSFORM, VIDEO_TRIM, JpegSheetConfig
from sashimi.core import preview as core_preview
from sashimi.core import video as core_video
from sashimi.core._metaread import MAX_VALUE_CHARS
from sashimi.errors import ProcessingError, UndecodableVideoError, UnsupportedMediaError
from tests import metadata_samples as samples
from tests.conftest import needs_ffmpeg

pytestmark = needs_ffmpeg
CAPTURE_FIELDS = {"com.apple.quicktime.creationdate", "date_recorded", "DATE_RECORDED"}


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], check=True)


def clip(*args: str) -> list[str]:
    return ["-f", "lavfi", "-i", "testsrc2=s=160x120:d=2:r=10", "-f", "lavfi", "-i", "sine=d=2", *args]


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    folder = tmp_path_factory.mktemp("read")
    made = {"mov": samples.tagged_mov(tmp_path_factory.mktemp("mov"))}
    made["mkv"] = samples.tagged_mkv(tmp_path_factory.mktemp("mkv"), made["mov"])
    made["mp3"] = samples.tagged_mp3(tmp_path_factory.mktemp("mp3"))
    made["flac"] = samples.tagged_flac(tmp_path_factory.mktemp("flac"))
    plain = folder / "plain.mkv"
    ffmpeg(
        *clip("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-f", "matroska"),
        str(plain),
    )
    made["plain mkv"] = plain
    remuxed = folder / "remuxed.mp4"
    ffmpeg(
        *clip("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"),
        *("-metadata", "creation_time=2024-03-11T03:36:33Z", "-f", "mp4", str(remuxed)),
    )
    made["remuxed mp4"] = remuxed
    phone = folder / "phone.mov"
    ffmpeg(
        *clip("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"),
        *("-metadata", "com.apple.quicktime.creationdate=2024-09-06T18:08:07+0200"),
        *("-metadata", "creation_time=2024-09-06T16:08:10Z", "-movflags", "use_metadata_tags", "-f", "mov"),
        str(phone),
    )
    made["phone mov"] = phone
    made["undecodable"] = undecodable(folder)
    return made


def undecodable(folder):
    """A QuickTime file without an ``ftyp`` whose video codec FFmpeg doesn't know (its sample entry renamed)."""
    source = folder / "known.mov"
    ffmpeg(
        *clip("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-f", "mov"), str(source)
    )
    data = source.read_bytes()
    assert data.count(b"avc1") == 1
    path = folder / "unknown.mov"
    path.write_bytes(data.replace(b"avc1", b"zq9x").replace(b"ftyp", b"free", 1))
    return path


def sections(info, carrier):
    return [s for s in info.sections if s.carrier == carrier]


def fields(found):
    return {field.name: field.value for field in found.fields}


def test_quicktime_tags_streams_and_chapters_are_sections(files):
    info = metadata.read(files["mov"])
    (file_tags,) = sections(info, "QuickTime/MP4 tags")
    assert fields(file_tags)["com.apple.quicktime.make"] == "SASHIMI-PRIVACY-MARKER-make"
    streams = sections(info, "stream tags")
    assert [s.stream for s in streams][:2] == [0, 1]
    assert fields(streams[0])["handler_name"] == "SASHIMI-PRIVACY-MARKER-vh"
    assert streams[0].name.startswith("Stream 0 tags (video, h264")
    (chapters,) = sections(info, "chapters")
    assert "SASHIMI-PRIVACY-MARKER-chapter" in fields(chapters)["Chapter 1"]


def test_a_containers_creation_time_is_not_a_capture_date(files):
    info = metadata.read(files["remuxed mp4"])
    assert info.date_taken is None and info.creation_time == "2024-03-11T03:36:33.000000Z"
    tagged = metadata.read(files["mov"])
    assert tagged.date_taken is None and tagged.creation_time == samples.CREATED


def test_a_quicktime_capture_date_is_taken_with_its_offset(files):
    info = metadata.read(files["phone mov"])
    assert info.date_taken == "2024-09-06T18:08:07+0200" and info.date_taken_offset == "+02:00"
    assert info.creation_time.startswith("2024-09-06T16:08:10")


def test_encoder_and_handler_tags_are_listed_but_not_counted(files):
    info = metadata.read(files["plain mkv"])
    assert info.carriers == [] and not info.has_metadata
    names = {(detail.name, detail.stream) for detail in info.not_counted}
    assert ("writing application", None) in names and ("encoder", 0) in names and ("encoder", 1) in names
    writing = next(d for d in info.not_counted if d.name == "writing application")
    assert writing.value.startswith("Lavf")
    assert info.sections and all(not s.counted for s in info.sections)
    assert set(info.looked_for) >= {"Matroska tags", "stream tags", "chapters", "cover art"}


@pytest.mark.parametrize(("name", "family"), [("mp3", "ID3"), ("flac", "Vorbis comments"), ("mkv", "Matroska tags")])
def test_audio_and_matroska_tags_name_their_carrier(files, name, family):
    info = metadata.read(files[name])
    (found,) = sections(info, family)
    assert found.counted and family in info.carriers
    assert any("SASHIMI-PRIVACY-MARKER" in value for value in fields(found).values())


@pytest.mark.parametrize("name", ["mov", "mkv", "mp3", "flac", "plain mkv", "remuxed mp4", "phone mov", "undecodable"])
def test_every_media_kind_reads_bounded_sections_that_agree_with_its_carriers(files, name):
    info = metadata.read(files[name])
    assert {s.carrier for s in info.sections if s.counted} <= set(info.carriers)
    assert set(info.carriers) <= set(info.looked_for)
    for found in info.sections:
        for field in found.fields:
            assert isinstance(field.value, str) and len(field.value) <= MAX_VALUE_CHARS + 40
            assert not any(ord(c) < 32 and c not in "\t\n" for c in field.value)
    if info.date_taken is not None:
        stored = {f.value for s in info.sections for f in s.fields if f.name in CAPTURE_FIELDS}
        assert info.date_taken in stored
        assert info.date_taken != info.creation_time


@pytest.mark.parametrize("name", ["mov", "mkv", "mp3", "flac", "phone mov"])
def test_after_strip_nothing_counted_is_left(files, tmp_path, name):
    out = tmp_path / "out"
    metadata.strip(files[name], out, "remove_all")
    info = metadata.read(out)
    assert info.carriers == [] and all(not s.counted for s in info.sections)
    assert info.date_taken is None and samples.MARKER not in out.read_bytes()


def test_undecodable_video_is_known_up_front(files):
    info = sashimi.probe(files["undecodable"])
    assert info.has_video and not info.video_decodable and info.video_codec is None
    assert info.video_codec_tag == "zq9x"
    assert sashimi.probe(files["plain mkv"]).video_decodable


@pytest.mark.parametrize(
    "route",
    [
        lambda s, o: sheets.render(s, o / "sheet.jpg", JpegSheetConfig()),
        lambda s, o: video.convert(s, o / "out.mp4", VIDEO_CONVERT.from_params({}, target="mp4")),
        lambda s, o: video.convert(s, o / "out.gif", VIDEO_CONVERT.from_params({}, target="gif")),
        lambda s, o: core_preview.preview(s, o / "p.webp", VIDEO_PREVIEW.from_params({}, target="webp")),
        lambda s, o: video.transform(s, o / "r.mp4", VIDEO_TRANSFORM.from_params({"flip": "horizontal"}, target="mp4")),
        lambda s, o: video.trim(
            s, o / "t.mp4", VIDEO_TRIM.from_params({"start_seconds": 0.5, "container": "mp4", "accurate": True})
        ),
    ],
)
def test_routes_that_decode_the_picture_raise_undecodable_video_error(files, tmp_path, route):
    with pytest.raises(UndecodableVideoError) as caught:
        route(files["undecodable"], tmp_path)
    assert caught.value.codec_tag == "zq9x"
    assert "can't decode this video's picture" in caught.value.user_message and "zq9x" in caught.value.user_message
    assert "damaged" not in caught.value.user_message
    assert isinstance(caught.value, UnsupportedMediaError)
    assert not [p for p in tmp_path.iterdir() if not p.name.startswith(".")]


def test_copies_audio_and_strip_still_work_on_undecodable_video(files, tmp_path):
    source = files["undecodable"]
    video.trim(source, tmp_path / "cut.mov", VIDEO_TRIM.from_params({"container": "mov"}))
    assert sashimi.probe(tmp_path / "cut.mov").video_codec_tag == "zq9x"
    audio.convert(source, tmp_path / "sound.mp3", AUDIO_CONVERT.from_params({}, target="mp3"))
    assert sashimi.probe(tmp_path / "sound.mp3").has_audio
    result = metadata.strip(source, tmp_path / "stripped", "remove_all")
    assert result.format == "mov" and metadata.strip_format(source) == "mov"
    assert sashimi.probe(tmp_path / "stripped").video_codec_tag == "zq9x"


def test_a_failed_tail_read_is_noted_not_hidden(files, tmp_path, monkeypatch):
    real = core_video.run_ffmpeg

    def failing(cmd, *args, **kwargs):
        if "-read_intervals" in cmd and any(str(arg).endswith("%") and "+" not in str(arg) for arg in cmd):
            raise ProcessingError("ffprobe failed")
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(core_video, "run_ffmpeg", failing)
    info = sashimi.probe(files["phone mov"])
    assert core_video.hidden_tail(files["phone mov"], info) is None
    result = metadata.strip(files["phone mov"], tmp_path / "out.mov", "remove_location")
    assert core_video.TAIL_UNCHECKED in result.fidelity.notes
    copied = video.trim(files["phone mov"], tmp_path / "cut.mov", VIDEO_TRIM.from_params({"container": "mov"}))
    assert core_video.TAIL_UNCHECKED in copied.fidelity.notes


@pytest.mark.parametrize("command", [["thumbs"], ["preview"], ["convert", "mp4"]])
def test_the_cli_says_it_cant_decode_the_picture(files, tmp_path, command):
    from typer.testing import CliRunner

    from sashimi.cli.main import app

    source = tmp_path / "camera.mov"
    source.write_bytes(files["undecodable"].read_bytes())
    args = [command[0], str(source), *command[1:], "--output-dir", str(tmp_path / "out")]
    result = CliRunner().invoke(app, args, env={"COLUMNS": "300"})
    assert "can't decode this video's picture" in result.output and "zq9x" in result.output
    assert "damaged" not in result.output


@pytest.mark.parametrize(
    "route",
    [
        lambda s, o: video.trim(s, o / "cut.mov", VIDEO_TRIM.from_params({"container": "mov"})),
        lambda s, o: video.trim(s, o / "cut.mkv", VIDEO_TRIM.from_params({"start_seconds": 0, "container": "mkv"})),
        lambda s, o: video.convert(s, o / "copy.mp4", VIDEO_CONVERT.from_params({}, target="mp4")),
        lambda s, o: video.transform(s, o / "turned.mov", VIDEO_TRANSFORM.from_params({"rotate": 90}, target="mov")),
    ],
)
def test_a_failed_lead_read_is_noted_not_hidden(files, tmp_path, monkeypatch, route):
    real = core_video.run_ffmpeg

    def failing(cmd, *args, **kwargs):
        if any(str(arg).startswith("%+#") for arg in cmd):
            raise ProcessingError("ffprobe failed")
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(core_video, "run_ffmpeg", failing)
    assert core_video.hidden_lead(files["phone mov"]) is None
    result = route(files["phone mov"], tmp_path)
    assert core_video.LEAD_UNCHECKED in result.fidelity.notes


def test_a_readable_lead_adds_no_note(files, tmp_path):
    result = video.trim(files["phone mov"], tmp_path / "cut.mov", VIDEO_TRIM.from_params({"container": "mov"}))
    assert core_video.LEAD_UNCHECKED not in result.fidelity.notes
    assert core_video.hidden_lead(files["phone mov"]) == 0.0


def known_codec_without_decoder(folder):
    """A MOV whose video sample entry says EVC (``evc1``), a codec FFmpeg knows but decodes only with libxevd."""
    source = folder / "known.mov"
    ffmpeg(
        *clip("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-f", "mov"), str(source)
    )
    path = folder / "evc.mov"
    path.write_bytes(source.read_bytes().replace(b"avc1", b"evc1"))
    return path


def test_a_codec_this_ffmpeg_cant_decode_is_refused_by_name(files, tmp_path, monkeypatch):
    from sashimi.core import _capabilities

    monkeypatch.setattr(_capabilities, "decodable_codecs", lambda ffmpeg: frozenset({"aac"}))
    info = sashimi.probe(files["plain mkv"])
    assert info.video_codec == "h264" and not info.video_decodable
    with pytest.raises(UndecodableVideoError) as caught:
        sheets.render(files["plain mkv"], tmp_path / "sheet.jpg", JpegSheetConfig())
    assert caught.value.codec == "h264"
    assert "the FFmpeg it runs has no decoder for its codec, h264" in caught.value.user_message
    video.trim(files["plain mkv"], tmp_path / "copy.mkv", VIDEO_TRIM.from_params({"container": "mkv"}))


def test_an_unreadable_codec_listing_assumes_the_codec_decodes(files, monkeypatch):
    from sashimi.core import _capabilities

    monkeypatch.setattr(_capabilities, "decodable_codecs", lambda ffmpeg: None)
    assert sashimi.probe(files["plain mkv"]).video_decodable


def test_the_codec_listing_reads_decoding_flags():
    from sashimi.core import _capabilities

    listing = (
        "Codecs:\n D..... = Decoding supported\n -------\n DEV.LS h264  H.264\n ..V.L. evc  EVC\n .EVI.S ljpeg  LJ\n"
    )
    _capabilities.remember_listing("/nonexistent/ffmpeg", "-codecs", listing)
    assert _capabilities.decodable_codecs("/nonexistent/ffmpeg") == frozenset({"h264"})
    _capabilities.remember_listing("/nonexistent/ffmpeg", "-codecs", "")
    assert _capabilities.decodable_codecs("/nonexistent/ffmpeg") is None


def test_real_ffmpeg_without_an_evc_decoder_names_it(tmp_path):
    from sashimi.core._capabilities import decodable_codecs
    from sashimi.core._ffmpeg import ffmpeg_binary

    if "evc" in (decodable_codecs(ffmpeg_binary()) or {"evc"}):
        pytest.skip("this FFmpeg decodes EVC")
    source = known_codec_without_decoder(tmp_path)
    assert sashimi.probe(source).video_codec == "evc" and not sashimi.probe(source).video_decodable
    with pytest.raises(UndecodableVideoError) as caught:
        video.convert(source, tmp_path / "out.mp4", VIDEO_CONVERT.from_params({}, target="mp4"))
    assert "no decoder for its codec, evc (evc1)" in caught.value.user_message
    assert "damaged" not in caught.value.user_message
    metadata.strip(source, tmp_path / "stripped", "remove_all")
    audio.convert(source, tmp_path / "sound.mp3", AUDIO_CONVERT.from_params({}, target="mp3"))
