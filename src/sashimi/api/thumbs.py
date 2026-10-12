import os
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from sashimi.api._batch import VIDEO_EXTENSIONS, FileProgress, PathInput, YieldType, run_batch
from sashimi.api._consume import consume_generator
from sashimi.config import JpegSheetConfig, SheetConfig
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
) -> Iterator[YieldType]:
    """
    A contact sheet for every video found, still or animated as `config` says (a JPEG sheet by
    default); each is written next to its video as ``<name>.<target>``. The font and emoji
    arguments are passed to :func:`sashimi.sheets.render`.
    """
    sheet = config or JpegSheetConfig()
    return run_batch(
        paths,
        extensions=extensions,
        recursive=recursive,
        force=force,
        output_for=lambda p: p.with_name(f"{p.name}.{sheet.target}"),
        process=lambda src, dst, progress: render(
            src,
            dst,
            sheet,
            header_font=header_font,
            timestamp_font=timestamp_font,
            emoji_dir=emoji_dir,
            max_workers=max_workers,
            on_progress=progress,
        ),
        on_file_progress=on_file_progress,
        success_message=f"{'Animated sheet' if sheet.animated else 'Contact sheet'} created: {{name}}",
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
    desc = "Generating Animated Thumbs" if sheet.animated else "Generating Static Thumbs"
    return consume_generator(make_thumbs_generator(paths, sheet, **kwargs), show_progress, desc)
