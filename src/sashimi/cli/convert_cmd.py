# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""`sashimi convert`, `resize`, `rotate` and `favicon`."""

from __future__ import annotations

import pathlib
from enum import Enum
from typing import Annotated, Any, List, Optional

import typer

from sashimi.cli import _options as opt
from sashimi.cli._output import BatchRunner, err_console, exit_on_invalid_options, fail, refusals, warn_unused

MANIFEST = "Web app manifest"

FLAGS = {
    "quality": "--quality",
    "lossless": "--lossless",
    "preserve_transparency": "--flatten",
    "background_color": "--background",
    "optimize": "--optimize/--no-optimize",
    "progressive": "--progressive",
    "jpeg_subsampling": "--subsampling",
    "webp_method": "--webp-method",
    "png_compress_level": "--compress-level",
    "tiff_compression": "--tiff-compression",
    "resample": "--resample",
    "auto_orient": "--no-orient",
    "metadata": "--metadata",
    "keep_icc": "--strip-icc",
    "max_width": "--width",
    "max_height": "--height",
    "start_seconds": "--start",
    "duration_seconds": "--duration",
    "fps": "--fps",
    "crf": "--crf",
    "preset": "--preset",
    "speed": "--speed",
    "audio_bitrate_kbps": "--audio-bitrate",
    "mute": "--mute",
    "loop": "--no-loop",
    "dither": "--dither",
    "palette_mode": "--palette",
    "bitrate_kbps": "--bitrate",
    "mono": "--mono",
    "sample_rate": "--sample-rate",
    "wav_bit_depth": "--bit-depth",
    "flac_compression_level": "--flac-level",
    "sizes": "--ico-sizes",
    "fit": "--ico-fit",
    "ico_sizes": "--ico-sizes",
    "name": "--name",
    "short_name": "--short-name",
    "theme_color": "--theme-color",
    "manifest_background_color": "--manifest-background",
    "target": "--output-format",
    "rotate": "angle",
    "flip": "--flip",
}


class Target(str, Enum):
    jpeg = "jpeg"
    jpg = "jpg"
    png = "png"
    webp = "webp"
    gif = "gif"
    bmp = "bmp"
    tiff = "tiff"
    tif = "tif"
    ico = "ico"
    webm = "webm"
    mp4 = "mp4"
    mp3 = "mp3"
    m4a = "m4a"
    wav = "wav"
    ogg = "ogg"
    flac = "flac"


class ImageFormat(str, Enum):
    jpeg = "jpeg"
    jpg = "jpg"
    png = "png"
    webp = "webp"
    gif = "gif"
    bmp = "bmp"
    tiff = "tiff"
    tif = "tif"


class RotateFormat(str, Enum):
    jpeg = "jpeg"
    jpg = "jpg"
    png = "png"
    webp = "webp"
    gif = "gif"
    bmp = "bmp"
    tiff = "tiff"
    tif = "tif"
    mp4 = "mp4"
    webm = "webm"


class Angle(str, Enum):
    a0 = "0"
    a90 = "90"
    a180 = "180"
    a270 = "270"


class Flip(str, Enum):
    none = "none"
    horizontal = "horizontal"
    vertical = "vertical"


def _validate(model: Any, data: dict[str, Any]) -> Any:
    from pydantic import ValidationError

    try:
        return model.model_validate(data)
    except ValidationError as e:
        exit_on_invalid_options(e, FLAGS)


def _flags(fields: list[str]) -> list[str]:
    return [FLAGS.get(field, field) for field in fields]


