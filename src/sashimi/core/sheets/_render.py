# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Header and timestamp rendering for contact sheets. Local fonts only; never touches the network."""

import datetime
import math
import functools
import io
import logging
import pathlib
from typing import Any

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("sashimi")

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont

_FONT_CANDIDATES = [
    "msgothic.ttc",
    "meiryo.ttc",
    "segoeui.ttf",
    "Arial Unicode.ttf",
    "AppleGothic.ttf",
    "PingFang.ttc",
    "NotoSansCJK-Regular.ttc",
    "DejaVuSans.ttf",
]


@functools.lru_cache(maxsize=16)
def load_font(font_path: str | None, size: int) -> Font:
    candidates = ([font_path] if font_path else []) + _FONT_CANDIDATES
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def format_duration(seconds: float | None, tenths: bool = False) -> str:
    """``01:05`` (or ``1:01:05``); with `tenths`, ``00:00.4``, for videos shorter than a second."""
    if seconds is None:
        return "N/A"
    if tenths and seconds < 60:
        return f"00:{math.floor(seconds * 10) / 10:04.1f}"
    total = int(datetime.timedelta(seconds=int(seconds)).total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def format_size(size: int | None) -> str:
    if size is None:
        return "N/A"
    if size < 1024:
        return f"{size} B"
    if size < 1024**2:
        return f"{size / 1024:.1f} KB"
    if size < 1024**3:
        return f"{size / 1024**2:.1f} MB"
    return f"{size / 1024**3:.1f} GB"


class HeaderStyle:
    """Precomputed header metrics for a font, matching the original sashimi layout."""

    def __init__(self, font: Font, font_size: int):
        line_height = font_size * 1.2
        try:
            bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), "Aj", font=font)
            line_height = max(line_height, bbox[3] - bbox[1])
        except (OSError, ValueError):
            pass
        self.font = font
        self.height = max(int(font_size * 1.5), int(line_height * (0.5 + 4 * 1.2 + 0.5)))
        self.padding_top = int(line_height * 0.5)
        self.line_spacing = int(line_height * 1.2)


def _local_emoji_source(directory: str) -> Any:
    """A pilmoji source that reads Twemoji-style PNGs (e.g. '1f3ac.png') from a local directory."""
    from pilmoji.source import BaseSource  # type: ignore[import-untyped,import-not-found,unused-ignore]

    root = pathlib.Path(directory)

    class LocalEmojiSource(BaseSource):  # type: ignore[misc]
        def get_emoji(self, emoji: str, /) -> io.BytesIO | None:
            code = "-".join(f"{ord(c):x}" for c in emoji if ord(c) != 0xFE0F)
            path = root / f"{code}.png"
            return io.BytesIO(path.read_bytes()) if path.is_file() else None

        def get_discord_emoji(self, id: int, /) -> io.BytesIO | None:
            return None

    return LocalEmojiSource()


def render_header(
    width: int,
    lines: list[str],
    style: HeaderStyle,
    bg_color: tuple[int, int, int],
    text_color: tuple[int, int, int],
    emoji_dir: str | None = None,
) -> Image.Image:
    """
    Draws the header block. Text is plain by default. With `emoji_dir` and the optional
    ``pilmoji`` package, emoji are drawn from local images in that directory (never downloaded).
    """
    image = Image.new("RGB", (width, style.height), bg_color)
    if emoji_dir:
        try:
            from pilmoji import Pilmoji  # type: ignore[import-untyped,import-not-found,unused-ignore]

            y = style.padding_top
            with Pilmoji(image, source=_local_emoji_source(emoji_dir), render_discord_emoji=False) as pilmoji:
                for line in lines:
                    pilmoji.text((10, y), line, fill=text_color, font=style.font)
                    y += style.line_spacing
            return image
        except ImportError:
            logger.warning("emoji_dir is set but pilmoji isn't installed; drawing the header as plain text.")
        except Exception as e:
            logger.warning(f"Emoji header rendering failed ({e}); drawing the header as plain text.")
            image = Image.new("RGB", (width, style.height), bg_color)

    draw = ImageDraw.Draw(image)
    y = style.padding_top
    for line in lines:
        draw.text((10, y), line, fill=text_color, font=style.font)
        y += style.line_spacing
    return image


@functools.lru_cache(maxsize=512)
def timestamp_label(
    text: str, font: Font, bg_color: tuple[int, int, int, int], text_color: tuple[int, int, int]
) -> Image.Image:
    """An RGBA label (translucent box + text) for a tile's bottom-right corner."""
    padding = 2
    try:
        bbox = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=font)
        text_w, text_h = int(bbox[2] - bbox[0]), int(bbox[3] - bbox[1])
    except (OSError, ValueError):
        text_w, text_h = 50, 14
    label = Image.new("RGBA", (text_w + 2 * padding + 1, text_h + 2 * padding + 1), bg_color)
    ImageDraw.Draw(label).text((padding, padding), text, fill=text_color, font=font)
    return label


LABEL_REFERENCE = "0123456789:Aj"
"""Characters whose combined height sets every padded label's height, so all labels match."""


@functools.lru_cache(maxsize=512)
def padded_label(
    text: str, font: Font, bg_color: tuple[int, int, int, int], text_color: tuple[int, int, int], padding: int = 4
) -> Image.Image:
    """
    An RGBA label for animated sheets: `padding` pixels around the text on every side, the same
    height for every label, and the text centred on it whatever the font's internal spacing.
    """
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    try:
        left, _, right, _ = draw.textbbox((0, 0), text, font=font)
        _, top, _, bottom = draw.textbbox((0, 0), LABEL_REFERENCE, font=font)
    except (OSError, ValueError):
        left, top, right, bottom = 0, 0, 50, 14
    label = Image.new("RGBA", (int(right - left) + 2 * padding, int(bottom - top) + 2 * padding), bg_color)
    ImageDraw.Draw(label).text((padding - left, padding - top), text, fill=text_color, font=font)
    return label


def stamp(tile: Image.Image, label: Image.Image, margin: int | None = None) -> None:
    """
    Composites a timestamp label onto a tile's bottom-right corner, in place, `margin` pixels in
    from both edges (still sheets: 1).
    """
    inset = 1 if margin is None else margin
    x = max(0, tile.width - label.width - inset)
    y = max(0, tile.height - label.height - inset)
    tile.paste(label, (x, y), label)
