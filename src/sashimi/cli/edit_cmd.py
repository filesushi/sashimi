# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""`sashimi edit` (rotate, flip, crop, brightness, contrast, saturation) and `sashimi frames`."""

from __future__ import annotations

import pathlib
import re
from enum import Enum
from typing import Annotated, Any, List, Optional

import typer

from sashimi.cli import _options as opt
from sashimi.cli._output import BatchRunner, exit_on_invalid_options, fail, warn_unused

EDIT = "Edit"
FRAMES = "Frames"
SAVE = "Saving"

FLAGS = {
    "rotate": "--rotate",
    "flip": "--flip",
    "crop_left": "--crop",
    "crop_top": "--crop",
    "crop_width": "--crop",
    "crop_height": "--crop",
    "brightness": "--brightness",
    "contrast": "--contrast",
    "saturation": "--saturation",
    "quality": "--quality",
    "lossless": "--lossless",
    "webp_method": "--webp-method",
    "png_compress_level": "--compress-level",
    "optimize": "--optimize/--no-optimize",
    "progressive": "--progressive",
    "jpeg_subsampling": "--subsampling",
    "tiff_compression": "--tiff-compression",
    "metadata": "--metadata",
    "keep_icc": "--strip-icc",
    "keep": "--keep",
    "speed": "--speed",
    "target": "--format",
}
"""Config field to CLI flag, for `edit` and `frames`."""

_CROP = re.compile(r"(\d+)x(\d+)(?:\+(\d+)\+(\d+))?")


class Rotation(str, Enum):
    r0 = "0"
    r90 = "90"
    r180 = "180"
    r270 = "270"


class Flip(str, Enum):
    none = "none"
    horizontal = "horizontal"
    vertical = "vertical"


def _option(*names: str, help: str, panel: str = EDIT, **kwargs: Any) -> Any:
    return opt.option(*names, help=help, panel=panel, **kwargs)


def parse_crop(value: str | None) -> dict[str, int]:
    """``WxH+X+Y`` (offsets optional, default +0+0) as crop fields."""
    if value is None:
        return {}
    found = _CROP.fullmatch(value.strip().lower())
    if not found:
        fail(f"--crop: {value!r} should be WIDTHxHEIGHT+LEFT+TOP, e.g. 800x600+100+50.")
    width, height, left, top = (int(part) if part else 0 for part in found.groups())
    return {"crop_width": width, "crop_height": height, "crop_left": left, "crop_top": top}


def parse_frames(value: str | None, flag: str) -> list[int] | None:
    """Frame numbers counted from 1 ("1-10,15") as indices counted from 0."""
    if value is None:
        return None
    frames: list[int] = []
    for part in value.replace(" ", "").split(","):
        if not part:
            continue
        first, dash, last = part.partition("-")
        if not first.isdigit() or (dash and not last.isdigit()):
            fail(f"{flag}: {value!r} should be frame numbers and ranges, e.g. 1-10,15.")
        low, high = int(first), int(last) if dash else int(first)
        if low < 1 or high < low:
            fail(f"{flag}: frames are numbered from 1, and ranges go low-high (e.g. 3-7).")
        frames += range(low - 1, high)
    if not frames:
        fail(f"{flag}: give at least one frame number.")
    return frames


def _save_options(
    quality, lossless, webp_method, compress_level, optimize, progressive, subsampling, tiff_compression, metadata,
    strip_icc,
) -> dict[str, Any]:  # fmt: skip
    return {
        "quality": opt.parse_quality(quality),
        "lossless": lossless or None,
        "webp_method": webp_method,
        "png_compress_level": compress_level,
        "optimize": optimize,
        "progressive": progressive or None,
        "jpeg_subsampling": opt.enum_value(subsampling),
        "tiff_compression": opt.enum_value(tiff_compression),
        "metadata": opt.enum_value(metadata),
        "keep_icc": False if strip_icc else None,
    }


def _configs(family: Any, options: dict[str, Any], fmt: str | None, kind: str) -> tuple[dict[str, Any], Any]:
    """A validated config per target (or just `fmt`'s), and a function that returns one per source format."""
    from pydantic import ValidationError

    targets = [fmt] if fmt else list(family.targets)
    configs = {}
    for name in targets:
        model = family.model_for(target=name)
        data, _ = opt.split_options(model, options)
        try:
            configs[name] = model.model_validate(data)
        except ValidationError as e:
            exit_on_invalid_options(e, FLAGS)
    used = {field for name in targets for field in family.model_for(target=name).model_fields}
    unused = [field for field in opt.given(options) if field not in used]
    warn_unused([FLAGS.get(field, field) for field in unused], f"{targets[0].upper()} {kind}" if fmt else kind)
    warned: set[str] = set()

    def config_for(source_format: str) -> Any:
        name = fmt or source_format
        model = family.model_for(target=name)
        skipped = [f for f in opt.given(options) if f not in model.model_fields and f not in unused]
        if skipped and name not in warned:
            warned.add(name)
            warn_unused([FLAGS.get(field, field) for field in skipped], f"{name.upper()} {kind}")
        return configs[name]

    return configs, config_for


