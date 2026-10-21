# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Help text shows every option's default, and those defaults match the config models."""

import json
import re

import pytest
import typer
from typer.testing import CliRunner

from sashimi.api._naming import default_output_name
from sashimi import config
from sashimi.cli import settings
from sashimi.cli._defaults import DISCRIMINATORS, flags_for, format_value
from sashimi.cli._limits import format_limit
from sashimi.cli.main import app

runner = CliRunner()
COMMANDS = typer.main.get_command(app).commands  # type: ignore[attr-defined]
NOTE = re.compile(r"\\\[default: (.*?)\]")


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
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
        "--optimize": f"{on(C.JpegImageConfig, 'optimize')} for JPEG, {on(C.PngImageConfig, 'optimize')} for PNG/GIF",
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
        "--output-format": "same as input",
        "--quality": d(C.JpegResizeConfig, "quality"),
        "--background": d(C.JpegImageConfig, "background_color"),
    },
    "extract": {
        "--workers": "min(4, CPUs)",
        "--start": d(C.CopyTrimConfig, "start_seconds"),
        "--end": "the end",
        "--duration": "to the end",
        "--output-format": "from -o, else the input's, else mkv",
    },
    "thumbs": {
        "--workers": "min(4, CPUs)",
        "--output-format": (
            f"{d(C.JpegSheetConfig, 'target')} as .jpg, animated {d(C.AnimatedWebpSheetConfig, 'target')}"
        ),
        "--width": f"{d(C.JpegSheetConfig, 'width')}, animated {d(C.AnimatedWebpSheetConfig, 'width')}",
        "--cols": d(C.JpegSheetConfig, "cols"),
        "--rows": d(C.JpegSheetConfig, "rows"),
        "--gap": d(C.JpegSheetConfig, "gap"),
        "--quality": f"{d(C.JpegSheetConfig, 'quality')}, animated {d(C.AnimatedWebpSheetConfig, 'quality')}",
        "--start-offset": d(C.JpegSheetConfig, "start_offset_percent"),
        "--end-offset": d(C.JpegSheetConfig, "end_offset_percent"),
        "--clip-length": d(C.AnimatedWebpSheetConfig, "sample_duration"),
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
EXPECTED["resize"].update(
    {
        flag: EXPECTED["convert"][flag]
        for flag in ("--optimize", "--subsampling", "--webp-method", "--compress-level")
        + ("--tiff-compression", "--resample")
    }
)
EXPECTED["rotate"] = {flag: EXPECTED["convert"][flag] for flag in ("--workers", "--crf", "--preset", "--audio-bitrate")}
EXPECTED["rotate"]["--output-format"] = "images keep theirs, videos mp4"
EXPECTED["rotate"]["--angle"] = "the last argument, or 90"
EXPECTED["convert"]["--output-format"] = "the last argument"
EXPECTED["rotate"]["--quality"] = f"{d(C.JpegEditConfig, 'quality')}, high, since the image is re-encoded"
P = C.WebpPreviewConfig
EXPECTED["preview"] = {
    "--output-format": "webp",
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
    "--ico-sizes": d(F, "ico_sizes"),
    "--fit": d(F, "fit"),
    "--resample": d(F, "resample"),
    "--background": d(F, "background_color"),
    "--name": "none",
    "--short-name": "none",
    "--theme-color": "none",
    "--manifest-background": "none",
}
E = C.JpegEditConfig
EXPECTED["edit"] = {
    "--rotate": d(E, "rotate"),
    "--flip": d(E, "flip"),
    "--crop": "no crop",
    "--aspect": "free",
    "--brightness": d(E, "brightness"),
    "--contrast": d(E, "contrast"),
    "--saturation": d(E, "saturation"),
    "--output-format": "same as input",
    "--quality": f"{d(E, 'quality')}, high, since the image is re-encoded",
    "--webp-method": d(C.WebpEditConfig, "webp_method"),
    "--compress-level": d(C.PngEditConfig, "png_compress_level"),
    "--optimize": f"{on(E, 'optimize')} for JPEG, {on(C.PngEditConfig, 'optimize')} for PNG/GIF",
    "--subsampling": d(E, "jpeg_subsampling"),
    "--tiff-compression": d(C.TiffEditConfig, "tiff_compression"),
}
EXPECTED["frames"] = {
    "--keep": "all",
    "--drop": "none",
    "--speed": d(C.GifFramesConfig, "speed"),
    "--output-format": "same as input",
    "--quality": f"{d(C.WebpFramesConfig, 'quality')}, high, since the frames are re-encoded",
    "--webp-method": d(C.WebpFramesConfig, "webp_method"),
    "--optimize": on(C.GifFramesConfig, "optimize"),
}
EXPECTED["remux"] = {"--output-format": "mkv, or from -o"}
EXPECTED["strip"] = {"--remove": "remove_all"}
for command, notes in EXPECTED.items():
    notes["--output"] = "named by --output-name, next to the input"
    notes["--output-name"] = default_output_name(command)
EXPECTED["thumbs"]["--output-name"] += ", with the role sheet, or animated-sheet for animated sheets"
for command in ("convert", "resize", "rotate", "extract", "remux", "edit", "frames"):
    EXPECTED[command]["--metadata"] = d(C.JpegImageConfig, "metadata")
L = C.Limits()
LIMIT_NOTES = {
    "--max-pixels": format_limit("max_pixels", L.max_pixels),
    "--max-frame-bytes": format_limit("max_frame_bytes", L.max_frame_bytes),
    "--max-animation-bytes": format_limit("max_animation_bytes", L.max_animation_bytes),
    "--max-sheet-pixels": format_limit("max_sheet_pixels", L.max_sheet_pixels),
}
for command, flags in {
    "convert": ("--max-pixels", "--max-frame-bytes"),
    "resize": ("--max-pixels",),
    "favicon": ("--max-pixels",),
    "edit": ("--max-pixels", "--max-animation-bytes"),
    "rotate": ("--max-pixels", "--max-animation-bytes"),
    "frames": ("--max-pixels", "--max-animation-bytes"),
    "thumbs": ("--max-sheet-pixels", "--max-frame-bytes"),
    "preview": ("--max-frame-bytes",),
}.items():
    EXPECTED[command].update({flag: LIMIT_NOTES[flag] for flag in flags})
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
    "rotate": [*C.VIDEO_TRANSFORM.models, *C.IMAGE_EDIT.models],
    "extract": [C.H264TrimConfig, C.Vp9TrimConfig],
    "thumbs": list(C.CONTACT_SHEET.models),
    "preview": list(C.VIDEO_PREVIEW.models),
    "favicon": [C.FaviconPackConfig],
    "edit": list(C.IMAGE_EDIT.models),
    "frames": list(C.IMAGE_FRAMES.models),
    "remux": [C.CopyTrimConfig],
    "strip": [],
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
            if flag in NO_DEFAULT or (command == "edit" and field.startswith("crop_")):
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
        '[thumbs]\nwidth = 1920\ngap = 5\nloop = false\nheader_bg_color = "#202020"\noutput_name = "{input_name}.jpeg"\n'
        '[animated_thumbs]\ntarget = "gif"\nwidth = 960\n'
    )
    output = " ".join(runner.invoke(app, ["thumbs", "--help"], env={"COLUMNS": "250"}).output.replace("│", " ").split())
    assert "[default: 2560, animated 1280; sashimi.toml: 1920, animated 960]" in output
    assert "[default: jpeg as .jpg, animated webp; sashimi.toml: animated gif]" in output
    assert "[default: #000000; sashimi.toml: #202020]" in output
    assert "[sashimi.toml: loop = false]" in output
    assert "[default: 5]." in output
    assert "sheets; sashimi.toml: {input_name}.jpeg]" in output
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
    assert [section["model"] for section in report] == [None, "Mp4TransformConfig", "WebmTransformConfig"]
    names = {row["field"]: row["default"] for row in report[0]["fields"]}
    assert names == {"output_name": "{input_name}.{role}.{output_ext}", "output_dir": None}
    rotate = next(row for row in report[1]["fields"] if row["field"] == "rotate")
    assert (rotate["option"], rotate["default"], rotate["model_default"]) == ("angle", 90, 0)
    crf = next(row for row in report[1]["fields"] if row["field"] == "crf")
    assert (crf["default"], crf["accepted"]) == (23, "0-51")
    assert all(row["description"] for section in report for row in section["fields"])


