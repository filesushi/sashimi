# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Video conversion (GIF, animated WebP, WebM, MP4), previews sampled from across a video, lossless
or frame-accurate trimming (including whole-file remuxing), and rotate/flip (lossless rotation where it can be). Requires ffmpeg on PATH.
"""

from sashimi.core.preview import build_preview_commands, build_preview_palette_command, plan_preview, preview
from sashimi.core.video import (
    CopyPlan,
    RotationRoute,
    TransformResult,
    TrimResult,
    VideoConversion,
    VideoRoute,
    build_convert_command,
    build_palette_command,
    build_rotation_command,
    build_transform_command,
    build_trim_command,
    check_copy_compatible,
    convert,
    plan_copy,
    plan_mp4_route,
    plan_rotation,
    rotation_container,
    transform,
    trim,
    two_pass,
)

__all__ = [
    "CopyPlan",
    "RotationRoute",
    "TransformResult",
    "TrimResult",
    "VideoConversion",
    "VideoRoute",
    "build_convert_command",
    "build_palette_command",
    "build_preview_commands",
    "build_preview_palette_command",
    "build_rotation_command",
    "build_transform_command",
    "build_trim_command",
    "check_copy_compatible",
    "convert",
    "plan_copy",
    "plan_mp4_route",
    "plan_preview",
    "plan_rotation",
    "preview",
    "rotation_container",
    "transform",
    "trim",
    "two_pass",
]
