# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Image editing (rotate, flip, crop, and brightness, contrast and saturation that match CSS filters),
frame selection and speed for animated GIF and WebP, frame information, and frame strips for UIs.
Pillow only; no ffmpeg needed.
"""

import math
import os
import struct
from collections.abc import Iterator, Sequence
from typing import Any

from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict

from sashimi.config import (
    MIN_FRAME_MS,
    WEBP_MAX_SIDE,
    FramesConfig,
    FrameStripConfig,
    GifEditConfig,
    ImageEditConfig,
    Limits,
)
from sashimi.config._base import SourceMetadata
from sashimi.config._image import _BmpSave, _GifSave, _JpegSave, _Metadata, _WebpSave
from sashimi.config._limits import megabytes, resolve_limits
from sashimi.core._atomic import PathLike, atomic_output
from sashimi.core._ffmpeg import ProgressCallback
from sashimi.core._imagemeta import Carriers, add_tiff_groups, apply_mode, read_carriers, save_arguments, tiff_groups
from sashimi.core.image import _flatten, _has_alpha, _open_image, encode_for
from sashimi.errors import InvalidOptionsError, MediaTooLargeError, ProcessingError, UnsupportedMediaError

__all__ = [
    "FramesInfo",
    "FrameStrip",
    "adjust_colors",
    "aspect_crop",
    "crop_box",
    "edit",
    "frame_strip",
    "frames_info",
    "plan_frame_strip",
    "select_frames",
]

WHITE = (255, 255, 255)
ANIMATED_FORMATS = {"GIF": "gif", "WEBP": "webp"}
"""Pillow formats whose animations are edited frame by frame, and the target that keeps them."""
EDIT_TARGETS = {
    "JPEG": "jpeg",
    "MPO": "jpeg",
    "HEIF": "jpeg",
    "PNG": "png",
    "WEBP": "webp",
    "GIF": "gif",
    "BMP": "bmp",
    "TIFF": "tiff",
}
"""The edit target that keeps each input format (HEIC/HEIF has no writer, so it becomes JPEG)."""
GIF_MAX_FRAME_MS = 655350
"""The longest frame a GIF can store, in milliseconds (65535 hundredths of a second)."""
WEBP_MAX_FRAME_MS = 0xFFFFFF
"""The longest frame an animated WebP can store, in milliseconds."""

ANIMATION_TOO_LARGE = "This animation is too large to edit. Try one with fewer frames or smaller pictures."
NOT_ANIMATED = "This image isn't animated."
ANIMATED_PNG = "Animated PNGs can't be edited yet."

_ROTATE = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}
_FLIP = {"horizontal": Image.Transpose.FLIP_LEFT_RIGHT, "vertical": Image.Transpose.FLIP_TOP_BOTTOM}
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
_EXIF_ORIENTATION = 0x0112


class FramesInfo(BaseModel):
    """What an image holds, read from its headers without decoding any pixels."""

    model_config = ConfigDict(frozen=True)

    format: str
    """The image's format, by content: ``jpeg``, ``png``, ``webp``, ``gif``, ``bmp``, ``tiff``, ``heif`` or ``mpo``."""
    edit_target: str
    """The ``IMAGE_EDIT`` target that keeps this format (HEIC/HEIF and MPO become ``jpeg``)."""
    width: int
    """Width as shown, in pixels: the EXIF orientation is applied, as edits' crop boxes expect."""
    height: int
    """Height as shown, in pixels."""
    frames: int
    """Number of frames: 1 for a still image."""
    durations_ms: list[int]
    """Each frame's duration in milliseconds, as stored (browsers play under 20 ms as 100 ms); ``[0]`` for a still image."""
    loop: int | None
    """How often an animation repeats: 0 is forever; ``None`` plays it once (or a still image)."""
    animated: bool
    """True for a GIF or WebP with more than one frame: what ``select_frames`` and animated edits take."""


