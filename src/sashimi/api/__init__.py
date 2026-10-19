# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Batch API: file discovery, extension filters, outputs written next to inputs, progress and
summaries, built on the single-file core (:mod:`sashimi.video`, :mod:`sashimi.image`,
:mod:`sashimi.sheets`). Submodules are imported on first use (PEP 562).
"""

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .convert import (
        convert_files,
        convert_files_generator,
        convert_images,
        convert_images_generator,
        to_jpeg,
        to_png,
    )
    from .extract import extract, parse_time_to_seconds
    from .flatten import flatten
    from .resize import resize_images, resize_images_generator, source_target
    from .thumbs import make_thumbs, make_thumbs_generator
    from .transform import transform_videos, transform_videos_generator

_EXPORTS = {
    "make_thumbs": ".thumbs",
    "make_thumbs_generator": ".thumbs",
    "convert_files": ".convert",
    "convert_files_generator": ".convert",
    "convert_images": ".convert",
    "convert_images_generator": ".convert",
    "to_jpeg": ".convert",
    "to_png": ".convert",
    "extract": ".extract",
    "parse_time_to_seconds": ".extract",
    "flatten": ".flatten",
    "resize_images": ".resize",
    "resize_images_generator": ".resize",
    "source_target": ".resize",
    "transform_videos": ".transform",
    "transform_videos_generator": ".transform",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(_EXPORTS[name], __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals()) + __all__)
