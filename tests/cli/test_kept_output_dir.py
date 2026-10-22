# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Where outputs go, as the CLI decides it: ``output_dir`` kept per command in sashimi.toml
(relative to where sashimi runs, or absolute), ``--output-dir`` and ``--no-output-dir`` for one
run, ``-o`` for one file, and folders at the start of a name pattern, made inside each input's
folder. Every processing command is run; processing is stubbed.
"""

import hashlib
import importlib
import json
import os
import pathlib

import pytest
from typer.testing import CliRunner

from sashimi.cli import settings
from sashimi.cli.main import app

runner = CliRunner()

FIXTURE = ("photo.jpg", "photo.png", "anim.gif", "clip.mp4", "clip.mkv", "song.mp3")

COMMANDS = {
    "convert": ["convert", "{path}", "webp", "-y"],
    "resize": ["resize", "{path}", "-w", "8"],
    "rotate": ["rotate", "{path}", "90", "-y"],
    "edit": ["edit", "{path}", "--rotate", "90"],
    "frames": ["frames", "{path}", "--speed", "2"],
    "favicon": ["favicon", "{path}"],
    "extract": ["extract", "{path}", "1", "2"],
    "remux": ["remux", "{path}"],
    "strip": ["strip", "{path}", "-y"],
    "thumbs": ["thumbs", "{path}"],
    "animated_thumbs": ["thumbs", "{path}", "-a"],
    "preview": ["preview", "{path}"],
}
"""Each command's arguments, by its sashimi.toml section."""


def made(src, dst, *args, **kwargs):
    pathlib.Path(dst).write_bytes(b"made from " + pathlib.Path(src).name.encode())


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
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
        ("sashimi.api.extract", "trim"),
        ("sashimi.api.remux", "trim"),
        ("sashimi.api.strip", "strip"),
        ("sashimi.api.thumbs", "render"),
        ("sashimi.api.preview", "preview"),
        ("sashimi.api.favicon", "favicon_pack"),
    ):
        monkeypatch.setattr(importlib.import_module(module), name, made)
    extract = importlib.import_module("sashimi.api.extract")
    monkeypatch.setattr(extract, "_clip_message", lambda config, source, output: "Extracted to {name}")
    yield
    settings.load_raw_config.cache_clear()


def media(folder, names=FIXTURE):
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(name.encode())
    return folder


def keep(section, key, value, where=None):
    settings.load_raw_config.cache_clear()
    path = (where or pathlib.Path.cwd()) / "sashimi.toml"
    text = path.read_text() if path.exists() else ""
    path.write_text(text + f"[{section}]\n{key} = {json.dumps(value)}\n")
    settings.load_raw_config.cache_clear()


def run(section, path, *extra, code=0):
    args = [str(path) if part == "{path}" else part for part in COMMANDS[section]]
    settings.load_raw_config.cache_clear()
    result = runner.invoke(app, [*args, *extra, "--json"])
    assert result.exit_code == code, result.output
    return json.loads(result.stdout) if result.stdout.strip() else None


def outputs(report):
    return sorted(pathlib.Path(r["output"]) for r in report["results"] if r["status"] == "success")


def snapshot(folder):
    return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path(folder).rglob("*") if p.is_file()}


@pytest.mark.parametrize("where", ["relative", "absolute"])
@pytest.mark.parametrize("section", sorted(COMMANDS))
def test_a_kept_output_dir_is_used_reruns_write_nothing_and_originals_are_kept(tmp_path, section, where):
    media(tmp_path / "files")
    media(tmp_path / "files" / "sub", ("photo.jpg", "clip.mp4", "anim.gif"))
    originals = snapshot(tmp_path / "files")
    folder = "out" if where == "relative" else str(tmp_path / "kept")
    keep(section, "output_dir", folder)
    first = run(section, "files", "-r")
    made_here = outputs(first)
    assert made_here, section
    expected = pathlib.Path(folder)
    assert all(path.is_relative_to(expected) for path in made_here), made_here
    before = snapshot(tmp_path)
    again = run(section, "files", "-r")
    assert snapshot(tmp_path) == before
    assert {r["status"] for r in again["results"]} <= {"exists", "skipped"}
    assert {k: v for k, v in snapshot(tmp_path / "files").items() if k in originals} == originals


