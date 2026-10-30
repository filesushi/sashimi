# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pathlib
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Literal

from sashimi.api._batch import VIDEO_EXTENSIONS, Batch, FileProgress, PathInput, run_batch
from sashimi.api._kinds import unsupported
from sashimi.api._naming import NameTemplate, naming, output_format
from sashimi.api._consume import consume_generator
from sashimi.config import CopyTrimConfig, Limits, MetadataMode
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
    metadata: MetadataMode = "keep",
    recursive: bool = False,
    force: bool = False,
    extensions: Iterable[str] | None = None,
    on_file_progress: FileProgress | None = None,
    output: PathInput | None = None,
    output_dir: PathInput | None = None,
    dry_run: bool = False,
    limits: Limits | None = None,
    output_name: str | NameTemplate | None = None,
) -> Batch:
    """
    Copies every video found into `container` without re-encoding (a whole-file
    :func:`sashimi.video.trim` with a :class:`~sashimi.config.CopyTrimConfig` and no end), writing
    ``{input_name}.{output_ext}`` next to each video (``clip.mp4`` → ``clip.mp4.mkv``), under
    `output_dir`, or to `output` for a single video. `output_name` sets the pattern
    (``{input_stem}.{output_ext}`` for ``clip.mkv``), as for every batch function (see
    :mod:`sashimi.api`). Every stream is kept (but timed metadata tracks when `metadata` removes anything, and
    cover art for ``remove_all``); text subtitles the container can't hold are converted, and data
    tracks it can't hold (a timecode, a GoPro's GPS, Apple's metadata) are left out, which the
    success message says. Videos whose video, audio or subtitles the container can't hold are
    reported as errors before anything is written.
    Sources are never changed or removed. Files already in that container aren't picked up.
    `metadata` keeps the source's tags (the default) or removes location tags or all of them.
    """
    config = CopyTrimConfig.from_params(container=container, metadata=metadata)
    own = CONTAINER_EXTENSIONS[config.container]
    label = config.container.upper()
    fmt = output_format(config.container)
    names = naming("remux", lambda _: fmt, output_name, fixed=fmt)

    def process(src: pathlib.Path, dst: pathlib.Path, progress: Callable[[float, str], None] | None) -> Any:
        return trim(src, dst, config, limits=limits, on_progress=progress)

    def reject_reason(path: pathlib.Path) -> str:
        return f"Already {label}" if path.suffix.lower() in own else unsupported("remux", "videos", path)

    return run_batch(
        paths,
        names=names,
        extensions=extensions if extensions is not None else [ext for ext in VIDEO_EXTENSIONS if ext not in own],
        recursive=recursive,
        force=force,
        process=process,
        success_message="Remuxed to {name}",
        accepts="videos",
        route=lambda _: "trim",
        on_file_progress=on_file_progress,
        reject_reason=reject_reason,
        already=lambda path: path.suffix.lower() in own,
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
