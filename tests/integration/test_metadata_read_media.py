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
from sashimi.core import _capabilities as core_capabilities
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


@pytest.fixture
def without_h264(monkeypatch):
    """This FFmpeg, as if it had no decoder for H.264."""
    real = core_capabilities.decodable_codecs

    def lacking(binary: str) -> frozenset[str] | None:
        return (real(binary) or frozenset()) - {"h264"}

    monkeypatch.setattr(core_capabilities, "decodable_codecs", lacking)


@pytest.fixture(scope="module")
def plain_h264(tmp_path_factory):
    path = tmp_path_factory.mktemp("copy") / "plain.mp4"
    ffmpeg(*clip("-c:v", "libx264", "-preset", "ultrafast", "-g", "5", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)))
    return path


@pytest.mark.parametrize("params", [{}, {"mute": True}], ids=["sound", "mute"])
def test_a_copy_into_mp4_needs_no_decoder(tmp_path, plain_h264, without_h264, params):
    info = sashimi.probe(plain_h264)
    assert info.video_decodable is False
    output = tmp_path / "out.mp4"
    done = video.convert(plain_h264, output, VIDEO_CONVERT.from_params(target="mp4", **params))
    assert done.route == "copy" and output.stat().st_size > 0


def test_copying_the_video_and_encoding_the_sound_needs_no_decoder(tmp_path, without_h264):
    source = tmp_path / "opus.mkv"
    ffmpeg(*clip("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "libopus", str(source)))
    done = video.convert(source, tmp_path / "out.mp4", VIDEO_CONVERT.from_params(target="mp4"))
    assert done.route != "encode"


@pytest.mark.parametrize(
    "params",
    [
        {"target": "webm"},
        {"target": "gif"},
        {"target": "webp"},
        {"target": "mp4", "crf": 30},
        {"target": "mp4", "start_seconds": 0.5},
        {"target": "mp4", "max_width": 80},
    ],
    ids=lambda p: "-".join(str(v) for v in p.values()),
)
def test_a_route_that_decodes_is_still_refused_and_writes_nothing(tmp_path, plain_h264, without_h264, params):
    output = tmp_path / "out"
    with pytest.raises(UndecodableVideoError):
        video.convert(plain_h264, output, VIDEO_CONVERT.from_params(**params))
    assert not output.exists()


def test_a_copy_that_fails_refuses_rather_than_decoding(tmp_path, plain_h264, without_h264, monkeypatch):
    real = core_video.run_ffmpeg

    def copy_fails(cmd, **kwargs):
        if "copy" in cmd:
            raise ProcessingError("the copy failed")
        return real(cmd, **kwargs)

    monkeypatch.setattr(core_video, "run_ffmpeg", copy_fails)
    output = tmp_path / "out.mp4"
    with pytest.raises(UndecodableVideoError):
        video.convert(plain_h264, output, VIDEO_CONVERT.from_params(target="mp4"))
    assert not output.exists()


def test_a_cut_from_the_middle_says_nothing_of_a_lead_it_cant_show(tmp_path, plain_h264, monkeypatch):
    monkeypatch.setattr(core_video, "hidden_lead", lambda _path: None)
    cut = video.trim(
        plain_h264,
        tmp_path / "mid.mp4",
        VIDEO_TRIM.from_params(accurate=False, start_seconds=1, duration_seconds=0.5, container="mp4"),
    )
    assert core_video.LEAD_UNCHECKED not in cut.fidelity.notes
    whole = video.trim(
        plain_h264,
        tmp_path / "start.mp4",
        VIDEO_TRIM.from_params(accurate=False, duration_seconds=0.5, container="mp4"),
    )
    assert core_video.LEAD_UNCHECKED in whole.fidelity.notes


HOSTILE_TAGS = (
    *("-metadata", "title=\x1b]0;pwned\x07\x1b[31mred"),
    *("-metadata", "comment=" + "c\u202e" * 2000),
    *("-metadata", "location=+abc/"),
    *("-metadata", "creation_time=\x9b31m"),
    *("-metadata", "date_recorded=\x1b[2J"),
    *("-metadata", "com.apple.quicktime.location.ISO6709=+99999999999999999999.0+999999999999.0/"),
    *("-metadata", "com.apple.quicktime.camera.focal_length.35mm_equivalent=inf"),
    *("-metadata:s:v:0", "handler_name=\x1b[31mhandler"),
    *("-metadata:s:v:0", "rotate=abc"),
    *("-metadata:s:a:0", "language=\x1b[31"),
)


@pytest.fixture(scope="module", params=["mov", "mkv", "mp3", "flac"])
def hostile_media(request, tmp_path_factory):
    path = tmp_path_factory.mktemp("hostile") / f"tags.{request.param}"
    if request.param in ("mp3", "flac"):
        ffmpeg("-f", "lavfi", "-i", "sine=d=1", *HOSTILE_TAGS[:12], str(path))
    else:
        video_args = ("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac")
        extra = ("-movflags", "use_metadata_tags") if request.param == "mov" else ()
        ffmpeg(*clip(*video_args, *HOSTILE_TAGS, *extra, str(path)))
    return path


def test_hostile_tags_keep_a_reads_promises(hostile_media):
    from tests.core.test_hostile_metadata import assert_keeps_promises

    info = metadata.read(hostile_media)
    assert_keeps_promises(info)
    assert info.latitude is None or abs(info.latitude) <= 90
    assert info.longitude is None or abs(info.longitude) <= 180


@pytest.mark.parametrize("mode", ["remove_all", "remove_location"])
def test_hostile_tags_never_break_strip(tmp_path, hostile_media, mode):
    output = tmp_path / f"out{hostile_media.suffix}"
    done = metadata.strip(hostile_media, output, mode)
    assert output.exists() and done.fidelity is not None


def test_hostile_tags_never_break_a_conversion(tmp_path, hostile_media):
    if hostile_media.suffix in (".mp3", ".flac"):
        output = tmp_path / "out.wav"
        done = audio.convert(hostile_media, output, AUDIO_CONVERT.from_params(target="wav"))
    else:
        output = tmp_path / "out.webm"
        done = video.convert(hostile_media, output, VIDEO_CONVERT.from_params(target="mp4", crf=30))
    assert done.fidelity is not None


def test_many_tags_and_streams_stay_within_the_bound(tmp_path):
    from tests.core.test_hostile_metadata import READ_LIMIT

    source = tmp_path / "many.mkv"
    tags = [arg for number in range(400) for arg in ("-metadata", f"key{number}=" + "é" * 400)]
    ffmpeg("-f", "lavfi", "-i", "sine=d=1", *tags, str(source))
    info = metadata.read(source)
    assert len(info.model_dump_json().encode()) <= READ_LIMIT
    assert len(info.tags) + info.tags_omitted >= 400 and info.tags_omitted > 0


def test_ffprobe_output_of_the_wrong_shape_is_a_refusal(tmp_path, plain_h264, monkeypatch):
    from sashimi.core import probe as core_probe

    shaped = '{"format": {"format_name": "mov", "duration": "1"}, "streams": [{"index": "x", "codec_type": 7}]}'
    monkeypatch.setattr(core_probe, "run_ffmpeg", lambda *_args, **_kwargs: shaped.encode())
    copy = tmp_path / "copy.mp4"
    copy.write_bytes(plain_h264.read_bytes())
    with pytest.raises(UnsupportedMediaError):
        sashimi.probe(copy)


def test_a_damaged_gif_through_video_routes_is_only_ever_refused(tmp_path):
    import random

    from tests.core.test_hostile_metadata import BASES, damaged

    rng = random.Random("gif as video")
    path = tmp_path / "input.gif"
    for _ in range(12):
        path.write_bytes(damaged(BASES["gif"], rng))
        output = tmp_path / "out.mp4"
        try:
            done = video.convert(path, output, VIDEO_CONVERT.from_params(target="mp4", crf=30))
        except sashimi.SashimiError:
            assert not output.exists()
        else:
            assert done.fidelity is not None
        output.unlink(missing_ok=True)
