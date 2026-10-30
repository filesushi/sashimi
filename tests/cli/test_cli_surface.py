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
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
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
        ["remux"],
        ["favicon"],
        ["thumbs"],
        ["preview"],
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
    for panel in ("Convert", "Sheets & previews", "Inspect", "Files", "Configuration"):
        assert panel in output
    assert f"Powered by sashimi v{__version__}" in output


def test_config_set_show_unset(tmp_path):
    assert json.loads(runner.invoke(app, ["config", "show", "--json"]).stdout)["settings"].get("thumbs") is None
    assert not (tmp_path / "home").exists()
    assert runner.invoke(app, ["config", "set", "thumbs.cols", "4"]).exit_code == 0
    shown = json.loads(runner.invoke(app, ["config", "show", "--json"]).stdout)
    assert shown["settings"]["thumbs"]["cols"] == 4
    bad = runner.invoke(app, ["config", "set", "thumbs.width", "10"])
    assert bad.exit_code == 2 and "greater than or equal to 320" in bad.stderr
    assert runner.invoke(app, ["config", "set", "thumbs.nope", "1"]).exit_code == 2
    assert runner.invoke(app, ["config", "set", "thumbs.static_width", "1000"]).exit_code == 2
    assert runner.invoke(app, ["config", "unset", "thumbs.cols"]).exit_code == 0
    assert "cols" not in (tmp_path / "home" / "sashimi.toml").read_text()
    assert not (tmp_path / "sashimi.toml").exists()


def test_unknown_settings_warn(tmp_path):
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "sashimi.toml").write_text("[thumbs]\noutput_width = 2560\nquality = 80\n")
    (tmp_path / "sashimi.toml").write_text("[thumbs]\ncols = 4\n[animated_thumbs]\nspeed = 2\n")
    result = runner.invoke(app, ["config", "show"], env={"COLUMNS": "300"})
    warnings = " ".join(result.stderr.split()).split("Warning:")[1:]
    assert len(warnings) == 2
    assert "thumbs.output_width; they're ignored" in warnings[0] and "unset thumbs.output_width" in warnings[0]
    assert "animated_thumbs.speed;" in warnings[1] and "unset --project animated_thumbs.speed" in warnings[1]
    assert "Warning" not in runner.invoke(app, ["thumbs", "--help"]).stderr


def test_resize_command(tmp_path):
    Image.new("RGB", (400, 200), "red").save(tmp_path / "a.jpg")
    result = runner.invoke(app, ["resize", "a.jpg", "--width", "100", "--json"])
    assert result.exit_code == 0, result.output
    with Image.open(tmp_path / "a.jpg.resized.jpg") as img:
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
    with Image.open(tmp_path / "a.png.resized.png") as png, Image.open(tmp_path / "b.jpeg.resized.jpeg") as jpeg:
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

    assert probe(tmp_path / "clip.mkv.gif").width == 160
    assert probe(tmp_path / "clip.mkv.mp3").duration == pytest.approx(2.0, abs=0.1)


@needs_ffmpeg
def test_info_and_rotate(video_blob, tmp_path):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    info = json.loads(runner.invoke(app, ["info", "clip.mkv", "--json"]).stdout)
    assert (info["width"], info["height"]) == (320, 180)
    rotated = runner.invoke(app, ["rotate", "clip.mkv", "90", "--output-format", "webm", "--crf", "45", "--json"])
    assert rotated.exit_code == 0, rotated.output
    info = json.loads(runner.invoke(app, ["info", "clip.mkv.rotated.webm", "--json"]).stdout)
    assert (info["width"], info["height"], info["video_codec"]) == (180, 320, "vp9")


