# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Batch API: file discovery, extension filters, output naming, progress and reports, built on the
single-file core (:mod:`sashimi.video`, :mod:`sashimi.image`, :mod:`sashimi.sheets`).
Submodules are imported on first use (PEP 562).

Output naming, the same for every function here:

- a conversion writes ``<stem>.<ext>`` (``photo.png`` → ``photo.webp``; ``remux_files`` too);
- a derived output writes ``<stem>.<role>.<ext>``: ``.sheet`` and ``.animated-sheet``
  (``make_thumbs``), ``.preview`` (``make_previews``), ``.resized``, ``.rotated``,
  ``.extracted``, ``.favicons`` (``make_favicon_packs``, a ``.zip``) and ``.edited``
  (``edit_images`` and ``edit_frames``);
- outputs go next to each input, or under ``output_dir`` (keeping the subfolders of searched
  directories), or to ``output`` when exactly one file is processed;
- when two inputs would get the same output (``a.png`` and ``a.jpeg`` → ``a.webp``), both keep
  their extension in the name (``a.png.webp``, ``a.jpeg.webp``); an output that would replace one
  of the inputs is an error;
- an existing output is reported as ``exists`` and kept unless ``force``, which replaces it and
  says so in the result. Without ``force`` nothing is ever replaced, even a file that appears
  while the batch runs;
- ``dry_run=True`` plans every output and reports what would be written, replaced or skipped,
  without processing anything.

Every ``*_generator`` yields the protocol described in ``YieldType``; the other functions run it
and return :func:`build_report`'s report, the same JSON the CLI prints with ``--json``.
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
    from ._batch import YieldType, output_name
    from ._consume import build_report
    from .edit import edit_frames, edit_frames_generator, edit_images, edit_images_generator
    from .extract import extract, extract_generator, parse_time_to_seconds
    from .favicon import make_favicon_packs, make_favicon_packs_generator
    from .flatten import FlattenPlan, flatten, plan_flatten
    from .preview import make_previews, make_previews_generator
    from .remux import remux_files, remux_files_generator
    from .resize import resize_images, resize_images_generator, source_target
    from .thumbs import make_thumbs, make_thumbs_generator, sheet_name
    from .transform import transform_videos, transform_videos_generator

_EXPORTS = {
    "YieldType": "._batch",
    "output_name": "._batch",
    "build_report": "._consume",
    "make_thumbs": ".thumbs",
    "make_thumbs_generator": ".thumbs",
    "sheet_name": ".thumbs",
    "make_previews": ".preview",
    "make_previews_generator": ".preview",
    "make_favicon_packs": ".favicon",
    "make_favicon_packs_generator": ".favicon",
    "remux_files": ".remux",
    "remux_files_generator": ".remux",
    "convert_files": ".convert",
    "convert_files_generator": ".convert",
    "convert_images": ".convert",
    "convert_images_generator": ".convert",
    "to_jpeg": ".convert",
    "to_png": ".convert",
    "edit_images": ".edit",
    "edit_images_generator": ".edit",
    "edit_frames": ".edit",
    "edit_frames_generator": ".edit",
    "extract": ".extract",
    "extract_generator": ".extract",
    "parse_time_to_seconds": ".extract",
    "flatten": ".flatten",
    "plan_flatten": ".flatten",
    "FlattenPlan": ".flatten",
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