class EditFormat(str, Enum):
    jpeg = "jpeg"
    png = "png"
    webp = "webp"
    gif = "gif"
    bmp = "bmp"
    tiff = "tiff"


class FramesFormat(str, Enum):
    gif = "gif"
    webp = "webp"


Quality = Annotated[
    Optional[str],
    _option(
        "--quality",
        "-q",
        help="JPEG/WebP quality: 92, 92% or 0.92 [default: 92, high, since the image is re-encoded].",
        panel=SAVE,
    ),
]
Lossless = Annotated[bool, _option("--lossless", help="WebP: lossless compression.", panel=SAVE)]
WebpMethod = Annotated[
    Optional[int],
    _option("--webp-method", min=0, max=6, help="WebP effort, 0 (fast) to 6 (small) [default: 4].", panel=SAVE),
]
StripIcc = Annotated[bool, _option("--strip-icc", help="Drop the colour profile.", panel=SAVE)]
Optimize = Annotated[
    Optional[bool],
    _option(
        "--optimize/--no-optimize",
        help="Extra compression pass [default: on for JPEG, off for PNG/GIF].",
        show_default=False,
        panel=SAVE,
    ),
]


def edit_cmd(
    paths: Annotated[List[pathlib.Path], typer.Argument(help="Image files or directories.", show_default=False)],
    rotate: Annotated[Optional[Rotation], _option("--rotate", help="Rotate clockwise by degrees [default: 0].")] = None,
    flip: Annotated[
        Optional[Flip], _option("--flip", help="Mirror the picture, after rotating [default: none].")
    ] = None,
    crop: Annotated[
        Optional[str],
        _option(
            "--crop",
            help="Keep WIDTHxHEIGHT pixels from LEFT,TOP, measured after rotating, e.g. 800x600+100+50 "
            "(offsets default to +0+0) [default: no crop].",
        ),
    ] = None,
    aspect: Annotated[
        Optional[str],
        _option(
            "--aspect", help="Crop to the largest centred box of this shape, e.g. 16:9, 1:1, 9:16 [default: free]."
        ),
    ] = None,
    brightness: Annotated[
        Optional[float],
        _option("--brightness", help="0 is black, 1 unchanged, 2 twice as bright, like CSS [default: 1]."),
    ] = None,
    contrast: Annotated[
        Optional[float], _option("--contrast", help="0 is flat grey, 1 unchanged, 2 double [default: 1].")
    ] = None,
    saturation: Annotated[
        Optional[float], _option("--saturation", help="0 is greyscale, 1 unchanged, 2 twice as vivid [default: 1].")
    ] = None,
    output_format: Annotated[Optional[EditFormat], opt.format_option("Output format [default: same as input].")] = None,
    output: Annotated[Optional[pathlib.Path], opt.output_option("<name>.edited.<ext> next to the input")] = None,
    output_dir: opt.OutputDir = None,
    recursive: opt.Recursive = False,
    force: opt.Force = False,
    dry_run: opt.DryRun = False,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
    quality: Quality = None,
    lossless: Lossless = False,
    webp_method: WebpMethod = None,
    compress_level: Annotated[
        Optional[int],
        _option("--compress-level", min=0, max=9, help="PNG zlib level 0-9 [default: 6].", panel=SAVE),
    ] = None,
    optimize: Optimize = None,
    progressive: Annotated[bool, _option("--progressive", help="JPEG: progressive encoding.", panel=SAVE)] = False,
    subsampling: Annotated[
        Optional[opt.Subsampling],
        _option("--subsampling", help="JPEG chroma subsampling [default: 4:2:0].", panel=SAVE),
    ] = None,
    tiff_compression: Annotated[
        Optional[opt.TiffCompression],
        _option("--tiff-compression", help="TIFF compression [default: lzw].", panel=SAVE),
    ] = None,
    metadata: opt.MetadataOpt = None,
    strip_icc: StripIcc = False,
    max_pixels: opt.MaxPixels = None,
    max_animation_bytes: opt.MaxAnimationBytes = None,
):
    """
    Edit images: the photo's own orientation first, then [bold]--rotate[/bold] and
    [bold]--flip[/bold], then [bold]--crop[/bold] (or [bold]--aspect[/bold]), then
    [bold]--brightness[/bold], [bold]--contrast[/bold] and [bold]--saturation[/bold] (the same as
    CSS filters). Each image keeps its format; animated GIFs and WebPs have every frame edited.
    Writes [cyan]<name>.edited.<ext>[/cyan] next to each image, under --output-dir, or to -o for a
    single image.

    [dim]Examples:  sashimi edit photo.jpg --rotate 90 --crop 1200x800+0+100   ·   sashimi edit photos/ --aspect 1:1 --saturation 1.2[/dim]
    """
    from sashimi.api.edit import edit_images_generator
    from sashimi.cli import _limits
    from sashimi.config import IMAGE_EDIT
    from sashimi.config._base import normalize_target

    opt.single_file_or_fail(paths, output)
    limits = _limits.resolve(max_pixels=max_pixels, max_animation_bytes=max_animation_bytes)
    if crop is not None and aspect is not None:
        fail("Give --crop or --aspect, not both.")
    if aspect is not None:
        from sashimi.core.edit import aspect_crop
        from sashimi.errors import InvalidOptionsError

        try:
            aspect_crop(16, 9, aspect)
        except InvalidOptionsError as e:
            fail(f"--aspect: {e.user_message}")
    suffixes = {f".{name}": name for name in EditFormat.__members__} | {".jpg": "jpeg", ".tif": "tiff"}
    fmt = normalize_target(opt.format_from_output(opt.enum_value(output_format), output, suffixes))
    options = {
        "rotate": int(rotate.value) if rotate else None,
        "flip": opt.enum_value(flip),
        **parse_crop(crop),
        "brightness": brightness,
        "contrast": contrast,
        "saturation": saturation,
        **_save_options(
            quality, lossless, webp_method, compress_level, optimize, progressive, subsampling, tiff_compression,
            metadata, strip_icc,
        ),
    }  # fmt: skip
    configs, config_for = _configs(IMAGE_EDIT, options, fmt, "images")
    runner = BatchRunner("edit", "Edit", json_output, limit_output, dry_run=dry_run)
    gen = edit_images_generator(
        paths,
        configs[fmt] if fmt else config_for,
        crop_aspect=aspect,
        recursive=recursive,
        force=force,
        on_file_progress=runner.file_progress,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
        limits=limits,
    )
    runner.run(gen, paths)


