# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Image conversion, resizing, favicons (ICO), favicon packs (.zip), editing (rotate, flip, crop,
brightness, contrast, saturation), and frame selection for animated GIF and WebP. Pillow only;
no ffmpeg needed.
"""

from sashimi.core.edit import (
    FrameStrip,
    FramesInfo,
    adjust_colors,
    aspect_crop,
    crop_box,
    edit,
    frame_strip,
    frames_info,
    plan_frame_strip,
    select_frames,
)
from sashimi.core.image import convert, favicon_html, favicon_manifest, favicon_pack, heif_available, to_ico

__all__ = [
    "FrameStrip",
    "FramesInfo",
    "adjust_colors",
    "aspect_crop",
    "convert",
    "crop_box",
    "edit",
    "favicon_html",
    "favicon_manifest",
    "favicon_pack",
    "frame_strip",
    "frames_info",
    "heif_available",
    "plan_frame_strip",
    "select_frames",
    "to_ico",
]
