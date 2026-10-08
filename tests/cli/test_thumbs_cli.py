import json
import pathlib

import pytest
from typer.testing import CliRunner

from sashimi.cli.main import app
from sashimi.config import manager

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


def test_missing_path_is_reported_in_table(tmp_path):
    result = runner.invoke(app, ["thumbs", "typo.mp4"])

    assert result.exit_code == 1
    assert "No valid files found" in result.stdout
    assert "Path does not exist" in result.stdout


def test_non_video_file_is_reported(tmp_path):
    (tmp_path / "notes.txt").write_text("hi")

    result = runner.invoke(app, ["thumbs", "notes.txt", "--json"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["results"] == [
        {"file": str(tmp_path.resolve() / "notes.txt"), "status": "skipped", "message": "Not a video file"}
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


@pytest.mark.parametrize(
    "args, expected",
    [
        (["--fps", "10", "--no-loop"], "--fps, --no-loop only apply to animated (-a) mode"),
        (["-a", "--quality", "50"], "--quality only applies to static mode"),
    ],
)
def test_mode_specific_options_warn_when_ignored(tmp_path, args, expected):
    result = runner.invoke(app, ["thumbs", ".", *args, "--json"])

    assert expected in " ".join(result.stderr.split())
