# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import json
import pathlib

import pytest
from typer.testing import CliRunner

from sashimi.cli.main import app
from sashimi.cli import settings as manager

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Keep the CLI away from the real ~/.sashimi and any project sashimi.toml."""
    monkeypatch.setattr(manager, "GLOBAL_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    manager.load_raw_config.cache_clear()
    yield
    manager.load_raw_config.cache_clear()


def test_missing_path_is_reported_in_json(tmp_path):
    result = runner.invoke(app, ["thumbs", "typo.mp4", "--json"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert payload["summary"]["errors"] == 1
    assert payload["results"][0]["message"] == "Path does not exist"


def test_missing_path_is_reported_in_one_line(tmp_path):
    result = runner.invoke(app, ["thumbs", "typo.mp4"])

    assert result.exit_code == 1
    assert result.stdout.strip() == "Error: typo.mp4: Path does not exist (0.0s)"


def test_non_video_file_is_reported(tmp_path):
    (tmp_path / "notes.txt").write_text("hi")

    result = runner.invoke(app, ["thumbs", "notes.txt", "--json"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["results"] == [
        {"file": "notes.txt", "status": "skipped", "message": "Unsupported file type", "output": None}
    ]


@pytest.mark.parametrize(
    "args, flag",
    [(["--width", "0"], "--width"), (["--cols", "0"], "--cols"), (["--rows", "abc"], "--rows")],
)
def test_invalid_options_exit_cleanly(tmp_path, args, flag):
    result = runner.invoke(app, ["thumbs", ".", *args])

    assert result.exit_code == 2
    assert f"Invalid value for {flag}" in result.stderr
    assert "Traceback" not in result.output


def test_auto_grid_is_case_insensitive(tmp_path):
    result = runner.invoke(app, ["thumbs", ".", "--cols", "AUTO", "--json"])

    assert result.exit_code == 0


def test_static_only_warns_about_animated_options(tmp_path):
    result = runner.invoke(app, ["thumbs", ".", "--fps", "10", "--no-loop", "--json"])

    assert "--fps, --no-loop only apply to animated (-a) sheets" in " ".join(result.stderr.split())


@pytest.mark.parametrize(
    "args, message",
    [
        (["-a", "--format", "jpeg"], "Animated contact sheets can be WebP or GIF."),
        (["--format", "gif"], "GIF sheets must be animated."),
    ],
)
def test_format_must_suit_the_mode(tmp_path, args, message):
    result = runner.invoke(app, ["thumbs", ".", *args])

    assert result.exit_code == 2 and message in " ".join(result.stderr.split())
