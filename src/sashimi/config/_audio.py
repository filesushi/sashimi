# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, ClassVar, Literal

from pydantic import Field

from sashimi.config._base import ConfigFamily, FormInt, ParamsModel, TargetName
from sashimi.config._limits import AUDIO_CLIP_TOO_LONG, MAX_AUDIO_SECONDS

_Bitrate = Annotated[int, Field(ge=64, le=320)]


class _AudioBase(ParamsModel):
    start_seconds: float = Field(default=0.0, ge=0)
    """Where to start, in seconds from the beginning."""
    duration_seconds: float | None = Field(default=None, gt=0, le=MAX_AUDIO_SECONDS)
    """``None`` converts to the end of the file (which may be at most 60 minutes long)."""
    mono: bool = False
    """Mix down to one channel."""
    sample_rate: int | None = Field(default=None, ge=8000, le=192000)
    """Output sample rate in Hz. ``None`` keeps the source's."""

    bound_messages: ClassVar[Mapping[str, str]] = MappingProxyType({"duration_seconds": AUDIO_CLIP_TOO_LONG})


class Mp3AudioConfig(_AudioBase):
    """MP3 (LAME)."""

    target: Annotated[Literal["mp3"], TargetName] = "mp3"
    """The output format."""
    bitrate_kbps: _Bitrate = 192
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
    """Bitrate in kb/s."""
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