def test_info_on_a_non_media_file(tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    result = runner.invoke(app, ["info", "notes.txt"])
    assert result.exit_code == 1 and "doesn't look like media" in result.stderr


def _commands():
    from typer.main import get_command

    group = get_command(app)
    found = {name: command for name, command in group.commands.items() if not command.hidden}
    config = found.pop("config")
    found |= {f"config {name}": command for name, command in config.commands.items()}
    return found


KIND_WORDS = {
    "image": {"image", "images", "photo", "photos", "photo's"},
    "video": {"video", "videos", "video's"},
    "audio": {"audio"},
    "animation": {"gif", "gifs", "webp", "webps", "animated", "animation", "animations"},
}


def _kinds(text):
    import re

    words = set(re.findall(r"[a-z']+", text.lower()))
    return {kind for kind, names in KIND_WORDS.items() if words & names}


@pytest.mark.parametrize("name", sorted(_commands()))
def test_each_summary_names_the_media_its_help_does(name):
    command = _commands()[name]
    summary = command.short_help or command.help.split("\n\n")[0]
    first_sentence = command.help.split(". ")[0].split(": ")[0]
    assert _kinds(summary) <= _kinds(command.help), (name, summary)
    assert _kinds(first_sentence) <= _kinds(summary), (name, summary, first_sentence)


@pytest.mark.parametrize("name", sorted(_commands()))
def test_help_paragraphs_have_no_hard_line_breaks(name):
    for paragraph in _commands()[name].help.split("\n\n"):
        lines = paragraph.split("\n")
        assert len(lines) == 1 or all(line.startswith("[bold]") for line in lines[1:]), (name, paragraph)


@pytest.mark.parametrize("name", sorted(_commands()) + [""])
def test_help_reads_whole_at_80_columns(name):
    result = runner.invoke(app, [*name.split(), "--help"], env={"COLUMNS": "80"}, terminal_width=80)
    assert result.exit_code == 0
    cut = [line for line in result.stdout.splitlines() if line.rstrip(" │").endswith("…")]
    assert not cut, (name, cut)


def _help_texts():
    from typer.main import get_command

    def walk(command):
        yield command.help or ""
        for param in getattr(command, "params", []):
            yield getattr(param, "help", "") or ""
        for sub in getattr(command, "commands", {}).values():
            yield from walk(sub)

    yield from walk(get_command(app))


def _help_examples():
    """Every ``sashimi …`` command the help shows, as written."""
    import re

    found = set()
    for text in _help_texts():
        text = " ".join(text.split())
        found |= {m.strip() for m in re.findall(r"\[(?:cyan|bold)\](sashimi [^\[]+?)\[/(?:cyan|bold)\]", text)}
        for block in re.findall(r"Examples:(.*?)\[/dim\]", text):
            found |= {part.strip() for part in block.split("·")}
        found |= {m.strip() for m in re.findall(r"\((sashimi [^()]+?)[;)]", text)}
        found |= {m.strip() for m in re.findall(r"e\.g\. (sashimi .+?)\.(?:\s|$)", text)}
    return found


MENTIONS = {
    "sashimi config",
    "sashimi config defaults",
    "sashimi edit --rotate",
    "sashimi frames",
    "sashimi info",
    "sashimi remux --output-format mp4",
}
"""Commands the help names without arguments to run, as references."""


def test_every_help_example_is_listed_here():
    examples = _help_examples()
    assert examples - MENTIONS, examples
    assert MENTIONS <= examples


@pytest.mark.parametrize("example", sorted(_help_examples() - MENTIONS))
def test_every_example_in_the_help_runs_as_written(tmp_path, monkeypatch, example):
    import glob
    import importlib
    import shlex

    for module, name in (
        ("sashimi.core.image", "convert"),
        ("sashimi.core.video", "convert"),
        ("sashimi.core.edit", "edit"),
        ("sashimi.core.edit", "select_frames"),
        ("sashimi.api.resize", "convert"),
        ("sashimi.api.extract", "trim"),
        ("sashimi.api.remux", "trim"),
    ):
        monkeypatch.setattr(importlib.import_module(module), name, lambda *a, **k: None)
    for name in ("photo.jpg", "a.jpg", "b.jpg", "x.png", "clip.gif", "clip.webp"):
        Image.new("RGB", (8, 8)).save(tmp_path / name)
    for name in ("clip.mkv", "clip.mp4"):
        (tmp_path / name).write_bytes(b"video")
    for folder in ("photos", "webp", "90"):
        (tmp_path / folder).mkdir()
        Image.new("RGB", (8, 8)).save(tmp_path / folder / "inside.png")
    words = shlex.split(example)[1:]
    args = [match for word in words for match in (sorted(glob.glob(word)) if "*" in word else [word])]
    processing = args[0] not in ("config", "info", "metadata", "flatten")
    result = runner.invoke(app, [*args, *(["-n", "-y"] if processing else [])], env={"COLUMNS": "200"})
    assert result.exit_code == 0, (example, result.output)
    if processing:
        assert "Plan:" in result.stdout or "Would write" in result.stdout, (example, result.stdout)


@pytest.mark.parametrize(
    "command",
    [
        "convert",
        "remux",
        "resize",
        "rotate",
        "favicon",
        "edit",
        "frames",
        "extract",
        "strip",
        "thumbs",
        "preview",
        "limits",
    ],
)
def test_config_defaults_read_whole_at_80_columns(command):
    result = runner.invoke(app, ["config", "defaults", command], env={"COLUMNS": "80"}, terminal_width=80)
    assert result.exit_code == 0
    cut = [line for line in result.stdout.splitlines() if "…" in line]
    assert not cut, (command, cut)
    if command not in ("convert", "remux", "limits"):
        assert "{input_name}.{role}.{output_ext}" in result.stdout
