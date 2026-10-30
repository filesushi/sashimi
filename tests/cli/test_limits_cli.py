# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Limits on the command line: the --max-* flags, the [limits] section of sashimi.toml, and their precedence."""

import json

import pytest
from PIL import Image
from typer.testing import CliRunner

from sashimi.cli import _limits, settings
from sashimi.cli.main import app

from tests.conftest import needs_ffmpeg

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    settings.load_raw_config.cache_clear()
    yield
    settings.load_raw_config.cache_clear()


def toml(tmp_path, text):
    (tmp_path / "sashimi.toml").write_text(text)
    settings.load_raw_config.cache_clear()


def result_message(result):
    return json.loads(result.stdout)["results"][0]["message"]


@pytest.mark.parametrize(
    "field, text, value",
    [
        ("max_pixels", "200M", 200_000_000),
        ("max_pixels", "200MP", 200_000_000),
        ("max_pixels", "1.5k", 1500),
        ("max_pixels", 64_000_000, 64_000_000),
        ("max_frame_bytes", "2G", 2 * 2**30),
        ("max_frame_bytes", "512MiB", 512 * 2**20),
        ("max_frame_bytes", "1.5GB", 3 * 2**29),
        ("max_frame_bytes", "1073741824", 2**30),
        ("max_video_seconds", "10m", 600.0),
        ("max_video_seconds", "1h30m", 5400.0),
        ("max_audio_seconds", 90, 90.0),
        ("max_preview_clips", "12", 12),
        ("max_sheet_pixels", "none", None),
        ("max_gif_seconds", "NONE", None),
    ],
)
def test_limit_values(field, text, value):
    assert _limits.parse(field, text) == value


@pytest.mark.parametrize(
    "field, text",
    [
        ("max_pixels", "lots"),
        ("max_pixels", "0"),
        ("max_pixels", "0.5"),
        ("max_frame_bytes", "2X"),
        ("max_video_seconds", "soon"),
    ],
)
def test_invalid_limit_values(field, text):
    with pytest.raises(ValueError):
        _limits.parse(field, text)


def test_limits_are_written_back_the_way_they_are_typed():
    assert _limits.format_limit("max_pixels", 64_000_000) == "64M"
    assert _limits.format_limit("max_frame_bytes", 2**30) == "1G"
    assert _limits.format_limit("max_animation_bytes", 256 * 2**20) == "256M"
    assert _limits.format_limit("max_video_seconds", 600.0) == "600"
    assert _limits.format_limit("max_gif_seconds", None) == "none"


def test_max_pixels_flag_and_sashimi_toml(tmp_path):
    Image.new("RGB", (3000, 2000), "red").save(tmp_path / "big.png")
    refused = runner.invoke(app, ["convert", "big.png", "jpeg", "--max-pixels", "5M", "--json"])
    assert refused.exit_code == 1
    assert result_message(refused) == (
        "This image is too large to process: it has 6 megapixels (3000x2000), and the limit is 5 megapixels. "
        "It was set with --max-pixels 5M: give a higher value, or --max-pixels none for no limit."
    )
    toml(tmp_path, '[limits]\nmax_pixels = "5M"\n')
    assert runner.invoke(app, ["resize", "big.png", "-w", "100", "--json"]).exit_code == 1
    assert runner.invoke(app, ["resize", "big.png", "-w", "100", "--max-pixels", "none"]).exit_code == 0
    assert runner.invoke(app, ["favicon", "big.png", "--json"]).exit_code == 1
    assert runner.invoke(app, ["edit", "big.png", "--max-pixels", "6M"]).exit_code == 0


def test_invalid_limits_are_refused_before_anything_runs(tmp_path):
    Image.new("RGB", (8, 8)).save(tmp_path / "a.png")
    flag = runner.invoke(app, ["convert", "a.png", "jpeg", "--max-pixels", "lots"])
    assert flag.exit_code == 2
    assert "--max-pixels: expected a number of pixels, e.g. 200M or 200000000, or none." in " ".join(
        flag.stderr.split()
    )
    toml(tmp_path, "[limits]\nmax_frame_bytes = 0\n")
    setting = runner.invoke(app, ["convert", "a.png", "jpeg"])
    assert setting.exit_code == 2
    assert "limits.max_frame_bytes in sashimi.toml: must be more than 0, or none for no limit." in " ".join(
        setting.stderr.split()
    )
    assert not (tmp_path / "a.jpg").exists()


