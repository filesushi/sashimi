# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Every audio and video extension sashimi picks up works through the commands that take its kind,
from the CLI, the way it was found: ``sashimi convert a.mka mp3`` said "Can't convert .mka to MP3"
in 0.6.0, although ffmpeg reads Matroska audio.
"""

import json
import pathlib
import subprocess

import pytest
from typer.testing import CliRunner

from sashimi.api._kinds import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS, kind_of
from sashimi.api.strip import STRIP_EXTENSIONS
from sashimi.cli import settings
from sashimi.cli.main import app

from tests.conftest import _ffmpeg as make_media, _has_encoder, needs_ffmpeg

pytestmark = needs_ffmpeg

runner = CliRunner()

VIDEO = ("-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=3")
AUDIO = ("-f", "lavfi", "-i", "sine=f=440:d=3")
H264 = ("-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-pix_fmt", "yuv420p")

NEW = {
    ".mka": (*AUDIO, "-c:a", "aac", "-f", "matroska"),
    ".m4b": (*AUDIO, "-c:a", "aac", "-f", "ipod"),
    ".mp2": (*AUDIO, "-c:a", "mp2", "-f", "mp2"),
    ".weba": (*AUDIO, "-c:a", "libvorbis", "-f", "webm"),
    ".mk3d": (*VIDEO, *AUDIO, *H264, "-c:a", "aac", "-f", "matroska"),
    ".m2ts": (*VIDEO, *AUDIO, *H264, "-c:a", "aac", "-f", "mpegts", "-mpegts_m2ts_mode", "1"),
    ".mts": (*VIDEO, *AUDIO, *H264, "-c:a", "aac", "-f", "mpegts", "-mpegts_m2ts_mode", "1"),
    ".3g2": (*VIDEO, *AUDIO, *H264, "-c:a", "aac", "-f", "3g2"),
    ".vob": (*VIDEO, *AUDIO, "-c:v", "mpeg2video", "-c:a", "mp2", "-f", "vob"),
}
"""The extensions added in 0.7.0, each with an ffmpeg recipe for a small file of that kind."""
STRIPPED = {".mka", ".m4b", ".weba", ".mk3d"}
"""The new extensions ``strip`` takes: the ones written by a muxer it keeps the format with."""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    settings.load_raw_config.cache_clear()
    yield
    settings.load_raw_config.cache_clear()


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, pathlib.Path]:
    folder = tmp_path_factory.mktemp("media")
    made = {}
    for ext, recipe in NEW.items():
        path = folder / f"clip{ext}"
        make_media(*recipe, str(path))
        made[ext] = path
    return made


def run(*args: str) -> dict:
    result = runner.invoke(app, [*args, "--json"], env={"COLUMNS": "1000"})
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def statuses(report: dict) -> list[str]:
    return [row["status"] for row in report["results"]]


def copy(source: pathlib.Path, folder: pathlib.Path, name: str) -> pathlib.Path:
    folder.mkdir(exist_ok=True)
    target = folder / name
    target.write_bytes(source.read_bytes())
    return target


def test_the_new_extensions_are_listed_once_with_their_kind():
    for ext in NEW:
        assert (VIDEO_EXTENSIONS + AUDIO_EXTENSIONS).count(ext) == 1
        assert kind_of(pathlib.PurePath(f"x{ext.upper()}")) == (
            "audio" if ext in (".mka", ".m4b", ".mp2", ".weba") else "video"
        )
    assert STRIPPED <= set(STRIP_EXTENSIONS)


@pytest.mark.parametrize("ext", NEW)
def test_convert_to_audio_and_info(media, tmp_path, ext):
    source = copy(media[ext], tmp_path / "in", f"a{ext}")
    assert statuses(run("convert", str(source), "mp3")) == ["success"]
    assert (source.parent / f"a{ext}.mp3").is_file()
    info = runner.invoke(app, ["info", str(source), "--json"])
    assert info.exit_code == 0, info.output
    assert abs(json.loads(info.stdout)["duration"] - 3) < 0.2


@pytest.mark.parametrize("ext", [ext for ext in NEW if kind_of(pathlib.PurePath(f"x{ext}")) == "video"])
def test_video_routes(media, tmp_path, ext):
    source = copy(media[ext], tmp_path / "in", f"v{ext}")
    assert statuses(run("convert", str(source), "mp4")) == ["success"]
    assert statuses(run("thumbs", str(source), "--cols", "2", "--rows", "1")) == ["success"]
    assert statuses(run("extract", str(source), "1s", "2s")) == ["success"]
    assert (source.parent / f"v{ext}.extracted.mkv").is_file()
    assert statuses(run("extract", str(source), "1s", "--output-name", "{input_name}.end.{output_ext}")) == ["success"]
    assert statuses(run("remux", str(source))) == ["success"]
    assert abs(_length(source.parent / f"v{ext}.mkv") - 3) < 0.2


@pytest.mark.parametrize("ext", sorted(STRIPPED))
def test_strip_keeps_the_format_and_extension(media, tmp_path, ext):
    source = copy(media[ext], tmp_path / "in", f"s{ext}")
    assert statuses(run("strip", str(source))) == ["success"]
    assert (source.parent / f"s{ext}.stripped{ext}").is_file()


@pytest.mark.parametrize("ext", sorted(set(NEW) - STRIPPED))
def test_strip_says_which_formats_it_takes(media, tmp_path, ext):
    source = copy(media[ext], tmp_path / "in", f"s{ext}")
    result = runner.invoke(app, ["strip", str(source), "--json"], env={"COLUMNS": "1000"})
    (row,) = json.loads(result.stdout)["results"]
    assert row["status"] == "skipped"
    assert row["message"] == (
        "strip works on JPEG, PNG, WebP, GIF and TIFF images and MKV, WebM, MP4, MOV, M4A, MP3, FLAC, OGG and WAV "
        f"files, not {ext} files."
    )


def test_audio_files_are_not_cut_by_extract(media, tmp_path):
    source = copy(media[".mka"], tmp_path / "in", "a.mka")
    result = runner.invoke(app, ["extract", str(source), "1s", "--json"])
    (row,) = json.loads(result.stdout)["results"]
    assert (row["status"], row["message"]) == ("skipped", "extract works on videos, not .mka files.")


def test_a_mixed_folder_is_converted_once_and_then_exists(media, tmp_path):
    folder = tmp_path / "mixed"
    for ext, path in media.items():
        copy(path, folder, f"clip{ext}")
    copy(media[".mka"], folder, "song.MKA")
    first = run("convert", "mixed", "mp3", "-y")
    assert statuses(first) == ["success"] * (len(NEW) + 1)
    again = run("convert", "mixed", "mp3", "-y")
    assert statuses(again) == ["exists"] * (len(NEW) + 1)
    assert sorted(p.name for p in folder.iterdir() if p.suffix == ".mp3") == sorted(
        [f"clip{ext}.mp3" for ext in NEW] + ["song.MKA.mp3"]
    )


def _length(path: pathlib.Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout)


MPEG_PS = {
    ".mpg": (*VIDEO, *AUDIO, "-c:v", "mpeg2video", "-c:a", "mp2", "-f", "vob"),
    ".mpeg": (*VIDEO, *AUDIO, "-c:v", "mpeg2video", "-c:a", "mp2", "-f", "mpeg"),
    ".vob": (*VIDEO, *AUDIO, "-c:v", "mpeg2video", "-c:a", "mp2", "-f", "vob"),
}


@pytest.mark.parametrize("ext", sorted(MPEG_PS))
def test_mpeg_program_streams_are_copied(tmp_path, ext):
    source = tmp_path / "in" / f"dvd{ext}"
    source.parent.mkdir()
    make_media(*MPEG_PS[ext], str(source))
    assert statuses(run("remux", str(source))) == ["success"]
    assert abs(_length(source.parent / f"dvd{ext}.mkv") - 3) < 0.2
    assert statuses(run("extract", str(source), "1s")) == ["success"]
    assert statuses(run("extract", str(source), "1s", "2s", "-o", str(tmp_path / "cut.mp4"))) == ["success"]


SINE = (*AUDIO, "-ac", "2")
MORE_AUDIO = {
    ".aiff": ((*SINE, "-c:a", "pcm_s16be", "-f", "aiff"), "pcm_s16be"),
    ".aif": ((*SINE, "-c:a", "pcm_s16be", "-f", "aiff"), "pcm_s16be"),
    ".ac3": ((*SINE, "-c:a", "ac3", "-f", "ac3"), "ac3"),
    ".eac3": ((*SINE, "-c:a", "eac3", "-f", "eac3"), "eac3"),
    ".dts": ((*SINE, "-ar", "48000", "-c:a", "dca", "-strict", "-2", "-f", "dts"), "dca"),
    ".amr": (
        (*AUDIO, "-ar", "8000", "-ac", "1", "-c:a", "libopencore_amrnb", "-b:a", "12.2k", "-f", "amr"),
        "libopencore_amrnb",
    ),
    ".caf": ((*SINE, "-c:a", "alac", "-f", "caf"), "alac"),
    ".au": ((*SINE, "-c:a", "pcm_s16be", "-f", "au"), "pcm_s16be"),
    ".wv": ((*SINE, "-c:a", "wavpack", "-f", "wv"), "wavpack"),
    ".wma": ((*SINE, "-c:a", "wmav2", "-f", "asf"), "wmav2"),
    ".m4r": ((*SINE, "-c:a", "aac", "-f", "ipod"), "aac"),
}
"""Audio formats added in this round: a recipe each, and the encoder it needs."""
MORE_VIDEO = {
    ".ogv": ((*VIDEO, *AUDIO, "-c:v", "libtheora", "-g", "25", "-c:a", "libvorbis", "-f", "ogg"), "libtheora"),
    ".divx": ((*VIDEO, *AUDIO, "-c:v", "mpeg4", "-vtag", "DIVX", "-c:a", "libmp3lame", "-f", "avi"), "mpeg4"),
    ".f4v": ((*VIDEO, *AUDIO, *H264, "-c:a", "aac", "-f", "f4v"), "libx264"),
    ".y4m": ((*VIDEO, "-pix_fmt", "yuv420p", "-f", "yuv4mpegpipe"), "rawvideo"),
}
"""Video formats added in this round."""


def stripped(source: pathlib.Path) -> str:
    result = runner.invoke(app, ["strip", str(source), "--json"], env={"COLUMNS": "1000"})
    (row,) = json.loads(result.stdout)["results"]
    return row["status"]


def _made(tmp_path: pathlib.Path, ext: str, table: dict) -> pathlib.Path:
    recipe, encoder = table[ext]
    if not _has_encoder(encoder):
        pytest.skip(f"this ffmpeg has no {encoder} encoder")
    path = tmp_path / "in" / f"m{ext}"
    path.parent.mkdir(exist_ok=True)
    make_media(*recipe, str(path))
    return path


def test_the_added_formats_are_listed_once_with_their_kind():
    for ext in MORE_AUDIO:
        assert (VIDEO_EXTENSIONS + AUDIO_EXTENSIONS).count(ext) == 1 and kind_of(pathlib.PurePath(f"x{ext}")) == "audio"
    for ext in MORE_VIDEO:
        assert (VIDEO_EXTENSIONS + AUDIO_EXTENSIONS).count(ext) == 1 and kind_of(pathlib.PurePath(f"x{ext}")) == "video"
    assert {".m4r", ".f4v"} <= set(STRIP_EXTENSIONS)
    assert not {".rm", ".rmvb", ".ape"} & set(VIDEO_EXTENSIONS + AUDIO_EXTENSIONS)


@pytest.mark.parametrize("ext", sorted(MORE_AUDIO))
def test_added_audio_formats_convert_whole(tmp_path, ext):
    source = _made(tmp_path, ext, MORE_AUDIO)
    for target in ("mp3", "flac"):
        assert statuses(run("convert", str(source), target)) == ["success"]
        assert abs(_length(source.parent / f"m{ext}.{target}") - 3) < 0.15
    info = runner.invoke(app, ["info", str(source), "--json"])
    assert info.exit_code == 0 and abs(json.loads(info.stdout)["duration"] - 3) < 0.25
    assert stripped(source) == ("success" if ext == ".m4r" else "skipped")


@pytest.mark.parametrize("ext", sorted(MORE_VIDEO))
def test_added_video_formats_take_every_video_route(tmp_path, ext):
    source = _made(tmp_path, ext, MORE_VIDEO)
    for target in ("mp4", "gif"):
        assert statuses(run("convert", str(source), target)) == ["success"]
    assert statuses(run("thumbs", str(source), "--cols", "2", "--rows", "1")) == ["success"]
    assert statuses(run("preview", str(source), "--samples", "1", "--clip-length", "1")) == ["success"]
    assert statuses(run("extract", str(source), "1s", "2s")) == ["success"]
    assert statuses(run("extract", str(source), "1s", "--accurate", "-o", str(tmp_path / "acc.mp4"))) == ["success"]
    assert statuses(run("remux", str(source))) == ["success"]
    assert statuses(run("rotate", str(source), "90")) == ["success"]
    assert stripped(source) == ("success" if ext == ".f4v" else "skipped")
