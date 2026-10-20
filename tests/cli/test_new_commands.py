# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""The 0.6.0 CLI: preview, favicon, remux, completion, consistent flags and one JSON report."""

import json
import re
import zipfile

import pytest
import typer
from PIL import Image
from typer.testing import CliRunner

from sashimi.cli import settings
from sashimi.cli.main import app

from tests.conftest import needs_ffmpeg

runner = CliRunner()
COMMANDS = typer.main.get_command(app).commands  # type: ignore[attr-defined]
EMOJI = re.compile("[\U0001f300-\U0001faff✀-➿←-⇿]")


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "GLOBAL_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    settings.load_raw_config.cache_clear()
    yield
    settings.load_raw_config.cache_clear()


def options(command):
    return {name: param for param in COMMANDS[command].params for name in param.opts}


@pytest.mark.parametrize("command", sorted(set(COMMANDS) - {"config", "info", "flatten", "trim"}))
def test_o_is_the_output_path_and_format_has_no_short_flag(command):
    found = options(command)
    assert found["-o"].name == "output" and found["--output"] is found["-o"]
    assert "--output-dir" in found
    if "--format" in found:
        assert found["--format"].opts == ["--format"]


def test_duration_always_means_output_length():
    assert "--duration" not in options("thumbs") and "--clip-length" in options("thumbs")
    assert "--clip-length" in options("preview")
    assert "--duration" in options("convert") and "--duration" in options("extract")


def test_dash_h_is_help_everywhere():
    for command in ([], ["convert"], ["thumbs"], ["preview"]):
        result = runner.invoke(app, [*command, "-h"])
        assert result.exit_code == 0 and "Usage:" in result.output


def test_verbose_shows_no_metavar():
    output = runner.invoke(app, ["--help"], env={"COLUMNS": "200"}).output
    line = next(line for line in output.splitlines() if "--verbose" in line)
    assert "<int>" not in line and "INTEGER" not in line


def test_shell_completion():
    root = {name for param in typer.main.get_command(app).params for name in param.opts}
    assert {"--install-completion", "--show-completion"} <= root
    words = runner.invoke(
        app,
        [],
        prog_name="sashimi",
        env={"_SASHIMI_COMPLETE": "complete_bash", "COMP_WORDS": "sashimi pre", "COMP_CWORD": "1"},
    )
    assert words.output.split() == ["preview"]
    flags = runner.invoke(
        app,
        [],
        prog_name="sashimi",
        env={"_SASHIMI_COMPLETE": "complete_bash", "COMP_WORDS": "sashimi thumbs --clip", "COMP_CWORD": "2"},
    )
    assert flags.output.split() == ["--clip-length"]


def test_no_emoji_and_one_line_for_one_file(tmp_path):
    Image.new("RGB", (40, 20), "red").save(tmp_path / "a.png")
    result = runner.invoke(app, ["convert", "a.png", "webp"])
    assert result.exit_code == 0
    assert re.fullmatch(r"Success: a\.png: Converted to a\.webp \(\d+\.\ds\)", result.stdout.strip())
    assert not EMOJI.search(result.output)


def test_several_files_get_a_summary(tmp_path):
    for name in ("a", "b"):
        Image.new("RGB", (40, 20), "red").save(tmp_path / f"{name}.png")
    result = runner.invoke(app, ["convert", ".", "webp"], env={"COLUMNS": "120"})
    assert "Convert to WEBP: 2 files" in result.stdout and "summary" in result.stdout
    assert not EMOJI.search(result.output)


def test_json_report_is_the_same_everywhere(tmp_path):
    Image.new("RGBA", (40, 20), "red").save(tmp_path / "a.png")
    reports = [
        json.loads(runner.invoke(app, args).stdout)
        for args in (
            ["convert", "a.png", "jpeg", "--json"],
            ["resize", "a.png", "--width", "10", "--json"],
            ["favicon", "a.png", "--json"],
            ["thumbs", "missing.mp4", "--json"],
            ["flatten", ".", "--dry-run", "--json"],
        )
    ]
    for report in reports:
        assert report["schema"] == 1
        assert list(report["summary"]) == [
            "total_found", "success", "exists", "skipped", "errors", "time_taken_seconds", "avg_time_seconds",
        ]  # fmt: skip
        assert all(set(row) == {"file", "status", "message", "output"} for row in report["results"])
    assert [r["command"] for r in reports] == ["convert", "resize", "favicon", "thumbs", "flatten"]
    assert reports[0]["results"][0]["output"] == "a.jpg"


def test_output_names_and_options(tmp_path):
    Image.new("RGBA", (40, 20), (255, 0, 0, 128)).save(tmp_path / "a.png")
    Image.new("RGB", (40, 20), "blue").save(tmp_path / "a.jpeg")
    assert runner.invoke(app, ["convert", ".", "webp", "--json"]).exit_code == 0
    assert (tmp_path / "a.png.webp").exists() and (tmp_path / "a.jpeg.webp").exists()
    assert runner.invoke(app, ["resize", "a.png", "--width", "10", "-o", "small/x.png"]).exit_code == 0
    assert Image.open(tmp_path / "small" / "x.png").size == (10, 5)
    assert runner.invoke(app, ["resize", ".", "--width", "10", "--output-dir", "out", "--json"]).exit_code == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "a.jpeg.resized.webp", "a.png.resized.webp", "a.resized.jpeg", "a.resized.png",
    ]  # fmt: skip
    refused = runner.invoke(app, ["convert", ".", "jpeg", "-o", "x.jpg"])
    assert refused.exit_code == 2 and "--output-dir" in refused.stderr


