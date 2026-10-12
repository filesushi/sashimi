"""`sashimi convert`, `resize` and `rotate`."""

from __future__ import annotations

import pathlib
from enum import Enum
from typing import Annotated, Any, Optional

import typer

from sashimi.cli import _options as opt
from sashimi.cli._output import BatchRunner, console, err_console, exit_on_invalid_options, fail, warn_unused

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
    "keep_exif": "--keep-exif",
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
    "target": "--format",
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


class VideoFormat(str, Enum):
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
        exit_on_invalid_options(e, FLAGS, model)


def _flags(fields: list[str]) -> list[str]:
    return [FLAGS.get(field, field) for field in fields]


def _image_options(
    quality, lossless, flatten, background, optimize, progressive, subsampling, webp_method, compress_level,
    tiff_compression, resample, no_orient, keep_exif, strip_icc, width, height,
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
        "keep_exif": keep_exif or None,
        "keep_icc": False if strip_icc else None,
        "max_width": width,
        "max_height": height,
    }


def convert_cmd(
    path: Annotated[pathlib.Path, typer.Argument(help="File or directory to convert.", show_default=False)],
    target: Annotated[Target, typer.Argument(help="Output format.", show_default=False)],
    recursive: opt.Recursive = False,
    force: opt.Force = False,
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
    keep_exif: opt.KeepExif = False,
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
):
    """
    Convert images, videos and audio. Each file is handled by its type:

    [bold]Images[/bold] → jpeg, png, webp, gif, bmp, tiff, ico
    [bold]Videos[/bold] → gif, webp (animated, up to 30 s), webm, mp4; [bold]animated GIFs[/bold] → webm, mp4
    [bold]Videos and audio[/bold] → mp3, m4a, wav, ogg, flac

    Outputs are written next to each input. Files already in the target format are skipped.
    """
    t = "jpeg" if target is Target.jpg else target.value

    from sashimi.api._batch import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
    from sashimi.api.convert import TARGET_SUFFIX, convert_files_generator
    from sashimi.config import AUDIO_CONVERT, IMAGE_CONVERT, VIDEO_CONVERT, IcoConfig

    start_s, duration_s = opt.parse_time(start, "--start"), opt.parse_time(duration, "--duration")
    options = {
        **_image_options(
            quality, lossless, flatten, background, optimize, progressive, subsampling, webp_method,
            compress_level, tiff_compression, resample, no_orient, keep_exif, strip_icc, width, height,
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
        if kind == "video" and "image" in models and not (path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS):
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
                extensions = [ext for ext in IMAGE_EXTENSIONS if ext != TARGET_SUFFIX[t]]
        else:
            configs[kind] = _validate(model, data)

    if not json_output:
        console.print(f"[bold]🔄 Converting to {t.upper()}...[/bold]")
    runner = BatchRunner(f"Convert to {t.upper()}", json_output, limit_output)
    gen = convert_files_generator(
        path,
        t,  # type: ignore[arg-type]
        image_config=configs.get("image") if t != "ico" else None,
        ico_config=configs.get("image") if t == "ico" else None,
        video_config=configs.get("video"),
        audio_config=configs.get("audio"),
        recursive=recursive,
        force=force,
        max_workers=workers,
        extensions=extensions,
        on_file_progress=runner.file_progress,
    )
    runner.run(gen)


def resize_cmd(
    path: Annotated[pathlib.Path, typer.Argument(help="Image file or directory.", show_default=False)],
    width: opt.FitWidth = None,
    height: opt.FitHeight = None,
    output_format: Annotated[
        Optional[ImageFormat],
        opt.option("--format", "-o", help="Output format [default: same as input].", panel=opt.GENERAL),
    ] = None,
    suffix: Annotated[
        str, typer.Option("--suffix", help="Appended to each output's name.", rich_help_panel=opt.GENERAL)
    ] = "_resized",
    recursive: opt.Recursive = False,
    force: opt.Force = False,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
    quality: opt.ImageQuality = None,
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
    keep_exif: opt.KeepExif = False,
    strip_icc: opt.StripIcc = False,
):
    """
    Shrink images to fit --width and/or --height (never upscaling), keeping their format unless
    --format is given. Writes [cyan]<name>_resized.<ext>[/cyan] next to each image.
    """
    from sashimi.api.resize import resize_images_generator
    from sashimi.config import IMAGE_RESIZE

    if width is None and height is None:
        fail("Give --width and/or --height to resize to.")
    fmt = {"jpg": "jpeg"}.get(opt.enum_value(output_format), opt.enum_value(output_format))
    options = _image_options(
        quality, lossless, flatten, background, optimize, progressive, subsampling, webp_method,
        compress_level, tiff_compression, resample, no_orient, keep_exif, strip_icc, width, height,
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

    if not json_output:
        console.print("[bold]📐 Resizing images...[/bold]")
    runner = BatchRunner("Resize", json_output, limit_output)
    gen = resize_images_generator(
        path,
        configs[targets[0]] if fmt else config_for,
        recursive=recursive,
        force=force,
        suffix=suffix,
        on_file_progress=runner.file_progress,
    )
    runner.run(gen)


def rotate_cmd(
    path: Annotated[pathlib.Path, typer.Argument(help="Video file or directory.", show_default=False)],
    angle: Annotated[Angle, typer.Argument(help="Clockwise rotation in degrees.")] = Angle.a90,
    flip: Annotated[Flip, typer.Option("--flip", help="Mirror the picture.", rich_help_panel=opt.GENERAL)] = Flip.none,
    output_format: Annotated[
        VideoFormat, typer.Option("--format", "-o", help="Output format.", rich_help_panel=opt.GENERAL)
    ] = VideoFormat.mp4,
    suffix: Annotated[
        str, typer.Option("--suffix", help="Appended to each output's name.", rich_help_panel=opt.GENERAL)
    ] = "_rotated",
    recursive: opt.Recursive = False,
    force: opt.Force = False,
    workers: opt.Workers = None,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
    crf: opt.Crf = None,
    preset: opt.PresetOpt = None,
    speed: opt.Speed = None,
    audio_bitrate: opt.AudioBitrate = None,
    mute: opt.Mute = False,
):
    """
    Rotate (clockwise) and/or flip videos, re-encoding them. Writes
    [cyan]<name>_rotated.mp4[/cyan] next to each video. Videos are limited to 10 minutes.
    """
    from sashimi.api.transform import transform_videos_generator
    from sashimi.config import VIDEO_TRANSFORM

    model = VIDEO_TRANSFORM.model_for(target=output_format.value)
    options = {
        "target": output_format.value, "rotate": int(angle.value), "flip": flip.value, "crf": crf,
        "preset": opt.enum_value(preset), "speed": speed, "audio_bitrate_kbps": audio_bitrate, "mute": mute or None,
    }  # fmt: skip
    data, unused = opt.split_options(model, options)
    warn_unused(_flags(unused), f"{output_format.value.upper()} output")
    config = _validate(model, data)
    if not json_output:
        console.print("[bold]🔃 Rotating videos...[/bold]")
    runner = BatchRunner("Rotate", json_output, limit_output)
    gen = transform_videos_generator(
        path,
        config,
        recursive=recursive,
        force=force,
        suffix=suffix,
        max_workers=workers,
        on_file_progress=runner.file_progress,
    )
    runner.run(gen)
