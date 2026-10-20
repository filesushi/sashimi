# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Help text shows every option's default, and those defaults match the config models."""

import json
import re

import pytest
import typer
from typer.testing import CliRunner

from sashimi import config
from sashimi.cli import settings
from sashimi.cli._defaults import DISCRIMINATORS, flags_for, format_value
from sashimi.cli.main import app

runner = CliRunner()
COMMANDS = typer.main.get_command(app).commands  # type: ignore[attr-defined]
NOTE = re.compile(r"\\\[default: (.*?)\]")


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "GLOBAL_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    settings.load_raw_config.cache_clear()
    yield
    settings.load_raw_config.cache_clear()


def d(model, field):
    return format_value(model.model_fields[field].default, field)


def on(model, field):
    return "on" if model.model_fields[field].default else "off"


C = config
EXPECTED = {
    "convert": {
        "--workers": "min(4, CPUs)",
        "--width": f"source width; video to GIF/WebP {d(C.GifVideoConfig, 'max_width')}",
        "--height": "source height",
        "--start": d(C.Mp4VideoConfig, "start_seconds"),
        "--duration": f"to the end; GIF/WebP {d(C.GifVideoConfig, 'duration_seconds')}s",
        "--quality": f"{d(C.JpegImageConfig, 'quality')}; animated WebP {d(C.WebpVideoConfig, 'quality')}",
        "--background": f"{d(C.JpegImageConfig, 'background_color')}; ICO transparent",
        "--optimize": f"{on(C.JpegImageConfig, 'optimize')} for JPEG/WebP, {on(C.PngImageConfig, 'optimize')} for PNG/GIF",
        "--subsampling": d(C.JpegImageConfig, "jpeg_subsampling"),
        "--webp-method": d(C.WebpImageConfig, "webp_method"),
        "--compress-level": d(C.PngImageConfig, "png_compress_level"),
        "--tiff-compression": d(C.TiffImageConfig, "tiff_compression"),
        "--resample": d(C.JpegImageConfig, "resample"),
        "--ico-sizes": d(C.IcoConfig, "sizes"),
        "--ico-fit": d(C.IcoConfig, "fit"),
        "--fps": d(C.GifVideoConfig, "fps"),
        "--crf": f"WebM {d(C.WebmVideoConfig, 'crf')}, MP4 {d(C.Mp4VideoConfig, 'crf')}",
        "--preset": d(C.Mp4VideoConfig, "preset"),
        "--speed": d(C.WebmVideoConfig, "speed"),
        "--audio-bitrate": (
            f"WebM {d(C.WebmVideoConfig, 'audio_bitrate_kbps')}, MP4 {d(C.Mp4VideoConfig, 'audio_bitrate_kbps')}"
        ),
        "--dither": d(C.GifVideoConfig, "dither"),
        "--palette": d(C.GifVideoConfig, "palette_mode"),
        "--bitrate": (
            f"MP3 {d(C.Mp3AudioConfig, 'bitrate_kbps')}, M4A {d(C.M4aAudioConfig, 'bitrate_kbps')}, "
            f"OGG {d(C.OggAudioConfig, 'bitrate_kbps')}"
        ),
        "--sample-rate": "source",
        "--bit-depth": d(C.WavAudioConfig, "wav_bit_depth"),
        "--flac-level": d(C.FlacAudioConfig, "flac_compression_level"),
    },
    "resize": {
        "--format": "same as input",
        "--quality": d(C.JpegResizeConfig, "quality"),
        "--background": d(C.JpegImageConfig, "background_color"),
        "--output": "<name>.resized.<ext> next to the input",
    },
    "extract": {
        "--output": "<name>.extracted.<ext> next to the input",
        "--workers": "min(4, CPUs)",
        "--start": d(C.CopyTrimConfig, "start_seconds"),
        "--end": "the end",
        "--duration": "to the end",
        "--format": "from -o, else the input's, else mkv",
    },
    "thumbs": {
        "--workers": "min(4, CPUs)",
        "--format": f"{d(C.JpegSheetConfig, 'target')}, animated {d(C.AnimatedWebpSheetConfig, 'target')}",
        "--width": f"{d(C.JpegSheetConfig, 'width')}, animated {d(C.AnimatedWebpSheetConfig, 'width')}",
        "--cols": d(C.JpegSheetConfig, "cols"),
        "--rows": d(C.JpegSheetConfig, "rows"),
        "--gap": d(C.JpegSheetConfig, "gap"),
        "--quality": f"{d(C.JpegSheetConfig, 'quality')}, animated {d(C.AnimatedWebpSheetConfig, 'quality')}",
        "--start-offset": d(C.JpegSheetConfig, "start_offset_percent"),
        "--end-offset": d(C.JpegSheetConfig, "end_offset_percent"),
        "--clip-length": d(C.AnimatedWebpSheetConfig, "sample_duration"),
        "--output": "<name>.sheet.jpg (animated: <name>.animated-sheet.webp)",
        "--fps": d(C.AnimatedWebpSheetConfig, "fps"),
        "--bg-color": d(C.JpegSheetConfig, "grid_bg_color"),
        "--header-bg": d(C.JpegSheetConfig, "header_bg_color"),
        "--header-color": d(C.JpegSheetConfig, "header_text_color"),
        "--header-font": "a system font",
        "--header-size": d(C.JpegSheetConfig, "header_font_size"),
        "--timestamp-bg": d(C.JpegSheetConfig, "timestamp_bg_color"),
        "--timestamp-color": d(C.JpegSheetConfig, "timestamp_text_color"),
        "--timestamp-font": "a system font",
        "--timestamp-size": d(C.JpegSheetConfig, "timestamp_font_size"),
    },
}
EXPECTED["convert"]["--output"] = "<name>.<format> next to the input"
EXPECTED["resize"].update(
    {
        flag: EXPECTED["convert"][flag]
        for flag in ("--optimize", "--subsampling", "--webp-method", "--compress-level")
        + ("--tiff-compression", "--resample")
    }
)
EXPECTED["rotate"] = {flag: EXPECTED["convert"][flag] for flag in ("--workers", "--crf", "--preset", "--audio-bitrate")}
EXPECTED["rotate"].update({"--format": "mp4", "--output": "<name>.rotated.mp4 next to the input"})
P = C.WebpPreviewConfig
EXPECTED["preview"] = {
    "--format": "webp",
    "--output": "<name>.preview.webp next to the video",
    "--workers": "min(4, CPUs)",
    "--samples": d(P, "samples"),
    "--clip-length": d(P, "sample_duration"),
    "--start-offset": d(P, "start_offset_percent"),
    "--end-offset": d(P, "end_offset_percent"),
    "--height": d(P, "max_height"),
    "--width": "no limit",
    "--crop": d(P, "crop"),
    "--fps": d(P, "fps"),
    "--quality": d(P, "quality"),
}
F = C.FaviconPackConfig
EXPECTED["favicon"] = {
    "--output": "<name>.favicons.zip next to the image",
    "--ico-sizes": d(F, "ico_sizes"),
    "--fit": d(F, "fit"),
    "--resample": d(F, "resample"),
    "--background": d(F, "background_color"),
    "--name": "none",
    "--short-name": "none",
    "--theme-color": "none",
    "--manifest-background": "none",
}
EXPECTED["remux"] = {"--format": "mkv, or from -o", "--output": "<name>.mkv next to the input"}
for notes in EXPECTED.values():
    notes["--output-dir"] = "next to each input"
