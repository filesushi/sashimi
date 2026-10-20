# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Image conversion, resizing, ICO generation and favicon packs with Pillow."""

import contextlib
import html
import io
import json
import os
import warnings
import zipfile
from collections.abc import Iterator
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from sashimi.config import (
    DEFAULT_MAX_PIXELS,
    FAVICON_PNG_SIZES,
    BmpImageConfig,
    FaviconPackConfig,
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
from sashimi.errors import MediaTooLargeError, ProcessingError, UnsupportedMediaError

__all__ = ["convert", "to_ico", "favicon_pack", "heif_available"]

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


def _square(img: Image.Image, fit: str) -> Image.Image:
    """The image upright, in RGBA, padded (transparent) or cropped to a centred square."""
    oriented = ImageOps.exif_transpose(img).convert("RGBA")
    if fit == "crop":
        edge = min(oriented.size)
        left, top = (oriented.width - edge) // 2, (oriented.height - edge) // 2
        return oriented.crop((left, top, left + edge, top + edge))
    edge = max(oriented.size)
    square = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    square.paste(oriented, ((edge - oriented.width) // 2, (edge - oriented.height) // 2))
    return square


def _filled(img: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """An RGBA image with its transparent areas filled with `color`."""
    return Image.alpha_composite(Image.new("RGBA", img.size, (*color, 255)), img)


def _ico_base(square: Image.Image, sizes: tuple[int, ...], resample: Resample) -> Image.Image:
    largest = max(sizes)
    return square.resize((largest, largest), _RESAMPLE[resample])


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
    ``IcoConfig.fit``); small images are scaled up to the largest size, since icons need fixed
    sizes. Transparent areas stay transparent unless ``IcoConfig.background_color`` is set.
    """
    config = config or IcoConfig()
    with _open_image(input_path, max_pixels) as img:
        square = _square(img, config.fit)
        if config.background_color is not None:
            square = _filled(square, config.background_color)
        base = _ico_base(square, config.sizes, config.resample)
        _save(base, output_path, "ICO", {"sizes": [(s, s) for s in config.sizes]}, scratch_dir)
    if on_progress:
        on_progress(1.0, "done")


_TOUCH_ICON = 180
_MANIFEST_ICONS = (192, 512)
_TAB_ICONS = (16, 32, 48)


def _png_name(size: int) -> str:
    if size == _TOUCH_ICON:
        return "apple-touch-icon.png"
    if size in _MANIFEST_ICONS:
        return f"icon-{size}x{size}.png"
    return f"favicon-{size}x{size}.png"


def _hex(color: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{channel:02x}" for channel in color)


def favicon_manifest(config: FaviconPackConfig) -> str:
    """The pack's ``site.webmanifest``: JSON, with icon paths relative to the manifest. Pure."""
    manifest: dict[str, Any] = {}
    if config.name:
        manifest["name"] = config.name
    if config.short_name:
        manifest["short_name"] = config.short_name
    manifest["icons"] = [
        {"src": _png_name(size), "sizes": f"{size}x{size}", "type": "image/png"} for size in _MANIFEST_ICONS
    ]
    if config.theme_color is not None:
        manifest["theme_color"] = _hex(config.theme_color)
    if config.manifest_background_color is not None:
        manifest["background_color"] = _hex(config.manifest_background_color)
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def favicon_html(config: FaviconPackConfig) -> str:
    """The pack's ``favicon.html``: the ``<head>`` tags, with paths relative to the page. Pure."""

    def tag(element: str, **attributes: str) -> str:
        text = " ".join(f'{key}="{html.escape(value, quote=True)}"' for key, value in attributes.items())
        return f"<{element} {text}>"

    lines = [tag("link", rel="icon", href="favicon.ico", sizes="any")]
    lines += [
        tag("link", rel="icon", type="image/png", sizes=f"{size}x{size}", href=_png_name(size)) for size in _TAB_ICONS
    ]
    lines.append(tag("link", rel="apple-touch-icon", sizes="180x180", href=_png_name(_TOUCH_ICON)))
    lines.append(tag("link", rel="manifest", href="site.webmanifest"))
    if config.theme_color is not None:
        lines.append(tag("meta", name="theme-color", content=_hex(config.theme_color)))
    return "\n".join(lines) + "\n"


def _zip_entry(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o644 << 16
    return info


def favicon_pack(
    input_path: PathLike,
    output_path: PathLike,
    config: FaviconPackConfig | None = None,
    *,
    scratch_dir: PathLike | None = None,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    Makes a favicon pack: a .zip holding ``favicon.ico``, PNG icons (``favicon-16x16.png``,
    ``favicon-32x32.png``, ``favicon-48x48.png``, ``apple-touch-icon.png`` at 180 px, filled with
    ``background_color``, and ``icon-192x192.png``/``icon-512x512.png`` for the manifest), a
    ``site.webmanifest`` and ``favicon.html`` with the ``<head>`` tags. Paths inside are relative,
    so the files belong next to the pages that use them (or adjust the paths).

    The image is padded or cropped to a square as ``fit`` says and scaled to each size, up if
    needed. The zip's entries, order and timestamps are fixed, so the same input and config give
    the same bytes. Written atomically.
    """
    config = config or FaviconPackConfig()
    entries: list[tuple[str, bytes]] = []
    with _open_image(input_path, max_pixels) as img:
        square = _square(img, config.fit)
        buffer = io.BytesIO()
        _ico_base(square, config.ico_sizes, config.resample).save(
            buffer, "ICO", sizes=[(s, s) for s in config.ico_sizes]
        )
        entries.append(("favicon.ico", buffer.getvalue()))
        for size in FAVICON_PNG_SIZES:
            icon = square.resize((size, size), _RESAMPLE[config.resample])
            if size == _TOUCH_ICON:
                icon = _filled(icon, config.background_color).convert("RGB")
            buffer = io.BytesIO()
            icon.save(buffer, "PNG", compress_level=9)
            entries.append((_png_name(size), buffer.getvalue()))
    entries.append(("site.webmanifest", favicon_manifest(config).encode("utf-8")))
    entries.append(("favicon.html", favicon_html(config).encode("utf-8")))
    try:
        with atomic_output(output_path, scratch_dir) as temp:
            with zipfile.ZipFile(temp, "w") as archive:
                for name, data in entries:
                    archive.writestr(_zip_entry(name), data)
    except OSError as e:
        raise ProcessingError(f"Cannot write {os.fspath(output_path)}: {e}") from e
    if on_progress:
        on_progress(1.0, "done")
