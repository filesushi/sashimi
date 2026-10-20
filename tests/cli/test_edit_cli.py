# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""`sashimi edit` and `frames`, `--dry-run` everywhere, exit codes, -H and `info` for images."""

import json

import pytest
import typer
from PIL import Image
from typer.testing import CliRunner

from sashimi.cli import settings
from sashimi.cli.main import app

from tests.conftest import needs_ffmpeg

runner = CliRunner()
COMMANDS = typer.main.get_command(app).commands  # type: ignore[attr-defined]
PROCESSING = sorted(set(COMMANDS) - {"config", "info", "metadata", "trim"})


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "GLOBAL_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    settings.load_raw_config.cache_clear()
    yield
    settings.load_raw_config.cache_clear()


def options(command):
    return {name: param for param in COMMANDS[command].params for name in param.opts}


def report(result):
    return json.loads(result.stdout)


def gif(path, frames=5, size=(32, 16)):
    images = [Image.new("RGB", size, (index * 50, 100, 200)) for index in range(frames)]
    images[0].save(path, save_all=True, append_images=images[1:], duration=100, loop=0)


@pytest.mark.parametrize("command", PROCESSING)
def test_every_processing_command_has_a_dry_run(command):
    found = options(command)
    assert found["--dry-run"] is found["-n"]


@pytest.mark.parametrize("command", ["convert", "resize", "preview"])
def test_height_has_a_short_flag(command):
    found = options(command)
    assert found["--height"] is found["-H"]


def test_edit_keeps_each_format(tmp_path):
    Image.new("RGB", (40, 20), "red").save(tmp_path / "a.jpg")
    Image.new("RGBA", (40, 20), (0, 0, 255, 128)).save(tmp_path / "b.png")
    result = runner.invoke(app, ["edit", ".", "--rotate", "90", "--crop", "10x30+5+5", "--saturation", "0", "--json"])
    assert result.exit_code == 0, result.output
    assert [row["output"] for row in report(result)["results"]] == ["a.edited.jpg", "b.edited.png"]
    with Image.open(tmp_path / "a.edited.jpg") as jpeg, Image.open(tmp_path / "b.edited.png") as png:
        assert (jpeg.format, jpeg.size, png.format, png.size, png.mode) == ("JPEG", (10, 30), "PNG", (10, 30), "RGBA")


def test_edit_aspect_and_crop_syntax(tmp_path):
    Image.new("RGB", (400, 300), "red").save(tmp_path / "a.png")
    assert runner.invoke(app, ["edit", "a.png", "--aspect", "16:9"]).exit_code == 0
    assert Image.open(tmp_path / "a.edited.png").size == (400, 225)
    assert runner.invoke(app, ["edit", "a.png", "--crop", "50x40", "-o", "c.png"]).exit_code == 0
    assert Image.open(tmp_path / "c.png").size == (50, 40)
    bad = runner.invoke(app, ["edit", "a.png", "--crop", "50,40"])
    assert bad.exit_code == 2 and "WIDTHxHEIGHT+LEFT+TOP" in bad.stderr
    both = runner.invoke(app, ["edit", "a.png", "--crop", "5x5", "--aspect", "1:1"])
    assert both.exit_code == 2 and "not both" in both.stderr
    shape = runner.invoke(app, ["edit", "a.png", "--aspect", "wide"])
    assert shape.exit_code == 2 and "width:height" in shape.stderr
    outside = runner.invoke(app, ["edit", "a.png", "--crop", "500x10", "-f", "--json"])
    assert outside.exit_code == 1
    assert (
        report(outside)["results"][0]["message"]
        == "The crop area must fit inside the picture, which is 400x300 pixels."
    )
    assert runner.invoke(app, ["edit", "a.png", "--brightness", "3", "-f"]).exit_code == 0
    negative = runner.invoke(app, ["edit", "a.png", "--brightness", "-0.5", "-f"])
    assert negative.exit_code == 2 and "--brightness" in negative.stderr


def test_edit_warns_about_options_a_format_ignores(tmp_path):
    Image.new("RGB", (8, 8)).save(tmp_path / "a.png")
    result = runner.invoke(app, ["edit", "a.png", "-q", "50", "--json"])
    assert result.exit_code == 0
    assert "--quality doesn't apply to PNG images" in " ".join(result.stderr.split())


def test_frames_numbers_from_one(tmp_path):
    gif(tmp_path / "clip.gif")
    result = runner.invoke(app, ["frames", "clip.gif", "--keep", "1-4", "--drop", "2", "--speed", "2", "--json"])
    assert result.exit_code == 0, result.output
    assert report(result)["results"][0]["output"] == "clip.edited.gif"
    with Image.open(tmp_path / "clip.edited.gif") as out:
        colours = []
        for index in range(out.n_frames):
            out.seek(index)
            colours.append(out.convert("RGB").getpixel((0, 0))[0])
            assert out.info["duration"] == 50
    assert colours == [0, 100, 150]
    missing = runner.invoke(app, ["frames", "clip.gif", "--drop", "9", "-f", "--json"])
    assert missing.exit_code == 1 and "has 5 frames" in report(missing)["results"][0]["message"]
    zero = runner.invoke(app, ["frames", "clip.gif", "--keep", "0"])
    assert zero.exit_code == 2 and "numbered from 1" in zero.stderr


