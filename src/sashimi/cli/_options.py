# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Reusable CLI option declarations and value parsers. Kept import-light for fast ``--help``."""

from __future__ import annotations

import contextvars
import pathlib
from enum import Enum
from typing import Annotated, Any, NoReturn, Optional

import typer
from typer.core import TyperCommand

from sashimi.cli._output import fail

GENERAL = "General"
SIZE_TIME = "Size & time"
IMAGE = "Image options"
VIDEO = "Video options"
AUDIO = "Audio options"
SHEET = "Sheet layout"
STYLE = "Sheet style"
LIMITS = "Limits"


class Resample(str, Enum):
    lanczos = "lanczos"
    bicubic = "bicubic"
    bilinear = "bilinear"
    hamming = "hamming"
    box = "box"
    nearest = "nearest"


class Subsampling(str, Enum):
    s444 = "4:4:4"
    s422 = "4:2:2"
    s420 = "4:2:0"


class TiffCompression(str, Enum):
    none = "none"
    lzw = "lzw"
    deflate = "deflate"
    jpeg = "jpeg"


class Preset(str, Enum):
    ultrafast = "ultrafast"
    superfast = "superfast"
    veryfast = "veryfast"
    faster = "faster"
    fast = "fast"
    medium = "medium"
    slow = "slow"
    slower = "slower"
    veryslow = "veryslow"


class Dither(str, Enum):
    bayer = "bayer"
    sierra2_4a = "sierra2_4a"
    floyd_steinberg = "floyd_steinberg"
    none = "none"


class PaletteMode(str, Enum):
    diff = "diff"
    full = "full"
    single = "single"


class Metadata(str, Enum):
    keep = "keep"
    remove_location = "remove_location"
    remove_all = "remove_all"


class StripMode(str, Enum):
    remove_location = "remove_location"
    remove_all = "remove_all"


class IcoFit(str, Enum):
    pad = "pad"
    crop = "crop"


def reflow(text: str | None) -> str | None:
    """
    A command's docstring as help: each paragraph's line breaks become spaces, so Rich wraps it to
    the terminal, except in paragraphs whose lines start with markup (lists such as convert's).
    """
    if text is None:
        return None
    import inspect

    paragraphs = []
    for paragraph in inspect.cleandoc(text).split("\n\n"):
        lines = paragraph.split("\n")
        if len(lines) > 1 and all(line.startswith("[bold]") for line in lines[1:]):
            paragraphs.append(paragraph)
        else:
            paragraphs.append(" ".join(line.strip() for line in lines))
    return "\n\n".join(paragraphs)


class KeptCommand(TyperCommand):
    """
    A processing command whose ``--help`` shows what its sashimi.toml section keeps for ``--output-name`` and
    ``--output-dir``, and whose arguments as typed are kept while it runs (:func:`typed_command`).
    """

    SECTIONS = {"trim": "extract"}
    ARGS = "sashimi.args"

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        ctx.meta[self.ARGS] = list(args)
        return super().parse_args(ctx, args)

    def invoke(self, ctx: Any) -> Any:
        token = _TYPED.set((ctx.info_name, ctx.meta.get(self.ARGS)))
        try:
            return super().invoke(ctx)
        finally:
            _TYPED.reset(token)

    def toml_notes(self, ctx: Any, raw: dict[str, Any]) -> dict[str, str]:
        """Flag to the sashimi.toml value that replaces its built-in default."""
        from sashimi.cli import settings

        section = self.SECTIONS.get(ctx.info_name, ctx.info_name)
        notes = {}
        for flag, value in (
            ("--output-name", settings.output_name_setting(section, raw)),
            ("--output-dir", settings.output_dir_setting(section, raw)),
        ):
            if value is not None:
                notes[flag] = str(value)
        return notes

    def format_help(self, ctx: Any, formatter: Any) -> None:
        from sashimi.cli import settings
        from sashimi.cli._defaults import with_toml_note

        raw, _ = settings.read_config()
        notes = self.toml_notes(ctx, raw)
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


