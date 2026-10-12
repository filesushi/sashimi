"""Image conversion, resizing and favicon (ICO) generation. Pillow only; no ffmpeg needed."""

from sashimi.core.image import convert, heif_available, to_ico

__all__ = ["convert", "heif_available", "to_ico"]