class FrameStrip(BaseModel):
    """
    The layout of a frame strip: frame ``i`` is the tile at ``x = (i % columns) * tile_width``,
    ``y = (i // columns) * tile_height``.
    """

    model_config = ConfigDict(frozen=True)

    tile_width: int
    tile_height: int
    columns: int
    rows: int
    frames: int


def _u24(data: bytes, offset: int) -> int:
    return data[offset] | data[offset + 1] << 8 | data[offset + 2] << 16


def _gif_frames(data: bytes) -> tuple[list[int], int | None]:
    """Each frame's duration (ms) and the loop count of a GIF, from its blocks. Pure."""
    if len(data) < 13 or data[:3] != b"GIF":
        return [0], None
    pos = 13
    if data[10] & 0x80:
        pos += 3 << ((data[10] & 7) + 1)
    durations: list[int] = []
    loop: int | None = None
    delay = 0

    def skip_blocks(at: int) -> tuple[int, list[bytes]]:
        blocks = []
        while at < len(data) and data[at]:
            blocks.append(data[at + 1 : at + 1 + data[at]])
            at += 1 + data[at]
        return at + 1, blocks

    while pos < len(data):
        kind = data[pos]
        if kind == 0x21 and pos + 1 < len(data):
            label = data[pos + 1]
            pos, blocks = skip_blocks(pos + 2)
            if label == 0xF9 and blocks and len(blocks[0]) >= 3:
                delay = (blocks[0][1] | blocks[0][2] << 8) * 10
            elif label == 0xFF and len(blocks) >= 2 and blocks[0][:8] in (b"NETSCAPE", b"ANIMEXTS"):
                if len(blocks[1]) >= 3 and blocks[1][0] == 1:
                    loop = blocks[1][1] | blocks[1][2] << 8
        elif kind == 0x2C and pos + 10 <= len(data):
            packed = data[pos + 9]
            pos += 10
            if packed & 0x80:
                pos += 3 << ((packed & 7) + 1)
            pos, _ = skip_blocks(pos + 1)
            durations.append(delay)
            delay = 0
        else:
            break
    return durations or [0], loop