_TYPED: contextvars.ContextVar[tuple[str | None, list[str] | None] | None] = contextvars.ContextVar(
    "sashimi_typed", default=None
)


def typed_command() -> tuple[str, list[str]] | None:
    """The running command's name and its arguments as typed, for messages that suggest a command; None outside one."""
    typed = _TYPED.get()
    if typed is None or not typed[0] or typed[1] is None:
        return None
    return typed[0], typed[1]


def option(*names: str, help: str, panel: str | None = None, **kwargs: Any) -> Any:
    """A ``typer.Option`` in a help panel, showing a ``[default: ...]`` note as written rather than as Rich markup."""
    return typer.Option(*names, help=help.replace("[default:", "\\[default:"), rich_help_panel=panel, **kwargs)


Recursive = Annotated[bool, option("-r", "--recursive", help="Search directories recursively.", panel=GENERAL)]
Force = Annotated[
    bool,
    option(
        "-f",
        "--force",
        help="Replace whatever has the output's name: an earlier output, a file of yours, or the input "
        "itself with an in-place pattern (each is reported). Implies -y.",
        panel=GENERAL,
    ),
]
NoOutputDir = Annotated[
    bool,
    option(
        "--no-output-dir",
        help="Write next to each input for this run, even if sashimi.toml keeps an output_dir.",
        panel=GENERAL,
    ),
]


def output_dir_option(section: str) -> Any:
    """``--output-dir``: the folder outputs go into, for one run; `section` is where sashimi.toml keeps one."""
    also = " (animated_thumbs.output_dir for animated sheets)" if section == "thumbs" else ""
    return option(
        "--output-dir",
        help="Write outputs into this folder, created if needed: a relative path is relative to where you run "
        "sashimi, as for any path, and the subfolders of searched directories are kept (for a folder inside "
        "each input's folder, start --output-name with it). For this run only; keep one with sashimi config "
        f"set {section}.output_dir <folder>{also} [default: next to each input].",
        panel=GENERAL,
        show_default=False,
    )


def output_dir_args(
    section: str, output_dir: pathlib.Path | None, no_output_dir: bool, output: pathlib.Path | None
) -> dict[str, Any]:
    """
    The ``output_dir`` argument for a batch function: ``--output-dir``, else nothing with
    ``--no-output-dir``, else ``output_dir`` in `section` of sashimi.toml (relative to the
    current folder, like the flag; ``~`` is the home folder), else None for next to each input.
    ``-o`` names the output itself, so a kept folder doesn't apply to it, and ``--output-dir``
    with ``-o`` is refused.
    """
    from rich.markup import escape

    from sashimi.cli import settings

    if output is not None:
        if output_dir is not None:
            fail("-o/--output names the output file, so leave out --output-dir.")
        return {"output_dir": None}
    if output_dir is not None:
        if no_output_dir:
            fail("Give --output-dir or --no-output-dir, not both.")
        return {"output_dir": output_dir}
    if no_output_dir:
        return {"output_dir": None}
    kept = settings.output_dir_setting(section)
    if kept is None:
        return {"output_dir": None}
    if not isinstance(kept, str) or not kept.strip() or "\0" in kept:
        origin = settings.output_name_origin(section, "output_dir")
        fail(f'{origin} must be a folder in quotes, e.g. "sheets".')
    return {"output_dir": pathlib.Path(kept).expanduser()}


def output_option(default: str = "named by --output-name, next to the input") -> Any:
    """``-o/--output``: the output file, for a single input file."""
    return option("--output", "-o", help=f"Output file, for a single input file [default: {default}].", panel=GENERAL)


KEPT_EXAMPLES = {
    "convert": "{input_stem}.{output_ext}",
    "remux": "{input_stem}.{output_ext}",
    "thumbs": "{input_name}.jpeg",
}