def frames_cmd(
    paths: Annotated[
        List[pathlib.Path], typer.Argument(help="Animated GIFs or WebPs, or directories.", show_default=False)
    ],
    keep: Annotated[
        Optional[str],
        _option(
            "--keep",
            help="Frames to keep, numbered from 1 as `sashimi info` lists them, e.g. 1-10,15 [default: all].",
            panel=FRAMES,
        ),
    ] = None,
    drop: Annotated[
        Optional[str],
        _option("--drop", help="Frames to leave out, numbered from 1, e.g. 3,7-9 [default: none].", panel=FRAMES),
    ] = None,
    speed: Annotated[
        Optional[float],
        _option("--speed", help="Playback speed: 2 is twice as fast, 0.5 half as fast [default: 1].", panel=FRAMES),
    ] = None,
    output_format: Annotated[
        Optional[FramesFormat], opt.format_option("Output format [default: same as input].")
    ] = None,
    output: Annotated[Optional[pathlib.Path], opt.output_option("<name>.edited.<ext> next to the input")] = None,
    output_dir: opt.OutputDir = None,
    recursive: opt.Recursive = False,
    force: opt.Force = False,
    dry_run: opt.DryRun = False,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
    quality: Annotated[
        Optional[str],
        _option(
            "--quality",
            "-q",
            help="WebP quality: 92, 92% or 0.92 [default: 92, high, since the frames are re-encoded].",
            panel=SAVE,
        ),
    ] = None,
    lossless: Lossless = False,
    webp_method: WebpMethod = None,
    optimize: Annotated[
        Optional[bool],
        _option(
            "--optimize/--no-optimize",
            help="GIF: drop unused palette entries [default: off].",
            show_default=False,
            panel=SAVE,
        ),
    ] = None,
    metadata: opt.MetadataOpt = None,
    strip_icc: StripIcc = False,
    max_pixels: opt.MaxPixels = None,
    max_animation_bytes: opt.MaxAnimationBytes = None,
):
    """
    Keep or drop frames of animated GIFs and WebPs, and change their speed. Kept frames play in
    their original order, each as it looked in the animation. Each file keeps its format.
    Writes [cyan]<name>.edited.<ext>[/cyan] next to each animation, under --output-dir, or to -o
    for a single animation. See the frames and their durations with [bold]sashimi info[/bold].

    [dim]Examples:  sashimi frames clip.gif --drop 1-5   ·   sashimi frames clip.webp --keep 10-40 --speed 1.5[/dim]
    """
    from sashimi.api.edit import edit_frames_generator
    from sashimi.cli import _limits
    from sashimi.config import IMAGE_FRAMES

    opt.single_file_or_fail(paths, output)
    limits = _limits.resolve(max_pixels=max_pixels, max_animation_bytes=max_animation_bytes)
    fmt = opt.format_from_output(opt.enum_value(output_format), output, {".gif": "gif", ".webp": "webp"})
    options = {
        "keep": parse_frames(keep, "--keep"),
        "speed": speed,
        **_save_options(quality, lossless, webp_method, None, optimize, False, None, None, metadata, strip_icc),
    }
    dropped = parse_frames(drop, "--drop") or []
    configs, config_for = _configs(IMAGE_FRAMES, options, fmt, "animations")
    runner = BatchRunner("frames", "Frames", json_output, limit_output, dry_run=dry_run)
    gen = edit_frames_generator(
        paths,
        configs[fmt] if fmt else config_for,
        drop=dropped,
        recursive=recursive,
        force=force,
        on_file_progress=runner.file_progress,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
        limits=limits,
    )
    runner.run(gen, paths)