def test_animation_memory_flag(tmp_path):
    frames = [Image.new("RGB", (64, 64), (i * 40, 0, 0)) for i in range(4)]
    frames[0].save(tmp_path / "a.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    refused = runner.invoke(app, ["frames", "a.gif", "--speed", "2", "--max-animation-bytes", "32K", "--json"])
    assert refused.exit_code == 1
    assert result_message(refused).startswith("This animation is too large to edit: its frames would take 0 MB")
    assert runner.invoke(app, ["edit", "a.gif", "--rotate", "90", "--max-animation-bytes", "64K"]).exit_code == 0


def test_config_set_and_defaults_for_limits(tmp_path):
    assert runner.invoke(app, ["config", "set", "limits.max_pixels", "200M"]).exit_code == 0
    assert runner.invoke(app, ["config", "set", "limits.max_video_seconds", "none"]).exit_code == 0
    assert 'max_pixels = "200M"' in (tmp_path / "home" / "sashimi.toml").read_text()
    bad = runner.invoke(app, ["config", "set", "limits.max_pixels", "0"])
    assert bad.exit_code == 2 and "limits.max_pixels: must be more than 0" in bad.stderr
    unknown = runner.invoke(app, ["config", "set", "limits.max_seconds", "5"])
    assert unknown.exit_code == 2 and "Unknown option limits.max_seconds." in unknown.stderr
    report = json.loads(runner.invoke(app, ["config", "defaults", "limits", "--json"]).stdout)
    rows = {row["field"]: row for row in report[0]["fields"]}
    assert set(rows) == set(_limits.KINDS)
    assert (rows["max_pixels"]["option"], rows["max_pixels"]["default"]) == ("--max-pixels", 64_000_000)
    assert rows["max_pixels"]["sashimi_toml"] == 200_000_000
    assert rows["max_video_seconds"]["option"] is None and rows["max_video_seconds"]["default"] is None
    table = " ".join(runner.invoke(app, ["config", "defaults", "limits"], env={"COLUMNS": "250"}).stdout.split())
    assert "64M" in table and "sashimi.toml: 200M" in table


def test_unknown_limits_in_sashimi_toml_are_warned_about(tmp_path):
    Image.new("RGB", (8, 8)).save(tmp_path / "a.png")
    toml(tmp_path, "[limits]\nmax_megapixels = 5\n")
    result = runner.invoke(app, ["convert", "a.png", "jpeg"])
    assert result.exit_code == 0 and "limits.max_megapixels" in result.stderr


def test_info_reads_images_of_any_size(tmp_path):
    import struct

    screen = b"GIF89a" + struct.pack("<HHBBB", 20000, 20000, 0, 0, 0)
    (tmp_path / "huge.gif").write_bytes(
        screen + b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0) + b"\x02\x02\x44\x01\x00\x3b"
    )
    result = runner.invoke(app, ["info", "huge.gif", "--json"])
    assert result.exit_code == 0 and json.loads(result.stdout)["width"] == 20000


@needs_ffmpeg
def test_video_limits_come_from_sashimi_toml(video_blob, tmp_path):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    toml(tmp_path, '[limits]\nmax_video_seconds = "5s"\nmax_audio_seconds = 3\n')
    video = runner.invoke(app, ["convert", "clip.mkv", "webm", "--json"])
    assert video.exit_code == 1
    assert result_message(video) == (
        "This video is 6 seconds long, and the limit is 5 seconds. Set a shorter duration to convert part of it. "
        "To raise the limit (now 5s), run: sashimi config set limits.max_video_seconds 30s "
        "(or any higher value, or none for no limit)."
    )
    audio = runner.invoke(app, ["convert", "clip.mkv", "mp3", "--json"])
    assert result_message(audio).startswith("This video is 6 seconds long, and the limit is 3 seconds.")
    assert result_message(audio).endswith(
        "To raise the limit (now 3s), run: sashimi config set limits.max_audio_seconds 30s "
        "(or any higher value, or none for no limit)."
    )
    assert runner.invoke(app, ["extract", "clip.mkv", "0", "--json"]).exit_code == 0
    accurate = runner.invoke(app, ["extract", "clip.mkv", "0", "-o", "a.mp4", "--accurate", "--json"])
    assert result_message(accurate).startswith("This video is 6 seconds long, and the limit is 5 seconds.")


@needs_ffmpeg
def test_sheet_and_frame_limits(video_blob, tmp_path):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    sheet = runner.invoke(app, ["thumbs", "clip.mkv", "--max-sheet-pixels", "1M", "--json"])
    assert sheet.exit_code == 1
    assert result_message(sheet).startswith("These settings would make the contact sheet too large: it would have")
    animated = runner.invoke(app, ["thumbs", "clip.mkv", "-a", "--max-frame-bytes", "1M", "--json"])
    assert animated.exit_code == 1
    assert result_message(animated).startswith("These settings would make the animated sheet too large to render")


@needs_ffmpeg
@pytest.mark.parametrize("command", [["convert", "clip.mkv", "gif"], ["preview", "clip.mkv", "--output-format", "gif"]])
def test_gifs_have_no_frame_budget(video_blob, tmp_path, command):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    flag = runner.invoke(app, [*command, "--max-frame-bytes", "1M"])
    assert flag.exit_code == 2 and "No such option: --max-frame-bytes" in " ".join(flag.stderr.split())
    toml(tmp_path, "[limits]\nmax_frame_bytes = 1\n")
    made = runner.invoke(app, [*command, "--json"])
    assert made.exit_code == 0, made.output
    assert json.loads(made.stdout)["summary"]["success"] == 1


@pytest.mark.parametrize(
    "field, current, suggested",
    [
        ("max_pixels", 64_000_000, "100M"),
        ("max_pixels", 300_000_000, "500M"),
        ("max_frame_bytes", 2**30, "2G"),
        ("max_gif_seconds", 30, "1m"),
        ("max_gif_seconds", 3 * 3600, "6h"),
        ("max_video_seconds", 90_000, "49h"),
        ("max_scan_packets", 2_000_000, "5000000"),
    ],
)
def test_the_hint_suggests_a_round_value_above_the_limit_in_force(monkeypatch, field, current, suggested):
    from sashimi.config import Limits

    monkeypatch.setitem(_limits._resolved, "limits", Limits(**{field: current}))
    monkeypatch.setitem(_limits._resolved, "flags", {})
    message = _limits.hint(field)
    assert f"sashimi config set limits.{field} {suggested} " in message
    assert _limits.parse(field, suggested) > current


def test_the_hint_names_the_flag_that_set_the_limit(tmp_path):
    Image.new("RGB", (2000, 2000), "red").save(tmp_path / "big.png")
    toml(tmp_path, '[limits]\nmax_pixels = "200M"\n')
    refused = runner.invoke(app, ["convert", "big.png", "jpg", "--max-pixels", "1M", "--json"])
    assert refused.exit_code == 1
    message = result_message(refused)
    assert "config set" not in message
    assert message.endswith("It was set with --max-pixels 1M: give a higher value, or --max-pixels none for no limit.")
