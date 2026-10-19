# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Reusable CLI option declarations and value parsers. Kept import-light for fast ``--help``."""

from __future__ import annotations

import pathlib
from enum import Enum
from typing import Annotated, Any, Optional

import typer

from sashimi.cli._output import fail

GENERAL = "General"
SIZE_TIME = "Size & time"
IMAGE = "Image options"
VIDEO = "Video options"
AUDIO = "Audio options"
SHEET = "Sheet layout"
STYLE = "Sheet style"


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


class IcoFit(str, Enum):
    pad = "pad"
    crop = "crop"


def option(*names: str, help: str, panel: str | None = None, **kwargs: Any) -> Any:
    """A ``typer.Option`` in a help panel, showing a ``[default: ...]`` note as written rather than as Rich markup."""
    return typer.Option(*names, help=help.replace("[default:", "\\[default:"), rich_help_panel=panel, **kwargs)


Recursive = Annotated[bool, option("-r", "--recursive", help="Search directories recursively.", panel=GENERAL)]
Force = Annotated[bool, option("-f", "--force", help="Overwrite existing outputs.", panel=GENERAL)]
Workers = Annotated[
    Optional[int],
    option("--workers", min=1, help="Parallel ffmpeg processes/threads [default: min(4, CPUs)].", panel=GENERAL),
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
        "-h",
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
    Optional[int], option("--height", "-h", help="Maximum height in pixels (never upscales).", panel=SIZE_TIME)
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
        help="Colour behind flattened transparency, e.g. '#000000' [default: #ffffff].",
        panel=IMAGE,
    ),
]
Optimize = Annotated[
    Optional[bool],
    option(
        "--optimize/--no-optimize",
        help="Extra compression pass [default: on for JPEG/WebP, off for PNG/GIF].",
        show_default=False,
        panel=IMAGE,
    ),
]
Progressive = Annotated[bool, option("--progressive", help="JPEG: progressive encoding.", panel=IMAGE)]
SubsamplingOpt = Annotated[
    Optional[Subsampling], option("--subsampling", help="JPEG chroma subsampling [default: 4:2:0].", panel=IMAGE)
]
WebpMethod = Annotated[
    Optional[int],
    option("--webp-method", min=0, max=6, help="WebP effort, 0 (fast) to 6 (small) [default: 4].", panel=IMAGE),
]
CompressLevel = Annotated[
    Optional[int],
    option("--compress-level", min=0, max=9, help="PNG zlib level 0-9 [default: 6].", panel=IMAGE),
]
TiffCompressionOpt = Annotated[
    Optional[TiffCompression],
    option("--tiff-compression", help="TIFF compression [default: lzw].", panel=IMAGE),
]
ResampleOpt = Annotated[
    Optional[Resample], option("--resample", help="Scaling filter [default: lanczos].", panel=IMAGE)
]
NoOrient = Annotated[bool, option("--no-orient", help="Don't apply the EXIF orientation tag.", panel=IMAGE)]
KeepExif = Annotated[bool, option("--keep-exif", help="Keep EXIF metadata (camera, date, GPS).", panel=IMAGE)]
StripIcc = Annotated[bool, option("--strip-icc", help="Drop the colour profile.", panel=IMAGE)]
IcoSizes = Annotated[
    Optional[str],
    option("--ico-sizes", help="ICO: icon sizes, e.g. 16,32,48 [default: 16,32,48,64,128,256].", panel=IMAGE),
]
IcoFitOpt = Annotated[
    Optional[IcoFit],
    option("--ico-fit", help="ICO: pad or crop non-square images [default: pad].", panel=IMAGE),
]

Fps = Annotated[Optional[int], option("--fps", help="GIF/WebP frame rate, 1-30 [default: 12].", panel=VIDEO)]
Crf = Annotated[
    Optional[int],
    option("--crf", help="WebM/MP4 quality, lower is better [default: WebM 34, MP4 23].", panel=VIDEO),
]
PresetOpt = Annotated[Optional[Preset], option("--preset", help="MP4 encoder speed [default: veryfast].", panel=VIDEO)]
Speed = Annotated[
    Optional[int],
    option("--speed", min=0, max=8, help="WebM encoder speed 0-8, higher is faster [default: 8].", panel=VIDEO),
]
AudioBitrate = Annotated[
    Optional[int],
    option("--audio-bitrate", help="WebM/MP4 audio kb/s [default: WebM 96, MP4 128].", panel=VIDEO),
]
Mute = Annotated[bool, option("--mute", help="WebM/MP4: drop the audio track.", panel=VIDEO)]
NoLoop = Annotated[bool, option("--no-loop", help="GIF/WebP: play once instead of looping.", panel=VIDEO)]
DitherOpt = Annotated[Optional[Dither], option("--dither", help="GIF dithering [default: bayer].", panel=VIDEO)]
PaletteOpt = Annotated[
    Optional[PaletteMode], option("--palette", help="GIF palette mode [default: diff].", panel=VIDEO)
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
    option("--flac-level", min=0, max=12, help="FLAC compression 0-12 [default: 5].", panel=AUDIO),
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

    try:
        return parse_time_to_seconds(value)
    except ValueError as e:
        fail(f"{flag}: {e}")


def parse_sizes(value: str | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    try:
        return tuple(int(part) for part in value.replace(" ", "").split(",") if part)
    except ValueError:
        fail(f"--ico-sizes: {value!r} should be numbers separated by commas")


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