def test_force_reports_replacements(tmp_path):
    Image.new("RGB", (40, 20), "red").save(tmp_path / "a.png")
    (tmp_path / "a.jpg").write_bytes(b"someone's photo")
    kept = json.loads(runner.invoke(app, ["convert", "a.png", "jpeg", "--json"]).stdout)["results"][0]
    assert kept["status"] == "exists" and (tmp_path / "a.jpg").read_bytes() == b"someone's photo"
    replaced = json.loads(runner.invoke(app, ["convert", "a.png", "jpeg", "-f", "--json"]).stdout)["results"][0]
    assert replaced["message"] == "Converted to a.jpg (replaced the existing file)"


def test_ico_background(tmp_path):
    Image.new("RGBA", (32, 32), (0, 0, 0, 0)).save(tmp_path / "logo.png")
    assert (
        runner.invoke(app, ["convert", "logo.png", "ico", "--background", "#00ff00", "--ico-sizes", "16"]).exit_code
        == 0
    )
    with Image.open(tmp_path / "logo.ico") as ico:
        assert ico.convert("RGBA").getpixel((8, 8)) == (0, 255, 0, 255)


def test_favicon_command(tmp_path):
    Image.new("RGBA", (64, 32), (0, 0, 255, 255)).save(tmp_path / "logo.png")
    result = runner.invoke(
        app, ["favicon", "logo.png", "--name", "Demo & Co", "--theme-color", "#336699", "--background", "#000000"]
    )
    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(tmp_path / "logo.favicons.zip") as archive:
        assert json.loads(archive.read("site.webmanifest"))["name"] == "Demo & Co"
        assert 'content="#336699"' in archive.read("favicon.html").decode()
    bad = runner.invoke(app, ["favicon", "logo.png", "--ico-sizes", "8", "-f"])
    assert bad.exit_code == 2 and "16 and 256" in bad.stderr


@needs_ffmpeg
def test_preview_command(video_blob, tmp_path):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    result = runner.invoke(
        app, ["preview", "clip.mkv", "--format", "gif", "--samples", "2", "--clip-length", "1", "--fps", "5", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["results"][0]["output"] == "clip.preview.gif"
    with Image.open(tmp_path / "clip.preview.gif") as img:
        assert img.n_frames == 10 and img.size == (320, 180)
    webp = runner.invoke(app, ["preview", "clip.mkv", "-o", "p.webp", "--samples", "1", "--height", "90", "-q", "50"])
    assert webp.exit_code == 0, webp.output
    assert Image.open(tmp_path / "p.webp").size == (160, 90)
    warned = runner.invoke(app, ["preview", "clip.mkv", "--format", "gif", "-q", "50", "-f", "--samples", "1"])
    assert "--quality doesn't apply to GIF previews" in " ".join(warned.stderr.split())
    too_long = runner.invoke(app, ["preview", "clip.mkv", "--samples", "10", "--clip-length", "4"])
    assert too_long.exit_code == 2 and "at most 30 seconds" in too_long.stderr


@needs_ffmpeg
def test_remux_command(video_blob, tmp_path):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    result = runner.invoke(app, ["remux", "clip.mkv", "-o", "clip.mov"])
    assert result.exit_code == 0, result.output
    from sashimi import probe

    assert "mov" in probe(tmp_path / "clip.mov").container_names
    assert (tmp_path / "clip.mkv").read_bytes() == video_blob.read_bytes()
    skipped = runner.invoke(app, ["remux", "clip.mkv", "--json"])
    assert json.loads(skipped.stdout)["results"][0]["message"] == "Already MKV"
    refused = runner.invoke(app, ["remux", "clip.mkv", "--format", "webm", "--json"])
    assert refused.exit_code == 1 and "WEBM without re-encoding" in refused.stdout


@needs_ffmpeg
def test_thumbs_names_and_clip_length(video_blob, tmp_path):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    args = ["--width", "400", "--cols", "2", "--rows", "1", "--json"]
    assert runner.invoke(app, ["thumbs", "clip.mkv", *args]).exit_code == 0
    animated = runner.invoke(app, ["thumbs", "clip.mkv", "-a", "--clip-length", "1", "--fps", "2", *args])
    assert animated.exit_code == 0, animated.output
    assert (tmp_path / "clip.sheet.jpg").exists() and (tmp_path / "clip.animated-sheet.webp").exists()
    png = runner.invoke(app, ["thumbs", "clip.mkv", "-o", "s.png", *args])
    assert png.exit_code == 0 and Image.open(tmp_path / "s.png").format == "PNG"
