# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import math
from typing import Annotated, Any, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from sashimi.config._base import ParamsModel, invalid_options_error

MIB = 1024 * 1024

DEFAULT_GIF_SECONDS = 10
"""Length of a GIF or animated WebP when no ``duration_seconds`` is given."""
DURATION_TOLERANCE_SECONDS = 1
"""
How far past the probed length an encode may read when no length is given. Container durations
can be slightly short (MPEG-TS often understates its audio by a few hundred milliseconds), and
cutting exactly at the probed length would clip the end.
"""

DEFAULT_MAX_PIXELS = 64_000_000
"""The default of :attr:`Limits.max_pixels` (64 MP covers 48 MP phone photos)."""
DEFAULT_MAX_SCAN_PACKETS = 2_000_000
"""The default of :attr:`Limits.max_scan_packets`: about 7 hours of 30 fps video with AAC audio, or 18 hours of 30 fps video packets."""

WEBP_MAX_SIDE = 16383
"""The widest and tallest WebP image, in pixels: the format's limit (contact sheets, frame strips)."""
JPEG_MAX_SIDE = 65500
"""The widest and tallest JPEG image the JPEG library writes, in pixels."""
GIF_MAX_SIDE = 65535
"""The widest and tallest GIF image, in pixels: the format's limit."""
MIN_TILE_SIZE = 16
"""Smallest contact sheet tile, in pixels on either side; layouts that need smaller tiles are refused."""

MIN_FRAME_MS = 20
"""
Shortest frame duration written when an animation's speed changes, in milliseconds. Browsers play
frames shorter than this at 100 ms, so a faster setting would play slower.
"""
MAX_ANIMATION_FPS = 1000 // MIN_FRAME_MS
"""
The highest frame rate of a GIF or animated WebP made from a video (50): faster frames would be
shorter than :data:`MIN_FRAME_MS`, which browsers play at 100 ms.
"""
CROP_ASPECTS = ("free", "1:1", "4:3", "16:9", "9:16")
"""The crop shapes UIs offer: free, or a width:height ratio (see ``sashimi.image.aspect_crop``)."""

MP3_SAMPLE_RATES = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)
"""The sample rates the MP3 encoder (LAME) accepts, in Hz."""
AAC_SAMPLE_RATES = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 64000, 88200, 96000)
"""The sample rates the AAC encoder (M4A output) accepts, in Hz."""
OPUS_SAMPLE_RATES = (8000, 12000, 16000, 24000, 48000)
"""The sample rates the Opus encoder accepts, in Hz."""
OPUS_MAX_KBPS_PER_CHANNEL = 256
"""
The highest Opus bitrate per audio channel, in kb/s. A higher ``bitrate_kbps`` or
``audio_bitrate_kbps`` is lowered to this times the output's channels (256 for mono, 512 for stereo).
"""
FLAC_MAX_SAMPLE_RATE = 1_048_575
"""The highest sample rate FLAC can store, in Hz."""

KEYFRAME_NOTE = (
    "Trimming without re-encoding is instant and keeps the original quality, but cuts can only "
    "land on keyframes, so your clip may start a moment before the time you chose."
)
"""User-facing sentence explaining keyframe-aligned trims."""

TILES_TOO_SMALL = "These settings would make the contact sheet's tiles too small. Use fewer columns or a wider sheet."


def duration_text(seconds: float) -> str:
    """A limit in seconds as words: "30 seconds", "10 minutes", "60 minutes", "2 hours"."""
    if seconds > 3600 and seconds % 3600 == 0:
        return f"{int(seconds // 3600)} hours"
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return "1 minute" if minutes == 1 else f"{minutes} minutes"
    return "1 second" if seconds == 1 else f"{seconds:g} seconds"


