# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Batch API: file discovery, extension filters, output naming, progress and reports, built on the
single-file core (:mod:`sashimi.video`, :mod:`sashimi.image`, :mod:`sashimi.sheets`).
Submodules are imported on first use (PEP 562).

Output naming, the same for every function here (the full policy is in ``_batch``):

- a name depends only on its own input, the command and the settings, never on the other files
  in the batch;
- every command's default pattern is ``{input_name}.{role}.{output_ext}``:
  ``clip.mp4.sheet.jpg`` and ``clip.mp4.animated-sheet.webp`` (``make_thumbs``),
  ``clip.mp4.preview.webp`` (``make_previews``), ``logo.png.favicons.zip``
  (``make_favicon_packs``), ``photo.jpg.resized.jpg`` (``resize_images``),
  ``photo.jpg.rotated.png`` (``rotate_files`` saving PNG), ``clip.mkv.extracted.mkv``
  (``extract``), ``photo.jpg.edited.jpg`` (``edit_images``), ``anim.gif.frames.gif``
  (``edit_frames``) and ``clip.mov.stripped.mov`` (``strip_files``); conversions have no role:
  ``photo.png.webp`` (``convert_files``), ``clip.mp4.mkv`` (``remux_files``);
- ``output_name`` sets the pattern (:class:`NameTemplate`; :func:`default_output_name` gives
  each command's default), from the fields ``{input_name}`` (``clip.mp4``), ``{input_stem}``
  (``clip``), ``{input_ext}`` (``mp4``), ``{role}`` (``sheet``) and ``{output_ext}`` (``jpg``):
  ``{input_stem}.{role}.{output_ext}`` gives short names (``photo.rotated.png``), and
  ``{input_name}.jpeg`` names sheets ``clip.mp4.jpeg``. A literal extension must name the
  output's format;
- ``{output_ext}`` is the extension the caller chose (``output_ext``, or ``convert_files``'
  target as given: ``jpeg`` or ``jpg``), else the input's own as written when the output keeps
  its format, else sashimi's standard one (``jpg``, ``tif``);
- every planned name is checked before anything runs; outputs go next to each input, or under
  ``output_dir`` (keeping the subfolders of searched directories), or to ``output`` when
  exactly one file is processed;
- a run never processes its own outputs: a directory search leaves out each file this
  function, with these settings, would make from another file found anywhere in the search
  (``photo.jpg.resized.jpg`` for ``resize_images``), so reruns report ``exists``. Everything
  else found is processed, other commands' outputs included (``convert_files`` to WebP after
  ``make_thumbs`` converts the sheets);
- two inputs that would be written to the same file are both ``skipped``, never renamed, each
  message naming the other file and the shared name, and ``plan().clash_note`` naming the
  pattern and where it was set; an existing output is reported as ``exists`` and kept unless
  ``force``, which replaces whatever is there, the input itself included, and says so. Without
  ``force`` nothing is ever replaced, even a file that appears while the batch runs;
- ``dry_run=True`` plans every output and reports what would be written, replaced or refused,
  without processing anything.

Every ``*_generator`` returns a :class:`Batch`: call its ``plan()`` to see each file's output,
action, kind and route before anything runs (``plan().describe()`` gives "44 videos and 23
images", and ``plan().mixed`` says whether the files take more than one route, so a caller can
ask first), then iterate it for the results. ``rotate_files`` routes images through the image
editor and videos through the video transform, so a folder of both is mixed; ``convert_files``
and ``strip_files`` route by kind too. Nothing here ever prompts.

Every ``*_generator`` yields the protocol described in ``YieldType``; the other functions run it
and return :func:`build_report`'s report, the same JSON the CLI prints with ``--json``. Every
batch function takes ``limits`` (a :class:`sashimi.config.Limits`; default ``Limits()``) and
passes it to the converters; a file over a limit is an ``error`` result whose message states it.
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
    from ._batch import Batch, BatchPlan, PlannedFile, YieldType
    from ._naming import NameTemplate, default_output_name
    from ._consume import build_report
    from .edit import edit_frames, edit_frames_generator, edit_images, edit_images_generator
    from .extract import extract, extract_generator, parse_time_to_seconds
    from .favicon import make_favicon_packs, make_favicon_packs_generator
    from .flatten import FlattenPlan, flatten, plan_flatten
    from .preview import make_previews, make_previews_generator
    from .remux import remux_files, remux_files_generator
    from .resize import resize_images, resize_images_generator, source_target
    from .strip import STRIP_EXTENSIONS, strip_files, strip_files_generator
    from .thumbs import make_thumbs, make_thumbs_generator
    from .transform import rotate_files, rotate_files_generator, transform_videos, transform_videos_generator

_EXPORTS = {
    "YieldType": "._batch",
    "Batch": "._batch",
    "BatchPlan": "._batch",
    "PlannedFile": "._batch",
    "NameTemplate": "._naming",
    "default_output_name": "._naming",
    "build_report": "._consume",
    "make_thumbs": ".thumbs",
    "make_thumbs_generator": ".thumbs",
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
    "rotate_files": ".transform",
    "rotate_files_generator": ".transform",
    "transform_videos": ".transform",
    "transform_videos_generator": ".transform",
    "strip_files": ".strip",
    "strip_files_generator": ".strip",
    "STRIP_EXTENSIONS": ".strip",
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
