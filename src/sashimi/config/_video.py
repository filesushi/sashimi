# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from sashimi.config._base import (
    ConfigFamily,
    Flip,
    FormBool,
    ParamsModel,
    Rotation,
    SourceMetadata,
    TargetName,
    parse_flag,
    sentence,
)
from sashimi.config._limits import DEFAULT_GIF_SECONDS, MAX_ANIMATION_FPS, Limits, duration_text
from sashimi.errors import InvalidOptionsError

X264Preset = Literal["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
_AudioBitrate = Annotated[int, Field(ge=32, le=512)]


class _H264Options(SourceMetadata):
    """H.264 video with AAC audio."""

    crf: int = Field(default=23, ge=0, le=51)
    """Constant quality: lower is better and bigger."""
    preset: X264Preset = "veryfast"
    """Slower presets give smaller files."""
    audio_bitrate_kbps: _AudioBitrate = 128
    """AAC audio bitrate in kb/s."""
    mute: bool = False
    """Drop the audio track."""


class _Vp9Options(SourceMetadata):
    """VP9 video with Opus audio."""

    crf: int = Field(default=34, ge=0, le=63)
    """Constant quality: lower is better and bigger."""
    speed: int = Field(default=8, ge=0, le=8)
    """libvpx ``-cpu-used``: higher is faster and lower quality."""
    audio_bitrate_kbps: _AudioBitrate = 96
    """Opus audio bitrate in kb/s. Opus allows at most 256 per channel, so mono audio gets at most 256."""
    mute: bool = False
    """Drop the audio track."""


class _Clip(ParamsModel):
    start_seconds: float = Field(default=0.0, ge=0)
    """Where to start, in seconds from the beginning."""


def video_clip_too_long(limit: float) -> InvalidOptionsError:
    """The refusal of a video clip longer than ``Limits.max_video_seconds``."""
    return InvalidOptionsError(f"Video clips can be at most {duration_text(limit)} long.", limit="max_video_seconds")


class _Animated(_Clip):
    duration_seconds: float = Field(default=DEFAULT_GIF_SECONDS, gt=0)
    """Length in seconds."""
    max_width: int = Field(default=480, ge=64)
    """Never upscales; keeps the aspect ratio."""
    fps: int = Field(default=12, ge=1, le=MAX_ANIMATION_FPS)
    """Frames per second."""
    loop: bool = True
    """Loop forever, or play once."""

    def _check_limits(self, limits: Limits) -> None:
        limit = limits.max_gif_seconds
        if limit is not None and self.duration_seconds > limit:
            kind = "GIFs" if isinstance(self, GifVideoConfig) else "Animated WebPs"
            raise InvalidOptionsError(f"{kind} can be at most {duration_text(limit)} long.", limit="max_gif_seconds")


class _Transcode(_Clip):
    duration_seconds: float | None = Field(default=None, gt=0)
    """``None`` converts to the end of the video."""
    max_width: int | None = Field(default=None, ge=64)
    """``None`` keeps the source's width. Never upscales; keeps the aspect ratio."""

    def _check_limits(self, limits: Limits) -> None:
        limit = limits.max_video_seconds
        if limit is not None and self.duration_seconds is not None and self.duration_seconds > limit:
            raise video_clip_too_long(limit)


class GifVideoConfig(_Animated):
    """A GIF made from part of a video: no sound, a 256-colour palette."""

    target: Annotated[Literal["gif"], TargetName] = "gif"
    """The output format."""
    dither: Literal["bayer", "sierra2_4a", "floyd_steinberg", "none"] = "bayer"
    """Bayer gives smaller files; error diffusion (sierra2_4a, floyd_steinberg) smoother gradients."""
    palette_mode: Literal["diff", "full", "single"] = "diff"
    """Build the palette from moving parts ("diff"), all pixels ("full"), or per frame ("single")."""


class WebpVideoConfig(_Animated):
    """An animated WebP made from part of a video: no sound."""

    target: Annotated[Literal["webp"], TargetName] = "webp"
    """The output format."""
    quality: int = Field(default=75, ge=0, le=100)
    """Lossy quality, or compression effort when ``lossless``."""
    lossless: bool = False
    """Lossless frames (much bigger files)."""


class WebmVideoConfig(_Vp9Options, _Transcode):
    """A WebM (VP9 + Opus) video."""

    target: Annotated[Literal["webm"], TargetName] = "webm"
    """The output format."""


class Mp4VideoConfig(_H264Options, _Transcode):
    """An MP4 (H.264 + AAC) video."""

    target: Annotated[Literal["mp4"], TargetName] = "mp4"
    """The output format."""


class _Transform(ParamsModel):
    rotate: Rotation = 0
    """Clockwise rotation in degrees."""
    flip: Flip = "none"
    """Mirror the picture."""

    @model_validator(mode="after")
    def _check_something_to_do(self) -> Self:
        if self.rotate == 0 and self.flip == "none":
            raise sentence("Choose a rotation or a flip.")
        return self


class Mp4TransformConfig(_H264Options, _Transform):
    """Rotate and/or flip a video, re-encoding it as MP4 (H.264 + AAC)."""

    target: Annotated[Literal["mp4"], TargetName] = "mp4"
    """The output format."""


class WebmTransformConfig(_Vp9Options, _Transform):
    """Rotate and/or flip a video, re-encoding it as WebM (VP9 + Opus)."""

    target: Annotated[Literal["webm"], TargetName] = "webm"
    """The output format."""


class _TrimRange(ParamsModel):
    start_seconds: float = Field(default=0.0, ge=0)
    """Where the clip starts, in seconds."""
    end_seconds: float | None = Field(default=None, gt=0)
    """``None`` (with no ``duration_seconds``) trims to the end of the video."""
    duration_seconds: float | None = Field(default=None, gt=0)
    """The clip's length, instead of ``end_seconds``."""

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.end_seconds is not None and self.duration_seconds is not None:
            raise sentence("Give an end time or a length, not both.")
        if self.end_seconds is not None and self.end_seconds <= self.start_seconds:
            raise sentence("The end time must be after the start time.")
        return self

    @property
    def length(self) -> float | None:
        """The requested length in seconds, or ``None`` for "to the end"."""
        if self.end_seconds is not None:
            return self.end_seconds - self.start_seconds
        return self.duration_seconds


class _AccurateRange(_TrimRange):
    accurate: Annotated[Literal[True], FormBool] = True
    """Re-encode for a frame-accurate cut."""

    def _check_limits(self, limits: Limits) -> None:
        limit = limits.max_video_seconds
        if limit is not None and (self.length or 0) > limit:
            raise video_clip_too_long(limit)


class CopyTrimConfig(SourceMetadata, _TrimRange):
    """
    A lossless trim: streams are copied into `container`, so the cut is instant but starts at the
    keyframe at or before `start_seconds` (see :data:`KEYFRAME_NOTE`). The container must be able
    to hold the source's codecs.
    """

    container: Annotated[Literal["mkv", "mp4", "webm", "mov"], TargetName]
    """The output container; it must be able to hold the source's codecs."""
    accurate: Annotated[Literal[False], FormBool] = False
    """Copy the streams without re-encoding."""


class H264TrimConfig(_H264Options, _AccurateRange):
    """A frame-accurate trim, re-encoded as H.264 + AAC into MP4, MOV or MKV."""

    container: Annotated[Literal["mp4", "mov", "mkv"], TargetName]
    """The output container."""


class Vp9TrimConfig(_Vp9Options, _AccurateRange):
    """A frame-accurate trim, re-encoded as VP9 + Opus into WebM."""

    container: Annotated[Literal["webm"], TargetName] = "webm"
    """The output container."""


PreviewCrop = Literal["none", "9:16", "1:1"]


class _Preview(ParamsModel):
    samples: int = Field(default=5, ge=1)
    """How many clips to take from across the video. Short videos get fewer, so clips never overlap."""
    sample_duration: float = Field(default=2.0, gt=0)
    """Length of each clip in seconds."""
    start_offset_percent: float = Field(default=10.0, ge=0.0, le=100.0)
    """Skip this much of the start of the video (intros, logos)."""
    end_offset_percent: float = Field(default=10.0, ge=0.0, le=100.0)
    """Skip this much of the end of the video (credits)."""
    max_height: int = Field(default=360, ge=64)
    """Scale down to at most this height. Never upscales; keeps the aspect ratio."""
    max_width: int | None = Field(default=None, ge=64)
    """Also scale down to at most this width. Blank sets no width limit."""
    crop: PreviewCrop = "none"
    """Crop the centre of the picture to this shape first, e.g. "9:16" for vertical video sites."""
    fps: int = Field(default=15, ge=1, le=MAX_ANIMATION_FPS)
    """Frames per second."""
    loop: bool = True
    """Loop forever, or play once."""

    def _check_limits(self, limits: Limits) -> None:
        clips = limits.max_preview_clips
        if clips is not None and self.samples > clips:
            raise InvalidOptionsError(
                f"Previews can have at most {clips} {'clip' if clips == 1 else 'clips'}.", limit="max_preview_clips"
            )
        limit = limits.max_gif_seconds
        if limit is not None and self.samples * self.sample_duration > limit:
            raise InvalidOptionsError(
                f"Previews can be at most {duration_text(limit)} long. Use fewer clips or shorter ones.",
                limit="max_gif_seconds",
            )


class GifPreviewConfig(_Preview):
    """A GIF preview: short clips sampled from across a video, played one after another. No sound."""

    target: Annotated[Literal["gif"], TargetName] = "gif"
    """The output format."""


class WebpPreviewConfig(_Preview):
    """An animated WebP preview: short clips sampled from across a video, played one after another. No sound."""

    target: Annotated[Literal["webp"], TargetName] = "webp"
    """The output format."""
    quality: int = Field(default=75, ge=0, le=100)
    """Lossy quality, 0 (smallest) to 100 (best)."""


VideoConvertConfig = GifVideoConfig | WebpVideoConfig | WebmVideoConfig | Mp4VideoConfig
"""Any video conversion config: one model per output format."""
TransformConfig = Mp4TransformConfig | WebmTransformConfig
"""Any rotate/flip config."""
TrimConfig = CopyTrimConfig | H264TrimConfig | Vp9TrimConfig
"""Any trim config: a stream copy, or an accurate re-encode."""
PreviewConfig = GifPreviewConfig | WebpPreviewConfig
"""Any preview config."""

VIDEO_CONVERT: ConfigFamily[VideoConvertConfig] = ConfigFamily(
    "video convert",
    {"gif": GifVideoConfig, "webp": WebpVideoConfig, "webm": WebmVideoConfig, "mp4": Mp4VideoConfig},
)
"""Video conversion, by ``target``: gif, webp (animated), webm, mp4."""

VIDEO_PREVIEW: ConfigFamily[PreviewConfig] = ConfigFamily(
    "video preview", {"gif": GifPreviewConfig, "webp": WebpPreviewConfig}
)
"""
Previews (short clips sampled from across a video, played in turn), by ``target``: gif or webp.
``Limits.max_gif_seconds`` limits the clips' total length, and ``Limits.max_preview_clips`` their number.
"""

VIDEO_TRANSFORM: ConfigFamily[TransformConfig] = ConfigFamily(
    "video transform", {"mp4": Mp4TransformConfig, "webm": WebmTransformConfig}, default="mp4"
)
"""Rotate/flip, by ``target``: mp4 (the default) or webm."""


class _TrimFamily(ConfigFamily[TrimConfig]):
    @property
    def targets(self) -> tuple[str, ...]:
        return ("mkv", "mp4", "webm", "mov")

    def _pick(self, data: Mapping[str, Any]) -> tuple[type[ParamsModel], dict[str, Any]]:
        accurate = parse_flag("accurate", data.get("accurate", False))
        containers = dict.fromkeys(self.targets, CopyTrimConfig)
        name, model = self._lookup("container", data.get("container"), containers, None)
        if accurate:
            model = Vp9TrimConfig if name == "webm" else H264TrimConfig
        return model, {**data, "container": name, "accurate": accurate}


VIDEO_TRIM: ConfigFamily[TrimConfig] = _TrimFamily(
    "video trim",
    {"copy": CopyTrimConfig, "h264": H264TrimConfig, "vp9": Vp9TrimConfig},
    key="container",
)
"""
Trimming, by ``container`` (mkv, mp4, webm, mov) and ``accurate``: a stream copy
(:class:`CopyTrimConfig`) unless ``accurate``, which re-encodes as VP9 for WebM
(:class:`Vp9TrimConfig`) and H.264 otherwise (:class:`H264TrimConfig`).
"""
