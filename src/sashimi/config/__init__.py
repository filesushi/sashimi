# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Every config model, limit and user-facing constant, importable without Pillow or ffmpeg.

This module needs only pydantic, so a web app can validate a form against the exact models the
converters use, and read their defaults and bounds, without installing anything else.

There is one model per operation and output format, holding only the options that affect that
output, each with the default actually used. ``None`` is a default only where it means "keep the
source's value" (``max_width``, ``sample_rate``) or "to the end" (``duration_seconds``).

Build a config from a parameter dict with the operation's family, which picks the model:

>>> from sashimi.config import VIDEO_CONVERT
>>> config = VIDEO_CONVERT.from_params({"duration_seconds": 5}, target="gif")
>>> type(config).__name__, config.fps
('GifVideoConfig', 12)

Invalid or unknown options raise :class:`~sashimi.errors.InvalidOptionsError`, whose
``user_message`` is one readable sentence. Every model also has ``from_params`` itself.
"""

from sashimi.config._audio import (
    AUDIO_CONVERT,
    AudioConvertConfig,
    FlacAudioConfig,
    M4aAudioConfig,
    Mp3AudioConfig,
    OggAudioConfig,
    WavAudioConfig,
)
from sashimi.config._base import RGB, RGBA, ConfigFamily, ParamsModel, parse_color
from sashimi.config._image import (
    FAVICON_PNG_SIZES,
    IMAGE_CONVERT,
    IMAGE_RESIZE,
    BmpImageConfig,
    BmpResizeConfig,
    FaviconPackConfig,
    GifImageConfig,
    GifResizeConfig,
    IcoConfig,
    ImageConvertConfig,
    ImageResizeConfig,
    JpegImageConfig,
    JpegResizeConfig,
    PngImageConfig,
    PngResizeConfig,
    Resample,
    TiffImageConfig,
    TiffResizeConfig,
    WebpImageConfig,
    WebpResizeConfig,
)
from sashimi.config._limits import (
    AAC_SAMPLE_RATES,
    DEFAULT_GIF_SECONDS,
    DEFAULT_MAX_PIXELS,
    DURATION_TOLERANCE_SECONDS,
    KEYFRAME_NOTE,
    MAX_AUDIO_SECONDS,
    MAX_FRAME_BYTES,
    MAX_GIF_SECONDS,
    MAX_PREVIEW_SAMPLES,
    MAX_SHEET_HEIGHT,
    MAX_SHEET_PIXELS,
    MAX_VIDEO_SECONDS,
    MIN_TILE_SIZE,
    MP3_SAMPLE_RATES,
    OPUS_SAMPLE_RATES,
)
from sashimi.config._sheets import (
    CONTACT_SHEET,
    AnimatedGifSheetConfig,
    AnimatedSheetConfig,
    AnimatedWebpSheetConfig,
    GridValue,
    JpegSheetConfig,
    PngSheetConfig,
    SheetConfig,
    StillSheetConfig,
    WebpSheetConfig,
)
from sashimi.config._video import (
    VIDEO_CONVERT,
    VIDEO_PREVIEW,
    VIDEO_TRANSFORM,
    VIDEO_TRIM,
    CopyTrimConfig,
    GifPreviewConfig,
    GifVideoConfig,
    H264TrimConfig,
    Mp4TransformConfig,
    Mp4VideoConfig,
    PreviewConfig,
    PreviewCrop,
    TransformConfig,
    TrimConfig,
    VideoConvertConfig,
    Vp9TrimConfig,
    WebmTransformConfig,
    WebmVideoConfig,
    WebpPreviewConfig,
    WebpVideoConfig,
    X264Preset,
)

__all__ = [
    "ParamsModel",
    "ConfigFamily",
    "RGB",
    "RGBA",
    "parse_color",
    "IMAGE_CONVERT",
    "IMAGE_RESIZE",
    "VIDEO_CONVERT",
    "VIDEO_PREVIEW",
    "VIDEO_TRANSFORM",
    "VIDEO_TRIM",
    "AUDIO_CONVERT",
    "CONTACT_SHEET",
    "ImageConvertConfig",
    "JpegImageConfig",
    "PngImageConfig",
    "WebpImageConfig",
    "GifImageConfig",
    "BmpImageConfig",
    "TiffImageConfig",
    "ImageResizeConfig",
    "JpegResizeConfig",
    "PngResizeConfig",
    "WebpResizeConfig",
    "GifResizeConfig",
    "BmpResizeConfig",
    "TiffResizeConfig",
    "IcoConfig",
    "FaviconPackConfig",
    "FAVICON_PNG_SIZES",
    "Resample",
    "VideoConvertConfig",
    "GifVideoConfig",
    "WebpVideoConfig",
    "WebmVideoConfig",
    "Mp4VideoConfig",
    "TransformConfig",
    "Mp4TransformConfig",
    "WebmTransformConfig",
    "TrimConfig",
    "CopyTrimConfig",
    "H264TrimConfig",
    "Vp9TrimConfig",
    "X264Preset",
    "PreviewConfig",
    "GifPreviewConfig",
    "WebpPreviewConfig",
    "PreviewCrop",
    "AudioConvertConfig",
    "Mp3AudioConfig",
    "M4aAudioConfig",
    "OggAudioConfig",
    "WavAudioConfig",
    "FlacAudioConfig",
    "SheetConfig",
    "StillSheetConfig",
    "AnimatedSheetConfig",
    "JpegSheetConfig",
    "PngSheetConfig",
    "WebpSheetConfig",
    "AnimatedWebpSheetConfig",
    "AnimatedGifSheetConfig",
    "GridValue",
    "MAX_GIF_SECONDS",
    "DEFAULT_GIF_SECONDS",
    "MAX_PREVIEW_SAMPLES",
    "MAX_VIDEO_SECONDS",
    "MAX_AUDIO_SECONDS",
    "DURATION_TOLERANCE_SECONDS",
    "DEFAULT_MAX_PIXELS",
    "MAX_SHEET_HEIGHT",
    "MAX_SHEET_PIXELS",
    "MAX_FRAME_BYTES",
    "MIN_TILE_SIZE",
    "MP3_SAMPLE_RATES",
    "AAC_SAMPLE_RATES",
    "OPUS_SAMPLE_RATES",
    "KEYFRAME_NOTE",
]
