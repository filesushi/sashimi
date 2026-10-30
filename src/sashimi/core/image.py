# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Image conversion, resizing, ICO generation and favicon packs with Pillow."""

import contextlib
import html
import io
import json
import logging
import os
import threading
import warnings
import zipfile
from collections.abc import Iterator
from typing import Any, Literal

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict

from sashimi.config._image import (
    ANIMATED_SAVES,
    _ApngSave,
    _AvifSave,
    _BmpSave,
    _GifSave,
    _JpegSave,
    _PngSave,
    _TiffSave,
    _WebpSave,
)
from sashimi.config import (
    FAVICON_PNG_SIZES,
    ApngImageConfig,
    AvifImageConfig,
    BmpImageConfig,
    FaviconPackConfig,
    GifImageConfig,
    IcoConfig,
    ImageConvertConfig,
    JpegImageConfig,
    Limits,
    PngImageConfig,
    Resample,
    WEBP_MAX_SIDE,
    TiffImageConfig,
    WebpImageConfig,
)
from sashimi.config._base import SourceMetadata
from sashimi.config._limits import check_webp_size, megapixels, resolve_limits
from sashimi.core import _animation
from sashimi.core._atomic import PathLike, atomic_output
from sashimi.core._imagemeta import (
    Carriers,
    add_tiff_groups,
    apply_mode,
    clean_info,
    DPI_FORMATS,
    read_carriers,
    stated_dpi,
    save_arguments,
    tiff_groups,
)
from sashimi.core._ffmpeg import ProgressCallback
from sashimi.core.fidelity import (
    COLOUR_PROFILE,
    Fidelity,
    Outcome,
    derived,
    differences,
    fidelity,
    image_inventory,
    same_animation,
    same_picture,
    shown_length,
)
from sashimi.errors import MediaTooLargeError, ProcessingError, UnsupportedMediaError

__all__ = ["ImageConversion", "convert", "to_ico", "favicon_pack", "heif_available", "to_srgb"]

logger = logging.getLogger("sashimi")

NOT_AN_IMAGE = "This file isn't an image we can convert."
DAMAGED_IMAGE = "This image file is damaged or incomplete."
TOO_LARGE_IMAGE = "This image is too large to process."

_INPUT_FORMATS = frozenset({"PNG", "JPEG", "MPO", "WEBP", "GIF", "BMP", "TIFF", "HEIF", "AVIF", "ICO", "TGA", "PSD"})
_PIL_FORMAT = {
    "jpeg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "gif": "GIF",
    "bmp": "BMP",
    "tiff": "TIFF",
    "avif": "AVIF",
    "apng": "PNG",
}

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


_PILLOW_LIMIT = threading.Lock()


@contextlib.contextmanager
def _pillow_allows(max_pixels: int | None) -> Iterator[None]:
    """
    Lets ``Image.open`` read images of up to `max_pixels` pixels (``None``: any size). Pillow
    refuses images over twice ``PIL.Image.MAX_IMAGE_PIXELS`` (about 179 MP) on its own; when
    `max_pixels` is above that, its limit is raised for the call, under a lock, and restored.
    """
    current = Image.MAX_IMAGE_PIXELS
    if current is None or (max_pixels is not None and max_pixels <= 2 * current):
        yield
        return
    with _PILLOW_LIMIT:
        saved = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = None
        try:
            yield
        finally:
            Image.MAX_IMAGE_PIXELS = saved


def too_large_image(width: int, height: int, limit: int) -> MediaTooLargeError:
    """The refusal of an image over ``Limits.max_pixels``."""
    return MediaTooLargeError(
        f"This image is too large to process: it has {megapixels(width * height)} megapixels ({width}x{height}), "
        f"and the limit is {megapixels(limit)} megapixels.",
        detail=f"{width}x{height}",
        limit="max_pixels",
    )


