# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pathlib
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, Literal

from sashimi.api._batch import VIDEO_EXTENSIONS, FileProgress, PathInput, YieldType, output_name, run_batch
from sashimi.api._consume import consume_generator
from sashimi.config import CopyTrimConfig
from sashimi.core.video import trim

__all__ = ["remux_files", "remux_files_generator"]

Container = Literal["mkv", "mp4", "webm", "mov"]
CONTAINER_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "mkv": (".mkv",),
    "mp4": (".mp4", ".m4v"),
    "webm": (".webm",),
    "mov": (".mov",),
}


def remux_files_generator(
    paths: PathInput | Sequence[PathInput],
    container: Container = "mkv",
    *,
    recursive: bool = False,
    force: bool = False,
    extensions: Iterable[str] | None = None,
    on_file_progress: FileProgress | None = None,
    output: PathInput | None = None,
    output_dir: PathInput | None = None,
    dry_run: bool = False,
) -> Iterator[YieldType]:
    """
    Copies every video found into `container` without re-encoding (a whole-file
    :func:`sashimi.video.trim` with a :class:`~sashimi.config.CopyTrimConfig` and no end), writing
    ``<stem>.<container>`` next to each video, under `output_dir`, or to `output` for a single
    video. Every stream is kept; text subtitles the container can't hold are converted. Videos
    whose streams the container can't hold are reported as errors before anything is written.
    Sources are never changed or removed. Files already in that container aren't picked up.
    """
    config = CopyTrimConfig.from_params(container=container)
    own = CONTAINER_EXTENSIONS[config.container]
    label = config.container.upper()

    def reject_reason(path: pathlib.Path) -> str:
        return f"Already {label}" if path.suffix.lower() in own else f"Can't remux {path.suffix or 'this file'}"

    return run_batch(
        paths,
        extensions=extensions if extensions is not None else [ext for ext in VIDEO_EXTENSIONS if ext not in own],
        recursive=recursive,
        force=force,
        name_for=lambda _, stem: output_name(stem, own[0]),
        process=lambda src, dst, progress: trim(src, dst, config, on_progress=progress),
        success_message="Remuxed to {name}",
        on_file_progress=on_file_progress,
        reject_reason=reject_reason,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
    )


def remux_files(
    paths: PathInput | Sequence[PathInput],
    container: Container = "mkv",
    *,
    show_progress: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Like :func:`remux_files_generator`, but runs to completion and returns a summary."""
    gen = remux_files_generator(paths, container, **kwargs)
    return consume_generator(
        gen, show_progress, f"Remuxing to {container.upper()}", "remux", dry_run=kwargs.get("dry_run", False)
    )
