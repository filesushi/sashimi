# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

MAX_GIF_SECONDS = 30
"""Longest GIF or animated WebP made from a video, in seconds."""
DEFAULT_GIF_SECONDS = 10
"""Length of a GIF or animated WebP when no ``duration_seconds`` is given."""
MAX_VIDEO_SECONDS = 600
"""Longest video that is re-encoded (WebM/MP4 conversion, rotate/flip, accurate trims), in seconds."""
MAX_AUDIO_SECONDS = 3600
"""Longest audio that is converted, in seconds."""
DURATION_TOLERANCE_SECONDS = 1
"""
How far past the probed length an encode may read when no length is given. Container durations
can be slightly short (MPEG-TS often understates its audio by a few hundred milliseconds), and
cutting exactly at the probed length would clip the end.
"""

DEFAULT_MAX_PIXELS = 64_000_000
"""Images with more pixels than this are refused (64 MP covers 48 MP phone photos)."""

MAX_SHEET_HEIGHT = 16383
"""Tallest contact sheet rendered, in pixels: WebP's limit, and well inside JPEG's."""
MAX_SHEET_PIXELS = 64_000_000
"""Largest contact sheet rendered, in pixels; settings that need more are refused."""
MAX_FRAME_BYTES = 400 * 1024 * 1024
"""Decoded frames an animated sheet keeps in memory at once; settings that need more are refused."""
MIN_TILE_SIZE = 16
"""Smallest contact sheet tile, in pixels on either side; layouts that need smaller tiles are refused."""

MAX_PREVIEW_SAMPLES = 10
"""Most clips a preview stitches together."""

MP3_SAMPLE_RATES = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)
"""The sample rates the MP3 encoder (LAME) accepts, in Hz."""
AAC_SAMPLE_RATES = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 64000, 88200, 96000)
"""The sample rates the AAC encoder (M4A output) accepts, in Hz."""
OPUS_SAMPLE_RATES = (8000, 12000, 16000, 24000, 48000)
"""The sample rates the Opus encoder accepts, in Hz."""

KEYFRAME_NOTE = (
    "Trimming without re-encoding is instant and keeps the original quality, but cuts can only "
    "land on keyframes, so your clip may start a moment before the time you chose."
)
"""User-facing sentence explaining keyframe-aligned trims."""

GIF_TOO_LONG = f"GIFs can be at most {MAX_GIF_SECONDS} seconds long."
WEBP_TOO_LONG = f"Animated WebPs can be at most {MAX_GIF_SECONDS} seconds long."
VIDEO_CLIP_TOO_LONG = f"Video clips can be at most {MAX_VIDEO_SECONDS // 60} minutes long."
AUDIO_CLIP_TOO_LONG = f"Audio clips can be at most {MAX_AUDIO_SECONDS // 60} minutes long."
PREVIEW_TOO_LONG = f"Previews can be at most {MAX_GIF_SECONDS} seconds long. Use fewer clips or shorter ones."
TILES_TOO_SMALL = "These settings would make the contact sheet's tiles too small. Use fewer columns or a wider sheet."
