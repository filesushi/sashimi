# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
A start time inside a file's probed length but after its last video frame (or its last sound)
is refused on every route that takes one, before anything is written, instead of "succeeding"
with an output that holds no picture (or no sound). A start one frame before the last still
works everywhere, stream copies and variable frame rates included.
"""

import json
import pathlib
import subprocess

import pytest
from typer.testing import CliRunner

from sashimi import audio, video
from sashimi.cli import settings
from sashimi.cli.main import app
from sashimi.config import AUDIO_CONVERT, VIDEO_CONVERT, VIDEO_TRIM
from sashimi.errors import InvalidOptionsError

from tests.conftest import _ffmpeg as make_media, needs_ffmpeg

pytestmark = needs_ffmpeg

H264 = ("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac")
VIDEO_TARGETS = ["mp4", "webm", "gif", "webp"]
TRIMS = [("mkv", False), ("mp4", False), ("mp4", True), ("webm", True)]
AUDIO_TARGETS = ["mp3", "m4a", "ogg", "wav", "flac"]
AFTER_LAST_FRAME = "The start time ({start:g}s) is after the video's last frame, so there's nothing to {action}."
AFTER_THE_AUDIO = "The start time ({start:g}s) is after the end of the audio, so there's nothing to convert."


def _clip(factory, name, *, picture, sound, timing="", options=()):
    path = factory.mktemp(name) / "original.blob"
    make_media(
        *("-f", "lavfi", "-i", f"testsrc2=s=160x90:r=30:d={picture}{timing}"),
        *("-f", "lavfi", "-i", f"sine=f=440:d={sound}"),
        *H264,
        *options,
        *("-f", "matroska", str(path)),
    )
    return path


@pytest.fixture(scope="module")
def five(tmp_path_factory):
    """5 s at 30 fps, H.264 + AAC, one keyframe: the last frame starts at 4.967 s, the audio ends at 5.02 s."""
    return _clip(tmp_path_factory, "five", picture=5, sound=5)


@pytest.fixture(scope="module")
def long_audio(tmp_path_factory):
    """3 s of picture and 5 s of sound: the last frame starts at 2.967 s."""
    return _clip(tmp_path_factory, "long-audio", picture=3, sound=5)


@pytest.fixture(scope="module")
def long_picture(tmp_path_factory):
    """5 s of picture and 3 s of sound."""
    return _clip(tmp_path_factory, "long-picture", picture=5, sound=3)


@pytest.fixture(scope="module")
def variable(tmp_path_factory):
    """Variable frame rate: 30 fps for 2 s, then 20 fps; the last frame starts at 7.45 s, the audio ends at 7.5 s."""
    return _clip(
        tmp_path_factory,
        "variable",
        picture=5,
        sound=7.5,
        timing=",setpts='if(lt(T,2),PTS,PTS*1.5)'",
        options=("-fps_mode", "vfr"),
    )


@pytest.fixture(scope="module")
def transport_stream(tmp_path_factory):
    """
    6 s at 25 fps with B-frames and a keyframe every 2 s, and 7 s of sound, in MPEG-TS (which
    starts at 1.4 s): ffmpeg 8.1 decodes no frame when it seeks to 5 s, though frames run to 5.96 s.
    """
    path = tmp_path_factory.mktemp("transport") / "original.blob"
    make_media(
        *("-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=6", "-f", "lavfi", "-i", "sine=f=440:d=7"),
        *("-c:v", "libx264", "-bf", "3", "-g", "50", "-pix_fmt", "yuv420p", "-c:a", "aac"),
        *("-f", "mpegts", str(path)),
    )
    return path


def packets(path, kind):
    probe = ("ffprobe", "-v", "error", "-select_streams", kind[0], "-count_packets")
    probe += ("-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path))
    out = subprocess.run(probe, capture_output=True, text=True, check=True).stdout.split()
    return sum(int(n) for n in out if n.isdigit())


def written(folder):
    return sorted(p.name for p in pathlib.Path(folder).iterdir())


def convert_video(src, out, target, start):
    return video.convert(src, out, VIDEO_CONVERT.from_params({"start_seconds": start}, target=target))


def trim(src, out, container, accurate, start):
    video.trim(src, out, VIDEO_TRIM.from_params({"start_seconds": start, "accurate": accurate}, container=container))


def convert_audio(src, out, target, start):
    audio.convert(src, out, AUDIO_CONVERT.from_params({"start_seconds": start}, target=target))


def refused(run, start, message, tmp_path):
    with pytest.raises(InvalidOptionsError) as e:
        run(tmp_path / "out")
    assert e.value.user_message == message.format(start=start, action="convert")
    assert written(tmp_path) == []


@pytest.mark.parametrize("target", VIDEO_TARGETS)
def test_a_video_starting_after_the_last_frame_is_refused(five, tmp_path, target):
    refused(lambda out: convert_video(five, out, target, 4.97), 4.97, AFTER_LAST_FRAME, tmp_path)


@pytest.mark.parametrize("container, accurate", TRIMS)
def test_a_trim_starting_after_the_last_frame_is_refused(five, tmp_path, container, accurate):
    with pytest.raises(InvalidOptionsError) as e:
        trim(five, tmp_path / "out", container, accurate, 4.97)
    assert e.value.user_message == AFTER_LAST_FRAME.format(start=4.97, action="cut")
    assert written(tmp_path) == []


@pytest.mark.parametrize("target", VIDEO_TARGETS)
def test_a_video_starting_where_only_the_audio_goes_on_is_refused(long_audio, tmp_path, target):
    refused(lambda out: convert_video(long_audio, out, target, 4), 4, AFTER_LAST_FRAME, tmp_path)


@pytest.mark.parametrize("container, accurate", TRIMS)
def test_a_trim_starting_where_only_the_audio_goes_on_is_refused(long_audio, tmp_path, container, accurate):
    with pytest.raises(InvalidOptionsError) as e:
        trim(long_audio, tmp_path / "out", container, accurate, 4)
    assert e.value.user_message == AFTER_LAST_FRAME.format(start=4, action="cut")
    assert written(tmp_path) == []


@pytest.mark.parametrize("target", AUDIO_TARGETS)
def test_audio_starting_after_the_sound_ends_is_refused(long_picture, tmp_path, target):
    refused(lambda out: convert_audio(long_picture, out, target, 4), 4, AFTER_THE_AUDIO, tmp_path)


@pytest.mark.parametrize("target", VIDEO_TARGETS)
def test_a_video_starting_after_the_last_frame_of_a_variable_frame_rate_is_refused(variable, tmp_path, target):
    refused(lambda out: convert_video(variable, out, target, 7.47), 7.47, AFTER_LAST_FRAME, tmp_path)


@pytest.mark.parametrize("target, mode", [("gif", "diff"), ("gif", "single"), ("webp", None)])
@pytest.mark.parametrize("clip, start", [("five", 4.96), ("variable", 7.43)])
def test_an_animation_starting_inside_the_last_frame_is_refused_not_failed(
    request, tmp_path, target, mode, clip, start
):
    params = {"start_seconds": start} | ({"palette_mode": mode} if mode else {})
    with pytest.raises(InvalidOptionsError) as e:
        video.convert(request.getfixturevalue(clip), tmp_path / "out", VIDEO_CONVERT.from_params(params, target=target))
    assert e.value.user_message == (
        f"The start time ({start:g}s) is too close to the end of the video: it leaves less than one frame at 12 fps, "
        "so there's nothing to convert."
    )
    assert written(tmp_path) == []
    video.convert(
        request.getfixturevalue(clip),
        tmp_path / "out.mp4",
        VIDEO_CONVERT.from_params({"start_seconds": start}, target="mp4"),
    )
    assert packets(tmp_path / "out.mp4", "video") == 1


@pytest.mark.parametrize("target", VIDEO_TARGETS)
@pytest.mark.parametrize("clip, start", [("five", 4.93), ("long_audio", 2.93), ("variable", 7.4)])
def test_a_start_one_frame_before_the_last_converts(request, tmp_path, target, clip, start):
    out = tmp_path / "out"
    convert_video(request.getfixturevalue(clip), out, target, start)
    if target in ("mp4", "webm"):
        assert packets(out, "video") >= 1
    else:
        assert out.stat().st_size > 0


@pytest.mark.parametrize("container, accurate", TRIMS)
@pytest.mark.parametrize("clip, start", [("five", 4.93), ("long_audio", 2.93), ("variable", 7.4)])
def test_a_trim_one_frame_before_the_last_works(request, tmp_path, container, accurate, clip, start):
    out = tmp_path / "out"
    trim(request.getfixturevalue(clip), out, container, accurate, start)
    assert packets(out, "video") >= 1


@pytest.mark.parametrize("target", AUDIO_TARGETS)
def test_audio_starting_after_the_last_frame_but_before_the_sound_ends_converts(five, long_audio, tmp_path, target):
    for clip, start in [(five, 4.97), (long_audio, 4)]:
        out = tmp_path / f"{start}.{target}"
        convert_audio(clip, out, target, start)
        assert packets(out, "audio") >= 1


def test_a_video_target_keeps_the_picture_when_only_the_audio_has_ended(long_picture, tmp_path):
    for target in ("mp4", "webm"):
        out = tmp_path / f"out.{target}"
        convert_video(long_picture, out, target, 4)
        assert packets(out, "video") >= 1 and packets(out, "audio") == 0


def test_the_cli_refuses_and_a_rerun_refuses_the_same_way(five, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    settings.load_raw_config.cache_clear()
    (work / "five.mkv").write_bytes(five.read_bytes())
    runner = CliRunner()
    for _ in range(2):
        result = runner.invoke(app, ["convert", "five.mkv", "mp4", "-s", "4.97", "--json"])
        assert result.exit_code == 1, result.output
        row = json.loads(result.stdout)["results"][0]
        assert row["status"] == "error"
        assert row["message"] == AFTER_LAST_FRAME.format(start=4.97, action="convert")
        assert written(work) == ["five.mkv"]
    settings.load_raw_config.cache_clear()


@pytest.mark.parametrize("target", VIDEO_TARGETS)
def test_a_start_ffmpeg_cannot_decode_from_is_refused_with_its_own_reason(transport_stream, tmp_path, target):
    out = tmp_path / "out"
    try:
        convert_video(transport_stream, out, target, 5)
    except InvalidOptionsError as e:
        assert e.user_message == (
            "No frame of the video could be read from the start time (5s), so there's nothing to convert. "
            "Try an earlier start."
        )
        assert written(tmp_path) == []
    else:
        assert target not in ("mp4", "webm") or packets(out, "video") >= 1
