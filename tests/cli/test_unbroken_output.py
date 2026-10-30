# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
No path, file name or command the CLI prints is ever broken by an inserted newline or cut short,
whatever the width. These run under CliRunner without COLUMNS, as a pipe or a log sees the output
(80 columns), with paths far longer than that.
"""

import io
import logging
import pathlib
import shlex
import shutil

import pytest
from PIL import Image
from typer.testing import CliRunner

import sashimi
from sashimi.cli import _output, settings
from sashimi.cli.main import app
from sashimi.core import _ffmpeg

runner = CliRunner()

FOLDER = pathlib.Path("holiday photos from summer 2026", "sorted by the day taken")
NARROW = {"COLUMNS": None, "LINES": None}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv(_ffmpeg.ENVIRONMENT, raising=False)
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    settings.load_raw_config.cache_clear()
    _ffmpeg.use_setting(None)
    (tmp_path / FOLDER).mkdir(parents=True)
    yield tmp_path
    sashimi.set_ffmpeg_paths()
    _ffmpeg.use_setting(None)
    settings.load_raw_config.cache_clear()


def invoke(*args: str, **env: str | None):
    return runner.invoke(app, list(args), env={**NARROW, **env})


def assert_whole(output: str, *texts: str) -> None:
    """Every text is on one line of `output`, unbroken, and nothing was cut short with an ellipsis."""
    lines = output.splitlines()
    for text in texts:
        assert any(text in line for line in lines), (text, output)
    assert "…" not in output, output


def picture(name: str) -> pathlib.Path:
    path = FOLDER / name
    Image.new("RGB", (16, 12), "red").save(path)
    return path


def animation(name: str) -> pathlib.Path:
    path = FOLDER / name
    frames = [Image.new("RGB", (16, 12), colour) for colour in ("red", "green", "blue")]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    return path


def video(name: str) -> pathlib.Path:
    path = FOLDER / name
    path.write_bytes(b"video")
    return path


PICTURE = "the-photo-from-the-top-of-the-mountain-on-day-two.png"
ANIMATION = "the-little-animation-of-the-waves-on-the-beach.gif"
VIDEO = "the-video-of-the-whole-walk-up-the-mountain.mp4"

PROCESSING = {
    "convert": ([PICTURE, "webp"], PICTURE + ".webp"),
    "resize": ([PICTURE, "-w", "8"], PICTURE + ".resized.png"),
    "rotate": ([PICTURE, "90"], PICTURE + ".rotated.png"),
    "favicon": ([PICTURE], PICTURE + ".favicons.zip"),
    "edit": ([PICTURE, "--rotate", "90"], PICTURE + ".edited.png"),
    "frames": ([ANIMATION, "--keep", "1-2"], ANIMATION + ".frames.gif"),
    "strip": ([PICTURE], PICTURE + ".stripped.png"),
    "remux": ([VIDEO], VIDEO + ".mkv"),
    "extract": ([VIDEO, "-s", "1"], VIDEO + ".extracted.mp4"),
    "thumbs": ([VIDEO], VIDEO + ".sheet.jpg"),
    "preview": ([VIDEO], VIDEO + ".preview.webp"),
}
"""Every processing command: its arguments (file names in FOLDER) and the output name it gives."""

SOURCES = {PICTURE: picture, ANIMATION: animation, VIDEO: video}


def arguments(command: str) -> tuple[list[str], pathlib.Path, str]:
    args, output = PROCESSING[command]
    named = next(arg for arg in args if arg in SOURCES)
    source = SOURCES[named](named)
    return [str(source) if arg == named else arg for arg in args], source, output


def test_every_processing_command_is_covered():
    commands = {command.name for command in app.registered_commands if not command.hidden}
    assert commands - set(PROCESSING) == {"info", "metadata", "flatten"}


@pytest.mark.parametrize("command", [*PROCESSING, "info", "metadata", "flatten"])
def test_a_missing_path_is_named_whole(command):
    missing = str(FOLDER / "a-file-that-is-not-there-at-all-anywhere-in-this-folder.png")
    args = (
        [missing if arg in SOURCES else arg for arg in PROCESSING[command][0]] if command in PROCESSING else [missing]
    )
    result = invoke(command, *args)
    assert result.exit_code != 0
    assert_whole(result.output, missing)


@pytest.mark.parametrize("command", PROCESSING)
def test_a_dry_run_names_the_file_and_its_output_whole(command):
    args, source, output = arguments(command)
    result = invoke(command, *args, "-n", "-y")
    assert result.exit_code == 0, result.output
    assert_whole(result.stdout, f"{source}: Would write {output}")


@pytest.mark.parametrize("command", ["convert", "resize", "rotate", "favicon", "edit", "frames", "strip"])
def test_a_run_names_the_file_and_its_output_whole(command):
    args, source, output = arguments(command)
    result = invoke(command, *args)
    assert result.exit_code == 0, result.output
    assert_whole(result.stdout, str(source), output)
    assert (FOLDER / output).is_file()
    again = invoke(command, *args)
    assert_whole(again.stdout, str(source), output)


def test_several_files_too_long_for_a_table_print_one_line_each():
    names = [picture(PICTURE), picture("another-photo-from-a-little-further-up-the-hill.png")]
    result = invoke("convert", *map(str, names), "webp")
    assert result.exit_code == 0, result.output
    for name in names:
        assert_whole(result.stdout, f"Success: {name}: Converted to {name.name}.webp")
    assert "┃ File" not in result.stdout


def test_several_files_that_fit_still_get_a_table(tmp_path):
    Image.new("RGB", (8, 8)).save(tmp_path / "a.png")
    Image.new("RGB", (8, 8)).save(tmp_path / "b.png")
    result = invoke("resize", "a.png", "b.png", "-w", "4")
    assert result.exit_code == 0, result.output
    assert any(f"{border} File" in result.stdout for border in "┃│") and "│ a.png " in result.stdout
    assert max(len(line) for line in result.stdout.splitlines()) <= 80


@pytest.mark.parametrize("command", ["info", "metadata"])
def test_info_names_the_file_whole(command):
    source = picture(PICTURE)
    result = invoke(command, str(source))
    assert result.exit_code == 0, result.output
    assert_whole(result.stdout, str(source))


def test_metadata_shows_a_file_name_that_looks_like_markup_as_it_is():
    source = picture("[red]not-red.png")
    result = invoke("metadata", str(source))
    assert result.exit_code == 0, result.output
    assert_whole(result.stdout, str(source))


@pytest.mark.parametrize("command", ["info", "thumbs", "remux"])
def test_a_video_with_a_long_name_that_looks_like_markup_is_named_whole(video_blob, command):
    source = FOLDER / "[red]the-video-of-the-whole-walk-up-the-mountain.mkv"
    shutil.copyfile(video_blob, source)
    result = invoke(command, str(source), *(["--output-format", "mp4"] if command == "remux" else []))
    assert result.exit_code == 0, result.output
    assert_whole(result.stdout, str(source))


def test_flatten_names_a_file_given_as_its_folder_whole():
    source = picture(PICTURE)
    result = invoke("flatten", str(source))
    assert result.exit_code == 2
    assert_whole(result.output, f"{source} is a file")


def test_a_mixed_folder_refusal_names_the_folder_whole():
    picture(PICTURE)
    video(VIDEO)
    result = invoke("convert", str(FOLDER), "webp")
    assert result.exit_code == 2
    assert_whole(result.output, f"{FOLDER} contains 1 image and 1 video")


def test_the_format_note_names_the_file_and_command_whole():
    source = picture(PICTURE)
    pathlib.Path("webp").mkdir()
    result = invoke("convert", str(source), "webp", "-n")
    assert result.exit_code == 0, result.output
    assert_whole(result.output, f"sashimi convert {shlex.quote(str(source))} ./webp webp")


def test_config_set_ffmpeg_path_names_the_path_and_the_fix_whole(tmp_path):
    missing = str(tmp_path / FOLDER / "ffmpeg-8.1.3-built-here" / "bin" / "ffmpeg")
    result = invoke("config", "set", "ffmpeg.path", missing)
    assert result.exit_code == 0, result.output
    written = str(settings.USER_CONFIG_DIR / "sashimi.toml")
    assert_whole(result.stdout, f"Set ffmpeg.path = {missing!r} in {written}")
    assert_whole(result.stderr, f"Warning: {missing}, from ffmpeg.path in {written}, doesn't exist.")
    assert_whole(
        result.stderr, "sashimi config set ffmpeg.path <an FFmpeg's ffmpeg>", "sashimi config unset ffmpeg.path"
    )
    info = invoke("info", "--ffmpeg")
    assert info.exit_code == 1
    assert_whole(info.output, missing)


def test_a_missing_ffmpeg_from_the_environment_is_named_whole(tmp_path):
    missing = str(tmp_path / FOLDER / "ffmpeg-8.1.3-built-here" / "bin" / "ffmpeg")
    result = invoke("info", "--ffmpeg", **{_ffmpeg.ENVIRONMENT: missing})
    assert result.exit_code == 1
    assert_whole(result.output, missing)


def test_config_set_output_dir_names_the_folder_and_the_pattern_command_whole():
    folder = str(FOLDER / "contact sheets of every video")
    result = invoke("config", "set", "thumbs.output_dir", folder)
    assert result.exit_code == 0, result.output
    assert_whole(result.stdout, f"thumbs.output_dir = {folder!r}")
    pattern = f"{pathlib.PurePosixPath(*FOLDER.parts, 'contact sheets of every video')}/{{input_name}}.{{role}}.{{output_ext}}"
    assert_whole(result.stdout, f"sashimi config set thumbs.output_name '{pattern}'")
    shown = invoke("config", "defaults", "thumbs")
    assert_whole(shown.stdout, f"sashimi.toml: {folder}")


def test_config_show_path_and_unset_name_the_settings_file_whole(tmp_path, monkeypatch):
    home = tmp_path / FOLDER / "a-home-folder-with-a-long-name"
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", home)
    written = str(home / "sashimi.toml")
    assert invoke("config", "set", "thumbs.output_name", "{input_name}.jpeg").exit_code == 0
    assert_whole(invoke("config", "show").stdout, f"# Your settings: {written}")
    assert_whole(invoke("config", "path").stdout, written)
    assert_whole(invoke("config", "unset", "thumbs.output_name").stdout, f"Removed thumbs.output_name from {written}")
    assert_whole(invoke("config", "unset", "thumbs.output_name").stdout, f"thumbs.output_name isn't set in {written}.")


def test_unknown_settings_warning_names_the_file_whole(tmp_path, monkeypatch):
    home = tmp_path / FOLDER / "a-home-folder-with-a-long-name"
    home.mkdir()
    (home / "sashimi.toml").write_text("[thumbs]\nsomething_sashimi_does_not_know = 1\n")
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", home)
    result = invoke("info", "--ffmpeg")
    assert_whole(result.output, f"Warning: {home / 'sashimi.toml'} has settings sashimi doesn't use")


def test_messages_are_not_coloured_by_guesswork():
    for console in (_output.console, _output.err_console):
        text = console.render_str("Run sashimi config set ffmpeg.path <an FFmpeg's ffmpeg> for 8.1 at /a/b 'c'")
        assert not text.spans


def test_a_log_line_is_never_broken(tmp_path, monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    handler = _output.LineLogHandler()
    logger = logging.getLogger("sashimi.tests.unbroken")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        path = str(tmp_path / FOLDER / VIDEO)
        logger.warning("Reading %s with ffmpeg -i %s -f null -", path, path)
    finally:
        logger.removeHandler(handler)
        logger.propagate = True
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1, lines
    assert f"WARNING  Reading {path} with ffmpeg -i {path} -f null -" in lines[0]


def test_name_clashes_and_the_suggested_command_are_named_whole():
    first = picture(PICTURE)
    (FOLDER / "and a subfolder").mkdir()
    second = FOLDER / "and a subfolder" / PICTURE
    shutil.copyfile(first, second)
    out = "the output folder with a long name"
    result = invoke("resize", str(first), str(FOLDER / "and a subfolder"), "-w", "4", "--output-dir", out)
    assert result.exit_code == 1, result.output
    written = str(pathlib.Path(out, f"{PICTURE}.resized.png"))
    assert_whole(result.stdout, f"{first}: Clashes with {second}: both would be written as {written}.")
    assert_whole(result.stdout, f"{second}: Clashes with {first}: both would be written as {written}.")
    assert_whole(result.stdout, f"sashimi resize {shlex.quote(str(FOLDER))} -r -w 4 --output-dir {shlex.quote(out)}")
