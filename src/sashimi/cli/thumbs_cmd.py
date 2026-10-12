"""`sashimi thumbs`: static and animated contact sheets."""

from __future__ import annotations

import pathlib
from enum import Enum
from typing import Annotated, Any, List, Optional

import typer
from typer.core import TyperCommand

from sashimi.cli import _options as opt
from sashimi.cli._output import BatchRunner, console, exit_on_invalid_options, fail, warn_ignored, warn_unused

FLAGS = {
    "width": "--width",
    "cols": "--cols",
    "rows": "--rows",
    "gap": "--gap",
    "quality": "--quality",
    "target": "--format",
    "sample_duration": "--duration",
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
    png = "png"
    webp = "webp"
    gif = "gif"


class ThumbsCommand(TyperCommand):
    """Shows the sashimi.toml values that replace built-in defaults in ``sashimi thumbs --help``."""

    def format_help(self, ctx: Any, formatter: Any) -> None:
        from sashimi.cli import settings
        from sashimi.cli._defaults import toml_notes, with_toml_note

        raw, _ = settings.read_config()
        builtin = settings.DEFAULT_CONFIG_DICT
        unchanged = all(raw.get(name, {}) == builtin.get(name, {}) for name in ("thumbs", "animated_thumbs"))
        notes = {} if unchanged else toml_notes(raw)
        original = {param: getattr(param, "help", None) for param in self.params}
        try:
            for param, help in original.items():
                note = next((notes[name] for name in param.opts if name in notes), None)
                if note is not None and help is not None:
                    setattr(param, "help", with_toml_note(help, note))  # noqa: B010
            return super().format_help(ctx, formatter)
        finally:
            for param, help in original.items():
                setattr(param, "help", help)  # noqa: B010


def _option(*names: str, help: str, panel: str = opt.SHEET, **kwargs: Any) -> Any:
    return opt.option(*names, help=help, panel=panel, **kwargs)


def thumbs_cmd(
    paths: Annotated[List[pathlib.Path], typer.Argument(help="Video files or directories.", show_default=False)],
    animated: Annotated[
        bool, _option("-a", "--animated", help="Animated WebP/GIF sheet of short clips.", panel=opt.GENERAL)
    ] = False,
    recursive: opt.Recursive = False,
    force: opt.Force = False,
    workers: opt.Workers = None,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
    output_format: Annotated[
        Optional[SheetFormat],
        _option(
            "--format", "-o", help="jpeg, png or webp (static); webp or gif (animated) [default: jpeg, animated webp]."
        ),
    ] = None,
    width: Annotated[
        Optional[int], _option("--width", help="Sheet width in pixels [default: 2560, animated 1280].")
    ] = None,
    cols: Annotated[Optional[str], _option("--cols", help="Columns, 1-20, or 'auto' [default: auto].")] = None,
    rows: Annotated[Optional[str], _option("--rows", help="Rows, 1-20, or 'auto' [default: auto].")] = None,
    gap: Annotated[Optional[int], _option("--gap", help="Pixels between tiles [default: 5].")] = None,
    quality: Annotated[
        Optional[str], _option("--quality", "-q", help="JPEG/WebP quality [default: 85, animated 75].")
    ] = None,
    start_offset: Annotated[
        Optional[float], _option("--start-offset", help="Skip this % of the start [default: 7].")
    ] = None,
    end_offset: Annotated[Optional[float], _option("--end-offset", help="Skip this % of the end [default: 7].")] = None,
    duration: Annotated[
        Optional[float], _option("--duration", help="[Animated] Seconds per clip [default: 2].")
    ] = None,
    fps: Annotated[Optional[int], _option("--fps", help="[Animated] Frame rate, 1-30 [default: 12].")] = None,
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
):
    """
    Make video contact sheets: a header with the file's details above a grid of timestamped
    frames ([bold]--animated[/bold]: short looping clips). Writes [cyan]<video>.jpeg[/cyan] (or
    .png/.webp/.gif) next to each video. Defaults come from sashimi.toml; see [bold]sashimi config[/bold].
    """
    fmt = output_format.value if output_format else None
    if not animated:
        warn_ignored(
            [
                f
                for f, v in {"--duration": duration, "--fps": fps, "--no-loop": no_loop or None}.items()
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
        overrides.update(sample_duration=duration, fps=fps, loop=False if no_loop else None)

    from pydantic import ValidationError

    from sashimi.api._batch import VIDEO_EXTENSIONS
    from sashimi.api.thumbs import make_thumbs_generator
    from sashimi.cli import settings
    from sashimi.errors import InvalidOptionsError

    try:
        config, resources, unused = settings.resolve_sheet(animated, overrides)
    except InvalidOptionsError as e:
        fail(e.user_message)
    except ValidationError as e:
        exit_on_invalid_options(e, FLAGS)
    warn_unused([FLAGS.get(field, field) for field in unused], f"{config.target.upper()} sheets")

    mode = "Animated Thumbs" if animated else "Static Thumbs"
    if not json_output:
        console.print(f"[bold]{'🎬' if animated else '📸'} Starting {mode.lower()} generation...[/bold]")
    extensions = settings.video_extensions()
    runner = BatchRunner(mode, json_output, limit_output)
    gen = make_thumbs_generator(
        paths,
        config,
        recursive=recursive,
        force=force,
        max_workers=workers,
        extensions=extensions or VIDEO_EXTENSIONS,
        header_font=resources["header_font"],
        timestamp_font=resources["timestamp_font"],
        emoji_dir=resources["emoji_dir"],
        on_file_progress=runner.file_progress,
    )
    runner.run(gen)