def test_exit_codes(tmp_path):
    Image.new("RGB", (8, 8)).save(tmp_path / "still.gif")
    gif(tmp_path / "clip.gif")
    assert runner.invoke(app, ["frames", "still.gif"]).exit_code == 1
    scan = runner.invoke(app, ["frames", ".", "--json"])
    rows = {row["file"]: row["status"] for row in report(scan)["results"]}
    assert scan.exit_code == 0 and rows == {"clip.gif": "success", "still.gif": "skipped"}
    exists = runner.invoke(app, ["frames", "clip.gif", "--json"])
    assert exists.exit_code == 0 and report(exists)["results"][0]["status"] == "exists"
    (tmp_path / "notes.txt").write_text("hi")
    assert runner.invoke(app, ["edit", "notes.txt"]).exit_code == 1
    assert runner.invoke(app, ["convert", "still.gif", "gif"]).exit_code == 1
    assert runner.invoke(app, ["edit", "missing.png"]).exit_code == 1


def test_dry_run_plans_without_writing(tmp_path):
    Image.new("RGB", (8, 8)).save(tmp_path / "a.png")
    Image.new("RGB", (8, 8)).save(tmp_path / "b.png")
    (tmp_path / "b.webp").write_bytes(b"old")
    before = sorted(p.name for p in tmp_path.iterdir())
    result = runner.invoke(app, ["convert", ".", "webp", "-n", "--json"])
    assert result.exit_code == 0
    planned = report(result)
    assert planned["dry_run"] is True
    assert [(r["file"], r["status"], r["message"]) for r in planned["results"]] == [
        ("a.png", "success", "Would write a.webp"),
        ("b.png", "exists", "Output already exists: b.webp"),
    ]
    forced = report(runner.invoke(app, ["convert", ".", "webp", "-n", "-f", "--json"]))
    assert forced["results"][1]["message"] == "Would replace b.webp"
    text = runner.invoke(app, ["edit", "a.png", "--dry-run"])
    assert text.exit_code == 0 and text.stdout.strip() == "Plan: a.png: Would write a.edited.png"
    for args in (
        ["resize", ".", "-w", "4", "-n"],
        ["favicon", ".", "-n"],
        ["frames", ".", "-n"],
        ["thumbs", ".", "-n"],
    ):
        assert runner.invoke(app, args).exit_code == 0, args
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert report(runner.invoke(app, ["convert", "a.png", "webp", "--json"]))["dry_run"] is False


def test_dry_run_reports_collisions_and_options_still_checked(tmp_path):
    Image.new("RGB", (8, 8)).save(tmp_path / "a.png")
    Image.new("RGB", (8, 8)).save(tmp_path / "a.jpg")
    planned = report(runner.invoke(app, ["convert", ".", "webp", "-n", "--json"]))
    assert [r["output"] for r in planned["results"]] == ["a.jpg.webp", "a.png.webp"]
    assert runner.invoke(app, ["convert", ".", "webp", "-n", "-q", "nope"]).exit_code == 2


def test_info_on_images(tmp_path):
    gif(tmp_path / "clip.gif")
    info = json.loads(runner.invoke(app, ["info", "clip.gif", "--json"]).stdout)
    assert info == {
        "format": "gif", "edit_target": "gif", "width": 32, "height": 16, "frames": 5,
        "durations_ms": [100] * 5, "loop": 0, "animated": True,
    }  # fmt: skip
    text = " ".join(runner.invoke(app, ["info", "clip.gif"], env={"COLUMNS": "200"}).stdout.split())
    assert "Frames 5" in text and "0.50 s, loops forever" in text and "1-5: 100 ms" in text


def test_config_defaults_for_edit_and_frames():
    edit = json.loads(runner.invoke(app, ["config", "defaults", "edit", "jpg", "--json"]).stdout)
    fields = {row["field"]: row for row in edit[0]["fields"]}
    assert edit[0]["model"] == "JpegEditConfig" and fields["quality"]["default"] == 92
    assert fields["crop_left"]["option"] == "--crop" and fields["saturation"]["accepted"] == "at least 0"
    frames = json.loads(runner.invoke(app, ["config", "defaults", "frames", "--json"]).stdout)
    assert [section["model"] for section in frames] == ["GifFramesConfig", "WebpFramesConfig"]


@needs_ffmpeg
def test_convert_says_when_an_mp4_was_copied(video_blob, tmp_path):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    copied = report(runner.invoke(app, ["convert", "clip.mkv", "mp4", "--json"]))["results"][0]
    assert copied["message"] == "Converted to clip.mp4 (video and audio copied, not re-encoded)"
    encoded = report(runner.invoke(app, ["convert", "clip.mkv", "mp4", "--crf", "30", "-f", "--json"]))
    assert encoded["results"][0]["message"] == "Converted to clip.mp4 (replaced the existing file)"
