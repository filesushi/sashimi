# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Video conversion (GIF, animated WebP, WebM, MP4), previews sampled from across a video, lossless
or frame-accurate trimming (including whole-file remuxing), and rotate/flip. Requires ffmpeg on PATH.
"""

from sashimi.core.preview import build_preview_commands, plan_preview, preview
from sashimi.core.video import (
    VideoRoute,
    build_convert_command,
    build_transform_command,
    build_trim_command,
    check_copy_compatible,
    convert,
    plan_mp4_route,
    transform,
    trim,
)

__all__ = [
    "VideoRoute",
    "build_convert_command",
    "build_preview_commands",
    "build_transform_command",
    "build_trim_command",
    "check_copy_compatible",
    "convert",
    "plan_mp4_route",
    "plan_preview",
    "preview",
    "transform",
    "trim",
]