def output_name_option(section: str, default: str) -> Any:
    """``--output-name``: the pattern that names each output, for one run; `section` is where sashimi.toml keeps one."""
    example = KEPT_EXAMPLES.get(section, "{input_stem}.{role}.{output_ext}")
    also = " (animated_thumbs.output_name for animated sheets)" if section == "thumbs" else ""
    return option(
        "--output-name",
        help="Name pattern for outputs, e.g. '{input_stem}.{role}.{output_ext}' or '{input_name}.jpeg': "
        "{input_name} is the input's file name (clip.mp4), {input_stem} the name without its extension "
        "(clip), {input_ext} its extension (mp4), {role} what the output is (sheet), {output_ext} the "
        "output's extension (jpg). It may start with folders, made inside each input's folder: "
        f"'sheets/{{input_name}}.jpeg'. For this run only; keep one with sashimi config set "
        f"{section}.output_name '{example}'{also} [default: {default}].",
        panel=GENERAL,
        show_default=False,
    )


def naming_args(section: str, output_name: str | None, output: pathlib.Path | None) -> dict[str, Any]:
    """
    The ``output_name`` argument for a batch function: ``--output-name``, else ``output_name``
    in `section` of sashimi.toml, else None for the command's default, carrying where it was
    set, for messages. Only this command's section is read, so a broken pattern elsewhere
    doesn't stop it. Refuses a pattern that is invalid, or ``--output-name`` with ``-o``, which
    names the output itself.
    """
    from rich.markup import escape

    from sashimi.api._naming import ROLES, NameTemplate
    from sashimi.cli import settings
    from sashimi.errors import InvalidOptionsError

    if output is not None and output_name is not None:
        fail("-o/--output names the output file, so leave out --output-name.")

    def parse(command: str, text: Any, origin: str) -> NameTemplate:
        if not isinstance(text, str):
            fail(f'{origin} must be a pattern in quotes, e.g. "{{input_name}}.jpeg".')
        try:
            template = NameTemplate(text, origin)
        except InvalidOptionsError as e:
            fail(f"{origin}: {e.user_message}")
        if command not in ROLES and "role" in template.fields:
            fail(f"{origin}: {command} outputs have no role, so the pattern can't use {{role}}.")
        return template

    chosen = None
    if output_name is not None:
        chosen = parse(section, output_name, "--output-name")
    elif output is None:
        configured = settings.output_name_setting(section)
        if configured is not None:
            chosen = parse(section, configured, settings.output_name_origin(section))
    return {"output_name": chosen if output is None else None}


def output_format_option(help: str) -> Any:
    """``--output-format`` (no short flag): the output format, spelled as the extension to write."""
    return option("--output-format", help=help, panel=GENERAL, metavar="FORMAT")


def within(low: int, high: int | None = None) -> Any:
    """An option callback refusing numbers outside `low` to `high`; the help states the range, so it stays short."""

    def check(value: int | None) -> int | None:
        if value is not None and (value < low or (high is not None and value > high)):
            raise typer.BadParameter(f"must be {low} or more." if high is None else f"must be {low} to {high}.")
        return value

    return check


Yes = Annotated[
    bool,
    option(
        "--yes",
        "-y",
        help="Answer yes to questions, such as whether to go ahead with a folder of mixed file types (-f implies it).",
        panel=GENERAL,
    ),
]
Workers = Annotated[
    Optional[int],
    option(
        "--workers",
        callback=within(1),
        help="Parallel ffmpeg processes/threads, 1 or more [default: min(4, CPUs)].",
        panel=GENERAL,
    ),
]
DryRun = Annotated[
    bool,
    option(
        "--dry-run",
        "-n",
        help="Show what would be written, replaced (with -f) or skipped, and change nothing.",
        panel=GENERAL,
    ),
]
JsonOutput = Annotated[bool, option("--json", help="Print a JSON report for scripting.", panel=GENERAL)]
LimitOutput = Annotated[int, option("--limit-output", "-l", help="Maximum rows in the results table.", panel=GENERAL)]