@contextlib.contextmanager
def _open_image(input_path: PathLike, limits: Limits | None, *, load: bool = True) -> Iterator[Image.Image]:
    """
    Opens an image by content, refusing unsupported formats. The first frame is decoded unless
    `load` is False; only then are images over ``limits.max_pixels`` refused (reading headers and
    metadata decodes no pixels, so no pixel limit applies).
    """
    if "HEIF" in _INPUT_FORMATS:
        heif_available()
    max_pixels = resolve_limits(limits).max_pixels if load else None
    pillow_limit = Image.MAX_IMAGE_PIXELS
    try:
        with warnings.catch_warnings(), _pillow_allows(max_pixels):
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            img = Image.open(os.fspath(input_path))
    except Image.DecompressionBombError as e:
        if max_pixels is None or pillow_limit is None:
            raise MediaTooLargeError(TOO_LARGE_IMAGE, detail=str(e)) from e
        raise MediaTooLargeError(
            f"This image is too large to process: it has more than {megapixels(2 * pillow_limit)} megapixels, "
            f"and the limit is {megapixels(max_pixels)} megapixels.",
            detail=str(e),
            limit="max_pixels",
        ) from e
    except (UnidentifiedImageError, OSError) as e:
        raise UnsupportedMediaError(NOT_AN_IMAGE, detail=str(e)) from e
    with img:
        if img.format not in _INPUT_FORMATS:
            raise UnsupportedMediaError(NOT_AN_IMAGE, detail=f"Pillow format {img.format}")
        if max_pixels is not None and img.width * img.height > max_pixels:
            raise too_large_image(img.width, img.height, max_pixels)
        if load:
            try:
                img.load()
            except (OSError, SyntaxError, ValueError) as e:
                raise UnsupportedMediaError(DAMAGED_IMAGE, detail=str(e)) from e
        yield img


HIGH_BIT_DEPTH = frozenset({"I;16", "I;16B", "I;16L", "I;16N", "I", "F"})
"""Pillow modes with more than 8 bits per sample (16-bit and 32-bit greyscale, and floating point)."""


def eight_bit(img: Image.Image) -> Image.Image:
    """
    A greyscale image of more than 8 bits per sample as 8-bit ``L``, scaled to keep its tones (16-bit
    and 32-bit integer values are divided by 257, so 65535 is white; floating-point values from 0
    to 1 are multiplied by 255, and larger ones treated as 16-bit); other images as they are.
    Pillow's own conversion clips instead of scaling, which turns a 16-bit picture nearly white.
    """
    if img.mode not in HIGH_BIT_DEPTH:
        return img
    if img.mode == "F":
        _, top = img.getextrema()
        high = float(top) if isinstance(top, (int, float)) else 0.0
        factor = 255.0 if high <= 1.0 else 1 / 257 if high > 255 else 1.0
        return img.point(lambda v: v * factor).convert("L") if factor != 1.0 else img.convert("L")
    scaled = img.convert("I").point(lambda v: v * (1 / 257))
    return scaled.convert("L")


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


def _render(
    img: Image.Image, config: ImageConvertConfig, carriers: Carriers | None = None
) -> tuple[Image.Image, dict[str, Any], dict[int, dict[int, Any]]]:
    """
    Returns the image converted for `config.target`, the save() keyword arguments, and the EXIF
    directories to add to a TIFF afterwards.
    """
    rendered, kwargs, groups, _ = _render_from(img, config, carriers)
    return rendered, kwargs, groups


def _render_from(
    img: Image.Image, config: ImageConvertConfig, carriers: Carriers | None = None
) -> tuple[Image.Image, dict[str, Any], dict[int, dict[int, Any]], Image.Image]:
    """:func:`_render`, plus the source picture as shown (orientation applied, if asked), to compare the output with."""
    metadata = config if hasattr(config, "keep_icc") else None
    icc = img.info.get("icc_profile") if metadata and metadata.keep_icc else None
    cmyk_source = img.mode == "CMYK"
    turned = config.auto_orient and img.getexif().get(0x0112, 1) not in (None, 1)
    if config.auto_orient:
        img = ImageOps.exif_transpose(img)
    upright = img
    img = _fit(img, config.max_width, config.max_height, config.resample)

    flatten = isinstance(config, (JpegImageConfig, BmpImageConfig)) or not config.preserve_transparency
    if _has_alpha(img) and flatten:
        img = _flatten(img, config.background_color)
    img, kwargs = encode_for(img, config, config.background_color)
    if metadata is not None:
        kwargs["icc_profile"] = None if cmyk_source and img.mode != "CMYK" else icc
    groups: dict[int, dict[int, Any]] = {}
    if isinstance(config, SourceMetadata):
        kept = apply_mode(
            carriers or Carriers(), config.metadata, upright=config.auto_orient, edited=turned, size=img.size
        )
        kwargs.update(save_arguments(kept, _PIL_FORMAT[config.target]))
        if config.target == "tiff":
            groups = tiff_groups(kept)
    elif carriers is not None and carriers.dpi is not None and _PIL_FORMAT[config.target] in DPI_FORMATS:
        kwargs["dpi"] = carriers.dpi
    return clean_info(img), kwargs, groups, upright


