# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

from collections.abc import Iterable, Sequence
from typing import Any

from sashimi.api._batch import IMAGE_EXTENSIONS, Batch, FileProgress, PathInput, run_batch
from sashimi.api._naming import NameTemplate, naming, output_format
from sashimi.api._consume import consume_generator
from sashimi.config import FaviconPackConfig, Limits
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
    dry_run: bool = False,
    limits: Limits | None = None,
    output_name: str | NameTemplate | None = None,
) -> Batch:
    """
    A favicon pack (see :func:`sashimi.image.favicon_pack`) for every image found, written as
    ``{input_name}.favicons.{output_ext}`` (``logo.png.favicons.zip``) next to each image, under
    `output_dir`, or to `output` for a single image. `output_name` sets the pattern, as for
    every batch function (see :mod:`sashimi.api`).
    """
    chosen = config or FaviconPackConfig()
    fmt = output_format("zip")
    names = naming("favicon", lambda _: fmt, output_name, fixed=fmt)
    return run_batch(
        paths,
        names=names,
        extensions=extensions,
        recursive=recursive,
        force=force,
        accepts="images",
        route=lambda _: "favicon",
        process=lambda src, dst, progress: favicon_pack(src, dst, chosen, limits=limits, on_progress=progress),
        success_message="Favicon pack created: {name}",
        on_file_progress=on_file_progress,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
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
    return consume_generator(gen, show_progress, "Favicon packs", "favicon", dry_run=kwargs.get("dry_run", False))