MaxWidth = Annotated[
    Optional[int],
    option(
        "--width",
        "-w",
        help="Maximum width in pixels (never upscales) [default: source width; video to GIF/WebP 480].",
        panel=SIZE_TIME,
    ),
]
MaxHeight = Annotated[
    Optional[int],
    option(
        "--height",
        "-H",
        help="[Image] Maximum height in pixels (never upscales) [default: source height].",
        panel=SIZE_TIME,
    ),
]
Start = Annotated[
    Optional[str],
    option("--start", "-s", help="[Video/audio] Start time, e.g. 90, 1m30s, 00:01:30 [default: 0].", panel=SIZE_TIME),
]
Duration = Annotated[
    Optional[str],
    option(
        "--duration",
        "-d",
        help="[Video/audio] Length to convert, e.g. 10s, 2m [default: to the end; GIF/WebP 10s].",
        panel=SIZE_TIME,
    ),
]

FitWidth = Annotated[
    Optional[int], option("--width", "-w", help="Maximum width in pixels (never upscales).", panel=SIZE_TIME)
]
FitHeight = Annotated[
    Optional[int], option("--height", "-H", help="Maximum height in pixels (never upscales).", panel=SIZE_TIME)
]

Quality = Annotated[
    Optional[str],
    option(
        "--quality",
        "-q",
        help="JPEG/WebP quality: 85, 85% or 0.85 [default: 85; animated WebP 75].",
        panel=IMAGE,
    ),
]
ImageQuality = Annotated[
    Optional[str],
    option("--quality", "-q", help="JPEG/WebP/TIFF quality: 85, 85% or 0.85 [default: 85].", panel=IMAGE),
]
Lossless = Annotated[bool, option("--lossless", help="WebP: lossless compression.", panel=IMAGE)]
Flatten = Annotated[
    bool,
    option("--flatten", help="Flatten transparency onto --background (JPEG/BMP always do).", panel=IMAGE),
]
Background = Annotated[
    Optional[str],
    option(
        "--background",
        help="Colour behind flattened transparency, e.g. '#000000'; for ICO, fills transparency "
        "[default: #ffffff; ICO transparent].",
        panel=IMAGE,
    ),
]
ImageBackground = Annotated[
    Optional[str],
    option(
        "--background",
        help="Colour behind flattened transparency, e.g. '#000000' [default: #ffffff].",
        panel=IMAGE,
    ),
]
Optimize = Annotated[
    Optional[bool],
    option(
        "--optimize/--no-optimize",
        help="Extra compression pass [default: on for JPEG, off for PNG/GIF].",
        show_default=False,
        panel=IMAGE,
    ),
]
Progressive = Annotated[bool, option("--progressive", help="JPEG: progressive encoding.", panel=IMAGE)]
SubsamplingOpt = Annotated[
    Optional[Subsampling],
    option(
        "--subsampling",
        metavar="RATIO",
        help="JPEG chroma subsampling: 4:4:4, 4:2:2 or 4:2:0 [default: 4:2:0].",
        panel=IMAGE,
    ),
]
WebpMethod = Annotated[
    Optional[int],
    option(
        "--webp-method", callback=within(0, 6), help="WebP effort, 0 (fast) to 6 (small) [default: 4].", panel=IMAGE
    ),
]
CompressLevel = Annotated[
    Optional[int],
    option("--compress-level", callback=within(0, 9), help="PNG zlib level 0-9 [default: 6].", panel=IMAGE),
]
TiffCompressionOpt = Annotated[
    Optional[TiffCompression],
    option(
        "--tiff-compression",
        metavar="METHOD",
        help="TIFF compression: none, lzw, deflate or jpeg [default: lzw].",
        panel=IMAGE,
    ),
]
ResampleOpt = Annotated[
    Optional[Resample],
    option(
        "--resample",
        metavar="FILTER",
        help="Scaling filter: lanczos, bicubic, bilinear, hamming, box or nearest [default: lanczos].",
        panel=IMAGE,
    ),
]
NoOrient = Annotated[bool, option("--no-orient", help="Don't apply the EXIF orientation tag.", panel=IMAGE)]
MetadataOpt = Annotated[
    Optional[Metadata],
    option(
        "--metadata",
        metavar="MODE",
        help="The source's metadata (camera, dates, GPS, titles, comments): keep it, remove_location "
        "(GPS and place names) or remove_all [default: keep].",
        panel=GENERAL,
        show_default=False,
    ),
]
StripIcc = Annotated[bool, option("--strip-icc", help="Drop the colour profile.", panel=IMAGE)]
IcoSizes = Annotated[
    Optional[str],
    option("--ico-sizes", help="ICO: icon sizes, e.g. 16,32,48 [default: 16,32,48,64,128,256].", panel=IMAGE),
]
IcoFitOpt = Annotated[
    Optional[IcoFit],
    option("--ico-fit", help="ICO: pad or crop non-square images [default: pad].", panel=IMAGE),
]