SaveConfig = _JpegSave | _PngSave | _WebpSave | _GifSave | _BmpSave | _TiffSave | _AvifSave | _ApngSave


def encode_for(
    img: Image.Image, config: SaveConfig, background: tuple[int, int, int]
) -> tuple[Image.Image, dict[str, Any]]:
    """The image in a mode `config.target` can store, and the format's save() keyword arguments."""
    kwargs: dict[str, Any]
    keeps_depth = isinstance(config, _TiffSave) and config.tiff_compression != "jpeg"
    if isinstance(config, (_PngSave, _ApngSave)) and img.mode in HIGH_BIT_DEPTH and img.mode != "F":
        if img.mode != "I;16":
            img = img.convert("I").convert("I;16")
    elif not keeps_depth:
        img = eight_bit(img)
    if isinstance(config, _JpegSave):
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        kwargs = {
            "quality": config.quality,
            "optimize": config.optimize,
            "progressive": config.progressive,
            "subsampling": config.jpeg_subsampling,
        }
    elif isinstance(config, _WebpSave):
        check_webp_size(*img.size)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if _has_alpha(img) else "RGB")
        kwargs = {"quality": config.quality, "lossless": config.lossless, "method": config.webp_method}
    elif isinstance(config, (_PngSave, _ApngSave)):
        if img.mode not in ("1", "L", "LA", "I;16", "P", "RGB", "RGBA"):
            img = img.convert("RGBA" if _has_alpha(img) else "RGB")
        kwargs = {"optimize": config.optimize, "compress_level": config.png_compress_level}
    elif isinstance(config, _AvifSave):
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if _has_alpha(img) else "RGB")
        kwargs = _animation.avif_arguments(config)
    elif isinstance(config, _GifSave):
        if img.mode not in ("P", "L"):
            img = img.convert("RGBA" if _has_alpha(img) else "RGB")
        kwargs = {"optimize": config.optimize}
    elif isinstance(config, _TiffSave):
        kwargs = {"compression": _TIFF_COMPRESSION[config.tiff_compression]}
        if config.tiff_compression == "jpeg":
            img = _flatten(img, background) if _has_alpha(img) else img.convert("RGB")
            kwargs["quality"] = config.quality
    else:
        if img.mode not in ("1", "L", "P", "RGB"):
            img = img.convert("RGB")
        kwargs = {}
    return img, kwargs


def _save(
    img: Image.Image,
    output_path: PathLike,
    pil_format: str,
    kwargs: dict[str, Any],
    scratch_dir: PathLike | None,
    groups: dict[int, dict[int, Any]] | None = None,
) -> None:
    with atomic_output(output_path, scratch_dir) as temp:
        img.save(temp, format=pil_format, **kwargs)
        if groups:
            add_tiff_groups(temp, groups)


class ImageConversion(Outcome):
    """What :func:`convert` wrote, for telling people when part of the source was left out."""

    model_config = ConfigDict(frozen=True)

    frames: int
    """Frames in the output: 1 for a still image."""
    source_frames: int
    """Frames (or pages) in the source: 1 for a still image."""
    source: Literal["still", "animation", "pages"]
    """What the source holds: one picture, an animation (GIF, WebP, APNG or AVIF), or pages (a multi-page TIFF)."""
    reason: Literal["kept", "target", "asked"] = "kept"
    """
    Why frames were left out: ``kept`` (none were), ``target`` (the output format holds one
    picture, or only the first page is converted), or ``asked`` (``animated`` off).
    """


