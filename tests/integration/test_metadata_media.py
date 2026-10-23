# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
The metadata rule for video and audio, pinned on the output's bytes and ffprobe's view: every
route keeps the source's tags by default, ``remove_location`` drops location tags only, and
``remove_all`` keeps only what playback needs (streams, languages, display rotation). Also the
lossless removal, whose decoded streams match the source's.
"""

import json
import subprocess

import pytest

from sashimi import audio, metadata, video
from sashimi.config import AUDIO_CONVERT, VIDEO_CONVERT, VIDEO_TRANSFORM, VIDEO_TRIM
from tests import metadata_samples as samples
from tests.conftest import needs_ffmpeg

pytestmark = needs_ffmpeg
MODES = ("keep", "remove_location", "remove_all")


@pytest.fixture(scope="module")
def tagged(tmp_path_factory):
    def folder(name):
        return tmp_path_factory.mktemp(name)

    mov = samples.tagged_mov(folder("mov"))
    return {
        "mov": mov,
        "mkv": samples.tagged_mkv(folder("mkv"), mov),
        "mp3": samples.tagged_mp3(folder("mp3")),
        "flac": samples.tagged_flac(folder("flac")),
    }


def ffprobe(path):
    cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json", str(path)]
    return json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)


def rotation(data):
    return [side.get("rotation") for s in data["streams"] for side in s.get("side_data_list", []) if "rotation" in side]


def check(path, mode, *, chapters=False, rotated=False, language=False):
    data = path.read_bytes()
    found = metadata.read(path)
    probed = ffprobe(path)
    if mode == "keep":
        assert samples.MARKER in data and found.has_location
    elif mode == "remove_location":
        assert samples.MARKER in data and not found.has_location and b"48.8584" not in data
        assert found.latitude is None
    else:
        assert samples.MARKER not in data and b"48.8584" not in data
        assert not found.has_metadata and not probed.get("chapters")
    if chapters and mode != "remove_all":
        assert len(probed["chapters"]) == 1
    if rotated:
        assert rotation(probed) == [90]
    if language:
        assert "fra" in [s.get("tags", {}).get("language") for s in probed["streams"]]


VIDEO_JOBS = {
    "mp4 copy": ("mov", lambda s, o, m: video.convert(s, o, VIDEO_CONVERT.from_params({"metadata": m}, target="mp4"))),
    "mp4 copy_video": (
        "mkv",
        lambda s, o, m: video.convert(s, o, VIDEO_CONVERT.from_params({"metadata": m}, target="mp4")),
    ),
    "mp4 encode": (
        "mov",
        lambda s, o, m: video.convert(s, o, VIDEO_CONVERT.from_params({"metadata": m, "crf": 30}, target="mp4")),
    ),
    "webm": ("mov", lambda s, o, m: video.convert(s, o, VIDEO_CONVERT.from_params({"metadata": m}, target="webm"))),
    "remux mkv": ("mov", lambda s, o, m: video.trim(s, o, VIDEO_TRIM.from_params({"metadata": m, "container": "mkv"}))),
    "remux mov": ("mov", lambda s, o, m: video.trim(s, o, VIDEO_TRIM.from_params({"metadata": m, "container": "mov"}))),
    "remux mp4": ("mkv", lambda s, o, m: video.trim(s, o, VIDEO_TRIM.from_params({"metadata": m, "container": "mp4"}))),
    "lossless cut": (
        "mkv",
        lambda s, o, m: video.trim(
            s, o, VIDEO_TRIM.from_params({"metadata": m, "container": "mkv", "end_seconds": 1.5})
        ),
    ),
    "accurate cut": (
        "mov",
        lambda s, o, m: video.trim(
            s, o, VIDEO_TRIM.from_params({"metadata": m, "container": "mp4", "accurate": True, "end_seconds": 1.5})
        ),
    ),
    "rotate": (
        "mov",
        lambda s, o, m: video.transform(s, o, VIDEO_TRANSFORM.from_params({"metadata": m, "rotate": 90})),
    ),
    "rotate webm": (
        "mov",
        lambda s, o, m: video.transform(
            s, o, VIDEO_TRANSFORM.from_params({"metadata": m, "flip": "horizontal"}, target="webm")
        ),
    ),
}
COPIES = {"mp4 copy", "mp4 copy_video", "remux mkv", "remux mov", "remux mp4", "lossless cut"}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("job", list(VIDEO_JOBS))
def test_every_video_route_follows_the_metadata_mode(tagged, tmp_path, job, mode):
    source, run = VIDEO_JOBS[job]
    out = tmp_path / "out"
    run(tagged[source], out, mode)
    check(out, mode, chapters=job != "lossless cut", rotated=job in COPIES, language=True)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("target", ["mp3", "m4a", "ogg", "flac"])
@pytest.mark.parametrize("source", ["mov", "mp3", "flac"])
def test_every_audio_route_follows_the_metadata_mode(tagged, tmp_path, source, target, mode):
    out = tmp_path / "out"
    audio.convert(tagged[source], out, AUDIO_CONVERT.from_params({"metadata": mode}, target=target))
    check(out, mode)


@pytest.mark.parametrize("mode", MODES)
def test_wav_keeps_titles_but_cannot_hold_location(tagged, tmp_path, mode):
    out = tmp_path / "out.wav"
    audio.convert(tagged["mov"], out, AUDIO_CONVERT.from_params({"metadata": mode}, target="wav"))
    data = out.read_bytes()
    assert (samples.MARKER in data) == (mode != "remove_all") and b"48.8584" not in data


def framemd5(path):
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:v?",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-f",
        "framemd5",
        "-",
    ]
    lines = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.splitlines()
    return [line.split(",")[-1] for line in lines if not line.startswith("#")]


@pytest.mark.parametrize("mode", ["remove_location", "remove_all"])
@pytest.mark.parametrize("source", ["mov", "mkv", "mp3", "flac"])
def test_strip_copies_every_stream_without_its_metadata(tagged, tmp_path, source, mode):
    out = tmp_path / "out"
    extension = metadata.strip(tagged[source], out, mode)
    assert extension == source
    assert framemd5(out) == framemd5(tagged[source])
    video_source = source in ("mov", "mkv")
    check(out, mode, chapters=video_source, rotated=video_source, language=video_source)


def test_read_reports_video_metadata(tagged):
    found = metadata.read(tagged["mov"])
    assert found.kind == "video" and found.has_location
    assert (found.latitude, found.longitude, found.altitude) == (48.8584, 2.2945, 35.0)
    assert found.title == "SASHIMI-PRIVACY-MARKER-title" and found.artist == "SASHIMI-PRIVACY-MARKER-artist"
    assert "QuickTime/MP4 tags" in found.carriers and "chapters" in found.carriers
    assert metadata.read(tagged["mp3"]).carriers == ["ID3"]
    assert metadata.read(tagged["flac"]).carriers == ["Vorbis comments"]


def test_build_strip_command_is_pure_and_copies(tagged):
    from sashimi import probe

    info = probe(tagged["mov"])
    cmd = metadata.build_strip_command("in", "out", "remove_all", source=info, muxer="mov", ffmpeg="ffmpeg")
    assert cmd[cmd.index("-c") + 1] == "copy" and "-map_metadata" in cmd and "-map_chapters" in cmd