def _image_options(
    quality, lossless, flatten, background, optimize, progressive, subsampling, webp_method, compress_level,
    tiff_compression, resample, no_orient, metadata, strip_icc, width, height,
) -> dict[str, Any]:  # fmt: skip
    """Image fields from their flags; unset flags are None."""
    return {
        "quality": opt.parse_quality(quality),
        "lossless": lossless or None,
        "preserve_transparency": False if flatten else None,
        "background_color": background,
        "optimize": optimize,
        "progressive": progressive or None,
        "jpeg_subsampling": opt.enum_value(subsampling),
        "webp_method": webp_method,
        "png_compress_level": compress_level,
        "tiff_compression": opt.enum_value(tiff_compression),
        "resample": opt.enum_value(resample),
        "auto_orient": False if no_orient else None,
        "metadata": opt.enum_value(metadata),
        "keep_icc": False if strip_icc else None,
        "max_width": width,
        "max_height": height,
    }


def convert_cmd(
    arguments: Annotated[
        List[str],
        typer.Argument(
            metavar="PATH... FORMAT",
            help="Files or folders to convert, then the output format, written with the extension as typed: "
            "jpeg (or jpg), png, webp, gif, bmp, tiff (or tif), ico, webm, mp4, mp3, m4a, wav, ogg or flac.",
            show_default=False,
        ),
    ],
    output_format: Annotated[
        Optional[Target],
        opt.output_format_option(
            "The output format, instead of the last argument, for when a file or folder is named like a format: "
            "[cyan]sashimi convert webp --output-format jpeg[/cyan] converts the folder webp "
            "[default: the last argument]."
        ),
    ] = None,
    output: Annotated[Optional[pathlib.Path], opt.output_option()] = None,
    output_name: Annotated[Optional[str], opt.output_name_option("convert", "{input_name}.{output_ext}")] = None,
    output_dir: Annotated[Optional[pathlib.Path], opt.output_dir_option("convert")] = None,
    no_output_dir: opt.NoOutputDir = False,
    recursive: opt.Recursive = False,
    force: opt.Force = False,
    yes: opt.Yes = False,
    dry_run: opt.DryRun = False,
    workers: opt.Workers = None,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
    width: opt.MaxWidth = None,
    height: opt.MaxHeight = None,
    start: opt.Start = None,
    duration: opt.Duration = None,
    quality: opt.Quality = None,
    lossless: opt.Lossless = False,
    flatten: opt.Flatten = False,
    background: opt.Background = None,
    optimize: opt.Optimize = None,
    progressive: opt.Progressive = False,
    subsampling: opt.SubsamplingOpt = None,
    webp_method: opt.WebpMethod = None,
    compress_level: opt.CompressLevel = None,
    tiff_compression: opt.TiffCompressionOpt = None,
    resample: opt.ResampleOpt = None,
    no_orient: opt.NoOrient = False,
    metadata: opt.MetadataOpt = None,
    strip_icc: opt.StripIcc = False,
    ico_sizes: opt.IcoSizes = None,
    ico_fit: opt.IcoFitOpt = None,
    fps: opt.Fps = None,
    crf: opt.Crf = None,
    preset: opt.PresetOpt = None,
    speed: opt.Speed = None,
    audio_bitrate: opt.AudioBitrate = None,
    mute: opt.Mute = False,
    no_loop: opt.NoLoop = False,
    dither: opt.DitherOpt = None,
    palette: opt.PaletteOpt = None,
    bitrate: opt.Bitrate = None,
    mono: opt.Mono = False,
    sample_rate: opt.SampleRate = None,
    bit_depth: opt.BitDepth = None,
    flac_level: opt.FlacLevel = None,
    max_pixels: opt.MaxPixels = None,
    max_frame_bytes: opt.MaxFrameBytes = None,
):
    """
    Convert images, videos and audio. Each file is handled by its type:

    [bold]Images[/bold] → jpeg, png, webp, gif, bmp, tiff, ico
    [bold]Videos[/bold] → gif, webp (animated), webm, mp4; [bold]animated GIFs[/bold] → webm, mp4
    [bold]Videos and audio[/bold] → mp3, m4a, wav, ogg, flac

    Give the files or folders, then the format, as cp takes its destination last: [cyan]sashimi
    convert *.png webp[/cyan]. Writes [cyan]photo.png.webp[/cyan] for photo.png, with the
    extension as you type the format (jpeg or jpg), next to each input, under --output-dir, or to
    -o for a single file ([cyan]--output-name '{input_stem}.{output_ext}'[/cyan] for photo.webp).
    Files already in the target format, and this conversion's own earlier outputs in a searched
    folder, are skipped. A folder whose files convert differently (photos and videos to WebP) asks
    first unless -y.

    To MP4, a video keeps its first video and audio track, copied when they already fit MP4 and
    re-encoded otherwise; [bold]sashimi remux --output-format mp4[/bold] instead copies every
    stream as it is. Both write [cyan]clip.mkv.mp4[/cyan], so each reports the other's output as
    existing.
    """
    from sashimi.config._base import normalize_target

    paths, typed_target = opt.paths_then_format(
        arguments, [member.value for member in Target], opt.enum_value(output_format), "convert"
    )
    target = Target(typed_target)
    t = normalize_target(target.value)
    opt.single_file_or_fail(paths, output)
    from sashimi.cli import _limits

    limits = _limits.resolve(max_pixels=max_pixels, max_frame_bytes=max_frame_bytes)

    from sashimi.api._batch import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
    from sashimi.api.convert import convert_files_generator, own_extensions
    from sashimi.config import AUDIO_CONVERT, IMAGE_CONVERT, VIDEO_CONVERT, IcoConfig

    start_s, duration_s = opt.parse_time(start, "--start"), opt.parse_time(duration, "--duration")
    options = {
        **_image_options(
            quality, lossless, flatten, background, optimize, progressive, subsampling, webp_method,
            compress_level, tiff_compression, resample, no_orient, metadata, strip_icc, width, height,
        ),
        "sizes": opt.parse_sizes(ico_sizes), "fit": opt.enum_value(ico_fit),
        "start_seconds": start_s, "duration_seconds": duration_s, "fps": fps, "crf": crf,
        "preset": opt.enum_value(preset), "speed": speed, "audio_bitrate_kbps": audio_bitrate, "mute": mute or None,
        "loop": False if no_loop else None, "dither": opt.enum_value(dither), "palette_mode": opt.enum_value(palette),
        "bitrate_kbps": bitrate, "mono": mono or None, "sample_rate": sample_rate, "wav_bit_depth": bit_depth,
        "flac_compression_level": flac_level,
    }  # fmt: skip

    models: dict[str, Any] = {}
    if t == "ico":
        models["image"] = IcoConfig
    elif t in IMAGE_CONVERT.targets:
        models["image"] = IMAGE_CONVERT.model_for(target=t)
    if t in VIDEO_CONVERT.targets:
        models["video"] = VIDEO_CONVERT.model_for(target=t)
    if t in AUDIO_CONVERT.targets:
        models["audio"] = AUDIO_CONVERT.model_for(target=t)
    used = {field for model in models.values() for field in model.model_fields}
    warn_unused(_flags([field for field in opt.given(options) if field not in used]), f"{t.upper()} output")

    configs: dict[str, Any] = {}
    extensions = None
    for kind, model in models.items():
        data, _ = opt.split_options(model, options)
        named_video = any(path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS for path in paths)
        if kind == "video" and "image" in models and not named_video:
            from pydantic import ValidationError

            try:
                configs[kind] = model.model_validate(data)
            except ValidationError as e:
                detail = e.errors()[0]
                field = str(detail["loc"][0]) if detail["loc"] else "options"
                err_console.print(
                    f"[yellow]Warning: {FLAGS.get(field, field)} is invalid for videos ({detail['msg']}); "
                    "only images will be converted.[/yellow]"
                )
                extensions = [ext for ext in IMAGE_EXTENSIONS if ext not in own_extensions(t)]
        else:
            configs[kind] = _validate(model, data)

    runner = BatchRunner(
        "convert",
        f"Convert to {t.upper()}",
        json_output,
        limit_output,
        dry_run=dry_run,
        yes=yes or force,
        verb=f"convert {{every}} to {t.upper()}",
    )
    naming = opt.naming_args("convert", output_name, output)
    with refusals():
        gen = convert_files_generator(
            paths,
            target.value,  # type: ignore[arg-type]
            image_config=configs.get("image") if t != "ico" else None,
            ico_config=configs.get("image") if t == "ico" else None,
            video_config=configs.get("video"),
            audio_config=configs.get("audio"),
            recursive=recursive,
            force=force,
            max_workers=workers,
            extensions=extensions,
            on_file_progress=runner.file_progress,
            output=output,
            **opt.output_dir_args("convert", output_dir, no_output_dir, output),
            dry_run=dry_run,
            limits=limits,
            **naming,
        )
    runner.run(gen, paths)