@pytest.mark.parametrize("section", sorted(COMMANDS))
def test_a_kept_output_dir_isnt_searched_when_it_is_inside_the_search(tmp_path, section):
    media(tmp_path / "files")
    keep(section, "output_dir", "files/out")
    run(section, "files", "-r")
    before = snapshot(tmp_path)
    again = run(section, "files", "-r")
    assert snapshot(tmp_path) == before
    assert not [r for r in again["results"] if "/out/" in r["file"].replace(os.sep, "/")]
    assert all(r["status"] in ("exists", "skipped") for r in again["results"])


@pytest.mark.parametrize("section", sorted(COMMANDS))
def test_a_kept_folder_in_the_pattern_is_inside_each_inputs_folder_from_anywhere(tmp_path, section):
    media(tmp_path / "Show" / "S01")
    media(tmp_path / "Show" / "S02", ("photo.jpg", "clip.mp4", "anim.gif"))
    pattern = (
        "sheets/{input_name}.{output_ext}"
        if section in ("convert", "remux")
        else "sheets/{input_name}.{role}.{output_ext}"
    )
    keep(section, "output_name", pattern)
    first = run(section, "Show", "-r")
    assert first and outputs(first)
    for path in outputs(first):
        assert path.parent.name == "sheets" and path.parent.parent.name in ("S01", "S02"), path
    before = snapshot(tmp_path)
    assert {r["status"] for r in run(section, "Show", "-r")["results"]} <= {"exists", "skipped"}
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.chdir(elsewhere)
    keep(section, "output_name", pattern)
    assert {r["status"] for r in run(section, tmp_path / "Show" / "S01")["results"]} <= {"exists", "skipped"}
    named = tmp_path / "Show" / "S02" / NAMED.get(section, "photo.jpg")
    assert [r["status"] for r in run(section, named)["results"]] == ["exists"]
    os.chdir(tmp_path)
    assert {k: v for k, v in snapshot(tmp_path).items() if k in before} == before


NAMED = {
    "extract": "clip.mp4",
    "remux": "clip.mp4",
    "thumbs": "clip.mp4",
    "animated_thumbs": "clip.mp4",
    "preview": "clip.mp4",
    "frames": "anim.gif",
}
"""A file each command takes, named on its own."""


def test_the_flag_overrides_a_kept_folder_and_no_output_dir_writes_next_to_the_inputs(tmp_path):
    media(tmp_path / "videos", ("clip.mp4",))
    keep("thumbs", "output_dir", "sheets")
    assert outputs(run("thumbs", "videos")) == [pathlib.Path("sheets/clip.mp4.sheet.jpg")]
    assert outputs(run("thumbs", "videos", "--output-dir", "other")) == [pathlib.Path("other/clip.mp4.sheet.jpg")]
    assert outputs(run("thumbs", "videos", "--no-output-dir")) == [pathlib.Path("videos/clip.mp4.sheet.jpg")]
    both = runner.invoke(app, ["thumbs", "videos", "--output-dir", "x", "--no-output-dir"])
    assert both.exit_code == 2 and "not both" in both.stderr
    assert outputs(run("thumbs", "videos/clip.mp4", "-o", "one.jpg")) == [pathlib.Path("one.jpg")]


def test_a_kept_relative_folder_is_relative_to_where_sashimi_runs(tmp_path):
    media(tmp_path / "videos", ("clip.mp4",))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "sashimi.toml").write_text('[thumbs]\noutput_dir = "sheets"\n')
    run_from = tmp_path / "elsewhere"
    run_from.mkdir()
    os.chdir(run_from)
    assert outputs(run("thumbs", "../videos")) == [pathlib.Path("sheets/clip.mp4.sheet.jpg")]
    assert (run_from / "sheets" / "clip.mp4.sheet.jpg").exists()
    os.chdir(tmp_path / "videos")
    assert outputs(run("thumbs", ".")) == [pathlib.Path("sheets/clip.mp4.sheet.jpg")]
    assert (tmp_path / "videos" / "sheets" / "clip.mp4.sheet.jpg").exists()


def test_a_kept_folder_with_a_home_folder_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    media(tmp_path / "videos", ("clip.mp4",))
    keep("thumbs", "output_dir", "~/Sheets")
    assert outputs(run("thumbs", "videos")) == [tmp_path / "user" / "Sheets" / "clip.mp4.sheet.jpg"]


