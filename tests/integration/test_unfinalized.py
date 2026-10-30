# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Recordings that were never finalized (a live-stream dump written to a pipe: no stated length,
no index, often a start time well past zero) work on every route, with their length counted
from the first frame as ``ffmpeg -ss`` counts it. So do finalized Matroska files whose
timestamps start late, measured from their last packets: muxers disagree about what such a
file's stated length means.
"""

import json
import pathlib
import re
import struct
import subprocess
from unittest.mock import patch

import pytest
from PIL import Image
from typer.testing import CliRunner

from sashimi import audio, metadata, probe, sheets, video
from sashimi.api.extract import extract
from sashimi.api.thumbs import make_thumbs
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
    Limits,
    Mp4TransformConfig,
)
from sashimi.core import probe as probe_module
from sashimi.core.sheets import animated, static
from sashimi.errors import InvalidOptionsError, MediaTooLargeError

from tests.conftest import made_by_current_ffmpeg, needs_trusted_cuts, _ffmpeg as make_media, leftovers, needs_ffmpeg

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


@made_by_current_ffmpeg
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
        assert result.route == route
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
    rotated = probe(tmp_path / "rotated")
    assert (rotated.width, rotated.height, rotated.display_turn) == (180, 320, 90)
    video.transform(late_capture, tmp_path / "turned", Mp4TransformConfig(rotate=90, reencode=True), max_workers=2)
    turned = _ffprobe(tmp_path / "turned")["streams"][0]
    assert (turned["width"], turned["height"]) == (180, 320)


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


def _muxed_by_another_app(source: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    """`source` with every ``Lavf…`` app name (MuxingApp, WritingApp, the ENCODER tag) rewritten in place."""
    data = re.sub(rb"Lavf[0-9.]*", lambda m: (b"mkvmerge v88.0 " * 4)[: len(m.group())], source.read_bytes())
    path.write_bytes(data)
    return path


def test_a_late_matroska_is_measured_whoever_wrote_it(finalized_late, tmp_path):
    other = _muxed_by_another_app(finalized_late, tmp_path / "original.blob")
    stated = _ffprobe(other)["format"]
    assert stated["tags"]["ENCODER"].startswith("mkvmerge")
    assert float(stated["duration"]) == pytest.approx(SECONDS + 100, abs=0.1)
    assert probe(other).duration == pytest.approx(SECONDS, abs=0.1)
    assert probe(finalized_late).duration == pytest.approx(SECONDS, abs=0.1)


def _stated_as_a_span(source: pathlib.Path, path: pathlib.Path, span: float) -> pathlib.Path:
    """`source` with its Matroska Duration (8-byte float, milliseconds) rewritten to `span`, as mkvmerge writes it."""
    data = source.read_bytes()
    at = data.find(b"\x44\x89\x88")
    assert at > 0
    path.write_bytes(data[: at + 3] + struct.pack(">d", span * 1000) + data[at + 11 :])
    return path


@pytest.fixture(scope="module")
def remuxed_span(tmp_path_factory) -> pathlib.Path:
    """
    A file shaped like mkvmerge's remux of an ffmpeg file starting at 2 s: the ``ENCODER=Lavf…``
    tag kept, but the stated length the span (10 s) rather than the end time (12 s).
    """
    folder = tmp_path_factory.mktemp("span")
    late = folder / "late.mkv"
    make_media(
        *("-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=10", "-f", "lavfi", "-i", "sine=d=10"),
        *("-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-c:a", "aac", "-output_ts_offset", "2"),
        *("-f", "matroska", str(late)),
    )
    return _stated_as_a_span(late, folder / "original.blob", 10.031)


@made_by_current_ffmpeg
def test_a_late_matroska_stating_its_span_is_not_shortened(remuxed_span, tmp_path):
    stated = _ffprobe(remuxed_span)["format"]
    assert stated["tags"]["ENCODER"].startswith("Lavf") and float(stated["start_time"]) == 2.0
    assert float(stated["duration"]) == pytest.approx(10.031)
    assert probe(remuxed_span).duration == pytest.approx(10.0, abs=0.05)
    out = tmp_path / "out.mp4"
    video.convert(remuxed_span, out, VIDEO_CONVERT.from_params({}, target="mp4"))
    assert _length(out) == pytest.approx(10.0, abs=0.1)


def test_a_late_matroska_whose_end_cant_be_reached_keeps_its_stated_length(finalized_late):
    stated = float(_ffprobe(finalized_late)["format"]["duration"])
    probe_module._ends.clear()
    assert probe(finalized_late, limits=Limits(max_scan_packets=10)).duration == pytest.approx(stated)
    probe_module._ends.clear()


@pytest.fixture(scope="module")
def dense_capture(tmp_path_factory) -> pathlib.Path:
    """An unfinalized capture of 2 s at 1000 fps: 2,000 tiny packets."""
    path = tmp_path_factory.mktemp("dense") / "original.blob"
    lavfi = ("-f", "lavfi", "-i", "color=s=32x32:r=1000:d=2")
    return _piped(path, *lavfi, "-c:v", "libx264", "-preset", "ultrafast", "-f", "matroska")


def _refused(call) -> None:
    with pytest.raises(MediaTooLargeError) as e:
        call()
    assert e.value.limit == "max_scan_packets"
    assert "more than 1,000 audio and video packets" in e.value.user_message


def test_every_route_honours_the_scan_limit(dense_capture, tmp_path):
    limits = Limits(max_scan_packets=1000)
    assert probe(dense_capture, limits=Limits(max_scan_packets=2000)).duration == pytest.approx(2.0, abs=0.01)
    probe_module._scans.clear()
    _refused(lambda: probe(dense_capture, limits=limits))
    sheet = CONTACT_SHEET.from_params({"cols": 2, "rows": 1}, target="jpeg")
    _refused(lambda: sheets.render(dense_capture, tmp_path / "s", sheet, limits=limits))
    gif = VIDEO_CONVERT.from_params({}, target="gif")
    _refused(lambda: video.convert(dense_capture, tmp_path / "g", gif, limits=limits))
    _refused(lambda: video.trim(dense_capture, tmp_path / "t", CopyTrimConfig(container="mkv"), limits=limits))
    _refused(lambda: video.preview(dense_capture, tmp_path / "p", GifPreviewConfig(), limits=limits))
    _refused(lambda: metadata.strip(dense_capture, tmp_path / "m", limits=limits))
    assert not list(tmp_path.iterdir())


@needs_trusted_cuts
def test_a_file_is_measured_once_per_call(late_capture, tmp_path, monkeypatch):
    probe_module._scans.clear()
    work = tmp_path / "work"
    work.mkdir()
    clip = work / "stream.mkv"
    clip.write_bytes(late_capture.read_bytes())
    real = probe_module._scan_packets
    scans = []

    def counting(path, data, cap):
        scans.append(path)
        return real(path, data, cap)

    monkeypatch.setattr(probe_module, "_scan_packets", counting)
    assert extract(clip, start="1s", show_progress=False)["summary"]["success"] == 1
    assert make_thumbs(clip, show_progress=False)["summary"]["success"] == 1
    video.convert(clip, tmp_path / "out", VIDEO_CONVERT.from_params({}, target="gif"))
    assert len(scans) == 1
    clip.write_bytes(late_capture.read_bytes() + b"\0")
    assert abs(probe(clip).duration - SECONDS) < 0.1
    assert len(scans) == 2
    probe_module._scans.clear()


def test_cli_refuses_past_the_kept_scan_limit(dense_capture, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", home)
    (home / "sashimi.toml").write_text("[limits]\nmax_scan_packets = 1000\n")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    settings.load_raw_config.cache_clear()
    probe_module._scans.clear()
    (work / "dense.mkv").write_bytes(dense_capture.read_bytes())
    runner = CliRunner()
    result = runner.invoke(app, ["thumbs", "dense.mkv", "--json"])
    assert result.exit_code == 1, result.output
    (row,) = json.loads(result.output)["results"]
    assert row["status"] == "error" and "more than 1,000 audio and video packets" in row["message"]
    assert row["message"].endswith(
        "To raise the limit (now 1000), run: sashimi config set limits.max_scan_packets 2000 "
        "(or any higher value, or none for no limit)."
    )
    info = runner.invoke(app, ["info", "dense.mkv"], env={"COLUMNS": "1000"})
    assert info.exit_code == 1 and "more than 1,000 audio and video packets" in info.output
    assert "sashimi config set limits.max_scan_packets" in info.output
    (home / "sashimi.toml").write_text("[limits]\nmax_scan_packets = 2500\n")
    settings.load_raw_config.cache_clear()
    assert runner.invoke(app, ["info", "dense.mkv"]).exit_code == 0
    settings.load_raw_config.cache_clear()
