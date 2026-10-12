"""Video conversion (GIF, animated WebP, WebM, MP4), lossless or frame-accurate trimming, and rotate/flip. Requires ffmpeg on PATH."""

from sashimi.core.video import (
    build_convert_command,
    build_transform_command,
    build_trim_command,
    check_copy_compatible,
    convert,
    transform,
    trim,
)

__all__ = [
    "build_convert_command",
    "build_transform_command",
    "build_trim_command",
    "check_copy_compatible",
    "convert",
    "transform",
    "trim",
]