def _source_kind(img: Image.Image) -> tuple[Literal["still", "animation", "pages"], int]:
    if _animation.is_animation(img):
        return "animation", _animation.frame_count(img)
    if img.format == "TIFF" and _animation.frame_count(img) > 1:
        return "pages", _animation.frame_count(img)
    return "still", 1


def _fitted_size(width: int, height: int, config: ImageConvertConfig) -> tuple[int, int]:
    scale = min(
        config.max_width / width if config.max_width else 1.0,
        config.max_height / height if config.max_height else 1.0,
        1.0,
    )
    if scale >= 1.0:
        return width, height
    return max(1, round(width * scale)), max(1, round(height * scale))


def _convert_animation(
    img: Image.Image,
    input_path: PathLike,
    output_path: PathLike,
    config: GifImageConfig | WebpImageConfig | AvifImageConfig | ApngImageConfig | PngImageConfig,
    scratch_dir: PathLike | None,
    limits: Limits | None,
    on_progress: ProgressCallback | None,
) -> int:
    """Converts every frame of an animation to an animated GIF or WebP, as :func:`convert` describes."""
    total = _animation.frame_count(img)
    transpose = _orientation(img) if config.auto_orient else None
    width, height = (img.height, img.width) if transpose in _SWAPS_SIDES else img.size
    width, height = _fitted_size(width, height, config)
    _animation.check_budget(width, height, total, limits, "convert")
    if isinstance(config, WebpImageConfig):
        check_webp_size(width, height)
    carriers = read_carriers(input_path, img)
    plays = _animation.plays(img)
    icc = img.info.get("icc_profile") if getattr(config, "keep_icc", False) else None
    flatten = not config.preserve_transparency
    frames: list[Image.Image] = []
    durations: list[float] = []
    for index, frame, duration in _animation.frames_of(img):
        if transpose is not None:
            frame = frame.transpose(transpose)
        frame = _fit(frame, config.max_width, config.max_height, config.resample)
        if flatten:
            frame = _flatten(frame, config.background_color).convert("RGBA")
        frames.append(_animation.for_target(frame, config))
        durations.append(_animation.shown_ms(duration))
        if on_progress:
            on_progress((index + 1) / (total + 1), "converting")
    kept = apply_mode(
        carriers, config.metadata, upright=config.auto_orient, edited=transpose is not None, size=(width, height)
    )
    metadata = save_arguments(kept, _PIL_FORMAT[config.target])
    if icc:
        metadata["icc_profile"] = icc
    _animation.save_animation(
        frames, durations, _animation.writer_loop(plays, config), config, output_path, scratch_dir, metadata
    )
    return total


def _uses_alpha(img: Image.Image) -> bool:
    """Whether `img` has transparency that shows: an alpha channel with any pixel less than opaque."""
    if not _has_alpha(img):
        return False
    low, _ = img.convert("RGBA").getchannel("A").getextrema()
    return isinstance(low, (int, float)) and low < 255


def encoder_change(config: object) -> str | None:
    """How a lossy encoder changes the picture (``saved as JPEG at quality 85``), or None for a lossless one. Pure."""
    if isinstance(config, _JpegSave):
        return f"saved as JPEG at quality {config.quality}"
    if isinstance(config, _WebpSave) and not config.lossless:
        return f"saved as WebP at quality {config.quality}"
    if isinstance(config, _TiffSave) and config.tiff_compression == "jpeg":
        return f"saved as TIFF with JPEG compression at quality {config.quality}"
    if isinstance(config, _AvifSave):
        return f"saved as AVIF at quality {config.quality}"
    return None


