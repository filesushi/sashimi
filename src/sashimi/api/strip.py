# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Batch metadata removal, without re-encoding."""

from collections.abc import Iterable, Iterator, Sequence
from typing import Any, Literal

from sashimi.api._batch import FileProgress, PathInput, YieldType, output_name, run_batch
from sashimi.api._consume import consume_generator
from sashimi.config import Limits
from sashimi.core.metadata import strip

__all__ = ["STRIP_EXTENSIONS", "strip_files", "strip_files_generator"]

STRIP_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff",
    ".mp4", ".m4v", ".mov", ".mkv", ".webm",
    ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".oga", ".wav",
)  # fmt: skip
"""The extensions :func:`strip_files_generator` picks up: the formats :func:`sashimi.metadata.strip` handles."""


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
) -> Iterator[YieldType]:
    """
    Removes metadata from every file found without re-encoding (see
    :func:`sashimi.metadata.strip`), writing ``<stem>.stripped.<ext>`` in the file's own format
    next to it, under `output_dir`, or to `output` for a single file. `mode` is ``remove_all``
    (the default) or ``remove_location``; `keep_icc` False also drops images' colour profiles.
    """

    def process(src: Any, dst: Any, progress: Any) -> None:
        strip(src, dst, mode, keep_icc=keep_icc, limits=limits, on_progress=progress)

    return run_batch(
        paths,
        extensions=extensions,
        recursive=recursive,
        force=force,
        name_for=lambda path, stem: output_name(stem, path.suffix or ".bin", "stripped"),
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
