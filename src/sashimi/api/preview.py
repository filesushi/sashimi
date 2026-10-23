# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

from collections.abc import Iterable, Sequence
from typing import Any

from sashimi.api._batch import VIDEO_EXTENSIONS, Batch, FileProgress, PathInput, run_batch
from sashimi.api._naming import NameTemplate, naming, output_format
from sashimi.api._consume import consume_generator
from sashimi.config import Limits, PreviewConfig, WebpPreviewConfig
from sashimi.core.preview import preview

__all__ = ["make_previews", "make_previews_generator"]


def make_previews_generator(
    paths: PathInput | Sequence[PathInput],
    config: PreviewConfig | None = None,
    *,
    recursive: bool = False,
    force: bool = False,
    max_workers: int | None = None,
    extensions: Iterable[str] = VIDEO_EXTENSIONS,
    on_file_progress: FileProgress | None = None,
    output: PathInput | None = None,
    output_dir: PathInput | None = None,
    dry_run: bool = False,
    limits: Limits | None = None,
    output_name: str | NameTemplate | None = None,
) -> Batch:
    """
    A preview (see :func:`sashimi.video.preview`) of every video found, an animated WebP by
    default, written as ``{input_name}.preview.{output_ext}`` (``clip.mp4.preview.webp``) next
    to each video, under `output_dir`, or to `output` for a single video. `output_name` sets the
    pattern, as for every batch function (see :mod:`sashimi.api`).
    """
    chosen = config or WebpPreviewConfig()
    fmt = output_format(chosen.target)
    names = naming("preview", lambda _: fmt, output_name, fixed=fmt)
    return run_batch(
        paths,
        names=names,
        extensions=extensions,
        recursive=recursive,
        force=force,
        accepts="videos",
        route=lambda _: "preview",
        process=lambda src, dst, progress: preview(
            src, dst, chosen, max_workers=max_workers, limits=limits, on_progress=progress
        ),
        success_message="Preview created: {name}",
        on_file_progress=on_file_progress,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
    )


def make_previews(
    paths: PathInput | Sequence[PathInput],
    config: PreviewConfig | None = None,
    *,
    show_progress: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Like :func:`make_previews_generator`, but runs to completion and returns a summary."""
    return consume_generator(
        make_previews_generator(paths, config, **kwargs),
        show_progress,
        "Previews",
        "preview",
        dry_run=kwargs.get("dry_run", False),
    )
