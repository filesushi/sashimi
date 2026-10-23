# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import os
from collections.abc import Iterable, Sequence
from typing import Any

from sashimi.api._batch import VIDEO_EXTENSIONS, Batch, FileProgress, PathInput, run_batch
from sashimi.api._naming import NameTemplate, check_output_ext, naming, output_format
from sashimi.api._consume import consume_generator
from sashimi.config import JpegSheetConfig, Limits, SheetConfig
from sashimi.core.sheets import render

__all__ = ["make_thumbs", "make_thumbs_generator"]

PathLike = str | os.PathLike[str]


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
    output_name: str | NameTemplate | None = None,
    output_ext: str | None = None,
) -> Batch:
    """
    A contact sheet for every video found, still or animated as `config` says (a JPEG sheet by
    default), written as ``{input_name}.sheet.{output_ext}`` (``clip.mp4.sheet.jpg``;
    ``clip.mp4.animated-sheet.webp`` when animated) next to each video, under `output_dir`, or
    to `output` for a single video. `output_ext` spells the extension (``jpeg`` or ``jpg``),
    `output_name` sets the pattern, for example ``"{input_name}.jpeg"`` for ``clip.mp4.jpeg``,
    as for every batch function (see :mod:`sashimi.api`). The font and emoji arguments are passed to :func:`sashimi.sheets.render`.
    """
    sheet = config or JpegSheetConfig()
    check_output_ext(output_ext, (sheet.target,))
    fmt = output_format(sheet.target, typed=output_ext)
    names = naming("animated_thumbs" if sheet.animated else "thumbs", lambda _: fmt, output_name, fixed=fmt)
    return run_batch(
        paths,
        names=names,
        extensions=extensions,
        recursive=recursive,
        force=force,
        accepts="videos",
        route=lambda _: "sheet",
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
