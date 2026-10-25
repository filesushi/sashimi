# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Recordings that were never finalized (a live-stream dump written to a pipe: no stated length,
no index, often a start time well past zero) work on every route, with their length counted
from the first frame as ``ffmpeg -ss`` counts it. So do finalized Matroska files whose
timestamps start late, whose stated length counts from zero.
"""

import json
import pathlib
import subprocess
from unittest.mock import patch

import pytest
from PIL import Image
from typer.testing import CliRunner

from sashimi import audio, probe, sheets, video
from sashimi.cli import settings
from sashimi.cli.main import app
from sashimi.config import (
    AUDIO_CONVERT,
    CONTACT_SHEET,
    VIDEO_CONVERT,
    VIDEO_TRIM,
    CopyTrimConfig,
    GifPreviewConfig,
    JpegSheetConfig,
    Mp4TransformConfig,
)
from sashimi.core.sheets import animated, static
from sashimi.errors import InvalidOptionsError

from tests.conftest import _ffmpeg as make_media, leftovers, needs_ffmpeg

pytestmark = needs_ffmpeg

SECONDS = 6
SOURCES = (
    *("-f", "lavfi", "-i", f"testsrc2=s=320x180:r=25:d={SECONDS}"),
    *("-f", "lavfi", "-i", f"sine=f=440:d={SECONDS}"),
)
H264 = ("-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-pix_fmt", "yuv420p", "-c:a", "aac")
OFFSETS = [0.0, 1.734, 100.0]


def _ffprobe(path: pathlib.Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def _piped(path: pathlib.Path, *args: str) -> pathlib.Path:
    with path.open("wb") as out:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *args, "pipe:1"], stdout=out, check=True
        )
    return path


def _length(path: pathlib.Path) -> float:
    return float(_ffprobe(path)["format"]["duration"])


@pytest.fixture(scope="module", params=OFFSETS, ids=lambda offset: f"start{offset:g}")
def capture(request, tmp_path_factory) -> pathlib.Path:
    """6 s of H.264 + AAC piped as Matroska from `offset` seconds: no length, no cues."""
    path = tmp_path_factory.mktemp("capture") / "original.blob"
    return _piped(path, *SOURCES, *H264, "-output_ts_offset", str(request.param), "-f", "matroska")


@pytest.fixture(scope="module")
def late_capture(tmp_path_factory) -> pathlib.Path:
    path = tmp_path_factory.mktemp("late") / "original.blob"
    return _piped(path, *SOURCES, *H264, "-output_ts_offset", "100", "-f", "matroska")


@pytest.fixture(scope="module")
def finalized_late(tmp_path_factory, late_capture) -> pathlib.Path:
    """The late capture copied into a seekable file with its timestamps kept: a stated length of 106 s."""
    path = tmp_path_factory.mktemp("finalized") / "original.blob"
    make_media("-copyts", "-i", str(late_capture), "-c", "copy", "-f", "matroska", str(path))
    return path


def test_the_fixtures_are_what_they_claim(capture, late_capture, finalized_late):
    data = _ffprobe(capture)
    assert "duration" not in data["format"]
    assert all("duration" not in s and "DURATION" not in s.get("tags", {}) for s in data["streams"])
    assert float(data["format"]["start_time"]) in OFFSETS
    assert float(_ffprobe(late_capture)["format"]["start_time"]) == 100
    stated = _ffprobe(finalized_late)["format"]
    assert float(stated["start_time"]) == 100 and float(stated["duration"]) > 105


def test_probe_measures_an_unfinalized_capture(capture, tmp_path):
    finalized = tmp_path / "finalized.mkv"
    make_media("-i", str(capture), "-c", "copy", str(finalized))
    duration = probe(capture).duration
    assert duration is not None and abs(duration - SECONDS) < 0.1
    assert abs(duration - _length(finalized)) < 0.05


@pytest.mark.parametrize(
    "codecs, fmt",
    [
        (("-c:v", "libvpx", "-b:v", "300k", "-c:a", "libopus"), "webm"),
        (("-vn", "-c:a", "libopus"), "matroska"),
    ],
    ids=["webm", "audio-only"],
)
def test_probe_measures_other_unfinalized_recordings(tmp_path, codecs, fmt):
    path = _piped(tmp_path / "original.blob", *SOURCES, *codecs, "-output_ts_offset", "100", "-f", fmt)
    assert "duration" not in _ffprobe(path)["format"]
    duration = probe(path).duration
    assert duration is not None and abs(duration - SECONDS) < 0.1


def test_probe_counts_a_finalized_late_matroska_from_its_first_frame(finalized_late):
    duration = probe(finalized_late).duration
    assert duration is not None and abs(duration - SECONDS) < 0.1


def _counting(module, name):
    real = getattr(module, name)
    failures = []

    def wrapper(*args):
        result = real(*args)
        if result is None:
            failures.append(args[0])
        return result

    return patch.object(module, name, wrapper), failures


def _check_static_sheet(source: pathlib.Path, tmp_path: pathlib.Path) -> None:
    patcher, failures = _counting(static, "_grab_frame")
    with patcher:
        sheets.render(source, tmp_path / "r", JpegSheetConfig(width=480, cols=4, rows=4))
    assert failures == []
    with Image.open(tmp_path / "r") as img:
        assert img.format == "JPEG"
    assert leftovers(tmp_path) == []


def test_static_sheet_fills_every_tile(capture, tmp_path):
    _check_static_sheet(capture, tmp_path)


def test_static_sheet_of_a_finalized_late_matroska_fills_every_tile(finalized_late, tmp_path):
    _check_static_sheet(finalized_late, tmp_path)


@pytest.mark.parametrize("target", ["gif", "webp"])
@pytest.mark.parametrize("source", ["late_capture", "finalized_late"])
def test_animated_sheet_fills_every_tile(request, tmp_path, source, target):
    params = {"animated": True, "width": 400, "cols": 3, "rows": 3, "sample_duration": 1, "fps": 5}
    patcher, failures = _counting(animated, "_grab_clip")
    with patcher:
        sheets.render(request.getfixturevalue(source), tmp_path / "r", CONTACT_SHEET.from_params(params, target=target))
    assert failures == []
    with Image.open(tmp_path / "r") as img:
        assert img.n_frames == 5


@pytest.mark.parametrize("source", ["late_capture", "finalized_late"])
def test_preview_takes_every_clip(request, tmp_path, source):
    config = GifPreviewConfig(samples=3, sample_duration=1, fps=10, start_offset_percent=0, end_offset_percent=0)
    video.preview(request.getfixturevalue(source), tmp_path / "p", config)
    with Image.open(tmp_path / "p") as img:
        assert img.n_frames == 30


@pytest.mark.parametrize(
    "target, params, route",
    [
        ("mp4", {}, "copy"),
        ("mp4", {"crf": 30}, "encode"),
        ("webm", {}, "encode"),
        ("gif", {"fps": 5, "max_width": 160}, None),
        ("webp", {"fps": 5, "max_width": 160}, None),
    ],
)
def test_convert_keeps_the_whole_recording(late_capture, tmp_path, target, params, route):
    out = tmp_path / "out"
    result = video.convert(late_capture, out, VIDEO_CONVERT.from_params(params, target=target), max_workers=2)
    if route is not None:
        assert result == route
    if target in ("gif", "webp"):
        with Image.open(out) as img:
            assert img.n_frames == SECONDS * 5
    else:
        assert abs(_length(out) - SECONDS) < 0.15


@pytest.mark.parametrize("target", ["mp4", "gif"])
def test_a_start_time_counts_from_the_first_frame(late_capture, tmp_path, target):
    out = tmp_path / "out"
    video.convert(late_capture, out, VIDEO_CONVERT.from_params({"start_seconds": 4}, target=target))
    assert abs(_length(out) - 2) < 0.15
    with pytest.raises(InvalidOptionsError, match="at or past the end"):
        video.convert(late_capture, tmp_path / "late", VIDEO_CONVERT.from_params({"start_seconds": 7}, target=target))


@pytest.mark.parametrize("target", ["mp3", "m4a"])
def test_audio_from_an_unfinalized_capture(late_capture, tmp_path, target):
    out = tmp_path / "out"
    audio.convert(late_capture, out, AUDIO_CONVERT.from_params({}, target=target), max_workers=1)
    assert abs(_length(out) - SECONDS) < 0.15


@pytest.mark.parametrize("accurate", [False, True])
def test_extract_from_an_unfinalized_capture(late_capture, tmp_path, accurate):
    out = tmp_path / "out"
    config = VIDEO_TRIM.from_params({"start_seconds": 2, "duration_seconds": 2, "accurate": accurate}, container="mp4")
    video.trim(late_capture, out, config, max_workers=1)
    assert abs(_length(out) - 2) < 0.6


def test_remux_and_rotate_an_unfinalized_capture(late_capture, tmp_path):
    video.trim(late_capture, tmp_path / "remuxed", CopyTrimConfig(container="mp4"))
    assert abs(_length(tmp_path / "remuxed") - SECONDS) < 0.15
    video.transform(late_capture, tmp_path / "rotated", Mp4TransformConfig(rotate=90), max_workers=2)
    assert abs(_length(tmp_path / "rotated") - SECONDS) < 0.15
    rotated = _ffprobe(tmp_path / "rotated")["streams"][0]
    assert (rotated["width"], rotated["height"]) == (180, 320)


def test_cli_thumbs_and_info_on_a_live_dump(late_capture, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    settings.load_raw_config.cache_clear()
    (work / "stream.mkv").write_bytes(late_capture.read_bytes())
    runner = CliRunner()
    result = runner.invoke(app, ["thumbs", "stream.mkv", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["summary"]["success"] == 1
    assert (work / "stream.mkv.sheet.jpg").exists()
    info = runner.invoke(app, ["info", "stream.mkv", "--json"])
    assert abs(json.loads(info.output)["duration"] - SECONDS) < 0.1
    settings.load_raw_config.cache_clear()