def resize_cmd(
    paths: Annotated[List[pathlib.Path], typer.Argument(help="Image files or directories.", show_default=False)],
    width: opt.FitWidth = None,
    height: opt.FitHeight = None,
    output_format: Annotated[
        Optional[ImageFormat],
        opt.output_format_option(
            "Output format: jpeg (or jpg), png, webp, gif, bmp or tiff (or tif), written with the extension as "
            "typed [default: same as input]."
        ),
    ] = None,
    output: Annotated[Optional[pathlib.Path], opt.output_option()] = None,
    output_name: Annotated[Optional[str], opt.output_name_option("resize", "{input_name}.{role}.{output_ext}")] = None,
    output_dir: Annotated[Optional[pathlib.Path], opt.output_dir_option("resize")] = None,
    no_output_dir: opt.NoOutputDir = False,
    recursive: opt.Recursive = False,
    force: opt.Force = False,
    yes: opt.Yes = False,
    dry_run: opt.DryRun = False,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
    quality: opt.ImageQuality = None,
    lossless: opt.Lossless = False,
    flatten: opt.Flatten = False,
    background: opt.ImageBackground = None,
    optimize: opt.Optimize = None,
    progressive: opt.Progressive = False,
    subsampling: opt.SubsamplingOpt = None,
    webp_method: opt.WebpMethod = None,
    compress_level: opt.CompressLevel = None,
    tiff_compression: opt.TiffCompressionOpt = None,
    resample: opt.ResampleOpt = None,
    no_orient: opt.NoOrient = False,
    metadata: opt.MetadataOpt = None,
    strip_icc: opt.StripIcc = False,
    max_pixels: opt.MaxPixels = None,
):
    """
    Shrink images to fit --width and/or --height (never upscaling), keeping their format unless
    --output-format is given, e.g. [cyan]sashimi resize *.jpg -w 800[/cyan]. Writes
    [cyan]photo.jpg.resized.jpg[/cyan] for photo.jpg
    ([cyan]photo.jpg.resized.webp[/cyan] with --output-format webp) next to each image, under
    --output-dir, or to -o for a single image (whose extension sets the format if
    --output-format doesn't). Videos aren't supported yet.
    """
    from sashimi.api.resize import resize_images_generator
    from sashimi.cli import _limits
    from sashimi.config import IMAGE_RESIZE
    from sashimi.config._base import normalize_target

    if width is None and height is None:
        fail("Give --width and/or --height to resize to.")
    opt.single_file_or_fail(paths, output)
    limits = _limits.resolve(max_pixels=max_pixels)
    image_suffixes = {f".{name}": normalize_target(name) for name in ImageFormat.__members__} | {".tif": "tiff"}
    fmt = normalize_target(opt.format_from_output(opt.enum_value(output_format), output, image_suffixes))
    options = _image_options(
        quality, lossless, flatten, background, optimize, progressive, subsampling, webp_method,
        compress_level, tiff_compression, resample, no_orient, metadata, strip_icc, width, height,
    )  # fmt: skip
    targets = [fmt] if fmt else list(IMAGE_RESIZE.targets)
    configs = {}
    for name in targets:
        model = IMAGE_RESIZE.model_for(target=name)
        data, _ = opt.split_options(model, options)
        configs[name] = _validate(model, data)
    unused = [
        f for f in opt.given(options) if all(f not in IMAGE_RESIZE.model_for(target=n).model_fields for n in targets)
    ]
    warn_unused(_flags(unused), f"{targets[0].upper()} output" if fmt else "any image format")
    warned: set[str] = set()

    def config_for(source_format: str) -> Any:
        model = IMAGE_RESIZE.model_for(target=source_format)
        skipped = [f for f in opt.given(options) if f not in model.model_fields and f not in unused]
        if skipped and source_format not in warned:
            warned.add(source_format)
            warn_unused(_flags(skipped), f"{source_format.upper()} images")
        return configs[source_format]

    runner = BatchRunner("resize", "Resize", json_output, limit_output, dry_run=dry_run, yes=yes or force)
    naming = opt.naming_args("resize", output_name, output)
    with refusals():
        gen = resize_images_generator(
            paths,
            configs[targets[0]] if fmt else config_for,
            output_ext=opt.enum_value(output_format),
            recursive=recursive,
            force=force,
            on_file_progress=runner.file_progress,
            output=output,
            **opt.output_dir_args("resize", output_dir, no_output_dir, output),
            dry_run=dry_run,
            limits=limits,
            **naming,
        )
    runner.run(gen, paths)


