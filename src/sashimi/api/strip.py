# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Batch metadata removal, without re-encoding."""

from collections.abc import Iterable, Sequence
from typing import Any, Literal

from sashimi.api._batch import Batch, FileProgress, PathInput, run_batch
from sashimi.api._kinds import kind_of
from sashimi.api._naming import NameTemplate, naming, source_format
from sashimi.api._consume import consume_generator
from sashimi.config import Limits
from sashimi.core.metadata import strip

__all__ = ["STRIP_EXTENSIONS", "strip_files", "strip_files_generator"]

STRIP_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff",
    ".mp4", ".m4v", ".f4v", ".mov", ".mkv", ".mk3d", ".webm",
    ".mp3", ".m4a", ".m4b", ".m4r", ".mka", ".weba", ".flac", ".ogg", ".opus", ".oga", ".wav",
)  # fmt: skip
"""The extensions :func:`strip_files_generator` picks up: the formats :func:`sashimi.metadata.strip` handles."""
STRIP_ACCEPTS = "JPEG, PNG, WebP, GIF and TIFF images and MKV, WebM, MP4, MOV, M4A, MP3, FLAC, OGG and WAV files"
"""What :func:`strip_files_generator` works on, in words, for its message about a file it skips."""


def strip_files_generator(
    paths: PathInput | Sequence[PathInput],
    mode: Literal["remove_location", "remove_all"] = "remove_all",
    *,
    keep_icc: bool = True,
    recursive: bool = False,
    force: bool = False,
    extensions: Iterable[str] = STRIP_EXTENSIONS,
    on_file_progress: FileProgress | None = None,
    output: PathInput | None = None,
    output_dir: PathInput | None = None,
    dry_run: bool = False,
    limits: Limits | None = None,
    output_name: str | NameTemplate | None = None,
) -> Batch:
    """
    Removes metadata from every file found without re-encoding (see
    :func:`sashimi.metadata.strip`), writing ``{input_name}.stripped.{output_ext}`` in the file's
    own format next to it (``clip.mov.stripped.mov``), under `output_dir`, or to `output` for a
    single file. `output_name` sets the pattern (``{input_name}`` with ``force`` strips files in
    place), as for every batch function (see :mod:`sashimi.api`). The plan's routes are ``image`` and ``media`` (video and audio). `mode` is ``remove_all`` (the default) or ``remove_location``; `keep_icc` False also
    drops images' colour profiles.
    """
    names = naming("strip", source_format, output_name)

    def process(src: Any, dst: Any, progress: Any) -> None:
        strip(src, dst, mode, keep_icc=keep_icc, limits=limits, on_progress=progress)

    return run_batch(
        paths,
        names=names,
        extensions=extensions,
        recursive=recursive,
        force=force,
        accepts=STRIP_ACCEPTS,
        route=lambda path: "image" if kind_of(path) == "image" else "media",
        process=process,
        success_message="Metadata removed: {name}",
        on_file_progress=on_file_progress,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
    )


def strip_files(
    paths: PathInput | Sequence[PathInput],
    mode: Literal["remove_location", "remove_all"] = "remove_all",
    *,
    show_progress: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Like :func:`strip_files_generator`, but runs to completion and returns a summary."""
    gen = strip_files_generator(paths, mode, **kwargs)
    return consume_generator(gen, show_progress, "Removing metadata", "strip", dry_run=kwargs.get("dry_run", False))