def picture_changes(
    upright: Image.Image, source_mode: str, alpha: bool, written: Image.Image, config: object
) -> list[str]:
    """
    How the picture handed to the encoder (`written`) differs from the source as shown
    (`upright`), named for people: scaled, transparency flattened, reduced to 8 bits, converted
    from CMYK, and the lossy encoder (:func:`encoder_change`). Pure.
    """
    changes = []
    if (change := encoder_change(config)) is not None:
        changes.append(change)
    if written.size != upright.size:
        changes.append(f"scaled to {written.width}x{written.height}")
    if alpha and not _has_alpha(written):
        changes.append("transparency flattened")
    if source_mode in HIGH_BIT_DEPTH and written.mode not in HIGH_BIT_DEPTH:
        changes.append("reduced to 8 bits")
    if source_mode == "CMYK" and written.mode != "CMYK":
        changes.append("converted from CMYK to RGB")
    return changes


def _measured_change(config: object) -> str:
    return "reduced to 256 colours" if isinstance(config, _GifSave) else "colours changed"


def _asked_removals(config: object, source_has_profile: bool, first_frame_asked: bool) -> list[str]:
    removed = []
    if source_has_profile and getattr(config, "keep_icc", True) is False:
        removed.append(COLOUR_PROFILE)
    if first_frame_asked:
        removed.append("animation")
    return removed


def still_fidelity(
    input_path: PathLike,
    output_path: PathLike,
    config: object,
    *,
    upright: Image.Image,
    source_mode: str,
    alpha: bool,
    written: Image.Image,
    first_frame_asked: bool = False,
    limits: Limits | None = None,
    changed: list[str] | None = None,
    removed: list[str] | None = None,
) -> Fidelity:
    """
    What a still image output kept of its source (see :mod:`sashimi.core.fidelity`): the changes
    the pipeline made (:func:`picture_changes`, plus `changed`), and, when there are none, the
    output decoded and compared with `upright` pixel for pixel; then the source's and the output's
    metadata, colour profile and structure, with what ``metadata``, ``keep_icc``, ``animated``
    and `removed` asked to remove counted as removed.
    """
    changes = [*(changed or []), *picture_changes(upright, source_mode, alpha, written, config)]
    if not changes:
        with _open_image(output_path, None) as out:
            if not same_picture(upright, out):
                changes.append(_measured_change(config))
    source = image_inventory(input_path)
    output = image_inventory(output_path)
    lost, asked = differences(
        source,
        output,
        removed_metadata=getattr(config, "metadata", "keep"),
        removed=[*(removed or []), *_asked_removals(config, COLOUR_PROFILE in source.items, first_frame_asked)],
    )
    return fidelity("picture", changed=changes, lost=lost, removed=asked, checked=source.checked)


def _animation_fidelity(
    img: Image.Image,
    input_path: PathLike,
    output_path: PathLike,
    config: GifImageConfig | WebpImageConfig | AvifImageConfig | ApngImageConfig | PngImageConfig,
    limits: Limits | None,
) -> Fidelity:
    """What an animated GIF or WebP output kept of its source animation: as :func:`still_fidelity`, frame by frame."""
    transpose = _orientation(img) if config.auto_orient else None
    width, height = (img.height, img.width) if transpose in _SWAPS_SIDES else img.size
    size = _fitted_size(width, height, config)
    changes = []
    if (change := encoder_change(config)) is not None:
        changes.append(change)
    if size != (width, height):
        changes.append(f"scaled to {size[0]}x{size[1]}")
    if not config.preserve_transparency and _uses_alpha(img):
        changes.append("transparency flattened")
    if not changes:
        frames = (
            frame.transpose(transpose) if transpose is not None else frame for _, frame, _ in _animation.frames_of(img)
        )
        with _open_image(output_path, None) as out:
            if not same_animation(frames, out):
                changes.append(_measured_change(config))
            elif abs(shown_length(out) - shown_length(img)) > 10 * _animation.frame_count(img):
                changes.append("timing changed")
    source = image_inventory(input_path)
    output = image_inventory(output_path)
    lost, asked = differences(
        source,
        output,
        removed_metadata=config.metadata,
        removed=_asked_removals(config, COLOUR_PROFILE in source.items, False),
    )
    if isinstance(config, _AvifSave) and _animation.plays(img) is not None:
        lost.append("play count (AVIF animations here loop forever)")
    return fidelity("picture", changed=changes, lost=lost, removed=asked, checked=source.checked)


