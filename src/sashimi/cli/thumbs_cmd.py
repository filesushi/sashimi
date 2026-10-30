# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""`sashimi thumbs` (still and animated contact sheets) and `sashimi preview`."""

from __future__ import annotations

import pathlib
from enum import Enum
from typing import Annotated, Any, List, Optional

import typer

from sashimi.cli import _options as opt
from sashimi.cli._output import BatchRunner, exit_on_invalid_options, fail, refusal, refusals, warn_ignored, warn_unused

FLAGS = {
    "width": "--width",
    "cols": "--cols",
    "rows": "--rows",
    "gap": "--gap",
    "quality": "--quality",
    "target": "--output-format",
    "sample_duration": "--clip-length",
    "fps": "--fps",
    "loop": "--no-loop",
    "show_header": "--no-header",
    "show_timestamps": "--no-timestamps",
    "start_offset_percent": "--start-offset",
    "end_offset_percent": "--end-offset",
    "grid_bg_color": "--bg-color",
    "header_bg_color": "--header-bg",
    "header_text_color": "--header-color",
    "header_font_size": "--header-size",
    "timestamp_bg_color": "--timestamp-bg",
    "timestamp_text_color": "--timestamp-color",
    "timestamp_font_size": "--timestamp-size",
    "header_font_path": "--header-font",
    "timestamp_font_path": "--timestamp-font",
    "emoji_dir": "--emoji-dir",
}


class SheetFormat(str, Enum):
    jpeg = "jpeg"
    jpg = "jpg"
    png = "png"
    webp = "webp"
    gif = "gif"


class ThumbsCommand(opt.KeptCommand):
    """Shows the sashimi.toml values that replace built-in defaults in ``sashimi thumbs --help``."""

    def toml_notes(self, ctx: Any, raw: dict[str, Any]) -> dict[str, str]:
        from sashimi.cli import settings
        from sashimi.cli._defaults import toml_notes

        builtin = settings.DEFAULT_CONFIG_DICT
        unchanged = all(raw.get(name, {}) == builtin.get(name, {}) for name in ("thumbs", "animated_thumbs"))
        return {} if unchanged else toml_notes(raw)


def _option(*names: str, help: str, panel: str = opt.SHEET, **kwargs: Any) -> Any:
    return opt.option(*names, help=help, panel=panel, **kwargs)


