"""Real ffmpeg runs on generated media. Inputs are named original.blob; outputs have no meaningful suffix."""

import hashlib
import subprocess

import pytest
from PIL import Image

from sashimi import audio, probe, video
from sashimi.config import (
    AUDIO_CONVERT,
    VIDEO_CONVERT,
    VIDEO_TRIM,
    CopyTrimConfig,
    GifVideoConfig,
    Mp3AudioConfig,
    Mp4TransformConfig,
    Mp4VideoConfig,
    OggAudioConfig,
    WebpVideoConfig,
)
from sashimi.errors import InvalidOptionsError, NoAudioTrackError, UnsupportedMediaError

from tests.conftest import leftovers, needs_ffmpeg

pytestmark = needs_ffmpeg


def run_convert(src, out, target, **params):
    progress = []
    video.convert(
        src,
        out,
        VIDEO_CONVERT.from_params(params, target=target),
        max_workers=2,
        on_progress=lambda f, s: progress.append(f),
    )
    assert progress[-1] == 1.0 and progress == sorted(progress)
    return probe(out)


def test_gif(video_blob, tmp_path):
    info = run_convert(video_blob, tmp_path / "result.bin", "gif", duration_seconds=2, max_width=160)
    assert info.container == "gif" and info.width == 160
    assert info.duration == pytest.approx(2.0, abs=0.2)
    assert leftovers(tmp_path) == []


def test_animated_webp(video_blob, tmp_path):
    out = tmp_path / "result.bin"
    config = WebpVideoConfig(duration_seconds=2, max_width=160, fps=10)
    video.convert(video_blob, out, config, max_workers=2)
    with Image.open(out) as img:
        assert (img.format, img.size, img.n_frames, img.info["loop"]) == ("WEBP", (160, 90), 20, 0)
    assert leftovers(tmp_path) == []


def test_webm_keeps_audio_and_even_dimensions(video_blob, tmp_path):
    info = run_convert(video_blob, tmp_path / "result.bin", "webm")
    assert (info.video_codec, info.audio_codec) == ("vp9", "opus")
    assert (info.width, info.height) == (320, 180)
    assert info.duration == pytest.approx(6.0, abs=0.2)


def test_mp4_part_of_video(video_blob, tmp_path):
    info = run_convert(video_blob, tmp_path / "result.bin", "mp4", start_seconds=1, duration_seconds=2, max_width=128)
    assert (info.video_codec, info.audio_codec, info.width) == ("h264", "aac", 128)
    assert info.duration == pytest.approx(2.0, abs=0.2)


def test_animated_gif_to_mp4(gif_blob, tmp_path):
    info = run_convert(gif_blob, tmp_path / "result.bin", "mp4")
    assert info.video_codec == "h264" and not info.has_audio


def test_gif_output_is_deterministic(video_blob, tmp_path):
    config = GifVideoConfig(duration_seconds=1, max_width=96)
    for name in ("a", "b"):
        video.convert(video_blob, tmp_path / name, config, max_workers=2)
    digest = [hashlib.sha256((tmp_path / n).read_bytes()).hexdigest() for n in ("a", "b")]
    assert digest[0] == digest[1]


def test_user_errors(video_blob, junk_blob, tmp_path):
    with pytest.raises(UnsupportedMediaError) as e:
        video.convert(junk_blob, tmp_path / "r", Mp4VideoConfig())
    assert e.value.user_message == "This file doesn't look like a video we can read."
    with pytest.raises(InvalidOptionsError) as e:
        video.convert(video_blob, tmp_path / "r", GifVideoConfig(start_seconds=60))
    assert e.value.user_message == "The start time (60s) is past the end of the video (6s)."
    assert not (tmp_path / "r").exists() and leftovers(tmp_path) == []


def test_still_image_is_not_a_video(tmp_path):

    src = tmp_path / "original.blob"
    Image.new("RGB", (64, 64)).save(src, "PNG")
    with pytest.raises(UnsupportedMediaError):
        video.convert(src, tmp_path / "r", Mp4VideoConfig())


@pytest.mark.parametrize("container, demuxer", [("mkv", "matroska"), ("mp4", "mp4"), ("mov", "mov")])
def test_trim_is_lossless_stream_copy(video_blob, tmp_path, container, demuxer):
    out = tmp_path / "result.bin"
    video.trim(video_blob, out, CopyTrimConfig(container=container, start_seconds=2, duration_seconds=2))
    info = probe(out)
    assert demuxer in info.container_names
    assert (info.video_codec, info.audio_codec) == ("h264", "aac")
    assert info.duration == pytest.approx(2.0, abs=0.3)