def length_text(seconds: float) -> str:
    """A media length as words, to the second: "45 seconds", "12 minutes 5 seconds", "1 hour 2 minutes"."""
    total = max(0, round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [(hours, "hour"), (minutes, "minute"), (secs, "second")]
    words = [f"{n} {unit}{'' if n == 1 else 's'}" for n, unit in parts if n]
    return " ".join(words[:2]) if words else "under a second"


def rough_length_text(seconds: float) -> str:
    """A rough media length: to the minute from a minute up ("43 minutes"), else to the second."""
    return length_text(round(seconds / 60) * 60 if seconds >= 60 else max(seconds, 1))


def _length_words(seconds: float, decimals: int) -> str:
    total = max(0.0, seconds)
    hours, rest = divmod(int(total), 3600)
    minutes = rest // 60
    secs = round(total - hours * 3600 - minutes * 60, decimals)
    if secs >= 60:
        minutes, secs = minutes + 1, secs - 60
        if minutes == 60:
            hours, minutes = hours + 1, 0
    words = [f"{n} {unit}{'' if n == 1 else 's'}" for n, unit in ((hours, "hour"), (minutes, "minute")) if n]
    if secs:
        text = f"{secs:.{decimals}f}".rstrip("0").rstrip(".") if decimals else str(int(secs))
        words.append(f"{text} second{'' if text == '1' else 's'}")
    return " ".join(words) if words else "0 seconds"


def _exact_length(seconds: float) -> tuple[str, float]:
    for decimals in range(4):
        if abs(round(seconds, decimals) - seconds) < 1e-9:
            return _length_words(seconds, decimals), round(seconds, decimals)
    return _length_words(seconds, 3), round(seconds, 3)


def over_limit_texts(length: float, limit: float) -> tuple[str, str]:
    """
    A length refused under `limit` and the limit, as words in the same units ("10 minutes 0.4
    seconds" and "10 minutes"), with the length given as precisely as it takes to read as more
    than the limit: to the second when that is enough, else to the tenth, hundredth or
    thousandth of a second, rounded down, or as "just over" the limit.
    """
    limit_words, shown_limit = _exact_length(limit)
    if round(length) > shown_limit:
        return _length_words(round(length), 0), limit_words
    for decimals in (1, 2, 3):
        cut = math.floor(length * 10**decimals) / 10**decimals
        if cut > shown_limit:
            return _length_words(cut, decimals), limit_words
    return f"just over {limit_words}", limit_words


def too_long_text(kind: str, length: float, start: float, limit: float) -> str:
    """Why a whole `kind` (from `start`) is refused under a length limit, and what to do."""
    length_words, limit_words = over_limit_texts(length, limit)
    what = f"This {kind} is {length_words} long" if start <= 0 else f"From {start:g}s, this {kind} runs {length_words}"
    return f"{what}, and the limit is {limit_words}. Set a shorter duration to convert part of it."


def megabytes(size: int) -> str:
    """A size in bytes as whole megabytes (MiB), as people read them: "256 MB"."""
    return f"{round(size / MIB):,} MB"


def megapixels(pixels: int) -> str:
    """A pixel count in megapixels, to one decimal place: "64", "12.5"."""
    return f"{round(pixels / 1_000_000, 1):g}"


def _no_limit(value: Any) -> Any:
    if isinstance(value, bool):
        raise ValueError("must be a number, or none for no limit")
    if isinstance(value, str) and value.strip().lower() in ("", "none"):
        return None
    return value


_Count = Annotated[Annotated[int, Field(gt=0)] | None, BeforeValidator(_no_limit)]
_Seconds = Annotated[Annotated[float, Field(gt=0)] | None, BeforeValidator(_no_limit)]


class Limits(BaseModel):
    """
    The limits sashimi enforces, all chosen by the caller. Every function that can meet one takes
    ``limits=``; ``None`` means ``Limits()``, these defaults.

    Each field is a number, or ``None`` for no limit. The defaults protect the machine (decoded
    pixels and frames held in memory, contact sheet size, the packets read to measure a recording
    that doesn't state its length) and leave lengths unlimited, since a longer video takes longer
    but needs no more memory. ``Limits.unlimited()`` turns every limit off.

    A refusal states the limit in force in its ``user_message`` and names the field in its
    ``limit`` attribute. Format limits (WebP's 16383 pixels, the encoders' sample rates) and
    correctness rules (:data:`MIN_FRAME_MS`, :data:`MIN_TILE_SIZE`) are fixed, not limits.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True, allow_inf_nan=False)

    max_pixels: _Count = DEFAULT_MAX_PIXELS
    """Images with more pixels than this are refused before they are decoded (conversions, icons, edits, frames)."""
    max_frame_bytes: _Count = 1024 * MIB
    """Video frames held in memory at once, at width x height x 3 bytes each: an animated contact sheet's clips, which are composited from memory. GIFs made from a video and GIF previews build their palette in a first pass, hold a few frames at a time, and don't count against it."""
    max_animation_bytes: _Count = 1024 * MIB
    """The frames of an animation held in memory, at width x height x 4 bytes each: edits, frame selection and frame strips of animated GIFs and WebPs, and converting or resizing an animation to an animated GIF or WebP (at the output size)."""
    max_sheet_pixels: _Count = 64_000_000
    """The largest contact sheet, in pixels."""
    max_video_seconds: _Seconds = None
    """The longest video that is re-encoded or copied into an MP4 (to WebM or MP4, rotate and flip, accurate cuts). Lossless cuts and remuxes have no limit."""
    max_audio_seconds: _Seconds = None
    """The longest audio that is converted or extracted."""
    max_gif_seconds: _Seconds = None
    """The longest GIF or animated WebP made from a video, and the longest preview (all its clips together)."""
    max_preview_clips: _Count = None
    """The most clips a preview takes."""
    max_scan_packets: _Count = DEFAULT_MAX_SCAN_PACKETS
    """Packets read to measure a file: a recording that doesn't state its length (a live capture that was never finalized) or a Matroska file that starts late is read to its end, and a video is read up to the time a contact sheet, preview or start time needs, to find the keyframe to decode from (about 20 microseconds of CPU time a packet). A file that needs more is refused. The default covers about 7 hours of 30 fps video with AAC audio, or 18 hours of video packets."""

    @classmethod
    def unlimited(cls) -> Self:
        """Limits with every limit turned off."""
        return cls.model_validate(dict.fromkeys(cls.model_fields))

    @classmethod
    def from_params(cls, params: dict[str, Any] | None = None, /, **fields: Any) -> Self:
        """
        Builds limits from a dict (a settings file, a form) plus explicit fields. A missing key
        keeps its default; ``None``, an empty string or ``"none"`` turns that limit off. Invalid
        values raise :class:`~sashimi.errors.InvalidOptionsError`.
        """
        try:
            return cls.model_validate({**(params or {}), **fields})
        except ValidationError as e:
            raise invalid_options_error(e) from e

    def check(self, config: ParamsModel) -> None:
        """
        Raises :class:`~sashimi.errors.InvalidOptionsError` if `config` alone breaks a limit: a GIF,
        clip or preview longer than allowed, or a preview of too many clips. The converters check
        this first, then the limits that depend on the file; a web app can call it when it queues
        a job, before the file is read.
        """
        config._check_limits(self)


DEFAULT_LIMITS = Limits()


def resolve_limits(limits: Limits | None) -> Limits:
    """`limits`, or the defaults when it is ``None``."""
    return DEFAULT_LIMITS if limits is None else limits


def check_webp_size(width: int, height: int) -> None:
    """Refuses a WebP output wider or taller than WebP can store (:data:`WEBP_MAX_SIDE`) with MediaTooLargeError."""
    from sashimi.errors import MediaTooLargeError

    if max(width, height) > WEBP_MAX_SIDE:
        raise MediaTooLargeError(
            f"This picture would be {width}x{height} pixels, and WebP can't be wider or taller than "
            f"{WEBP_MAX_SIDE:,} pixels. Set a maximum width and height of {WEBP_MAX_SIDE:,} or less, or choose "
            "another format.",
            detail=f"{width}x{height} > {WEBP_MAX_SIDE}",
        )