def thumbs_cmd(
    paths: Annotated[List[pathlib.Path], typer.Argument(help="Video files or directories.", show_default=False)],
    animated: Annotated[
        bool, _option("-a", "--animated", help="Animated WebP/GIF sheet of short clips.", panel=opt.GENERAL)
    ] = False,
    output: Annotated[Optional[pathlib.Path], opt.output_option()] = None,
    output_name: Annotated[
        Optional[str],
        opt.output_name_option(
            "thumbs", "{input_name}.{role}.{output_ext}, with the role sheet, or animated-sheet for animated sheets"
        ),
    ] = None,
    output_dir: Annotated[Optional[pathlib.Path], opt.output_dir_option("thumbs")] = None,
    no_output_dir: opt.NoOutputDir = False,
    recursive: opt.Recursive = False,
    force: opt.Force = False,
    yes: opt.Yes = False,
    dry_run: opt.DryRun = False,
    workers: opt.Workers = None,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
    output_format: Annotated[
        Optional[SheetFormat],
        _option(
            "--output-format",
            metavar="FORMAT",
            help="jpeg (or jpg), png or webp (still); webp or gif (animated), written with the extension "
            "as typed [default: jpeg as .jpg, animated webp].",
            panel=opt.GENERAL,
        ),
    ] = None,
    width: Annotated[
        Optional[int], _option("--width", "-w", help="Sheet width in pixels [default: 2560, animated 1280].")
    ] = None,
    cols: Annotated[
        Optional[str],
        _option("--cols", help="Columns, or 'auto': from the aspect ratio; animated 3, portrait 4 [default: auto]."),
    ] = None,
    rows: Annotated[
        Optional[str],
        _option("--rows", help="Rows, or 'auto': from the aspect ratio; animated 3, portrait 2 [default: auto]."),
    ] = None,
    gap: Annotated[Optional[int], _option("--gap", help="Pixels between tiles [default: 5].")] = None,
    quality: Annotated[
        Optional[str], _option("--quality", "-q", help="JPEG/WebP quality [default: 85, animated 75].")
    ] = None,
    start_offset: Annotated[
        Optional[float], _option("--start-offset", help="Skip this % of the start [default: 7].")
    ] = None,
    end_offset: Annotated[Optional[float], _option("--end-offset", help="Skip this % of the end [default: 7].")] = None,
    clip_length: Annotated[
        Optional[float], _option("--clip-length", help="[Animated] Seconds per clip [default: 2].")
    ] = None,
    fps: Annotated[Optional[int], _option("--fps", help="[Animated] Frame rate, 1-50 [default: 12].")] = None,
    no_loop: Annotated[bool, _option("--no-loop", help="[Animated] Play once instead of looping.")] = False,
    no_header: Annotated[
        bool, _option("--no-header", help="Leave out the file details header.", panel=opt.STYLE)
    ] = False,
    no_timestamps: Annotated[
        bool, _option("--no-timestamps", help="Leave out the time labels on tiles.", panel=opt.STYLE)
    ] = False,
    bg_color: Annotated[
        Optional[str], _option("--bg-color", help="Grid background [default: #141414].", panel=opt.STYLE)
    ] = None,
    header_bg: Annotated[
        Optional[str], _option("--header-bg", help="Header background [default: #000000].", panel=opt.STYLE)
    ] = None,
    header_color: Annotated[
        Optional[str], _option("--header-color", help="Header text colour [default: #ffffff].", panel=opt.STYLE)
    ] = None,
    header_font: Annotated[
        Optional[pathlib.Path],
        _option(
            "--header-font", help="TrueType/OpenType font for the header [default: a system font].", panel=opt.STYLE
        ),
    ] = None,
    header_size: Annotated[
        Optional[int], _option("--header-size", help="Header font size [default: 18].", panel=opt.STYLE)
    ] = None,
    timestamp_bg: Annotated[
        Optional[str],
        _option("--timestamp-bg", help="Time label background, with alpha [default: #000000b4].", panel=opt.STYLE),
    ] = None,
    timestamp_color: Annotated[
        Optional[str], _option("--timestamp-color", help="Time label text colour [default: #ffffff].", panel=opt.STYLE)
    ] = None,
    timestamp_font: Annotated[
        Optional[pathlib.Path],
        _option("--timestamp-font", help="Font for the time labels [default: a system font].", panel=opt.STYLE),
    ] = None,
    timestamp_size: Annotated[
        Optional[int], _option("--timestamp-size", help="Time label font size [default: 14].", panel=opt.STYLE)
    ] = None,
    emoji_dir: Annotated[
        Optional[pathlib.Path],
        _option(
            "--emoji-dir",
            help="Folder of Twemoji-style PNGs for emoji in names (needs the emoji extra).",
            panel=opt.STYLE,
        ),
    ] = None,
    max_sheet_pixels: opt.MaxSheetPixels = None,
    max_frame_bytes: opt.MaxFrameBytes = None,
):
    """
    Make video contact sheets: a header with the file's details above a grid of timestamped
    frames ([bold]--animated[/bold]: short looping clips, a 3x3 grid by default). Writes
    [cyan]clip.mp4.sheet.jpg[/cyan] for clip.mp4 ([cyan]clip.mp4.animated-sheet.webp[/cyan]) next
    to each video, under --output-dir, or to -o for a single video; --output-name changes the
    pattern, e.g. [cyan]'{input_name}.jpeg'[/cyan] for clip.mp4.jpeg, or
    [cyan]'sheets/{input_name}.jpeg'[/cyan] for a sheets folder beside each video. Defaults come
    from sashimi.toml; see [bold]sashimi config[/bold].
    """
    opt.single_file_or_fail(paths, output)
    suffixes = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp", ".gif": "gif"}
    typed = output_format.value if output_format else None
    fmt = opt.format_from_output("jpeg" if typed == "jpg" else typed, output, suffixes)
    if not animated:
        warn_ignored(
            [
                f
                for f, v in {"--clip-length": clip_length, "--fps": fps, "--no-loop": no_loop or None}.items()
                if v is not None
            ],
            "animated (-a) sheets",
        )

    overrides: dict[str, Any] = {
        "width": width,
        "cols": cols,
        "rows": rows,
        "gap": gap,
        "target": fmt,
        "quality": opt.parse_quality(quality),
        "start_offset_percent": start_offset,
        "end_offset_percent": end_offset,
        "show_header": False if no_header else None,
        "show_timestamps": False if no_timestamps else None,
        "grid_bg_color": bg_color,
        "header_bg_color": header_bg,
        "header_text_color": header_color,
        "header_font_path": str(header_font) if header_font else None,
        "header_font_size": header_size,
        "timestamp_bg_color": timestamp_bg,
        "timestamp_text_color": timestamp_color,
        "timestamp_font_path": str(timestamp_font) if timestamp_font else None,
        "timestamp_font_size": timestamp_size,
        "emoji_dir": str(emoji_dir) if emoji_dir else None,
    }
    if animated:
        overrides.update(sample_duration=clip_length, fps=fps, loop=False if no_loop else None)

    from pydantic import ValidationError

    from sashimi.api._batch import VIDEO_EXTENSIONS
    from sashimi.api.thumbs import make_thumbs_generator
    from sashimi.cli import _limits, settings
    from sashimi.errors import InvalidOptionsError

    limits = _limits.resolve(max_sheet_pixels=max_sheet_pixels, max_frame_bytes=max_frame_bytes)

    try:
        config, resources, unused = settings.resolve_sheet(animated, overrides)
    except InvalidOptionsError as e:
        fail(refusal(e))
    except ValidationError as e:
        exit_on_invalid_options(e, FLAGS)
    warn_unused([FLAGS.get(field, field) for field in unused], f"{config.target.upper()} sheets")

    extensions = settings.video_extensions()
    naming = opt.naming_args("animated_thumbs" if animated else "thumbs", output_name, output)
    runner = BatchRunner(
        "thumbs",
        "Animated sheets" if animated else "Contact sheets",
        json_output,
        limit_output,
        dry_run=dry_run,
        yes=yes or force,
    )
    with refusals():
        gen = make_thumbs_generator(
            paths,
            config,
            output_ext=typed if output is None else None,
            recursive=recursive,
            force=force,
            max_workers=workers,
            extensions=extensions or VIDEO_EXTENSIONS,
            header_font=resources["header_font"],
            timestamp_font=resources["timestamp_font"],
            emoji_dir=resources["emoji_dir"],
            on_file_progress=runner.file_progress,
            output=output,
            **opt.output_dir_args("animated_thumbs" if animated else "thumbs", output_dir, no_output_dir, output),
            dry_run=dry_run,
            limits=limits,
            **naming,
        )
    runner.run(gen, paths)