EXPECTED["extract"].update({flag: EXPECTED["rotate"][flag] for flag in ("--crf", "--preset", "--audio-bitrate")})
EXPECTED["convert"]["--speed"] = EXPECTED["rotate"]["--speed"] = EXPECTED["extract"]["--speed"] = d(
    C.WebmVideoConfig, "speed"
)
NO_DEFAULT = {"--width", "--height", "--start", "--end", "--emoji-dir"}
"""Options with nothing to default to: required ones, and the emoji folder."""
MODELS = {
    "convert": [*C.IMAGE_CONVERT.models, C.IcoConfig, *C.VIDEO_CONVERT.models, *C.AUDIO_CONVERT.models],
    "resize": list(C.IMAGE_RESIZE.models),
    "rotate": list(C.VIDEO_TRANSFORM.models),
    "extract": [C.H264TrimConfig, C.Vp9TrimConfig],
    "thumbs": list(C.CONTACT_SHEET.models),
    "preview": list(C.VIDEO_PREVIEW.models),
    "favicon": [C.FaviconPackConfig],
    "remux": [],
}


def _options(command):
    return {param.opts[0]: param for param in COMMANDS[command].params if param.param_type_name == "option"}


def _note(param):
    found = NOTE.findall(param.help or "")
    return found[0] if found else None


@pytest.mark.parametrize("command", sorted(EXPECTED))
def test_help_defaults_match_the_models(command):
    options = _options(command)
    shown = {flag: _note(param) for flag, param in options.items() if _note(param) is not None}
    assert shown == EXPECTED[command]


@pytest.mark.parametrize("command", sorted(EXPECTED))
def test_every_valued_option_names_its_default(command):
    for flag, param in _options(command).items():
        if param.is_flag or flag == "--help" or flag in NO_DEFAULT and command in ("resize", "extract"):
            continue
        has_typer_default = param.default is not None and param.show_default is not False
        assert _note(param) is not None or has_typer_default or flag in NO_DEFAULT, flag
        assert "[default:" not in (param.help or "").replace("\\[default:", ""), flag