Fps = Annotated[Optional[int], option("--fps", help="GIF/WebP frame rate, 1-50 [default: 12].", panel=VIDEO)]
Crf = Annotated[
    Optional[int],
    option("--crf", help="WebM/MP4 quality, lower is better [default: WebM 34, MP4 23].", panel=VIDEO),
]
PresetOpt = Annotated[
    Optional[Preset],
    option(
        "--preset",
        metavar="PRESET",
        help="MP4 encoder speed: ultrafast, superfast, veryfast, faster, fast, medium, slow, slower or "
        "veryslow [default: veryfast].",
        panel=VIDEO,
    ),
]
Speed = Annotated[
    Optional[int],
    option(
        "--speed", callback=within(0, 8), help="WebM encoder speed 0-8, higher is faster [default: 8].", panel=VIDEO
    ),
]
AudioBitrate = Annotated[
    Optional[int],
    option("--audio-bitrate", help="WebM/MP4 audio kb/s [default: WebM 96, MP4 128].", panel=VIDEO),
]
Mute = Annotated[bool, option("--mute", help="WebM/MP4: drop the audio track.", panel=VIDEO)]
NoLoop = Annotated[bool, option("--no-loop", help="GIF/WebP: play once instead of looping.", panel=VIDEO)]
DitherOpt = Annotated[
    Optional[Dither],
    option(
        "--dither",
        metavar="METHOD",
        help="GIF dithering: bayer, sierra2_4a, floyd_steinberg or none [default: bayer].",
        panel=VIDEO,
    ),
]
PaletteOpt = Annotated[
    Optional[PaletteMode],
    option("--palette", metavar="MODE", help="GIF palette mode: diff, full or single [default: diff].", panel=VIDEO),
]

MaxPixels = Annotated[
    Optional[str],
    option(
        "--max-pixels",
        metavar="PIXELS",
        help="Refuse images with more pixels than this, e.g. 200M, or none for no limit [default: 64M].",
        panel=LIMITS,
    ),
]
MaxFrameBytes = Annotated[
    Optional[str],
    option(
        "--max-frame-bytes",
        metavar="SIZE",
        help="Memory for the clips an animated sheet holds at once, e.g. 4G, or none [default: 1G].",
        panel=LIMITS,
    ),
]
MaxAnimationBytes = Annotated[
    Optional[str],
    option(
        "--max-animation-bytes",
        metavar="SIZE",
        help="Memory for the frames of an animated GIF or WebP being edited, e.g. 4G, or none [default: 1G].",
        panel=LIMITS,
    ),
]
MaxSheetPixels = Annotated[
    Optional[str],
    option(
        "--max-sheet-pixels",
        metavar="PIXELS",
        help="Refuse contact sheets with more pixels than this, e.g. 200M, or none [default: 64M].",
        panel=LIMITS,
    ),
]

