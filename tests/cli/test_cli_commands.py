# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import json

import pytest
from PIL import Image
from typer.testing import CliRunner

from sashimi.cli import settings
from sashimi.cli._options import parse_quality as _parse_quality
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


@pytest.mark.parametrize("text, value", [("85%", 85), ("85", 85), ("0.85", 85), ("1", 100), ("70.4%", 70)])
def test_parse_quality(text, value):
    assert _parse_quality(text) == value


def test_toml_uses_legacy_key_names(tmp_path):
    (tmp_path / "sashimi.toml").write_text(
        "[thumbs]\nstatic_width = 1000\njpeg_quality = 60\nanimated_width = 640\noutput_format = 'gif'\n"
        "output_fps = 8\ncols = 3\nvideo_extensions = ['.MP4', '.foo']\n[animated_thumbs]\ncols = 4\n"
    )
    static, _, _ = settings.resolve_sheet(False, {"gap": 2})
    animated, _, unused = settings.resolve_sheet(True, {"rows": 1, "quality": 50})
    assert (static.width, static.quality, static.cols, static.gap) == (1000, 60, 3, 2)
    assert (animated.width, animated.target, animated.fps, animated.cols, animated.rows) == (640, "gif", 8, 4, 1)
    assert unused == ["quality"]
    assert settings.video_extensions() == [".mp4", ".foo"]


def test_toml_font_paths_become_render_arguments(tmp_path):
    (tmp_path / "sashimi.toml").write_text("[thumbs]\nheader_font_path = 'fonts/h.ttf'\nemoji_dir = 'emoji'\n")
    config, paths, _ = settings.resolve_sheet(False, {"timestamp_font_path": "t.ttf"})
    assert paths == {"header_font": "fonts/h.ttf", "timestamp_font": "t.ttf", "emoji_dir": "emoji"}
    assert "header_font_path" not in type(config).model_fields


def test_convert_command(tmp_path):
    Image.new("RGBA", (200, 100), (255, 0, 0, 128)).save(tmp_path / "a.png")
    result = runner.invoke(app, ["convert", "a.png", "webp", "--width", "50", "-q", "70%", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert (report["command"], report["results"][0]["message"]) == ("convert", "Converted to a.webp")
    assert report["results"][0]["output"] == "a.webp"
    with Image.open(tmp_path / "a.webp") as img:
        assert img.size == (50, 25) and img.mode == "RGBA"


@needs_ffmpeg
def test_convert_video_to_animated_webp(video_blob, tmp_path):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    Image.new("RGB", (200, 100)).save(tmp_path / "a.png")
    result = runner.invoke(app, ["convert", ".", "webp", "-d", "1", "--fps", "5", "-q", "60", "--json"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)["results"]) == 2
    with Image.open(tmp_path / "clip.webp") as img:
        assert img.n_frames == 5 and img.size == (320, 180)


def test_convert_webp_with_image_only_width_skips_videos(tmp_path):
    (tmp_path / "clip.mkv").write_bytes(b"x")
    Image.new("RGB", (200, 100)).save(tmp_path / "a.png")
    result = runner.invoke(app, ["convert", ".", "webp", "--width", "50", "--json"])
    assert result.exit_code == 0, result.output
    assert [r["file"] for r in json.loads(result.stdout)["results"]] == ["a.png"]
    assert "only images will be converted" in result.stderr
    assert runner.invoke(app, ["convert", "clip.mkv", "webp", "--width", "50"]).exit_code == 2


def test_convert_rejects_bad_quality(tmp_path):
    result = runner.invoke(app, ["convert", ".", "jpeg", "-q", "lots"])
    assert result.exit_code == 2 and "--quality" in result.stderr


@needs_ffmpeg
def test_extract_command(video_blob, tmp_path):
    clip = tmp_path / "clip.mkv"
    clip.write_bytes(video_blob.read_bytes())
    result = runner.invoke(app, ["extract", "clip.mkv", "1s", "00:03"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "clip.extracted.mkv").exists()
    again = runner.invoke(app, ["extract", "clip.mkv", "1s", "3s"])
    assert "already exists" in again.stdout and again.exit_code == 0


@needs_ffmpeg
@pytest.mark.parametrize(
    "args, length",
    [(["4"], 2.0), (["-s", "4"], 2.0), (["-d", "2", "--accurate"], 2.0), (["-s", "1", "-d", "3", "--accurate"], 3.0)],
)
def test_extract_to_the_end_or_by_length(video_blob, tmp_path, args, length):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    result = runner.invoke(app, ["extract", "clip.mkv", *args, "-o", "cut.mkv", "--json"])
    assert result.exit_code == 0, result.output
    from sashimi import probe

    assert probe(tmp_path / "cut.mkv").duration == pytest.approx(length, abs=0.15)


def test_extract_needs_a_time(tmp_path):
    (tmp_path / "clip.mkv").write_bytes(b"x")
    result = runner.invoke(app, ["extract", "clip.mkv"])
    assert result.exit_code == 2 and "--duration" in result.stderr


def test_extract_refuses_bad_times_before_running(tmp_path):
    (tmp_path / "clip.mkv").write_bytes(b"x")
    for args, message in [(["5", "2"], "The end time must be after the start time."), (["abc"], "isn't a time")]:
        result = runner.invoke(app, ["extract", "clip.mkv", *args])
        assert result.exit_code == 2 and message in " ".join(result.stderr.split())


@needs_ffmpeg
def test_thumbs_command_with_symlinked_input(video_blob, tmp_path):
    (tmp_path / "holiday.mkv").symlink_to(video_blob)
    result = runner.invoke(app, ["thumbs", "holiday.mkv", "--width", "400", "--cols", "2", "--rows", "1", "--json"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "holiday.sheet.jpg").exists()
    assert not (tmp_path / "home").exists() and not (tmp_path / "sashimi.toml").exists()


@needs_ffmpeg
@pytest.mark.parametrize("suffix", [".mkv", ".mp4"])
@pytest.mark.parametrize("accurate", [False, True])
def test_extract_reports_real_length(video_blob, tmp_path, suffix, accurate):
    clip = tmp_path / "clip.mkv"
    clip.write_bytes(video_blob.read_bytes())
    args = ["extract", "clip.mkv", "1.6", "3.6", "-o", f"cut{suffix}", "--json", *(["--accurate"] * accurate)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    message = json.loads(result.stdout)["results"][0]["message"]
    assert ("accurate cut" in message) is not accurate
    assert message.startswith("Extracted 2.0s" if accurate else "Extracted 2.6s")


def test_extract_encoder_options_need_accurate(tmp_path):
    (tmp_path / "clip.mkv").write_bytes(b"x")
    result = runner.invoke(app, ["extract", "clip.mkv", "1", "2", "--crf", "20"])
    assert result.exit_code == 2
    assert "--crf only applies with --accurate" in " ".join(result.stderr.split())
