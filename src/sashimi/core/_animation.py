# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Reading and writing animated images (GIF, WebP, APNG and AVIF) frame by frame, with Pillow."""

import os
from collections.abc import Iterator, Sequence
from typing import Any

from PIL import Image, ImageOps

from sashimi.config import MIN_FRAME_MS, Limits
from sashimi.config._image import _ApngSave, _AvifSave, _GifSave, _PngSave, _WebpSave
from sashimi.config._limits import check_webp_size, megabytes, resolve_limits
from sashimi.core._atomic import PathLike, atomic_output
from sashimi.errors import MediaTooLargeError, ProcessingError

AnimatedSave = _GifSave | _WebpSave | _AvifSave | _ApngSave | _PngSave

ANIMATED_FORMATS = frozenset({"GIF", "WEBP", "PNG", "AVIF"})
"""Pillow formats that can hold an animation sashimi reads frame by frame (PNG: APNG)."""
GIF_MAX_FRAME_MS = 655350
"""The longest frame a GIF can store, in milliseconds (65535 hundredths of a second)."""
WEBP_MAX_FRAME_MS = 0xFFFFFF
"""The longest frame an animated WebP can store, in milliseconds."""
BROWSER_FRAME_MS = 100
"""How long browsers show a frame stored as shorter than :data:`~sashimi.config.MIN_FRAME_MS`."""


def frame_count(img: Image.Image) -> int:
    """How many frames Pillow reads from `img` (at least 1)."""
    return max(1, int(getattr(img, "n_frames", 1) or 1))


def is_animation(img: Image.Image) -> bool:
    """True for an animated GIF, WebP, APNG or AVIF: more than one frame meant to play in turn."""
    return img.format in ANIMATED_FORMATS and frame_count(img) > 1


def shown_ms(duration: float) -> float:
    """
    How long a frame stored as `duration` milliseconds plays in browsers: frames shorter than
    :data:`~sashimi.config.MIN_FRAME_MS` (0 and 10 ms, a common way to ask for "as fast as
    possible") play for :data:`BROWSER_FRAME_MS`. Every sashimi route that reads an animation's
    timing uses this rule.
    """
    return duration if duration >= MIN_FRAME_MS else BROWSER_FRAME_MS


def plays(img: Image.Image) -> int | None:
    """
    How many times `img`'s animation plays, as browsers play it: None is forever. A GIF's loop
    count is the number of repeats after the first play (and a GIF without one plays once); a
    WebP's and an APNG's is the number of plays (0 is forever).
    """
    loop = img.info.get("loop")
    if img.format == "GIF":
        if loop is None:
            return 1
        return None if int(loop) == 0 else int(loop) + 1
    if loop is None or int(loop) == 0:
        return None
    return int(loop)


def gif_frames(data: bytes) -> tuple[list[int], int | None]:
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


def gif_has_short_frames(path: PathLike) -> bool:
    """True for a GIF with a frame stored as shorter than :data:`~sashimi.config.MIN_FRAME_MS`, which browsers play for 100 ms."""
    with open(os.fspath(path), "rb") as f:
        durations, _ = gif_frames(f.read())
    return len(durations) > 1 and any(d < MIN_FRAME_MS for d in durations)


def gif_shown_seconds(path: PathLike) -> float:
    """How long a GIF plays in browsers, in seconds (see :func:`shown_ms`)."""
    with open(os.fspath(path), "rb") as f:
        durations, _ = gif_frames(f.read())
    return sum(shown_ms(d) for d in durations) / 1000


def check_budget(width: int, height: int, frames: int, limits: Limits | None, action: str = "edit") -> None:
    """Refuses an animation whose frames, held at `width` x `height` x 4 bytes, are over ``max_animation_bytes``."""
    budget = resolve_limits(limits).max_animation_bytes
    needed = width * height * 4 * frames
    if budget is not None and needed > budget:
        raise MediaTooLargeError(
            f"This animation is too large to {action}: its frames would take {megabytes(needed)} of memory, and the "
            f"limit is {megabytes(budget)}. Try one with fewer frames or smaller pictures.",
            detail=f"{width}x{height} x {frames} frames",
            limit="max_animation_bytes",
        )