Bitrate = Annotated[
    Optional[int],
    option("--bitrate", "-b", help="Audio kb/s, 64-320 [default: MP3 192, M4A 160, OGG 128].", panel=AUDIO),
]
Mono = Annotated[bool, option("--mono", help="Downmix to one channel.", panel=AUDIO)]
SampleRate = Annotated[
    Optional[int],
    option("--sample-rate", help="Output sample rate in Hz [default: source].", panel=AUDIO),
]
BitDepth = Annotated[
    Optional[int],
    option("--bit-depth", help="WAV sample size: 16, 24 or 32 [default: 16].", panel=AUDIO),
]
FlacLevel = Annotated[
    Optional[int],
    option("--flac-level", callback=within(0, 12), help="FLAC compression 0-12 [default: 5].", panel=AUDIO),
]


def parse_quality(value: str | None) -> int | None:
    """Accepts '85%', '85' or '0.85' and returns 1-100."""
    if value is None:
        return None
    text = value.strip()
    try:
        number = float(text[:-1]) if text.endswith("%") else float(text)
    except ValueError:
        fail(f"--quality: {value!r} is not a number or percentage")
    if not text.endswith("%") and number <= 1.0:
        number *= 100
    return round(number)


def parse_time(value: str | None, flag: str) -> float | None:
    if value is None:
        return None
    from sashimi.api.extract import parse_time_to_seconds
    from sashimi.errors import InvalidOptionsError

    try:
        return parse_time_to_seconds(value)
    except InvalidOptionsError as e:
        fail(f"{flag}: {e.user_message}")


def parse_sizes(value: str | None, flag: str = "--ico-sizes") -> tuple[int, ...] | None:
    if value is None:
        return None
    try:
        return tuple(int(part) for part in value.replace(" ", "").split(",") if part)
    except ValueError:
        fail(f"{flag}: {value!r} should be numbers separated by commas")


def enum_value(value: Enum | None) -> Any:
    return value.value if value is not None else None


def given(options: dict[str, Any]) -> dict[str, Any]:
    """Drops options the user didn't set (None)."""
    return {key: value for key, value in options.items() if value is not None}


