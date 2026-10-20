# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

from typing import Annotated, Literal

from pydantic import Field

from sashimi.config._base import ConfigFamily, FormInt, SourceMetadata, TargetName
from sashimi.config._limits import FLAC_MAX_SAMPLE_RATE, Limits, duration_text
from sashimi.errors import InvalidOptionsError

_Mp3Bitrate = Annotated[int, Field(ge=64, le=320)]
_Bitrate = Annotated[int, Field(ge=64, le=512)]


class _AudioBase(SourceMetadata):
    start_seconds: float = Field(default=0.0, ge=0)
    """Where to start, in seconds from the beginning."""
    duration_seconds: float | None = Field(default=None, gt=0)
    """``None`` converts to the end of the file."""
    mono: bool = False
    """Mix down to one channel."""
    sample_rate: int | None = Field(default=None, ge=8000)
    """Output sample rate in Hz. ``None`` keeps the source's."""

    def _check_limits(self, limits: Limits) -> None:
        limit = limits.max_audio_seconds
        if limit is not None and self.duration_seconds is not None and self.duration_seconds > limit:
            raise InvalidOptionsError(
                f"Audio clips can be at most {duration_text(limit)} long.", limit="max_audio_seconds"
            )


class Mp3AudioConfig(_AudioBase):
    """MP3 (LAME)."""

    target: Annotated[Literal["mp3"], TargetName] = "mp3"
    """The output format."""
    bitrate_kbps: _Mp3Bitrate = 192
    """Bitrate in kb/s."""
    sample_rate: Annotated[Literal[8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000], FormInt] | None = None
    """Output sample rate in Hz. ``None`` keeps the source's if MP3 supports it, and otherwise uses the nearest rate it does."""


class M4aAudioConfig(_AudioBase):
    """M4A (AAC)."""

    target: Annotated[Literal["m4a"], TargetName] = "m4a"
    """The output format."""
    bitrate_kbps: _Bitrate = 160
    """Bitrate in kb/s."""
    sample_rate: (
        Annotated[Literal[8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 64000, 88200, 96000], FormInt]
        | None
    ) = None
    """Output sample rate in Hz. ``None`` keeps the source's if AAC supports it, and otherwise uses the nearest rate it does."""


class OggAudioConfig(_AudioBase):
    """OGG (Opus)."""

    target: Annotated[Literal["ogg"], TargetName] = "ogg"
    """The output format."""
    bitrate_kbps: _Bitrate = 128
    """Bitrate in kb/s. Opus allows at most 256 per channel, so mono audio gets at most 256."""
    sample_rate: Annotated[Literal[8000, 12000, 16000, 24000, 48000], FormInt] | None = None
    """Output sample rate in Hz. ``None`` keeps the source's if Opus supports it, and otherwise uses 48 kHz."""


class WavAudioConfig(_AudioBase):
    """WAV (uncompressed PCM)."""

    target: Annotated[Literal["wav"], TargetName] = "wav"
    """The output format."""
    wav_bit_depth: Annotated[Literal[16, 24, 32], FormInt] = 16
    """PCM sample size in bits."""


class FlacAudioConfig(_AudioBase):
    """FLAC (lossless)."""

    target: Annotated[Literal["flac"], TargetName] = "flac"
    """The output format."""
    flac_compression_level: int = Field(default=5, ge=0, le=12)
    """0 is fastest, 12 smallest; lossless either way."""
    sample_rate: int | None = Field(default=None, ge=8000, le=FLAC_MAX_SAMPLE_RATE)
    """Output sample rate in Hz. ``None`` keeps the source's."""


AudioConvertConfig = Mp3AudioConfig | M4aAudioConfig | OggAudioConfig | WavAudioConfig | FlacAudioConfig
"""Any audio config: one model per output format."""

AUDIO_CONVERT: ConfigFamily[AudioConvertConfig] = ConfigFamily(
    "audio convert",
    {
        "mp3": Mp3AudioConfig,
        "m4a": M4aAudioConfig,
        "ogg": OggAudioConfig,
        "wav": WavAudioConfig,
        "flac": FlacAudioConfig,
    },
)
"""Audio conversion (or extraction from a video), by ``target``: mp3, m4a, ogg, wav, flac."""