_ORIENTATION = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}
_SWAPS_SIDES = frozenset(
    {Image.Transpose.TRANSPOSE, Image.Transpose.TRANSVERSE, Image.Transpose.ROTATE_90, Image.Transpose.ROTATE_270}
)


def _orientation(img: Image.Image) -> Image.Transpose | None:
    try:
        return _ORIENTATION.get(int(img.getexif().get(0x0112, 1)))
    except (TypeError, ValueError):
        return None


def convert(
    input_path: PathLike,
    output_path: PathLike,
    config: ImageConvertConfig,
    *,
    scratch_dir: PathLike | None = None,
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> ImageConversion:
    """
    Converts and/or shrinks an image to JPEG, PNG, WebP, GIF, BMP or TIFF, as `config` says (see
    :data:`~sashimi.config.IMAGE_CONVERT` and :data:`~sashimi.config.IMAGE_RESIZE`), and says
    what it wrote.

    Accepts PNG (and APNG), JPEG, WebP, GIF, BMP, TIFF, AVIF, ICO (its largest picture), TGA,
    PSD (its flattened picture), and HEIC/HEIF when ``pillow-heif`` is installed. The input type
    is detected from content. An animated GIF, WebP, APNG or AVIF converted to GIF or WebP stays
    animated: every frame is converted, keeping its timing (frames stored as shorter than
    :data:`~sashimi.config.MIN_FRAME_MS` play for 100 ms, as browsers play them), the loop count
    and transparency, unless ``animated`` is off, which asks for the first frame only; its frames are held in
    memory, so one over ``limits.max_animation_bytes`` raises MediaTooLargeError. To JPEG, PNG,
    BMP or TIFF, which hold one picture, an animation gives its first frame, and a multi-page TIFF
    gives its first page; the result says so. High-bit-depth greyscale (16-bit, 32-bit, floating
    point) is scaled to 8 bits for formats that can't hold it.

    The source's metadata (EXIF, XMP, IPTC, comments, PNG text) is kept, as far as the output
    format can hold it, unless ``metadata`` says to remove location data or all of it; when the
    orientation is applied, the orientation tag is reset to 1. The colour profile is kept unless
    ``keep_icc=False`` (a CMYK profile is dropped when the output is RGB). BMP outputs hold no
    metadata. Images with more than ``limits.max_pixels`` pixels raise MediaTooLargeError, and a
    WebP wider or taller than :data:`~sashimi.config.WEBP_MAX_SIDE` raises MediaTooLargeError.
    """
    pil_format = _PIL_FORMAT[config.target]
    with _open_image(input_path, limits) as img:
        kind, count = _source_kind(img)
        if kind == "animation" and isinstance(config, ANIMATED_SAVES) and getattr(config, "animated", True):
            frames = _convert_animation(img, input_path, output_path, config, scratch_dir, limits, on_progress)
            told = _animation_fidelity(img, input_path, output_path, config, limits)
            result = ImageConversion(frames=frames, source_frames=count, source=kind, fidelity=told)
        else:
            carriers = (
                read_carriers(input_path, img) if isinstance(config, SourceMetadata) else Carriers(dpi=stated_dpi(img))
            )
            source_mode = img.mode
            alpha = _uses_alpha(img)
            rendered, kwargs, groups, upright = _render_from(img, config, carriers)
            _save(rendered, output_path, pil_format, kwargs, scratch_dir, groups)
            asked = kind == "animation" and isinstance(config, ANIMATED_SAVES)
            reason: Literal["kept", "target", "asked"] = "kept" if count == 1 else "asked" if asked else "target"
            told = still_fidelity(
                input_path,
                output_path,
                config,
                upright=upright,
                source_mode=source_mode,
                alpha=alpha,
                written=rendered,
                first_frame_asked=reason == "asked",
                limits=limits,
            )
            result = ImageConversion(frames=1, source_frames=count, source=kind, reason=reason, fidelity=told)
    if on_progress:
        on_progress(1.0, "done")
    return result


def to_srgb(img: Image.Image) -> Image.Image:
    """
    The image's colours converted from its colour profile to sRGB, as RGB or RGBA (carrying no
    profile), or the image as it is when it has no profile or the profile can't be used.
    """
    icc = img.info.get("icc_profile")
    if not icc:
        return img
    alpha = img.convert("RGBA").getchannel("A") if _has_alpha(img) else None
    base = img if img.mode in ("RGB", "CMYK", "L") else img.convert("RGB")
    try:
        from PIL import ImageCms

        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        converted = ImageCms.profileToProfile(base, source, ImageCms.createProfile("sRGB"), outputMode="RGB")
    except Exception as e:  # noqa: BLE001
        logger.info(f"Kept the colours as they are: the colour profile can't be used ({e})")
        return img
    if converted is None:
        return img
    if alpha is not None:
        converted.putalpha(alpha)
    converted.info = {}
    return converted


PRESHRINK = 4
"""Images whose square would be more than this many times the largest icon are shrunk to that first, then squared."""


def _square(img: Image.Image, fit: str, largest: int) -> Image.Image:
    """
    The image upright, in sRGB and RGBA, padded (transparent) or cropped to a centred square,
    carrying none of the source's metadata or colour profile. An image whose square's edge would
    be more than :data:`PRESHRINK` times `largest` (the largest icon made from it) is shrunk to
    that first, so a panorama isn't padded to a huge square only to be shrunk again.
    """
    oriented = to_srgb(ImageOps.exif_transpose(eight_bit(img))).convert("RGBA")
    oriented.info = {}
    edge = min(oriented.size) if fit == "crop" else max(oriented.size)
    if edge > PRESHRINK * largest:
        scale = PRESHRINK * largest / edge
        size = (max(1, round(oriented.width * scale)), max(1, round(oriented.height * scale)))
        oriented = oriented.resize(size, Image.Resampling.LANCZOS)
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
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> Outcome:
    """
    Makes a multi-size .ico (favicon). Non-square images are padded or cropped to a square (see
    ``IcoConfig.fit``); small images are scaled up to the largest size, since icons need fixed
    sizes. Transparent areas stay transparent unless ``IcoConfig.background_color`` is set.
    Colours are converted to sRGB from the image's colour profile, if it has one, and the icon
    carries no profile or metadata (browsers treat icons as sRGB). Images with more than
    ``limits.max_pixels`` pixels raise MediaTooLargeError.
    """
    config = config or IcoConfig()
    with _open_image(input_path, limits) as img:
        square = _square(img, config.fit, max(config.sizes))
        if config.background_color is not None:
            square = _filled(square, config.background_color)
        base = _ico_base(square, config.sizes, config.resample)
        _save(base, output_path, "ICO", {"sizes": [(s, s) for s in config.sizes]}, scratch_dir)
    if on_progress:
        on_progress(1.0, "done")
    sizes = ", ".join(str(size) for size in sorted(config.sizes))
    return Outcome(fidelity=derived(input_path, "picture", [f"made into icons of {sizes} px"]))


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
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> Outcome:
    """
    Makes a favicon pack: a .zip holding ``favicon.ico``, PNG icons (``favicon-16x16.png``,
    ``favicon-32x32.png``, ``favicon-48x48.png``, ``apple-touch-icon.png`` at 180 px, filled with
    ``background_color``, and ``icon-192x192.png``/``icon-512x512.png`` for the manifest), a
    ``site.webmanifest`` and ``favicon.html`` with the ``<head>`` tags. Paths inside are relative,
    so the files belong next to the pages that use them (or adjust the paths).

    The image is padded or cropped to a square as ``fit`` says and scaled to each size, up if
    needed. Colours are converted to sRGB from the image's colour profile, if it has one, and no
    icon carries a profile or the source's metadata. The zip's entries, order and timestamps are
    fixed, so the same input and config give the same bytes. Images with more than
    ``limits.max_pixels`` pixels raise MediaTooLargeError. Written atomically.
    """
    config = config or FaviconPackConfig()
    entries: list[tuple[str, bytes]] = []
    with _open_image(input_path, limits) as img:
        square = _square(img, config.fit, max(*config.ico_sizes, *FAVICON_PNG_SIZES))
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
    return Outcome(fidelity=derived(input_path, "picture", ["made into icons of every favicon size"]))