def _webp_frames(data: bytes) -> tuple[list[int], int | None]:
    """Each frame's duration (ms) and the loop count of an animated WebP, from its chunks. Pure."""
    durations: list[int] = []
    loop: int | None = None
    pos = 12
    while pos + 8 <= len(data):
        fourcc, size = data[pos : pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        payload = pos + 8
        if fourcc == b"ANIM" and size >= 6:
            loop = struct.unpack_from("<H", data, payload + 4)[0]
        elif fourcc == b"ANMF" and size >= 16:
            durations.append(_u24(data, payload + 12))
        pos = payload + size + (size & 1)
    return durations or [0], loop


def _orientation(img: Image.Image) -> Image.Transpose | None:
    try:
        return _ORIENTATION.get(int(img.getexif().get(_EXIF_ORIENTATION, 1)))
    except (TypeError, ValueError):
        return None


def _png_header(path: PathLike) -> tuple[list[int], int | None, bytes | None]:
    """
    An (A)PNG's frame durations (ms) and loop count, from its ``acTL`` and ``fcTL`` chunks, and
    its ``eXIf`` chunk, read by skipping over the image data (so it decodes nothing).
    """
    durations: list[int] = []
    plays: int | None = None
    exif: bytes | None = None
    default_in_animation = False
    seen_data = False
    with open(os.fspath(path), "rb") as f:
        f.seek(8)
        while header := f.read(8):
            if len(header) < 8:
                break
            length, kind = struct.unpack(">I4s", header)
            if kind in (b"acTL", b"fcTL", b"eXIf"):
                payload = f.read(length)
                f.seek(4, os.SEEK_CUR)
                if kind == b"acTL" and len(payload) >= 8:
                    plays = struct.unpack_from(">I", payload, 4)[0]
                elif kind == b"fcTL" and len(payload) >= 26:
                    numerator, denominator = struct.unpack_from(">HH", payload, 20)
                    durations.append(round(numerator * 1000 / (denominator or 100)))
                    if not seen_data:
                        default_in_animation = True
                elif kind == b"eXIf" and exif is None:
                    exif = payload
            else:
                if kind == b"IDAT":
                    seen_data = True
                if kind == b"IEND":
                    break
                f.seek(length + 4, os.SEEK_CUR)
    if plays is None or not durations:
        return [0], None, exif
    if not default_in_animation:
        durations = [0, *durations]
    return durations, (None if plays == 1 else max(0, plays - 1) if plays else 0), exif


def _png_orientation(img: Image.Image, exif: bytes | None) -> Image.Transpose | None:
    """A PNG's orientation without loading it (Pillow's ``getexif`` decodes a PNG to find a late ``eXIf``)."""
    img.info.setdefault("exif", exif or b"")
    return _orientation(img)


def _frame_count(img: Image.Image) -> int:
    return int(getattr(img, "n_frames", 1))


def _lowest(channel: Image.Image) -> int:
    low, _ = channel.getextrema()
    return int(low) if isinstance(low, (int, float)) else 0


def _is_animated(img: Image.Image) -> bool:
    return img.format in ANIMATED_FORMATS and _frame_count(img) > 1


def frames_info(input_path: PathLike, *, limits: Limits | None = None) -> FramesInfo:
    """
    The format, size as shown, frame count, frame durations and loop count of an image, read from
    its headers: no pixels are decoded, so it is quick even for long animations. Raises
    UnsupportedMediaError for files that aren't images sashimi reads, and MediaTooLargeError for
    images with more than ``limits.max_pixels`` pixels (pass ``Limits.unlimited()`` to read any).
    """
    with _open_image(input_path, limits, load=False) as img:
        fmt = str(img.format)
        width, height = img.size
        loop: int | None = None
        if fmt == "PNG":
            durations, loop, exif = _png_header(input_path)
            frames = len(durations)
            orientation = _png_orientation(img, exif)
        else:
            orientation = _orientation(img)
            frames = _frame_count(img) if fmt in ("MPO", "TIFF") else 1
            durations = [0] * frames
        if orientation in _SWAPS_SIDES:
            width, height = height, width
        if fmt in ANIMATED_FORMATS:
            with open(os.fspath(input_path), "rb") as f:
                data = f.read()
            durations, loop = _gif_frames(data) if fmt == "GIF" else _webp_frames(data)
            frames = len(durations)
            if frames == 1:
                durations, loop = [0], None
    return FramesInfo(
        format=fmt.lower(),
        edit_target=EDIT_TARGETS[fmt],
        width=width,
        height=height,
        frames=frames,
        durations_ms=durations,
        loop=loop,
        animated=fmt in ANIMATED_FORMATS and frames > 1,
    )


def aspect_crop(width: int, height: int, aspect: str | tuple[int, int]) -> tuple[int, int, int, int]:
    """
    The largest centred crop box ``(left, top, width, height)`` of shape `aspect` ("16:9",
    "1:1", ``(4, 3)``; "free" is the whole picture) inside a `width` x `height` picture. Pure.
    Raises InvalidOptionsError for a shape that isn't ``W:H`` with positive whole numbers.
    """
    if aspect == "free":
        return 0, 0, width, height
    try:
        across, down = (int(part) for part in aspect.split(":")) if isinstance(aspect, str) else aspect
    except ValueError:
        raise InvalidOptionsError(f"{aspect!r} isn't a shape. Use width:height, e.g. 16:9.") from None
    if across < 1 or down < 1:
        raise InvalidOptionsError(f"{aspect!r} isn't a shape. Use width:height, e.g. 16:9.")
    if width * down > height * across:
        box_h, box_w = height, max(1, (height * across * 2 + down) // (2 * down))
    else:
        box_w, box_h = width, max(1, (width * down * 2 + across) // (2 * across))
    return (width - box_w) // 2, (height - box_h) // 2, box_w, box_h


def crop_box(width: int, height: int, config: ImageEditConfig) -> tuple[int, int, int, int]:
    """
    The crop as Pillow's ``(left, top, right, bottom)`` for a picture of `width` x `height` (after
    rotating and flipping). Pure. Raises InvalidOptionsError if the box doesn't fit inside it.
    """
    left, top = config.crop_left, config.crop_top
    right = left + (config.crop_width if config.crop_width is not None else max(0, width - left))
    bottom = top + (config.crop_height if config.crop_height is not None else max(0, height - top))
    if left >= width or top >= height or right > width or bottom > height or right <= left or bottom <= top:
        raise InvalidOptionsError(
            f"The crop area must fit inside the picture, which is {width}x{height} pixels.",
            detail=f"crop ({left}, {top}, {right}, {bottom}) of {width}x{height}",
        )
    return left, top, right, bottom


def _geometry(img: Image.Image, config: ImageEditConfig) -> Image.Image:
    if config.rotate in _ROTATE:
        img = img.transpose(_ROTATE[config.rotate])
    if config.flip in _FLIP:
        img = img.transpose(_FLIP[config.flip])
    if config.crops:
        box = crop_box(img.width, img.height, config)
        if box != (0, 0, img.width, img.height):
            img = img.crop(box)
    return img


def _channel_table(brightness: float, contrast: float) -> list[int]:
    """CSS ``brightness()`` then ``contrast()`` as one 8-bit lookup table, clamped after each step."""
    table = []
    for value in range(256):
        x = value / 255
        x = min(1.0, max(0.0, x * brightness))
        x = min(1.0, max(0.0, contrast * x - 0.5 * contrast + 0.5))
        table.append(int(x * 255 + 0.5))
    return table


def _saturation_matrix(s: float) -> tuple[float, ...]:
    """The Filter Effects ``feColorMatrix type="saturate"`` matrix, as Pillow's 12-value RGB matrix."""
    return (
        0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s, 0.0,
        0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s, 0.0,
        0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s, 0.0,
    )  # fmt: skip


def adjust_colors(
    img: Image.Image, brightness: float = 1.0, contrast: float = 1.0, saturation: float = 1.0
) -> Image.Image:
    """
    Applies CSS ``filter: brightness(b) contrast(c) saturate(s)``, in that order, as the Filter
    Effects spec defines them: in sRGB, on colour values that aren't premultiplied by alpha, with
    each result clamped. Alpha is unchanged. Brightness and contrast are exact to the nearest
    8-bit level. Saturation is computed in single precision, so alone it is within one level of
    the exact result; combined with brightness or contrast, whose result is rounded to 8 bits
    first, within two levels.
    Returns the image unchanged when nothing changes; otherwise an L, LA, RGB or RGBA image.
    """
    if (brightness, contrast, saturation) == (1.0, 1.0, 1.0):
        return img
    if img.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        img = img.convert("I").point(lambda v: v * (1 / 257)).convert("L") if img.mode != "F" else img.convert("L")
    elif img.mode not in ("L", "LA", "RGB", "RGBA"):
        if img.mode == "1":
            img = img.convert("L")
        else:
            img = img.convert("RGBA" if _has_alpha(img) else "RGB")
    alpha = img.getchannel("A") if img.mode in ("LA", "RGBA") else None
    colour = img.convert("L" if img.mode in ("L", "LA") else "RGB") if alpha is not None else img
    if (brightness, contrast) != (1.0, 1.0):
        table = _channel_table(brightness, contrast)
        colour = colour.point(table * len(colour.getbands()))
    if saturation != 1.0 and colour.mode == "RGB":
        colour = colour.convert("RGB", _saturation_matrix(saturation))
    if alpha is None:
        return colour
    colour.putalpha(alpha)
    return colour


def _check_budget(img: Image.Image, frames: int, limits: Limits | None) -> None:
    budget = resolve_limits(limits).max_animation_bytes
    needed = img.width * img.height * 4 * frames
    if budget is not None and needed > budget:
        raise MediaTooLargeError(
            f"This animation is too large to edit: its frames would take {megabytes(needed)} of memory, and the "
            f"limit is {megabytes(budget)}. Try one with fewer frames or smaller pictures.",
            detail=f"{img.width}x{img.height} x {frames} frames",
            limit="max_animation_bytes",
        )


def _frames(img: Image.Image, keep: frozenset[int] | None = None) -> Iterator[tuple[int, Image.Image, int]]:
    """Every frame (or those in `keep`) as a full RGBA canvas, with its index and duration in ms."""
    for index in range(_frame_count(img)):
        img.seek(index)
        if keep is not None and index not in keep:
            img.load()
            continue
        frame = img.convert("RGBA")
        duration = int(img.info.get("duration") or 0)
        frame.info = {}
        yield index, frame, duration


def _palette_frame(frame: Image.Image) -> Image.Image:
    """
    An RGBA frame as a GIF palette image. A frame with at most 256 colours (counting transparency
    as one) keeps every colour exactly; others are reduced to 255 colours plus transparency.
    Pixels at least half transparent become transparent. Only frames with transparent pixels get
    a ``transparency`` entry.
    """
    alpha = frame.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
    hidden = ImageOps.invert(alpha)
    transparent = _lowest(alpha) == 0
    rgb = frame.convert("RGB")
    if not transparent:
        if rgb.getcolors(256) is not None:
            return rgb.quantize(colors=256, method=Image.Quantize.MAXCOVERAGE, dither=Image.Dither.NONE)
        return rgb.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    rgb.paste((0, 0, 0), mask=hidden)
    marked = rgb.copy()
    marked.putalpha(alpha)
    colours = marked.getcolors(256)
    if colours is not None:
        opaque = {tuple(colour)[:3] for _, colour in colours if isinstance(colour, tuple) and colour[3] == 255}
        key = next(c for c in ((i >> 16, i >> 8 & 255, i & 255) for i in range(1 << 24)) if c not in opaque)
        rgb.paste(key, mask=hidden)
        indexed = rgb.quantize(colors=256, method=Image.Quantize.MAXCOVERAGE, dither=Image.Dither.NONE)
        palette = indexed.getpalette() or []
        indexed.info["transparency"] = [tuple(palette[i : i + 3]) for i in range(0, len(palette), 3)].index(key)
        return indexed
    indexed = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    palette = indexed.getpalette() or []
    index = len(palette) // 3
    indexed.putpalette(palette + [0, 0, 0])
    indexed.paste(index, mask=hidden)
    indexed.info["transparency"] = index
    return indexed


def _gif_duration(ms: float) -> int:
    """GIFs store hundredths of a second, so durations are rounded to 10 ms, up to the longest a GIF holds."""
    return min(int(ms / 10 + 0.5) * 10, GIF_MAX_FRAME_MS)


def _for_target(frame: Image.Image, config: object) -> Image.Image:
    """A finished RGBA frame as the target stores it: a palette image for GIF, RGBA otherwise."""
    return _palette_frame(frame) if isinstance(config, _GifSave) else frame


def _save_animation(
    frames: Sequence[Image.Image],
    durations: Sequence[float],
    loop: int | None,
    config: _GifSave | _WebpSave,
    output_path: PathLike,
    scratch_dir: PathLike | None,
    metadata: dict[str, Any],
) -> None:
    first, rest = frames[0], list(frames[1:])
    kwargs: dict[str, Any] = {"save_all": True, "append_images": rest}
    if isinstance(config, _GifSave):
        kwargs["duration"] = [_gif_duration(d) for d in durations]
        kwargs["optimize"] = config.optimize
        if any("transparency" in frame.info for frame in frames):
            kwargs["disposal"] = 2
        if loop is not None:
            kwargs["loop"] = loop
        kwargs.update(metadata)
        fmt = "GIF"
    else:
        kwargs["duration"] = [min(int(d + 0.5), WEBP_MAX_FRAME_MS) for d in durations]
        kwargs.update(quality=config.quality, lossless=config.lossless, method=config.webp_method)
        kwargs["loop"] = 1 if loop is None else loop
        kwargs.update(metadata)
        fmt = "WEBP"
    try:
        with atomic_output(output_path, scratch_dir) as temp:
            first.save(temp, format=fmt, **kwargs)
    except OSError as e:
        raise ProcessingError(f"Cannot write {os.fspath(output_path)}: {e}") from e


def _metadata(
    img: Image.Image, config: object, carriers: Carriers, size: tuple[int, int], pil_format: str
) -> dict[str, Any]:
    """
    The colour profile and the source's metadata to keep, as save() keyword arguments. Edits
    always apply the orientation, so it is reset to 1, and drop embedded thumbnails, which show
    the unedited picture.
    """
    kept: dict[str, Any] = {}
    if isinstance(config, _Metadata) and config.keep_icc and img.mode != "CMYK" and img.info.get("icc_profile"):
        kept["icc_profile"] = img.info["icc_profile"]
    if isinstance(config, SourceMetadata):
        carried = apply_mode(carriers, config.metadata, upright=True, edited=True, size=size)
        kept.update(save_arguments(carried, pil_format))
        if pil_format == "TIFF":
            kept["_groups"] = tiff_groups(carried)
    return kept


def _edit_still(
    img: Image.Image,
    config: ImageEditConfig,
    carriers: Carriers,
    output_path: PathLike,
    scratch_dir: PathLike | None,
) -> None:
    source = img
    img = ImageOps.exif_transpose(img)
    img = _geometry(img, config)
    img = adjust_colors(img, config.brightness, config.contrast, config.saturation)
    if isinstance(config, (_JpegSave, _BmpSave)) and _has_alpha(img):
        img = _flatten(img, WHITE)
    if isinstance(config, GifEditConfig) and img.mode not in ("P", "L"):
        img = _palette_frame(img.convert("RGBA"))
    img, kwargs = encode_for(img, config, WHITE)
    img.info = {key: value for key, value in img.info.items() if key == "transparency"}
    kwargs.update(_metadata(source, config, carriers, img.size, _PIL_FORMAT[config.target]))
    groups = kwargs.pop("_groups", None)
    try:
        with atomic_output(output_path, scratch_dir) as temp:
            img.save(temp, format=_PIL_FORMAT[config.target], **kwargs)
            if groups:
                add_tiff_groups(temp, groups)
    except OSError as e:
        raise ProcessingError(f"Cannot write {os.fspath(output_path)}: {e}") from e


_PIL_FORMAT = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "gif": "GIF", "bmp": "BMP", "tiff": "TIFF"}


def edit(
    input_path: PathLike,
    output_path: PathLike,
    config: ImageEditConfig,
    *,
    scratch_dir: PathLike | None = None,
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    Edits an image as `config` says (see :data:`~sashimi.config.IMAGE_EDIT`), in a fixed order:
    the EXIF orientation first (so the picture is the way it is shown), then ``rotate`` and
    ``flip``, then the crop box (in pixels of the rotated and flipped picture), then brightness,
    contrast and saturation (CSS ``filter: brightness() contrast() saturate()``; see
    :func:`adjust_colors`), then it saves as ``config.target``.

    The output format comes from the config: pass the source's own format to keep it
    (:func:`frames_info` gives it as ``edit_target``). Transparency is kept by formats that hold
    it. An animated GIF or WebP saved as GIF or WebP has every frame edited, keeping each
    frame's duration and the loop count; one whose frames need more than
    ``limits.max_animation_bytes`` raises MediaTooLargeError. Saved as anything else, an animation
    gives its first frame; animated PNGs raise UnsupportedMediaError. A crop box outside the
    picture raises InvalidOptionsError. Images over ``limits.max_pixels`` raise MediaTooLargeError.
    With nothing to change, a valid copy is still written. Written atomically.
    """
    with _open_image(input_path, limits) as img:
        if img.format == "PNG" and _frame_count(img) > 1:
            raise UnsupportedMediaError(ANIMATED_PNG)
        carriers = read_carriers(input_path, img) if isinstance(config, SourceMetadata) else Carriers()
        if _is_animated(img) and isinstance(config, (_GifSave, _WebpSave)):
            _check_budget(img, _frame_count(img), limits)
            transpose = _orientation(img)
            loop = img.info.get("loop")
            frames: list[Image.Image] = []
            durations: list[float] = []
            total = _frame_count(img)
            for index, frame, duration in _frames(img):
                if transpose is not None:
                    frame = frame.transpose(transpose)
                frame = _geometry(frame, config)
                frame = adjust_colors(frame, config.brightness, config.contrast, config.saturation)
                frames.append(_for_target(frame, config))
                durations.append(duration)
                if on_progress:
                    on_progress((index + 1) / (total + 1), "editing")
            metadata = _metadata(img, config, carriers, frames[0].size, _PIL_FORMAT[config.target])
            _save_animation(frames, durations, loop, config, output_path, scratch_dir, metadata)
        else:
            _edit_still(img, config, carriers, output_path, scratch_dir)
    if on_progress:
        on_progress(1.0, "done")


def _speed(duration: int, speed: float) -> float:
    if speed == 1.0:
        return duration
    shown = duration if duration >= MIN_FRAME_MS else 100
    return max(float(MIN_FRAME_MS), shown / speed)


def select_frames(
    input_path: PathLike,
    output_path: PathLike,
    config: FramesConfig,
    *,
    scratch_dir: PathLike | None = None,
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    Keeps some frames of an animated GIF or WebP, and changes its speed, as `config` says (see
    :data:`~sashimi.config.IMAGE_FRAMES`). Kept frames play in their original order, each for its
    own duration divided by ``speed``; when the speed changes, frames browsers would play at
    100 ms (under 20 ms) count as 100 ms, no frame gets shorter than
    :data:`~sashimi.config.MIN_FRAME_MS`, and none longer than the format can store. GIFs store
    durations in 10 ms steps, so in a GIF each frame's new duration is rounded to the nearest
    step (a 70 ms frame at speed 2 plays for 40 ms, not 35), and the total length can differ
    slightly from the source's divided by ``speed``; a preview of the length should round the
    same way. The loop count is kept. Every frame is as shown in the source (the full canvas), so dropping frames
    never leaves holes.

    The output format comes from the config: pass the source's own format to keep it. With every
    frame kept and a speed of 1 the animation is unchanged. Raises UnsupportedMediaError for an
    image that isn't animated, InvalidOptionsError for frames the animation doesn't have, and
    MediaTooLargeError over ``limits.max_animation_bytes`` or ``limits.max_pixels``. Written
    atomically.
    """
    with _open_image(input_path, limits) as img:
        if not _is_animated(img):
            raise UnsupportedMediaError(NOT_ANIMATED, detail=f"Pillow format {img.format}")
        total = _frame_count(img)
        _check_budget(img, total, limits)
        keep = config.keep if config.keep is not None else tuple(range(total))
        if keep[-1] >= total:
            raise InvalidOptionsError(
                f"Some of the frames to keep aren't in this animation, which has {total} frames.",
                detail=f"keep {keep}",
            )
        carriers = read_carriers(input_path, img) if isinstance(config, SourceMetadata) else Carriers()
        transpose = _orientation(img)
        loop = img.info.get("loop")
        frames: list[Image.Image] = []
        durations: list[float] = []
        for index, frame, duration in _frames(img, frozenset(keep)):
            frames.append(_for_target(frame.transpose(transpose) if transpose is not None else frame, config))
            durations.append(_speed(duration, config.speed))
            if on_progress:
                on_progress((index + 1) / (total + 1), "selecting")
        metadata = _metadata(img, config, carriers, frames[0].size, _PIL_FORMAT[config.target])
        _save_animation(frames, durations, loop, config, output_path, scratch_dir, metadata)
    if on_progress:
        on_progress(1.0, "done")


def plan_frame_strip(width: int, height: int, frames: int, config: FrameStripConfig | None = None) -> FrameStrip:
    """
    The layout :func:`frame_strip` uses for `frames` frames of `width` x `height`. Pure. Tiles
    are ``tile_height`` tall (never taller than the frames) with widths that keep the shape; more
    columns are used if the strip would be taller than WebP allows
    (:data:`~sashimi.config.WEBP_MAX_SIDE`). Raises MediaTooLargeError if it can't fit either way.
    """
    config = config or FrameStripConfig()
    tile_height = max(1, min(config.tile_height, height, WEBP_MAX_SIDE))
    tile_width = max(1, round(width * tile_height / height))
    columns = max(1, min(config.columns, frames))
    if math.ceil(frames / columns) * tile_height > WEBP_MAX_SIDE:
        columns = math.ceil(frames / (WEBP_MAX_SIDE // tile_height))
    if columns * tile_width > WEBP_MAX_SIDE:
        raise MediaTooLargeError(ANIMATION_TOO_LARGE, detail=f"{frames} tiles of {tile_width}x{tile_height}")
    return FrameStrip(
        tile_width=tile_width,
        tile_height=tile_height,
        columns=columns,
        rows=math.ceil(frames / columns),
        frames=frames,
    )


def frame_strip(
    input_path: PathLike,
    output_path: PathLike,
    config: FrameStripConfig | None = None,
    *,
    scratch_dir: PathLike | None = None,
    limits: Limits | None = None,
    on_progress: ProgressCallback | None = None,
) -> FrameStrip:
    """
    Draws every frame of an image (one for a still image) as a small tile in one still WebP, left
    to right and top to bottom, so a UI can show the frames to pick from, and returns the layout
    (see :func:`plan_frame_strip`). Tiles are as shown (EXIF orientation applied); transparent
    areas stay transparent. Refused over ``limits.max_animation_bytes`` and ``limits.max_pixels``,
    like edits. Written atomically.
    """
    config = config or FrameStripConfig()
    with _open_image(input_path, limits) as img:
        animated = _is_animated(img)
        total = _frame_count(img) if animated else 1
        _check_budget(img, total, limits)
        transpose = _orientation(img)
        width, height = (img.height, img.width) if transpose in _SWAPS_SIDES else img.size
        layout = plan_frame_strip(width, height, total, config)
        sheet = Image.new("RGBA", (layout.columns * layout.tile_width, layout.rows * layout.tile_height), (0, 0, 0, 0))
        source = _frames(img) if animated else iter([(0, img.convert("RGBA"), 0)])
        for index, frame, _ in source:
            if transpose is not None:
                frame = frame.transpose(transpose)
            tile = frame.resize((layout.tile_width, layout.tile_height), Image.Resampling.LANCZOS)
            sheet.paste(
                tile, ((index % layout.columns) * layout.tile_width, (index // layout.columns) * layout.tile_height)
            )
            if on_progress:
                on_progress((index + 1) / (total + 1), "drawing")
    try:
        with atomic_output(output_path, scratch_dir) as temp:
            sheet.save(temp, format="WEBP", quality=config.quality, method=4)
    except OSError as e:
        raise ProcessingError(f"Cannot write {os.fspath(output_path)}: {e}") from e
    if on_progress:
        on_progress(1.0, "done")
    return layout