def rotate_cmd(
    arguments: Annotated[
        List[str],
        typer.Argument(
            metavar="PATH... [ANGLE]",
            help="Images, animations, videos or folders, then the clockwise angle: 0, 90, 180 or 270, "
            "or 90 when left out.",
            show_default=False,
        ),
    ],
    angle_option: Annotated[
        Optional[Angle],
        opt.option(
            "--angle",
            metavar="ANGLE",
            help="The angle, instead of the last argument, for when a file or folder is named like one: "
            "[cyan]sashimi rotate 90 --angle 180[/cyan] rotates the folder 90 [default: the last argument, or 90].",
            panel=opt.GENERAL,
        ),
    ] = None,
    flip: Annotated[
        Flip,
        opt.option(
            "--flip", metavar="DIRECTION", help="Mirror the picture: none, horizontal or vertical.", panel=opt.GENERAL
        ),
    ] = Flip.none,
    output_format: Annotated[
        Optional[RotateFormat],
        opt.output_format_option(
            "Output format, written with the extension as typed: an image format (jpeg or jpg, png, webp, "
            "gif, bmp, tiff or tif) rotates only images (png is lossless), mp4 or webm only videos "
            "[default: images keep theirs, videos mp4]."
        ),
    ] = None,
    output: Annotated[Optional[pathlib.Path], opt.output_option()] = None,
    output_name: Annotated[Optional[str], opt.output_name_option("rotate", "{input_name}.{role}.{output_ext}")] = None,
    output_dir: Annotated[Optional[pathlib.Path], opt.output_dir_option("rotate")] = None,
    no_output_dir: opt.NoOutputDir = False,
    recursive: opt.Recursive = False,
    force: opt.Force = False,
    yes: opt.Yes = False,
    dry_run: opt.DryRun = False,
    workers: opt.Workers = None,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
    quality: Annotated[
        Optional[str],
        opt.option(
            "--quality",
            "-q",
            help="[Image] JPEG/WebP quality: 92, 92% or 0.92 [default: 92, high, since the image is re-encoded].",
            panel=opt.IMAGE,
        ),
    ] = None,
    crf: opt.Crf = None,
    preset: opt.PresetOpt = None,
    speed: opt.Speed = None,
    audio_bitrate: opt.AudioBitrate = None,
    mute: opt.Mute = False,
    metadata: opt.MetadataOpt = None,
    max_pixels: opt.MaxPixels = None,
    max_animation_bytes: opt.MaxAnimationBytes = None,
):
    """
    Rotate (clockwise) and/or flip photos, animated GIFs and WebPs (every frame) and videos:
    give the files or folders, then the angle ([cyan]sashimi rotate *.jpg 180[/cyan]; 90 when
    left out). Images go through the same code as [bold]sashimi edit --rotate[/bold] and keep their format
    (JPEGs are re-saved at high quality; --output-format png keeps every pixel); videos are
    re-encoded as MP4. Writes [cyan]photo.jpg.rotated.jpg[/cyan] for photo.jpg
    ([cyan]photo.jpg.rotated.png[/cyan] with --output-format png, [cyan]clip.mkv.rotated.mp4[/cyan]
    for clip.mkv) next to each file, under --output-dir, or to -o for a single file. A folder of
    both images and videos asks first unless -y.
    """
    from sashimi.api.transform import rotate_files_generator
    from sashimi.cli import _limits
    from sashimi.cli.edit_cmd import _configs
    from sashimi.config import IMAGE_EDIT, VIDEO_TRANSFORM
    from sashimi.config._base import normalize_target

    paths, typed_angle = opt.paths_then_angle(
        arguments, [member.value for member in Angle], opt.enum_value(angle_option), Angle.a90.value, "rotate"
    )
    angle = Angle(typed_angle)
    opt.single_file_or_fail(paths, output)
    limits = _limits.resolve(max_pixels=max_pixels, max_animation_bytes=max_animation_bytes)
    suffixes = {f".{member.value}": member.value for member in RotateFormat}
    typed = opt.format_from_output(opt.enum_value(output_format), output, suffixes)
    fmt = normalize_target(typed)
    images = fmt is None or fmt in IMAGE_EDIT.targets
    videos = fmt is None or fmt in VIDEO_TRANSFORM.targets
    turn = int(angle.value)
    image_options = {
        "rotate": turn,
        "flip": flip.value,
        "quality": opt.parse_quality(quality),
        "metadata": opt.enum_value(metadata),
    }
    video_options = {
        "rotate": turn, "flip": flip.value, "crf": crf, "preset": opt.enum_value(preset), "speed": speed,
        "audio_bitrate_kbps": audio_bitrate, "mute": mute or None, "metadata": opt.enum_value(metadata),
    }  # fmt: skip
    if turn == 0 and flip is Flip.none:
        fail("Choose a rotation or a flip.")
    unused = []
    if not images:
        unused += [field for field in ("quality",) if image_options[field] is not None]
    if not videos:
        unused += [f for f in ("crf", "preset", "speed", "audio_bitrate_kbps", "mute") if video_options[f] is not None]
    warn_unused(_flags(unused), f"{str(fmt).upper()} output")
    video_config = None
    if videos:
        model = VIDEO_TRANSFORM.model_for(target=fmt if fmt in VIDEO_TRANSFORM.targets else "mp4")
        data, _ = opt.split_options(model, video_options)
        video_config = _validate(model, data)
    image_config: Any = None
    if images:
        configs, config_for = _configs(IMAGE_EDIT, opt.given(image_options), fmt, "images")
        image_config = configs[fmt] if fmt else config_for
    runner = BatchRunner(
        "rotate",
        "Rotate",
        json_output,
        limit_output,
        dry_run=dry_run,
        yes=yes or force,
        verb="rotate {every}",
    )
    naming = opt.naming_args("rotate", output_name, output)
    with refusals():
        gen = rotate_files_generator(
            paths,
            turn,
            flip.value,
            output_format=typed,
            image_config=image_config,
            video_config=video_config,
            recursive=recursive,
            force=force,
            max_workers=workers,
            on_file_progress=runner.file_progress,
            output=output,
            **opt.output_dir_args("rotate", output_dir, no_output_dir, output),
            dry_run=dry_run,
            limits=limits,
            **naming,
        )
    runner.run(gen, paths)


