# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Image conversion, resizing and ICO generation with Pillow."""

import contextlib
import os
import warnings
from collections.abc import Iterator
from typing import Any, Literal

from PIL import Image, ImageOps, UnidentifiedImageError

from sashimi.config import (
    DEFAULT_MAX_PIXELS,
    BmpImageConfig,
    GifImageConfig,
    IcoConfig,
    ImageConvertConfig,
    JpegImageConfig,
    PngImageConfig,
    Resample,
    TiffImageConfig,
    WebpImageConfig,
)
from sashimi.core._atomic import PathLike, atomic_output
from sashimi.core._ffmpeg import ProgressCallback
from sashimi.errors import MediaTooLargeError, UnsupportedMediaError

__all__ = ["convert", "to_ico", "heif_available"]

NOT_AN_IMAGE = "This file isn't an image we can convert."
DAMAGED_IMAGE = "This image file is damaged or incomplete."
TOO_LARGE_IMAGE = "This image is too large to process."

_INPUT_FORMATS = frozenset({"PNG", "JPEG", "MPO", "WEBP", "GIF", "BMP", "TIFF", "HEIF"})
_PIL_FORMAT = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "gif": "GIF", "bmp": "BMP", "tiff": "TIFF"}

_heif_registered: bool | None = None


def heif_available() -> bool:
    """True if HEIC/HEIF input is supported (the optional ``pillow-heif`` package is installed)."""
    global _heif_registered
    if _heif_registered is None:
        try:
            import pillow_heif  # type: ignore[import-untyped,import-not-found,unused-ignore]

            pillow_heif.register_heif_opener()
            _heif_registered = True
        except ImportError:
            _heif_registered = False
    return _heif_registered


_RESAMPLE = {
    "lanczos": Image.Resampling.LANCZOS,
    "bicubic": Image.Resampling.BICUBIC,
    "bilinear": Image.Resampling.BILINEAR,
    "hamming": Image.Resampling.HAMMING,
    "box": Image.Resampling.BOX,
    "nearest": Image.Resampling.NEAREST,
}
_TIFF_COMPRESSION = {"none": None, "lzw": "tiff_lzw", "deflate": "tiff_adobe_deflate", "jpeg": "jpeg"}


@contextlib.contextmanager
def _open_image(input_path: PathLike, max_pixels: int) -> Iterator[Image.Image]:
    """Opens an image by content (first frame only), refusing unsupported formats and oversized images."""
    if "HEIF" in _INPUT_FORMATS:
        heif_available()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            img = Image.open(os.fspath(input_path))
    except Image.DecompressionBombError as e:
        raise MediaTooLargeError(TOO_LARGE_IMAGE, detail=str(e)) from e
    except (UnidentifiedImageError, OSError) as e:
        raise UnsupportedMediaError(NOT_AN_IMAGE, detail=str(e)) from e
    with img:
        if img.format not in _INPUT_FORMATS:
            raise UnsupportedMediaError(NOT_AN_IMAGE, detail=f"Pillow format {img.format}")
        if img.width * img.height > max_pixels:
            raise MediaTooLargeError(TOO_LARGE_IMAGE, detail=f"{img.width}x{img.height}")
        try:
            img.load()
        except (OSError, SyntaxError, ValueError) as e:
            raise UnsupportedMediaError(DAMAGED_IMAGE, detail=str(e)) from e
        yield img


def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)