def split_options(model: Any, options: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The set options that `model` has, and the names of the set options it doesn't."""
    set_options = given(options)
    fields = model.model_fields
    return {k: v for k, v in set_options.items() if k in fields}, [k for k in set_options if k not in fields]


def exists_or_fail(path: pathlib.Path) -> None:
    if not path.exists():
        fail(f"Path does not exist: {path}", code=1)


def file_or_fail(path: pathlib.Path, command: str) -> None:
    """Commands that read one file refuse a folder, saying so."""
    if path.is_dir():
        fail(f"{path} is a folder; {command} shows one file at a time.", code=1)


def format_from_output(given: str | None, output: pathlib.Path | None, formats: dict[str, str]) -> str | None:
    """`given`, or else the format `output`'s extension names (from `formats`, extension to format), if any."""
    if given is not None or output is None:
        return given
    return formats.get(output.suffix.lower())


def _refuse(message: str) -> NoReturn:
    fail(message)


def _kind_of_path(text: str) -> str:
    return "folder" if pathlib.Path(text).is_dir() else "file"


def _example(command: str, *words: str) -> str:
    import shlex

    return " ".join(["sashimi", command, *(word if word == "…" else shlex.quote(word) for word in words)])


def _some(paths: list[str]) -> list[str]:
    """The paths for an example in a message: all of them, or the first and "…" when there are more than two."""
    return paths if len(paths) <= 2 else [paths[0], "…"]


def paths_then_format(
    arguments: list[str], formats: list[str], given: str | None, command: str
) -> tuple[list[pathlib.Path], str]:
    """
    Splits ``PATH... FORMAT`` the way ``cp`` splits ``SOURCE... DEST``: the last argument is
    always the format, unless `given` (``--output-format``) names it, when every argument is a
    path. When the format is also the name of a file or folder here, a note says how to convert
    that instead; a single argument that is both is taken as the path, missing its format.
    """
    import os

    from sashimi.cli._output import err_console

    listed = ", ".join(formats)
    if given is not None:
        if len(arguments) > 1 and arguments[-1] in formats and not os.path.lexists(arguments[-1]):
            _refuse(f"Give the format once: last, or with --output-format, not both (got {arguments[-1]} and {given}).")
        return [pathlib.Path(a) for a in arguments], given
    last = arguments[-1]
    if last not in formats or (len(arguments) == 1 and os.path.lexists(last)):
        if os.path.lexists(last):
            _refuse(
                f"End with the format to {command} to, e.g. {_example(command, *_some(arguments), 'webp' if last != 'webp' else 'jpeg')} ({listed})."
            )
        _refuse(f"{last} isn't a format sashimi converts to, nor a file here; end with one of {listed}.")
    if len(arguments) == 1:
        _refuse(f"Name the files or folders to convert, then the format: {_example(command, 'photo.png', last)}.")
    if os.path.lexists(last) and last not in arguments[:-1]:
        from rich.markup import escape

        kind = _kind_of_path(last)
        err_console.print(
            escape(
                f"Note: {last} is taken as the format, since the format always comes last; to convert the {kind} "
                f"{last} too, name it before the format ({_example(command, *_some(arguments[:-1]), f'./{last}', last)})."
            ),
            style="dim",
        )
    return [pathlib.Path(a) for a in arguments[:-1]], last


def paths_then_angle(
    arguments: list[str], angles: list[str], given: str | None, default: str, command: str
) -> tuple[list[pathlib.Path], str]:
    """
    Splits ``PATH... [ANGLE]``: a last argument that is an angle is the angle (`default` when
    there is none), unless `given` (``--angle``) names it, when every argument is a path. A last
    argument that is an angle and also a file or folder here is refused, saying how to write each,
    unless it was named as a path before it too (``sashimi rotate * 90``).
    """
    import os
    import re

    listed = ", ".join(angles[:-1]) + " or " + angles[-1]
    if given is not None:
        if len(arguments) > 1 and arguments[-1] in angles and not os.path.lexists(arguments[-1]):
            _refuse(f"Give the angle once: last, or with --angle, not both (got {arguments[-1]} and {given}).")
        return [pathlib.Path(a) for a in arguments], given
    last = arguments[-1]
    if last in angles:
        if os.path.lexists(last) and last not in arguments[:-1]:
            if len(arguments) == 1:
                return [pathlib.Path(last)], default
            kind = _kind_of_path(last)
            _refuse(
                f"{last} is an angle and also a {kind} here, so sashimi won't guess. To rotate by {last}, give it "
                f"as --angle {last} ({_example(command, *_some(arguments[:-1]), '--angle', last)}); to rotate the {kind}, "
                f"write ./{last} ({_example(command, *_some(arguments[:-1]), f'./{last}')})."
            )
        if len(arguments) == 1:
            _refuse(f"Name the files or folders to {command}, then the angle: {_example(command, 'photo.jpg', last)}.")
        return [pathlib.Path(a) for a in arguments[:-1]], last
    if re.fullmatch(r"[+-]?\d+(\.\d*)?", last) and not os.path.lexists(last):
        _refuse(f"{last} isn't a file here, nor an angle {command} takes ({listed}).")
    return [pathlib.Path(a) for a in arguments], default


def single_file_or_fail(paths: list[pathlib.Path], output: pathlib.Path | None) -> None:
    """``-o`` names one output, so it needs exactly one input, and that input must be a file."""
    if output is not None and (len(paths) != 1 or paths[0].is_dir()):
        fail("-o/--output needs a single input file; use --output-dir for several.")