def favicon_cmd(
    paths: Annotated[List[pathlib.Path], typer.Argument(help="Image files or directories.", show_default=False)],
    output: Annotated[Optional[pathlib.Path], opt.output_option()] = None,
    output_name: Annotated[Optional[str], opt.output_name_option("favicon", "{input_name}.{role}.{output_ext}")] = None,
    output_dir: Annotated[Optional[pathlib.Path], opt.output_dir_option("favicon")] = None,
    no_output_dir: opt.NoOutputDir = False,
    recursive: opt.Recursive = False,
    force: opt.Force = False,
    yes: opt.Yes = False,
    dry_run: opt.DryRun = False,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
    ico_sizes: Annotated[
        Optional[str],
        opt.option("--ico-sizes", help="Sizes inside favicon.ico, e.g. 16,32,48 [default: 16,32,48].", panel=opt.IMAGE),
    ] = None,
    fit: Annotated[
        Optional[opt.IcoFit],
        opt.option("--fit", help="Pad or crop non-square images [default: pad].", panel=opt.IMAGE),
    ] = None,
    resample: opt.ResampleOpt = None,
    background: Annotated[
        Optional[str],
        opt.option(
            "--background",
            help="Fill for the Apple touch icon, which must be opaque [default: #ffffff].",
            panel=opt.IMAGE,
        ),
    ] = None,
    name: Annotated[
        Optional[str],
        opt.option("--name", help="The web app's name in site.webmanifest [default: none].", panel=MANIFEST),
    ] = None,
    short_name: Annotated[
        Optional[str], opt.option("--short-name", help="A short name for home screens [default: none].", panel=MANIFEST)
    ] = None,
    theme_color: Annotated[
        Optional[str],
        opt.option(
            "--theme-color",
            help="Browser UI colour, e.g. '#336699', in the manifest and HTML [default: none].",
            panel=MANIFEST,
        ),
    ] = None,
    manifest_background: Annotated[
        Optional[str],
        opt.option(
            "--manifest-background", help="Splash screen colour in the manifest [default: none].", panel=MANIFEST
        ),
    ] = None,
    max_pixels: opt.MaxPixels = None,
):
    """
    Make a favicon pack for each image: a .zip with [cyan]favicon.ico[/cyan], PNG icons (16, 32, 48,
    180 for Apple and 192, 512 for web apps), [cyan]site.webmanifest[/cyan] and [cyan]favicon.html[/cyan]
    with the <head> tags to paste. Writes [cyan]logo.png.favicons.zip[/cyan] for logo.png, next
    to each image, under --output-dir, or to -o for a single image.
    """
    from sashimi.api.favicon import make_favicon_packs_generator
    from sashimi.cli import _limits
    from sashimi.config import FaviconPackConfig

    opt.single_file_or_fail(paths, output)
    limits = _limits.resolve(max_pixels=max_pixels)
    options = {
        "ico_sizes": opt.parse_sizes(ico_sizes, "--ico-sizes"),
        "fit": opt.enum_value(fit),
        "resample": opt.enum_value(resample),
        "background_color": background,
        "name": name,
        "short_name": short_name,
        "theme_color": theme_color,
        "manifest_background_color": manifest_background,
    }
    config = _validate(FaviconPackConfig, opt.given(options))
    runner = BatchRunner("favicon", "Favicon packs", json_output, limit_output, dry_run=dry_run, yes=yes or force)
    naming = opt.naming_args("favicon", output_name, output)
    with refusals():
        gen = make_favicon_packs_generator(
            paths,
            config,
            recursive=recursive,
            force=force,
            on_file_progress=runner.file_progress,
            output=output,
            **opt.output_dir_args("favicon", output_dir, no_output_dir, output),
            dry_run=dry_run,
            limits=limits,
            **naming,
        )
    runner.run(gen, paths)