@pytest.mark.parametrize("command", sorted(EXPECTED))
def test_every_model_default_appears_in_the_help(command):
    flags = flags_for(command)
    options = _options(command)
    for model in MODELS[command]:
        for field, info in model.model_fields.items():
            flag = flags.get(field)
            if flag is None or flag.split("/")[0] not in options or info.default is None:
                continue
            if isinstance(info.default, bool) or field in DISCRIMINATORS:
                continue
            if flag in NO_DEFAULT:
                continue
            param = options[flag.split("/")[0]]
            note = _note(param) or str(getattr(param.default, "value", param.default))
            assert note is not None and format_value(info.default, field) in note, (model.__name__, field, note)


def test_help_shows_the_defaults():
    output = runner.invoke(app, ["convert", "--help"], env={"COLUMNS": "200"}).output
    assert "[default: 85; animated WebP 75]" in output
    assert "WebM 34, MP4 23" in output


def test_thumbs_help_shows_sashimi_toml_values(tmp_path):
    (tmp_path / "sashimi.toml").write_text(
        '[thumbs]\nstatic_width = 1920\ngap = 5\nloop = false\nheader_bg_color = "#202020"\n'
        '[animated_thumbs]\noutput_format = "gif"\nanimated_width = 960\n'
    )
    output = " ".join(runner.invoke(app, ["thumbs", "--help"], env={"COLUMNS": "250"}).output.split())
    assert "[default: 2560, animated 1280; sashimi.toml: 1920, animated 960]" in output
    assert "[default: jpeg, animated webp; sashimi.toml: animated gif]" in output
    assert "[default: #000000; sashimi.toml: #202020]" in output
    assert "[sashimi.toml: loop = false]" in output
    assert "[default: 5]." in output
    assert not (tmp_path / "home").exists()
    plain = " ".join(runner.invoke(app, ["thumbs", "--help"], env={"COLUMNS": "250"}).output.split())
    assert plain.count("sashimi.toml: 1920") == 1


def test_config_defaults_table():
    output = runner.invoke(app, ["config", "defaults", "convert", "jpg"], env={"COLUMNS": "200"}).output
    assert "Images → JPEG" in output and "JpegImageConfig" in output and "4:2:0" in output
    assert "Videos" not in output


def test_config_defaults_json_lists_every_field():
    result = runner.invoke(app, ["config", "defaults", "rotate", "--json"])
    report = json.loads(result.stdout)
    assert [section["model"] for section in report] == ["Mp4TransformConfig", "WebmTransformConfig"]
    rotate = next(row for row in report[0]["fields"] if row["field"] == "rotate")
    assert (rotate["option"], rotate["default"], rotate["model_default"]) == ("angle", 90, 0)
    crf = next(row for row in report[0]["fields"] if row["field"] == "crf")
    assert (crf["default"], crf["accepted"]) == (23, "0-51")
    assert all(row["description"] for section in report for row in section["fields"])


def test_config_defaults_shows_sashimi_toml_for_thumbs(tmp_path):
    (tmp_path / "sashimi.toml").write_text("[thumbs]\ngap = 2\n")
    report = json.loads(runner.invoke(app, ["config", "defaults", "thumbs", "png", "--json"]).stdout)
    gap = next(row for row in report[0]["fields"] if row["field"] == "gap")
    assert (gap["default"], gap["sashimi_toml"]) == (5, 2)


def test_config_defaults_unknown_format():
    result = runner.invoke(app, ["config", "defaults", "rotate", "gif"])
    assert result.exit_code == 2 and "mp4, webm" in result.stderr


@pytest.mark.parametrize("operation", ["convert", "resize", "rotate", "favicon", "extract", "thumbs", "preview"])
def test_config_defaults_renders_every_command(operation):
    result = runner.invoke(app, ["config", "defaults", operation], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    report = json.loads(runner.invoke(app, ["config", "defaults", operation, "--json"]).stdout)
    assert report and all(row["description"] for section in report for row in section["fields"])


def test_config_defaults_for_new_outputs():
    output = " ".join(
        runner.invoke(app, ["config", "defaults", "preview", "webp"], env={"COLUMNS": "250"}).stdout.split()
    )
    assert "--width max_width │ no limit" in output
    ico = runner.invoke(app, ["config", "defaults", "convert", "ico", "--json"]).stdout
    background = next(r for r in json.loads(ico)[0]["fields"] if r["field"] == "background_color")
    assert (background["option"], background["default"], background["accepted"]) == ("--background", None, "#rrggbb")
