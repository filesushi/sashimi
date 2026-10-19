# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from sashimi.api._batch import VIDEO_EXTENSIONS, FileProgress, PathInput, YieldType, run_batch
from sashimi.api._consume import consume_generator
from sashimi.config import TransformConfig
from sashimi.core.video import transform

__all__ = ["transform_videos", "transform_videos_generator"]


def transform_videos_generator(
    paths: PathInput | Sequence[PathInput],
    config: TransformConfig,
    *,
    recursive: bool = False,
    force: bool = False,
    suffix: str = "_rotated",
    max_workers: int | None = None,
    extensions: Iterable[str] = VIDEO_EXTENSIONS,
    on_file_progress: FileProgress | None = None,
) -> Iterator[YieldType]:
    """Rotates/flips every video found, writing ``<stem><suffix>.<target>`` next to it."""
    return run_batch(
        paths,
        extensions=extensions,
        recursive=recursive,
        force=force,
        output_for=lambda p: p.with_name(f"{p.stem}{suffix}.{config.target}"),
        process=lambda src, dst, progress: transform(src, dst, config, max_workers=max_workers, on_progress=progress),
        success_message="Saved {name}",
        on_file_progress=on_file_progress,
    )


def transform_videos(
    paths: PathInput | Sequence[PathInput],
    config: TransformConfig,
    *,
    show_progress: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Like :func:`transform_videos_generator`, but runs to completion and returns a summary."""
    return consume_generator(transform_videos_generator(paths, config, **kwargs), show_progress, "Rotating")
