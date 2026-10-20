# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from sashimi.api._batch import IMAGE_EXTENSIONS, FileProgress, PathInput, YieldType, output_name, run_batch
from sashimi.api._consume import consume_generator
from sashimi.config import FaviconPackConfig
from sashimi.core.image import favicon_pack

__all__ = ["make_favicon_packs", "make_favicon_packs_generator"]


def make_favicon_packs_generator(
    paths: PathInput | Sequence[PathInput],
    config: FaviconPackConfig | None = None,
    *,
    recursive: bool = False,
    force: bool = False,
    extensions: Iterable[str] = IMAGE_EXTENSIONS,
    on_file_progress: FileProgress | None = None,
    output: PathInput | None = None,
    output_dir: PathInput | None = None,
) -> Iterator[YieldType]:
    """
    A favicon pack (see :func:`sashimi.image.favicon_pack`) for every image found, written as
    ``<stem>.favicons.zip`` next to each image, under `output_dir`, or to `output` for a single image.
    """
    chosen = config or FaviconPackConfig()
    return run_batch(
        paths,
        extensions=extensions,
        recursive=recursive,
        force=force,
        name_for=lambda _, stem: output_name(stem, "zip", "favicons"),
        process=lambda src, dst, progress: favicon_pack(src, dst, chosen, on_progress=progress),
        success_message="Favicon pack created: {name}",
        on_file_progress=on_file_progress,
        output=output,
        output_dir=output_dir,
    )


def make_favicon_packs(
    paths: PathInput | Sequence[PathInput],
    config: FaviconPackConfig | None = None,
    *,
    show_progress: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Like :func:`make_favicon_packs_generator`, but runs to completion and returns a summary."""
    gen = make_favicon_packs_generator(paths, config, **kwargs)
    return consume_generator(gen, show_progress, "Favicon packs", "favicon")
