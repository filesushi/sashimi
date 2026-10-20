# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import os
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from sashimi.api._batch import VIDEO_EXTENSIONS, FileProgress, PathInput, YieldType, output_name, run_batch
from sashimi.api._consume import consume_generator
from sashimi.config import JpegSheetConfig, Limits, SheetConfig
from sashimi.core.sheets import render

__all__ = ["make_thumbs", "make_thumbs_generator", "sheet_name"]

PathLike = str | os.PathLike[str]
_EXTENSION = {"jpeg": "jpg", "png": "png", "webp": "webp", "gif": "gif"}


def sheet_name(stem: str, config: SheetConfig) -> str:
    """``<stem>.sheet.<ext>`` for a still sheet, ``<stem>.animated-sheet.<ext>`` for an animated one."""
    return output_name(stem, _EXTENSION[config.target], "animated-sheet" if config.animated else "sheet")


def make_thumbs_generator(
    paths: PathInput | Sequence[PathInput],
    config: SheetConfig | None = None,
    *,
    recursive: bool = False,
    force: bool = False,
    max_workers: int | None = None,
    extensions: Iterable[str] = VIDEO_EXTENSIONS,
    header_font: PathLike | None = None,
    timestamp_font: PathLike | None = None,
    emoji_dir: PathLike | None = None,
    on_file_progress: FileProgress | None = None,
    output: PathInput | None = None,
    output_dir: PathInput | None = None,
    dry_run: bool = False,
    limits: Limits | None = None,
) -> Iterator[YieldType]:
    """
    A contact sheet for every video found, still or animated as `config` says (a JPEG sheet by
    default), written as ``<stem>.sheet.<ext>`` (``<stem>.animated-sheet.<ext>`` when animated)
    next to each video, under `output_dir`, or to `output` for a single video. The font and emoji
    arguments are passed to :func:`sashimi.sheets.render`.
    """
    sheet = config or JpegSheetConfig()
    return run_batch(
        paths,
        extensions=extensions,
        recursive=recursive,
        force=force,
        name_for=lambda _, stem: sheet_name(stem, sheet),
        process=lambda src, dst, progress: render(
            src,
            dst,
            sheet,
            header_font=header_font,
            timestamp_font=timestamp_font,
            emoji_dir=emoji_dir,
            max_workers=max_workers,
            limits=limits,
            on_progress=progress,
        ),
        on_file_progress=on_file_progress,
        success_message=f"{'Animated sheet' if sheet.animated else 'Contact sheet'} created: {{name}}",
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
    )


def make_thumbs(
    paths: PathInput | Sequence[PathInput],
    config: SheetConfig | None = None,
    *,
    show_progress: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Like :func:`make_thumbs_generator`, but runs to completion and returns a summary."""
    sheet = config or JpegSheetConfig()
    desc = "Making animated sheets" if sheet.animated else "Making contact sheets"
    return consume_generator(
        make_thumbs_generator(paths, sheet, **kwargs),
        show_progress,
        desc,
        "thumbs",
        dry_run=kwargs.get("dry_run", False),
    )