@pytest.mark.parametrize("container", ["mkv", "mp4", "mov"])
def test_copied_trim_keeps_lead_in_from_keyframe(video_blob, tmp_path, container):
    out = tmp_path / "result.bin"
    video.trim(video_blob, out, CopyTrimConfig(container=container, start_seconds=2.6, end_seconds=4.6))
    assert probe(out).duration == pytest.approx(2.6, abs=0.15)
    ignore_editlist = [] if container == "mkv" else ["-ignore_editlist", "1"]
    edit_list_ignored = subprocess.run(
        ["ffprobe", "-v", "error", *ignore_editlist, "-show_entries", "format=duration", "-of", "csv=p=0", out],
        capture_output=True,
        text=True,
        check=True,
    )
    assert float(edit_list_ignored.stdout) == pytest.approx(2.6, abs=0.15)


@pytest.mark.parametrize("container", ["mkv", "mp4", "webm"])
def test_accurate_trim_is_exact(video_blob, tmp_path, container):
    out = tmp_path / "result.bin"
    progress = []
    config = VIDEO_TRIM.from_params({"start_seconds": 2.6, "end_seconds": 4.6, "accurate": True}, container=container)
    video.trim(video_blob, out, config, max_workers=1, on_progress=lambda f, s: progress.append(f))
    info = probe(out)
    assert info.duration == pytest.approx(2.0, abs=0.1)
    assert info.video_codec == ("vp9" if container == "webm" else "h264")
    assert progress[-1] == 1.0


@pytest.mark.parametrize(
    "container, message",
    [
        ("webm", "This video can't be trimmed to WEBM without re-encoding. Try MKV instead."),
    ],
)
def test_trim_refuses_containers_that_cannot_hold_the_streams(video_blob, tmp_path, container, message):
    with pytest.raises(InvalidOptionsError) as e:
        video.trim(video_blob, tmp_path / "r", CopyTrimConfig(container=container, duration_seconds=1))
    assert e.value.user_message == message
    assert leftovers(tmp_path) == [] and not (tmp_path / "r").exists()


def test_trim_container_check_reads_every_stream(tmp_path):
    src = tmp_path / "original.blob"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=160x120:r=10:d=2", "-c:v", "libx264",
         "-timecode", "00:00:00:00", "-f", "mov", str(src)],
        check=True,
    )  # fmt: skip
    info = probe(src)
    assert [s.type for s in info.streams] == ["video", "data"]
    with pytest.raises(InvalidOptionsError) as e:
        video.trim(src, tmp_path / "r", CopyTrimConfig(container="mkv"))
    assert e.value.user_message == "This video can't be trimmed to MKV without re-encoding. Try MOV instead."
    video.trim(src, tmp_path / "r", CopyTrimConfig(container="mov"))


def test_transform_rotates(video_blob, tmp_path):
    out = tmp_path / "result.bin"
    video.transform(video_blob, out, Mp4TransformConfig(rotate=90), max_workers=2)
    info = probe(out)
    assert (info.width, info.height) == (180, 320)


@pytest.mark.parametrize(
    "target, codec", [("mp3", "mp3"), ("m4a", "aac"), ("wav", "pcm_s16le"), ("ogg", "opus"), ("flac", "flac")]
)
def test_audio_from_video(video_blob, tmp_path, target, codec):
    out = tmp_path / "result.bin"
    audio.convert(video_blob, out, AUDIO_CONVERT.from_params({"duration_seconds": 2}, target=target), max_workers=2)
    info = probe(out)
    assert info.audio_codec == codec and not info.has_video
    assert info.duration == pytest.approx(2.0, abs=0.1)


def test_audio_to_audio_mono_ignores_cover_art(audio_blob, tmp_path):
    import json
    import subprocess

    out = tmp_path / "result.bin"
    audio.convert(audio_blob, out, OggAudioConfig(mono=True))
    streams = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)], capture_output=True, check=True
        ).stdout
    )["streams"]
    assert [(s["codec_type"], s["channels"]) for s in streams] == [("audio", 1)]


def test_video_without_audio(silent_video_blob, tmp_path):
    with pytest.raises(NoAudioTrackError) as e:
        audio.convert(silent_video_blob, tmp_path / "r", Mp3AudioConfig())
    assert e.value.user_message == "This video has no audio track."


def test_not_audio(junk_blob, tmp_path):
    with pytest.raises(UnsupportedMediaError) as e:
        audio.convert(junk_blob, tmp_path / "r", Mp3AudioConfig())
    assert e.value.user_message == "This file doesn't look like audio we can read."


def test_probe_real_files(video_blob, rotated_video_blob, audio_blob):
    v = probe(video_blob)
    assert "matroska" in v.container_names and (v.width, v.height) == (320, 180) and v.has_audio
    r = probe(rotated_video_blob)
    assert (r.width, r.height, r.rotation) == (180, 320, 90)
    a = probe(audio_blob)
    assert a.has_audio and not a.has_video and a.container == "mp3"