def frames_of(img: Image.Image, keep: frozenset[int] | None = None) -> Iterator[tuple[int, Image.Image, int]]:
    """Every frame (or those in `keep`) as a full RGBA canvas, with its index and stored duration in ms."""
    for index in range(frame_count(img)):
        img.seek(index)
        if keep is not None and index not in keep:
            img.load()
            continue
        frame = img.convert("RGBA")
        duration = int(img.info.get("duration") or 0)
        frame.info = {}
        yield index, frame, duration


def _lowest(channel: Image.Image) -> int:
    low, _ = channel.getextrema()
    return int(low) if isinstance(low, (int, float)) else 0


def palette_frame(frame: Image.Image) -> Image.Image:
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


def gif_duration(ms: float) -> int:
    """GIFs store hundredths of a second, so durations are rounded to 10 ms, up to the longest a GIF holds."""
    return min(int(ms / 10 + 0.5) * 10, GIF_MAX_FRAME_MS)


def for_target(frame: Image.Image, config: object) -> Image.Image:
    """A finished RGBA frame as the target stores it: a palette image for GIF, RGBA otherwise."""
    return palette_frame(frame) if isinstance(config, _GifSave) else frame


def save_animation(
    frames: Sequence[Image.Image],
    durations: Sequence[float],
    loop: int | None,
    config: AnimatedSave,
    output_path: PathLike,
    scratch_dir: PathLike | None,
    metadata: dict[str, Any],
) -> None:
    """
    Writes `frames` as an animated GIF, WebP, AVIF or APNG. `loop` is the value Pillow's writer
    takes: for a GIF, the repeats after the first play (None writes no loop count, so it plays
    once); for a WebP or APNG, the number of plays (None plays once). 0 is forever. Pillow's AVIF
    writer has no loop count: AVIF animations play forever.
    """
    first, rest = frames[0], list(frames[1:])
    kwargs: dict[str, Any] = {"save_all": True, "append_images": rest}
    if isinstance(config, _GifSave):
        kwargs["duration"] = [gif_duration(d) for d in durations]
        kwargs["optimize"] = config.optimize
        if any("transparency" in frame.info for frame in frames):
            kwargs["disposal"] = 2
        if loop is not None:
            kwargs["loop"] = loop
        kwargs.update(metadata)
        fmt = "GIF"
    elif isinstance(config, _WebpSave):
        check_webp_size(*first.size)
        kwargs["duration"] = [min(int(d + 0.5), WEBP_MAX_FRAME_MS) for d in durations]
        kwargs.update(quality=config.quality, lossless=config.lossless, method=config.webp_method)
        kwargs["loop"] = 1 if loop is None else loop
        kwargs.update(metadata)
        fmt = "WEBP"
    elif isinstance(config, _AvifSave):
        kwargs["duration"] = [max(1, int(d + 0.5)) for d in durations]
        kwargs.update(avif_arguments(config))
        kwargs.update(metadata)
        fmt = "AVIF"
    else:
        kwargs["duration"] = [min(int(d + 0.5), APNG_MAX_FRAME_MS) for d in durations]
        kwargs.update(compress_level=config.png_compress_level, optimize=config.optimize)
        kwargs["loop"] = 1 if loop is None else loop
        kwargs.update(metadata)
        fmt = "PNG"
    try:
        with atomic_output(output_path, scratch_dir) as temp:
            first.save(temp, format=fmt, **kwargs)
    except OSError as e:
        raise ProcessingError(f"Cannot write {os.fspath(output_path)}: {e}") from e


APNG_MAX_FRAME_MS = 65535
"""The longest frame an APNG holds with Pillow's writer (a 16-bit count of milliseconds)."""


def avif_arguments(config: _AvifSave) -> dict[str, Any]:
    """Pillow's AVIF save() arguments for `config`, with the encoder's threads bounded (see :func:`~sashimi.core._resources.resolve_max_workers`)."""
    from sashimi.core._resources import resolve_max_workers

    return {"quality": config.quality, "speed": config.avif_speed, "max_threads": resolve_max_workers(None)}


def writer_loop(plays_count: int | None, config: AnimatedSave) -> int | None:
    """The `loop` :func:`save_animation` takes for an animation that plays `plays_count` times (None: forever)."""
    if plays_count is None:
        return 0
    if isinstance(config, _GifSave):
        return None if plays_count <= 1 else plays_count - 1
    return plays_count
