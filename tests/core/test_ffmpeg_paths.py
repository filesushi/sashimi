# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
A missing, unrunnable or wrong FFmpeg is reported as exactly that: the path, where it was chosen
(the SASHIMI_FFMPEG variable, ffmpeg.path in a sashimi.toml, set_ffmpeg_paths(), PATH) and how to
fix it, with the same message wherever it shows: ``info --ffmpeg`` (exit 1), every command (said
once per run), and the Python API (:class:`~sashimi.errors.FFmpegPathError`).
"""

import json
import os
import pathlib

import pytest
from typer.testing import CliRunner

import sashimi
from sashimi import audio, sheets, video
from sashimi.cli import settings
from sashimi.cli.main import app
from sashimi.config import AUDIO_CONVERT, CONTACT_SHEET, VIDEO_CONVERT, VIDEO_TRIM
from sashimi.core import _ffmpeg
from sashimi.errors import BinaryNotFoundError, FFmpegPathError, UserInputError
from tests.core.test_capabilities import fake_ffmpeg

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv(_ffmpeg.ENVIRONMENT, raising=False)
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    settings.load_raw_config.cache_clear()
    _ffmpeg.use_setting(None)
    yield tmp_path
    sashimi.set_ffmpeg_paths()
    _ffmpeg.use_setting(None)
    settings.load_raw_config.cache_clear()


def _said(result) -> str:
    return " ".join(result.output.split())


def _broken(tmp_path: pathlib.Path, problem: str) -> tuple[str, str]:
    """A path that is broken as `problem` says, as given to sashimi, and the executable named in the message."""
    folder = tmp_path / f"ffmpeg-{problem}"
    if problem == "missing":
        return str(folder / "ffmpeg"), str(folder / "ffmpeg")
    if problem == "folder":
        folder.mkdir()
        return str(folder), str(folder / "ffmpeg")
    if problem == "not_executable":
        fake = fake_ffmpeg(folder, "n8.1.3")
        fake.chmod(0o644)
        return str(fake), str(fake)
    if problem == "not_ffmpeg":
        folder.mkdir()
        for name in ("ffmpeg", "ffprobe"):
            (folder / name).write_text("#!/bin/sh\necho 'Python 3.14.0'\n")
            (folder / name).chmod(0o755)
        return str(folder / "ffmpeg"), str(folder / "ffmpeg")
    if problem == "ffprobe_missing":
        fake = fake_ffmpeg(folder, "n8.1.3")
        (folder / "ffprobe").unlink()
        return str(fake), str(folder / "ffprobe")
    raise AssertionError(problem)


KINDS = {
    "missing": "missing",
    "folder": "missing",
    "not_executable": "not_executable",
    "not_ffmpeg": "not_ffmpeg",
    "ffprobe_missing": "missing",
}
WORDS = {
    "missing": "doesn't exist",
    "folder": "doesn't exist",
    "not_executable": "isn't executable",
    "not_ffmpeg": "isn't FFmpeg's ffmpeg: running it with -version printed 'Python 3.14.0'",
    "ffprobe_missing": "doesn't exist. sashimi needs both ffmpeg and ffprobe",
}


def _choose(how: str, path: str, monkeypatch) -> str:
    """Points sashimi at `path` the way `how` says, and returns the origin messages name."""
    if how == "environment":
        monkeypatch.setenv(_ffmpeg.ENVIRONMENT, path)
        return "the SASHIMI_FFMPEG environment variable"
    if how == "setting":
        assert runner.invoke(app, ["config", "set", "ffmpeg.path", path]).exit_code == 0
        settings.load_raw_config.cache_clear()
        _ffmpeg.use_setting(settings.ffmpeg_setting)
        return f"ffmpeg.path in {settings.get_user_config_path()}"
    sashimi.set_ffmpeg_paths(path)
    return "sashimi.set_ffmpeg_paths()"


FIXES = {
    "environment": "unset it so sashimi uses ffmpeg.path",
    "setting": "sashimi config unset ffmpeg.path",
    "api": "call sashimi.set_ffmpeg_paths() with nothing",
}


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions and shell scripts")
@pytest.mark.parametrize("problem", list(KINDS))
@pytest.mark.parametrize("how", ["environment", "setting", "api"])
def test_a_broken_ffmpeg_is_named_with_its_origin_and_the_fix(tmp_path, monkeypatch, how, problem):
    given, named = _broken(tmp_path, problem)
    origin = _choose(how, given, monkeypatch)
    with pytest.raises(FFmpegPathError) as e:
        _ffmpeg.checked_paths()
    error = e.value
    assert (error.problem, error.path, error.origin) == (KINDS[problem], named, origin)
    assert error.name == ("ffprobe" if problem == "ffprobe_missing" else "ffmpeg")
    message = str(error)
    assert named in message and origin in message and WORDS[problem] in message and FIXES[how] in message
    assert "Cannot run" not in message and "Errno" not in message
    for call in (_ffmpeg.ffmpeg_binary, _ffmpeg.ffprobe_binary):
        with pytest.raises(FFmpegPathError) as again:
            call()
        assert str(again.value) == message


def test_no_ffmpeg_on_path_offers_every_way_to_point_at_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(FFmpegPathError) as e:
        sashimi.probe(tmp_path / "x.mp4")
    assert e.value.problem == "not_found" and e.value.origin == "PATH" and e.value.path is None
    message = str(e.value)
    assert message.startswith("No ffmpeg was found on PATH.")
    for fix in ("SASHIMI_FFMPEG", "sashimi config set ffmpeg.path", "sashimi.set_ffmpeg_paths()"):
        assert fix in message


def test_a_working_ffmpeg_given_as_a_folder_or_an_executable_passes(tmp_path, monkeypatch):
    fake = fake_ffmpeg(tmp_path / "ok", "n8.1.3")
    for how, given in (("environment", str(fake.parent)), ("setting", str(fake.parent)), ("api", str(fake))):
        monkeypatch.delenv(_ffmpeg.ENVIRONMENT, raising=False)
        sashimi.set_ffmpeg_paths()
        _choose(how, given, monkeypatch)
        paths = _ffmpeg.checked_paths()
        assert (paths.ffmpeg, paths.ffprobe) == (str(fake), str(fake.parent / "ffprobe")), how


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell scripts")
def test_the_check_runs_once_per_executable(tmp_path, monkeypatch):
    fake = fake_ffmpeg(tmp_path / "ok", "n8.1.3")
    sashimi.set_ffmpeg_paths(str(fake))
    runs = []
    real = _ffmpeg.subprocess.run

    def counting(cmd, **kwargs):
        runs.append(os.path.basename(cmd[0]))
        return real(cmd, **kwargs)

    monkeypatch.setattr(_ffmpeg.subprocess, "run", counting)
    for _ in range(5):
        _ffmpeg.checked_paths()
        _ffmpeg.ffprobe_binary()
        sashimi.ffmpeg_info()
    assert sorted(runs) == ["ffmpeg", "ffmpeg", "ffmpeg", "ffprobe"]


def test_an_ffmpeg_replaced_after_the_check_is_checked_again(tmp_path):
    folder = tmp_path / "bin"
    fake = fake_ffmpeg(folder, "n8.1.3")
    sashimi.set_ffmpeg_paths(str(fake))
    _ffmpeg.checked_paths()
    fake.write_text("#!/bin/sh\necho nothing to see here\n")
    with pytest.raises(FFmpegPathError) as e:
        _ffmpeg.checked_paths()
    assert e.value.problem == "not_ffmpeg"


MEDIA_CALLS = {
    "probe": lambda src, out: sashimi.probe(src),
    "video.convert": lambda src, out: video.convert(src, out, VIDEO_CONVERT.from_params({}, target="gif")),
    "video.trim": lambda src, out: video.trim(src, out, VIDEO_TRIM.from_params({"end_seconds": 1}, container="mkv")),
    "audio.convert": lambda src, out: audio.convert(src, out, AUDIO_CONVERT.from_params({}, target="mp3")),
    "sheets.render": lambda src, out: sheets.render(src, out, CONTACT_SHEET.from_params({})),
}


@pytest.mark.parametrize("name", list(MEDIA_CALLS))
def test_every_api_call_raises_the_same_named_error(tmp_path, name):
    given, named = _broken(tmp_path, "missing")
    sashimi.set_ffmpeg_paths(given)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00" * 1024)
    with pytest.raises(FFmpegPathError) as e:
        MEDIA_CALLS[name](src, tmp_path / "out")
    assert isinstance(e.value, BinaryNotFoundError) and not isinstance(e.value, UserInputError)
    assert str(e.value).startswith(f"{named}, from sashimi.set_ffmpeg_paths(), doesn't exist.")


COMMANDS = [
    ["thumbs", "clip.mp4"],
    ["preview", "clip.mp4"],
    ["convert", "clip.mp4", "other.mp4", "gif"],
    ["remux", "clip.mp4"],
    ["extract", "clip.mp4", "0", "1"],
    ["rotate", "clip.mp4", "90"],
    ["strip", "clip.mp4"],
    ["info", "clip.mp4"],
    ["metadata", "clip.mp4"],
    ["info", "--ffmpeg"],
]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions and shell scripts")
@pytest.mark.parametrize("problem", ["missing", "not_executable", "not_ffmpeg", "ffprobe_missing"])
@pytest.mark.parametrize("how", ["environment", "setting"])
@pytest.mark.parametrize("command", COMMANDS, ids=lambda c: "-".join(c[:2]))
def test_every_command_says_it_once_and_fails(tmp_path, monkeypatch, how, problem, command):
    for name in ("clip.mp4", "other.mp4"):
        (tmp_path / name).write_bytes(b"\x00" * 1024)
    given, named = _broken(tmp_path, problem)
    origin = _choose(how, given, monkeypatch)
    expected = str(_ffmpeg.path_error("ffprobe" if problem == "ffprobe_missing" else "ffmpeg"))
    result = runner.invoke(app, command)
    assert result.exit_code == 1, result.output
    said = _said(result)
    squeezed = "".join(result.output.split())
    assert squeezed.count("".join(expected.split())) == 1, said
    assert "".join(named.split()) in squeezed and "".join(origin.split()) in squeezed and "Cannot run" not in said
    assert "Version unknown" not in said and "Lacks" not in said


def test_info_ffmpeg_json_says_what_is_wrong(tmp_path, monkeypatch):
    given, named = _broken(tmp_path, "missing")
    monkeypatch.setenv(_ffmpeg.ENVIRONMENT, given)
    result = runner.invoke(app, ["info", "--ffmpeg", "--json"])
    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["usable"] is False and report["origin"] == "the SASHIMI_FFMPEG environment variable"
    assert report["problem"]["kind"] == "missing" and report["problem"]["path"] == named
    fake = fake_ffmpeg(tmp_path / "ok", "n8.1.3")
    monkeypatch.setenv(_ffmpeg.ENVIRONMENT, str(fake))
    good = runner.invoke(app, ["info", "--ffmpeg", "--json"])
    assert good.exit_code == 0 and json.loads(good.output)["usable"] is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell scripts")
def test_config_set_warns_about_a_path_that_cant_be_used(tmp_path, monkeypatch):
    missing = str(tmp_path / "nope" / "ffmpeg")
    result = runner.invoke(app, ["config", "set", "ffmpeg.path", missing])
    assert result.exit_code == 0
    said = _said(result)
    assert "Warning:" in said and missing in said and "doesn't exist" in said
    assert "sashimi config unset ffmpeg.path" in said
    fake = fake_ffmpeg(tmp_path / "ok", "n8.1.3")
    fine = runner.invoke(app, ["config", "set", "ffmpeg.path", str(fake)])
    assert fine.exit_code == 0 and "Warning" not in _said(fine)
    assert f"sashimi now runs {fake}" in _said(fine)
    monkeypatch.setenv(_ffmpeg.ENVIRONMENT, str(fake))
    shadowed = runner.invoke(app, ["config", "set", "ffmpeg.path", str(tmp_path / "other")])
    assert "comes before ffmpeg.path" in _said(shadowed)


def test_a_kept_setting_in_a_project_file_suggests_the_project_commands(tmp_path, monkeypatch):
    missing = str(tmp_path / "nope" / "ffmpeg")
    assert runner.invoke(app, ["config", "set", "--project", "ffmpeg.path", missing]).exit_code == 0
    settings.load_raw_config.cache_clear()
    result = runner.invoke(app, ["info", "--ffmpeg"])
    said = _said(result)
    assert result.exit_code == 1
    assert "sashimi config set --project ffmpeg.path" in said and "sashimi config unset --project ffmpeg.path" in said


def test_an_executable_named_directly_that_wont_start_is_still_reported(tmp_path):
    with pytest.raises(BinaryNotFoundError) as e:
        _ffmpeg.run_ffmpeg([str(tmp_path / "nowhere" / "ffmpeg"), "-version"])
    assert "Cannot run" in str(e.value)