def test_the_owners_layout_with_sheets_in_the_pattern(tmp_path):
    videos = media(tmp_path / "New folder", ("Bluebeard-5.mkv", "Bluebeard-6.mkv", "Bluebeard-7.mkv"))
    assert runner.invoke(app, ["config", "set", "thumbs.output_name", "sheets/{input_name}.jpeg"]).exit_code == 0
    first = run("thumbs", "New folder", "-r")
    assert outputs(first) == [pathlib.Path(f"New folder/sheets/Bluebeard-{n}.mkv.jpeg") for n in (5, 6, 7)]
    assert run("thumbs", "New folder", "-r")["summary"]["exists"] == 3
    os.chdir(videos)
    assert run("thumbs", ".")["summary"]["exists"] == 3
    report = run("thumbs", "Bluebeard-5.mkv")
    assert report["results"][0]["message"] == "Output already exists: sheets/Bluebeard-5.mkv.jpeg"
    combined = run("thumbs", ".", "-r", "--output-dir", "../collected")
    assert outputs(combined) == [pathlib.Path(f"../collected/sheets/Bluebeard-{n}.mkv.jpeg") for n in (5, 6, 7)]


def test_config_keeps_shows_and_removes_an_output_dir(tmp_path):
    assert runner.invoke(app, ["config", "set", "thumbs.output_dir", "sheets"]).exit_code == 0
    assert runner.invoke(app, ["config", "set", "animated_thumbs.output_dir", "/srv/anim"]).exit_code == 0
    assert runner.invoke(app, ["config", "set", "rotate.output_dir", "./turned"]).exit_code == 0
    text = (tmp_path / "home" / "sashimi.toml").read_text()
    assert 'output_dir = "sheets"' in text and 'output_dir = "./turned"' in text
    shown = runner.invoke(app, ["config", "show", "--json"]).stdout
    assert json.loads(shown)["settings"]["animated_thumbs"] == {"output_dir": "/srv/anim"}
    settings.load_raw_config.cache_clear()
    help_text = " ".join(runner.invoke(app, ["rotate", "--help"], env={"COLUMNS": "200"}).stdout.split())
    assert "sashimi.toml: ./turned" in help_text
    assert runner.invoke(app, ["config", "unset", "thumbs.output_dir"]).exit_code == 0
    assert "sheets" not in (tmp_path / "home" / "sashimi.toml").read_text()
    refused = runner.invoke(app, ["config", "set", "rotate.output_dir", " "])
    assert refused.exit_code == 2 and "must be a folder" in refused.stderr


def test_a_broken_kept_folder_stops_its_own_command(tmp_path):
    media(tmp_path / "videos", ("clip.mp4",))
    (tmp_path / "sashimi.toml").write_text("[thumbs]\noutput_dir = 3\n")
    settings.load_raw_config.cache_clear()
    result = runner.invoke(app, ["thumbs", "videos"], env={"COLUMNS": "1000"})
    assert result.exit_code == 2
    assert f"output_dir in [thumbs] of {tmp_path / 'sashimi.toml'}" in " ".join(result.stderr.split())
    assert runner.invoke(app, ["resize", "videos", "-w", "8"]).exit_code == 0


def test_dry_run_shows_where_each_file_goes(tmp_path):
    media(tmp_path / "videos" / "sub", ("clip.mp4",))
    keep("thumbs", "output_dir", "sheets")
    result = runner.invoke(app, ["thumbs", "videos", "-r", "-n"], env={"COLUMNS": "200"})
    assert "Would write " + str(pathlib.Path("sheets/sub/clip.mp4.sheet.jpg")) in result.stdout


@pytest.mark.parametrize("section", sorted(COMMANDS))
def test_a_relative_kept_folder_points_at_a_pattern_that_works(tmp_path, section):
    import re
    import shlex

    said = " ".join(runner.invoke(app, ["config", "set", f"{section}.output_dir", "./sheets"]).stdout.split())
    suggested = re.search(r"put it in the name pattern: (sashimi config set .*)$", said)
    assert suggested, said
    words = shlex.split(suggested.group(1))
    assert words[-1].startswith("sheets/")
    assert runner.invoke(app, words[1:]).exit_code == 0
    assert (
        "put it in the name pattern"
        not in runner.invoke(app, ["config", "set", f"{section}.output_dir", "../up"]).stdout
    )
