# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""The CLI's shape: version, help on bare commands, and the commands added in 0.4."""

import json

import pytest
from PIL import Image
from typer.testing import CliRunner

from sashimi import __version__
from sashimi.cli import settings
from sashimi.cli.main import app

from tests.conftest import needs_ffmpeg

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "GLOBAL_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    settings.load_raw_config.cache_clear()
    yield
    settings.load_raw_config.cache_clear()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and result.stdout.strip() == f"sashimi {__version__}"


@pytest.mark.parametrize(
    "command",
    [
        [],
        ["convert"],
        ["resize"],
        ["rotate"],
        ["extract"],
        ["thumbs"],
        ["info"],
        ["flatten"],
        ["config"],
        ["config", "set"],
    ],
)
def test_bare_commands_show_help(command):
    result = runner.invoke(app, command)
    assert "Usage:" in result.output


def test_help_lists_command_groups():
    output = runner.invoke(app, ["--help"]).output
    for panel in ("Convert", "Contact sheets", "Inspect", "Files", "Configuration"):
        assert panel in output
    assert f"Powered by sashimi v{__version__}" in output


def test_config_set_show_unset(tmp_path):
    assert json.loads(runner.invoke(app, ["config", "show", "--json"]).stdout)["settings"].get("thumbs") is None
    assert not (tmp_path / "home").exists()
    assert runner.invoke(app, ["config", "set", "thumbs.cols", "4"]).exit_code == 0
    shown = json.loads(runner.invoke(app, ["config", "show", "--json"]).stdout)
    assert shown["settings"]["thumbs"]["cols"] == 4
    bad = runner.invoke(app, ["config", "set", "thumbs.static_width", "10"])
    assert bad.exit_code == 2 and "greater than or equal to 320" in bad.stderr
    assert runner.invoke(app, ["config", "set", "thumbs.nope", "1"]).exit_code == 2
    assert runner.invoke(app, ["config", "unset", "thumbs.cols"]).exit_code == 0
    assert "cols" not in (tmp_path / "sashimi.toml").read_text()


def test_unknown_settings_warn(tmp_path):
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "sashimi.toml").write_text("[thumbs]\noutput_width = 2560\nquality = 80\n")
    (tmp_path / "sashimi.toml").write_text("[thumbs]\ncols = 4\n[animated_thumbs]\nspeed = 2\n")
    result = runner.invoke(app, ["config", "show"], env={"COLUMNS": "300"})
    warnings = [line for line in result.stderr.splitlines() if "Warning" in line]
    assert len(warnings) == 2
    assert "thumbs.output_width; they're ignored" in warnings[0] and "unset -g thumbs.output_width" in warnings[0]
    assert "animated_thumbs.speed;" in warnings[1] and "unset animated_thumbs.speed" in warnings[1]
    assert "Warning" not in runner.invoke(app, ["thumbs", "--help"]).stderr


def test_resize_command(tmp_path):
    Image.new("RGB", (400, 200), "red").save(tmp_path / "a.jpg")
    result = runner.invoke(app, ["resize", "a.jpg", "--width", "100", "--json"])
    assert result.exit_code == 0, result.output
    with Image.open(tmp_path / "a_resized.jpg") as img:
        assert img.size == (100, 50)


def test_ignored_options_warn(tmp_path):
    Image.new("RGB", (10, 10)).save(tmp_path / "a.png")
    result = runner.invoke(app, ["convert", "a.png", "jpeg", "--crf", "20", "--mono", "--json"])
    assert result.exit_code == 0
    warnings = " ".join(result.stderr.split())
    assert "--crf, --mono don't apply to JPEG output; ignored." in warnings


def test_options_for_another_image_format_warn(tmp_path):
    Image.new("RGB", (10, 10)).save(tmp_path / "a.png")
    result = runner.invoke(app, ["convert", "a.png", "jpeg", "--lossless", "--compress-level", "9", "--json"])
    assert result.exit_code == 0
    assert "--lossless, --compress-level don't apply to JPEG output" in " ".join(result.stderr.split())


def test_resize_needs_a_size_and_keeps_each_format(tmp_path):
    assert runner.invoke(app, ["resize", "."]).exit_code == 2
    Image.new("RGB", (400, 200), "red").save(tmp_path / "a.png")
    Image.new("RGB", (400, 200), "red").save(tmp_path / "b.jpeg")
    result = runner.invoke(app, ["resize", ".", "--width", "100", "-q", "50", "--json"])
    assert result.exit_code == 0, result.output
    assert "--quality doesn't apply to PNG images" in " ".join(result.stderr.split())
    with Image.open(tmp_path / "a_resized.png") as png, Image.open(tmp_path / "b_resized.jpeg") as jpeg:
        assert (png.format, png.size, jpeg.format) == ("PNG", (100, 50), "JPEG")


def test_rotate_needs_something_to_do(tmp_path):
    result = runner.invoke(app, ["rotate", ".", "0"])
    assert result.exit_code == 2 and "Choose a rotation or a flip." in result.stderr


@needs_ffmpeg
def test_convert_video_and_audio(video_blob, tmp_path):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    gif = runner.invoke(
        app, ["convert", "clip.mkv", "gif", "--duration", "2s", "--width", "160", "--fps", "8", "--json"]
    )
    assert gif.exit_code == 0, gif.output
    mp3 = runner.invoke(app, ["convert", "clip.mkv", "mp3", "-s", "1", "-d", "2", "--mono", "--json"])
    assert mp3.exit_code == 0, mp3.output
    from sashimi import probe

    assert probe(tmp_path / "clip.gif").width == 160
    assert probe(tmp_path / "clip.mp3").duration == pytest.approx(2.0, abs=0.1)


@needs_ffmpeg
def test_info_and_rotate(video_blob, tmp_path):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    info = json.loads(runner.invoke(app, ["info", "clip.mkv", "--json"]).stdout)
    assert (info["width"], info["height"]) == (320, 180)
    rotated = runner.invoke(app, ["rotate", "clip.mkv", "90", "--format", "webm", "--crf", "45", "--json"])
    assert rotated.exit_code == 0, rotated.output
    info = json.loads(runner.invoke(app, ["info", "clip_rotated.webm", "--json"]).stdout)
    assert (info["width"], info["height"], info["video_codec"]) == (180, 320, "vp9")


def test_info_on_a_non_media_file(tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    result = runner.invoke(app, ["info", "notes.txt"])
    assert result.exit_code == 1 and "doesn't look like media" in result.stderr