PREVIEW_FLAGS = {
    "samples": "--samples",
    "sample_duration": "--clip-length",
    "start_offset_percent": "--start-offset",
    "end_offset_percent": "--end-offset",
    "max_height": "--height",
    "max_width": "--width",
    "crop": "--crop",
    "fps": "--fps",
    "loop": "--no-loop",
    "quality": "--quality",
    "target": "--output-format",
}


class PreviewFormat(str, Enum):
    webp = "webp"
    gif = "gif"


class Crop(str, Enum):
    none = "none"
    portrait = "9:16"
    square = "1:1"


PREVIEW = "Preview"


def preview_cmd(
    paths: Annotated[List[pathlib.Path], typer.Argument(help="Video files or directories.", show_default=False)],
    output_format: Annotated[
        Optional[PreviewFormat],
        _option("--output-format", metavar="FORMAT", help="webp or gif [default: webp].", panel=opt.GENERAL),
    ] = None,
    output: Annotated[Optional[pathlib.Path], opt.output_option()] = None,
    output_name: Annotated[Optional[str], opt.output_name_option("preview", "{input_name}.{role}.{output_ext}")] = None,
    output_dir: Annotated[Optional[pathlib.Path], opt.output_dir_option("preview")] = None,
    no_output_dir: opt.NoOutputDir = False,
    recursive: opt.Recursive = False,
    force: opt.Force = False,
    yes: opt.Yes = False,
    dry_run: opt.DryRun = False,
    workers: opt.Workers = None,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
    samples: Annotated[Optional[int], _option("--samples", help="Clips to take [default: 5].", panel=PREVIEW)] = None,
    clip_length: Annotated[
        Optional[float], _option("--clip-length", help="Seconds per clip [default: 2].", panel=PREVIEW)
    ] = None,
    start_offset: Annotated[
        Optional[float], _option("--start-offset", help="Skip this % of the start [default: 10].", panel=PREVIEW)
    ] = None,
    end_offset: Annotated[
        Optional[float], _option("--end-offset", help="Skip this % of the end [default: 10].", panel=PREVIEW)
    ] = None,
    height: Annotated[
        Optional[int], _option("--height", "-H", help="Maximum height in pixels [default: 360].", panel=PREVIEW)
    ] = None,
    width: Annotated[
        Optional[int], _option("--width", "-w", help="Maximum width in pixels [default: no limit].", panel=PREVIEW)
    ] = None,
    crop: Annotated[
        Optional[Crop], _option("--crop", help="Crop the centre to this shape first [default: none].", panel=PREVIEW)
    ] = None,
    fps: Annotated[Optional[int], _option("--fps", help="Frame rate, 1-50 [default: 15].", panel=PREVIEW)] = None,
    quality: Annotated[
        Optional[str], _option("--quality", "-q", help="WebP quality: 75, 75% or 0.75 [default: 75].", panel=PREVIEW)
    ] = None,
    no_loop: Annotated[bool, _option("--no-loop", help="Play once instead of looping.", panel=PREVIEW)] = False,
):
    """
    Make a short preview of each video: [bold]--samples[/bold] clips of [bold]--clip-length[/bold]
    seconds taken from across the video (skipping the first and last 10%), played one after
    another as an animated WebP or GIF. Writes
    [cyan]clip.mp4.preview.webp[/cyan] for clip.mp4, next to each video, under --output-dir, or to
    -o for a single video.
    """
    from sashimi.api.preview import make_previews_generator
    from sashimi.cli import _limits
    from sashimi.config import VIDEO_PREVIEW

    opt.single_file_or_fail(paths, output)
    limits = _limits.resolve()
    suffixes = {".webp": "webp", ".gif": "gif"}
    fmt = opt.format_from_output(output_format.value if output_format else None, output, suffixes) or "webp"
    model = VIDEO_PREVIEW.model_for(target=fmt)
    options = {
        "samples": samples,
        "sample_duration": clip_length,
        "start_offset_percent": start_offset,
        "end_offset_percent": end_offset,
        "max_height": height,
        "max_width": width,
        "crop": opt.enum_value(crop),
        "fps": fps,
        "quality": opt.parse_quality(quality),
        "loop": False if no_loop else None,
    }
    data, unused = opt.split_options(model, options)
    warn_unused([PREVIEW_FLAGS.get(field, field) for field in unused], f"{fmt.upper()} previews")
    from pydantic import ValidationError

    try:
        config = model.model_validate(data)
    except ValidationError as e:
        exit_on_invalid_options(e, PREVIEW_FLAGS)
    naming = opt.naming_args("preview", output_name, output)
    runner = BatchRunner("preview", "Previews", json_output, limit_output, dry_run=dry_run, yes=yes or force)
    with refusals():
        gen = make_previews_generator(
            paths,
            config,
            recursive=recursive,
            force=force,
            max_workers=workers,
            on_file_progress=runner.file_progress,
            output=output,
            **opt.output_dir_args("preview", output_dir, no_output_dir, output),
            dry_run=dry_run,
            limits=limits,
            **naming,
        )
    runner.run(gen, paths)