def test_config_defaults_shows_sashimi_toml_for_thumbs(tmp_path):
    (tmp_path / "sashimi.toml").write_text("[thumbs]\ngap = 2\n")
    report = json.loads(runner.invoke(app, ["config", "defaults", "thumbs", "png", "--json"]).stdout)
    gap = next(row for row in report[1]["fields"] if row["field"] == "gap")
    assert (gap["default"], gap["sashimi_toml"]) == (5, 2)


def test_config_defaults_shows_each_commands_output_name(tmp_path):
    (tmp_path / "sashimi.toml").write_text(
        '[thumbs]\noutput_name = "{input_name}.jpeg"\noutput_dir = "sheets"\n'
        '[convert]\noutput_name = "{input_stem}.{output_ext}"\n'
    )
    thumbs = json.loads(runner.invoke(app, ["config", "defaults", "thumbs", "--json"]).stdout)[0]
    assert thumbs["title"] == "Output names and folder"
    rows = [(row["option"], row["default"], row.get("sashimi_toml")) for row in thumbs["fields"]]
    assert rows == [
        ("--output-name", "{input_name}.{role}.{output_ext}", "{input_name}.jpeg"),
        ("--output-name -a", "{input_name}.{role}.{output_ext}", None),
        ("--output-dir", None, "sheets"),
        ("--output-dir -a", None, None),
    ]
    text = runner.invoke(app, ["config", "defaults", "thumbs"], env={"COLUMNS": "200"}).stdout
    assert "next to each input" in text and "sashimi.toml: sheets" in text
    convert = json.loads(runner.invoke(app, ["config", "defaults", "convert", "--json"]).stdout)[0]["fields"]
    assert [(row["field"], row["default"], row.get("sashimi_toml")) for row in convert] == [
        ("output_name", "{input_name}.{output_ext}", "{input_stem}.{output_ext}"),
        ("output_dir", None, None),
    ]
    for command, default in (("remux", "{input_name}.{output_ext}"), ("strip", "{input_name}.{role}.{output_ext}")):
        report = json.loads(runner.invoke(app, ["config", "defaults", command, "--json"]).stdout)
        assert [section["title"] for section in report] == ["Output names and folder"]
        assert report[0]["fields"][0]["default"] == default
    assert runner.invoke(app, ["config", "defaults", "strip", "mp4"]).exit_code == 2


def test_config_defaults_unknown_format():
    result = runner.invoke(app, ["config", "defaults", "rotate", "gif"])
    assert result.exit_code == 2 and "mp4, webm" in result.stderr


@pytest.mark.parametrize(
    "operation",
    ["convert", "remux", "resize", "rotate", "favicon", "edit", "frames", "extract", "strip", "thumbs", "preview"]
    + ["limits"],
)
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
    background = next(r for r in json.loads(ico)[1]["fields"] if r["field"] == "background_color")
    assert (background["option"], background["default"], background["accepted"]) == ("--background", None, "#rrggbb")
