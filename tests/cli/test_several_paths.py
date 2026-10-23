# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Commands given several files, as a shell glob gives them: the plan is the plan of their folder,
reruns write nothing (the glob now names the earlier outputs too), and a folder run followed by
a glob run writes nothing. The folder also holds files and folders named like formats and
angles. Processing is stubbed.
"""

import glob
import hashlib
import importlib
import json
import pathlib

import pytest
from typer.testing import CliRunner

from sashimi.cli import settings
from sashimi.cli.main import app

runner = CliRunner()

FIXTURE = ("photo.jpg", "photo.png", "IMG 01.JPEG", "anim.gif", "anim.webp", "clip.mp4", "clip.mkv", "song.mp3")

COMMANDS = {
    "resize": (["resize"], ["-w", "8"]),
    "convert_webp": (["convert"], ["webp", "-y"]),
    "convert_jpeg": (["convert"], ["jpeg", "-y"]),
    "convert_mp3": (["convert"], ["mp3", "-y"]),
    "rotate": (["rotate"], ["-y"]),
    "rotate_180": (["rotate"], ["180", "-y"]),
    "rotate_png": (["rotate"], ["270", "--output-format", "png"]),
    "edit": (["edit"], ["--rotate", "90"]),
    "frames": (["frames"], ["--speed", "2"]),
    "favicon": (["favicon"], []),
    "remux": (["remux"], []),
    "strip": (["strip"], ["-y"]),
    "thumbs": (["thumbs"], []),
    "preview": (["preview"], []),
}
"""Each command's words before the paths, and after them."""


def made(src, dst, *args, **kwargs):
    pathlib.Path(dst).write_bytes(b"made from " + pathlib.Path(src).name.encode())


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    folder = tmp_path / "files"
    folder.mkdir()
    for name in FIXTURE:
        (folder / name).write_bytes(name.encode())
    (folder / "jpeg").write_bytes(b"a file named like a format")
    for name in ("webp", "90"):
        (folder / name).mkdir()
    monkeypatch.chdir(folder)
    settings.load_raw_config.cache_clear()
    for module, name in (
        ("sashimi.core.image", "convert"),
        ("sashimi.core.image", "to_ico"),
        ("sashimi.core.video", "convert"),
        ("sashimi.core.audio", "convert"),
        ("sashimi.core.edit", "edit"),
        ("sashimi.core.edit", "select_frames"),
        ("sashimi.api.resize", "convert"),
        ("sashimi.api.transform", "transform"),
        ("sashimi.api.remux", "trim"),
        ("sashimi.api.strip", "strip"),
        ("sashimi.api.thumbs", "render"),
        ("sashimi.api.preview", "preview"),
        ("sashimi.api.favicon", "favicon_pack"),
    ):
        monkeypatch.setattr(importlib.import_module(module), name, made)
    yield
    settings.load_raw_config.cache_clear()


def run(command, paths, *extra):
    before, after = COMMANDS[command]
    result = runner.invoke(app, [*before, *paths, *after, *extra, "--json"])
    assert result.exit_code in (0, 1), result.output
    return json.loads(result.stdout)


def planned(report):
    return {r["file"]: (r["status"], r["output"]) for r in report["results"] if r["status"] in ("success", "exists")}


def globbed():
    """``*.*``, as a shell expands it: every file with an extension, outputs included."""
    return sorted(glob.glob("*.*"))


def snapshot():
    root = pathlib.Path.cwd()
    return {p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_several_files_plan_as_their_folder(command):
    folder = planned(run(command, ["."], "-n"))
    assert folder, command
    files = planned(run(command, globbed(), "-n"))
    assert files == folder


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_rerunning_a_glob_writes_nothing(command):
    first = run(command, globbed())
    assert first["summary"]["success"], command
    before = snapshot()
    again = run(command, globbed())
    assert snapshot() == before, command
    assert again["summary"]["success"] == 0, again["results"]
    assert {r["file"] for r in first["results"] if r["status"] == "success"} == {
        r["file"] for r in again["results"] if r["status"] == "exists"
    }


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_a_glob_after_a_folder_run_writes_nothing(command):
    run(command, ["."])
    before = snapshot()
    assert run(command, globbed())["summary"]["success"] == 0
    assert snapshot() == before
    assert run(command, ["."])["summary"]["success"] == 0
