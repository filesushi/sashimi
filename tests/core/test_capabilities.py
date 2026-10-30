# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Per-task checks of the FFmpeg sashimi runs, with stand-in executables that only describe
themselves: a task is refused only when an encoder or filter it uses is missing, or the release
is known to get it wrong; the refusal names what's missing, the FFmpeg (version, path, where it
was chosen) and what to do. ffmpeg and ffprobe are chosen as a pair, an explicit choice first.
No other warning is given for a version number.
"""

import json
import logging
import os
import pathlib
import stat

import pytest
from typer.testing import CliRunner

import sashimi
from sashimi.api._batch import FFMPEG_REFUSALS, FFMPEG_REFUSED, classify
from sashimi.cli import settings
from sashimi.cli.main import app
from sashimi.core import _capabilities, _ffmpeg
from sashimi.errors import BinaryNotFoundError, FFmpegTooOldError, MissingEncodersError, MissingFiltersError

runner = CliRunner()
EVERYTHING = ["libx264", "libx265", "libvpx-vp9", "libopus", "libmp3lame", "libwebp_anim", "libwebp", "aac", "gif"]
FILTERS = ["scale", "fps", "zscale", "tonemap", "sidedata", "palettegen", "paletteuse"]
EXE = ".exe" if os.name == "nt" else ""
runs_fakes = pytest.mark.skipif(
    os.name == "nt", reason="the stand-in ffmpeg is a POSIX shell script, which Windows can't run"
)


def fake_ffmpeg(folder: pathlib.Path, version: str, encoders=EVERYTHING, filters=FILTERS) -> pathlib.Path:
    """
    An ffmpeg (and ffprobe beside it) that answers -version, -encoders and -filters, and fails
    anything else: a shell script, named as FFmpeg's executables are on this platform (``ffmpeg.exe``
    on Windows, where it can be found and paired but not run).
    """
    folder.mkdir(parents=True, exist_ok=True)
    encoder_lines = "".join(f" V....D {name:20} {name}\\n" for name in encoders)
    filter_lines = "".join(f" .. {name:16} V->V       {name}\\n" for name in filters)
    for name in ("ffmpeg", "ffprobe"):
        path = folder / (name + EXE)
        path.write_text(
            "#!/bin/sh\n"
            f'case "$2" in\n'
            f"  -version) printf '{name} version {version} Copyright (c) 2000-2026\\n';;\n"
            f"  -encoders) printf 'Encoders:\\n V..... = Video\\n ------\\n{encoder_lines}';;\n"
            f"  -filters) printf 'Filters:\\n  T.. = Timeline support\\n  ------\\n{filter_lines}';;\n"
            "  *) exit 1;;\n"
            "esac\n"
        )
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return folder / ("ffmpeg" + EXE)


def ffprobe_beside(ffmpeg: pathlib.Path) -> str:
    """The ffprobe :func:`fake_ffmpeg` made beside `ffmpeg`."""
    return str(ffmpeg.with_name("ffprobe" + EXE))


def same_path(found: str | None, expected: str) -> bool:
    """True when `found` names `expected`, ignoring case where the file system does (Windows' PATH lookup says ``.EXE``)."""
    return found is not None and os.path.normcase(found) == os.path.normcase(expected)


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.delenv(_ffmpeg.ENVIRONMENT, raising=False)
    _ffmpeg.use_setting(None)
    yield
    sashimi.set_ffmpeg_paths()
    _ffmpeg.use_setting(None)


@runs_fakes
def test_a_command_needing_a_missing_encoder_is_refused_before_it_runs(tmp_path):
    ffmpeg = fake_ffmpeg(tmp_path / "bin", "n9.0.2", encoders=["aac", "gif"])
    sashimi.set_ffmpeg_paths(str(ffmpeg), origin="a test")
    with pytest.raises(MissingEncodersError) as e:
        _ffmpeg.run_ffmpeg([str(ffmpeg), "-i", "x", "-c:v", "libx264", "-c:a", "aac", "out.mp4"])
    message = str(e.value)
    assert "H.264 encoder (libx264)" in message and "n9.0.2" in message and str(ffmpeg) in message
    assert "from a test" in message and "SASHIMI_FFMPEG" in message and "set_ffmpeg_paths" in message
    assert e.value.missing == ["libx264"] and e.value.info.series == (9, 0)
    assert isinstance(e.value, BinaryNotFoundError)


@runs_fakes
def test_a_command_needing_a_missing_filter_is_refused(tmp_path):
    ffmpeg = fake_ffmpeg(tmp_path / "bin", "n8.1.3", filters=["scale", "fps"])
    with pytest.raises(MissingFiltersError) as e:
        _ffmpeg.run_ffmpeg([str(ffmpeg), "-i", "x", "-vf", "fps=10,split[a][b];[a]palettegen=stats_mode=diff", "o"])
    assert e.value.missing == ["palettegen"] and "GIFs from video" in str(e.value)


def test_commands_using_only_built_in_encoders_and_filters_are_not_checked(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(_capabilities, "_listing", lambda ffmpeg, option: calls.append(option) or "")
    assert _capabilities.needs(["ffmpeg", "-i", "x", "-c", "copy", "-vf", "scale=2:2", "o"]) == ([], [])
    _capabilities.check_command(["ffmpeg", "-i", "x", "-c", "copy", "-vf", "scale=2:2,fps=5", "-c:a", "aac", "o"])
    _capabilities.check_command(["ffprobe", "-i", "x"])
    assert calls == []


def test_an_ffmpeg_that_cant_be_run_is_reported_as_missing_not_as_lacking(tmp_path):
    missing = str(tmp_path / "nowhere" / "ffmpeg")
    with pytest.raises(BinaryNotFoundError) as e:
        _ffmpeg.run_ffmpeg([missing, "-i", "x", "-c:v", "libx264", "out.mp4"])
    assert not isinstance(e.value, MissingEncodersError) and "Cannot run" in str(e.value)


@runs_fakes
@pytest.mark.parametrize("version, refused", [("8.0.1-3ubuntu2", True), ("n8.1.3", False), ("n9.0.2", False)])
@pytest.mark.parametrize("task", ["lossless cuts", "start times"])
def test_only_releases_known_to_get_a_task_wrong_refuse_it(tmp_path, version, refused, task):
    ffmpeg = fake_ffmpeg(tmp_path / "bin", version)
    if refused:
        with pytest.raises(FFmpegTooOldError) as e:
            _capabilities.require_behaviour(task, str(ffmpeg))
        assert e.value.task == task and version in str(e.value) and "8.1" in str(e.value)
    else:
        _capabilities.require_behaviour(task, str(ffmpeg))


@runs_fakes
@pytest.mark.parametrize("version, status", [("n10.0", "newer"), ("N-123456-gabcdef", "unknown"), ("n9.0.2", "tested")])
def test_newer_and_unknown_releases_are_allowed_and_reported_once(tmp_path, caplog, version, status):
    ffmpeg = fake_ffmpeg(tmp_path / "bin", version)
    with caplog.at_level(logging.INFO, logger="sashimi"):
        for _ in range(3):
            info = _capabilities.ffmpeg_info(str(ffmpeg))
            _capabilities.require_behaviour("lossless cuts", str(ffmpeg))
    assert info.status == status and not info.refused
    reported = [r for r in caplog.records if str(ffmpeg) in r.getMessage()]
    assert len(reported) == (0 if status == "tested" else 1)
    assert all(r.levelno < logging.WARNING for r in caplog.records)


@runs_fakes
def test_each_listing_runs_once_per_executable(tmp_path, monkeypatch):
    ffmpeg = fake_ffmpeg(tmp_path / "bin", "n8.1.3")
    runs = []
    real = _capabilities.subprocess.run

    def counting(cmd, **kwargs):
        runs.append(cmd[-1])
        return real(cmd, **kwargs)

    monkeypatch.setattr(_capabilities.subprocess, "run", counting)
    for _ in range(5):
        _capabilities.ffmpeg_info(str(ffmpeg))
        _capabilities.check_command([str(ffmpeg), "-c:v", "libx264", "-vf", "zscale", "o"])
    assert sorted(runs) == ["-encoders", "-filters", "-version"]


def test_ffmpeg_and_ffprobe_are_chosen_as_a_pair(tmp_path, monkeypatch):
    first = fake_ffmpeg(tmp_path / "one", "n8.1.3")
    second = fake_ffmpeg(tmp_path / "two", "n9.0.2")
    monkeypatch.setenv("PATH", f"{first.parent}")
    found = _ffmpeg.ffmpeg_paths()
    assert same_path(found.ffmpeg, str(first)) and same_path(found.ffprobe, ffprobe_beside(first))
    assert (found.origin, found.fix, found.folder) == ("PATH", None, None)
    _ffmpeg.use_setting(lambda: (str(second), "ffmpeg.path in a test file"))
    assert _ffmpeg.ffmpeg_paths().ffprobe == ffprobe_beside(second)
    monkeypatch.setenv(_ffmpeg.ENVIRONMENT, str(second.parent))
    paths = _ffmpeg.ffmpeg_paths()
    assert (paths.ffmpeg, paths.ffprobe) == (str(second), ffprobe_beside(second))
    assert paths.origin == "the SASHIMI_FFMPEG environment variable"
    sashimi.set_ffmpeg_paths(str(first))
    paths = _ffmpeg.ffmpeg_paths()
    assert (paths.ffmpeg, paths.ffprobe, paths.origin) == (
        str(first),
        ffprobe_beside(first),
        "sashimi.set_ffmpeg_paths()",
    )


def test_the_batch_says_ffmpeg_refusals_once_when_asked(tmp_path):
    error = FFmpegTooOldError("FFmpeg 8.0 can't be trusted with lossless cuts.", task="lossless cuts")
    assert classify(error) == ("error", "Failed: FFmpeg 8.0 can't be trusted with lossless cuts.")
    gathered: dict[str, None] = {}
    token = FFMPEG_REFUSALS.set(gathered)
    try:
        assert classify(error) == ("error", FFMPEG_REFUSED)
        assert classify(error) == ("error", FFMPEG_REFUSED)
    finally:
        FFMPEG_REFUSALS.reset(token)
    assert list(gathered) == ["FFmpeg 8.0 can't be trusted with lossless cuts."]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    settings.load_raw_config.cache_clear()
    yield tmp_path
    settings.load_raw_config.cache_clear()


@runs_fakes
def test_info_shows_the_ffmpeg_and_a_kept_path_is_used(isolated, monkeypatch):
    old = fake_ffmpeg(isolated / "old", "8.0.1-3ubuntu2", encoders=["aac", "gif", "libx264"])
    assert runner.invoke(app, ["config", "set", "ffmpeg.path", str(old)]).exit_code == 0
    shown = runner.invoke(app, ["info", "--ffmpeg", "--json"])
    report = json.loads(shown.output)
    assert report["ffmpeg"] == str(old) and report["ffprobe"] == ffprobe_beside(old)
    assert report["origin"].startswith("ffmpeg.path in ") and report["status"] == "older"
    assert set(report["refused"]) == {"lossless cuts", "start times"}
    assert "HEVC encoder (libx265)" in report["missing"]
    newer = fake_ffmpeg(isolated / "new", "n10.0")
    monkeypatch.setenv(_ffmpeg.ENVIRONMENT, str(newer))
    table = " ".join(runner.invoke(app, ["info", "--ffmpeg"]).output.split())
    assert "the SASHIMI_FFMPEG environment variable" in table and "newer than the series" in table
    assert runner.invoke(app, ["info"]).exit_code != 0