def _flatten(img: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    rgba = img.convert("RGBA")
    background = Image.new("RGB", rgba.size, color)
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background


def _fit(img: Image.Image, max_width: int | None, max_height: int | None, resample: Resample) -> Image.Image:
    """Scales down to fit within the box, keeping the aspect ratio. Never upscales."""
    scale = min(
        max_width / img.width if max_width else 1.0,
        max_height / img.height if max_height else 1.0,
        1.0,
    )
    if scale >= 1.0:
        return img
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    if img.mode in ("P", "1"):
        img = img.convert("RGBA" if _has_alpha(img) else "RGB")
    return img.resize(size, _RESAMPLE[resample])


def _render(img: Image.Image, config: ImageConvertConfig) -> tuple[Image.Image, dict[str, Any]]:
    """Returns the image converted for `config.target` and the save() keyword arguments."""
    metadata = (
        config if isinstance(config, (JpegImageConfig, PngImageConfig, WebpImageConfig, TiffImageConfig)) else None
    )
    icc = img.info.get("icc_profile") if metadata and metadata.keep_icc and img.mode != "CMYK" else None
    if config.auto_orient:
        img = ImageOps.exif_transpose(img)
    exif = img.getexif() if metadata and metadata.keep_exif else None
    img = _fit(img, config.max_width, config.max_height, config.resample)

    flatten = isinstance(config, (JpegImageConfig, BmpImageConfig)) or not config.preserve_transparency
    if _has_alpha(img) and flatten:
        img = _flatten(img, config.background_color)
    kwargs: dict[str, Any]
    if isinstance(config, JpegImageConfig):
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        kwargs = {
            "quality": config.quality,
            "optimize": config.optimize,
            "progressive": config.progressive,
            "subsampling": config.jpeg_subsampling,
        }
    elif isinstance(config, WebpImageConfig):
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if _has_alpha(img) else "RGB")
        kwargs = {
            "quality": config.quality,
            "lossless": config.lossless,
            "optimize": config.optimize,
            "method": config.webp_method,
        }
    elif isinstance(config, PngImageConfig):
        if img.mode not in ("1", "L", "LA", "I", "I;16", "P", "RGB", "RGBA"):
            img = img.convert("RGBA" if _has_alpha(img) else "RGB")
        kwargs = {"optimize": config.optimize, "compress_level": config.png_compress_level}
    elif isinstance(config, GifImageConfig):
        if img.mode not in ("P", "L"):
            img = img.convert("RGBA" if _has_alpha(img) else "RGB")
        kwargs = {"optimize": config.optimize}
    elif isinstance(config, TiffImageConfig):
        kwargs = {"compression": _TIFF_COMPRESSION[config.tiff_compression]}
        if config.tiff_compression == "jpeg":
            img = _flatten(img, config.background_color) if _has_alpha(img) else img.convert("RGB")
            kwargs["quality"] = config.quality
    else:
        if img.mode not in ("1", "L", "P", "RGB"):
            img = img.convert("RGB")
        kwargs = {}
    if icc:
        kwargs["icc_profile"] = icc
    if exif is not None and len(exif):
        kwargs["exif"] = exif
    return img, kwargs


def _save(
    img: Image.Image, output_path: PathLike, pil_format: str, kwargs: dict[str, Any], scratch_dir: PathLike | None
) -> None:
    with atomic_output(output_path, scratch_dir) as temp:
        img.save(temp, format=pil_format, **kwargs)


def convert(
    input_path: PathLike,
    output_path: PathLike,
    config: ImageConvertConfig,
    *,
    scratch_dir: PathLike | None = None,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    Converts and/or shrinks an image to JPEG, PNG, WebP, a static GIF, BMP or TIFF, as `config`
    says (see :data:`~sashimi.config.IMAGE_CONVERT` and :data:`~sashimi.config.IMAGE_RESIZE`).

    Accepts PNG, JPEG, WebP, BMP, TIFF, the first frame of a GIF, and HEIC/HEIF when
    ``pillow-heif`` is installed. The input type is detected from content. EXIF metadata is
    dropped unless ``keep_exif``; the colour profile is kept unless ``keep_icc=False``.
    Images with more than `max_pixels` pixels raise MediaTooLargeError.
    """
    pil_format = _PIL_FORMAT[config.target]
    with _open_image(input_path, max_pixels) as img:
        rendered, kwargs = _render(img, config)
        _save(rendered, output_path, pil_format, kwargs, scratch_dir)
    if on_progress:
        on_progress(1.0, "done")


def to_ico(
    input_path: PathLike,
    output_path: PathLike,
    config: IcoConfig | None = None,
    *,
    scratch_dir: PathLike | None = None,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    Makes a multi-size .ico (favicon). Non-square images are padded or cropped to a square (see
    ``IcoConfig.fit``); small images are scaled up to the largest size, since icons need fixed sizes.
    """
    config = config or IcoConfig()
    largest = max(config.sizes)
    with _open_image(input_path, max_pixels) as img:
        oriented = ImageOps.exif_transpose(img).convert("RGBA")
        if config.fit == "crop":
            edge = min(oriented.size)
            left, top = (oriented.width - edge) // 2, (oriented.height - edge) // 2
            square = oriented.crop((left, top, left + edge, top + edge))
        else:
            edge = max(oriented.size)
            square = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
            square.paste(oriented, ((edge - oriented.width) // 2, (edge - oriented.height) // 2))
        base = square.resize((largest, largest), _RESAMPLE[config.resample])
        _save(base, output_path, "ICO", {"sizes": [(s, s) for s in config.sizes]}, scratch_dir)
    if on_progress:
        on_progress(1.0, "done")
