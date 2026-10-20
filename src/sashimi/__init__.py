# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
sashimi: file conversion and video contact sheets, built on FFmpeg and Pillow.

Public modules: :mod:`sashimi.config` (every config model and limit; needs only pydantic),
:mod:`sashimi.video`, :mod:`sashimi.image`, :mod:`sashimi.audio`, :mod:`sashimi.sheets`,
:mod:`sashimi.metadata`,
:mod:`sashimi.errors`, and the batch helpers in :mod:`sashimi.api`.
Submodules load on first use, so ``import sashimi`` stays cheap.
"""

import importlib
from typing import TYPE_CHECKING, Any

from sashimi._version import __version__

if TYPE_CHECKING:
    from sashimi import api, audio, config, errors, image, metadata, sheets, video
    from sashimi.core._ffmpeg import set_ffmpeg_paths
    from sashimi.core.probe import MediaInfo, StreamInfo, probe
    from sashimi.errors import ProcessingError, SashimiError, UserInputError

_SUBMODULES = {"api", "audio", "config", "errors", "image", "metadata", "sheets", "video"}
_ATTRIBUTES = {
    "probe": "sashimi.core.probe",
    "MediaInfo": "sashimi.core.probe",
    "StreamInfo": "sashimi.core.probe",
    "set_ffmpeg_paths": "sashimi.core._ffmpeg",
    "SashimiError": "sashimi.errors",
    "UserInputError": "sashimi.errors",
    "ProcessingError": "sashimi.errors",
}

__all__ = ["__version__", *sorted(_SUBMODULES), *_ATTRIBUTES]


def __getattr__(name: str) -> Any:
    if name in _SUBMODULES:
        value = importlib.import_module(f"sashimi.{name}")
    elif name in _ATTRIBUTES:
        value = getattr(importlib.import_module(_ATTRIBUTES[name]), name)
    else:
        raise AttributeError(f"module 'sashimi' has no attribute {name!r}")
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
