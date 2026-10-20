# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pathlib
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any

from sashimi.api._batch import IMAGE_EXTENSIONS, FileProgress, PathInput, YieldType, output_name, run_batch
from sashimi.api._consume import consume_generator
from sashimi.api.convert import _SAME_FORMAT, TARGET_SUFFIX
from sashimi.config import ImageResizeConfig
from sashimi.core.image import convert

__all__ = ["resize_images", "resize_images_generator", "source_target"]

_TARGET_FOR_SUFFIX = {
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
    ".webp": "webp",
    ".gif": "gif",
    ".bmp": "bmp",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".heic": "jpeg",
    ".heif": "jpeg",
}


def source_target(path: pathlib.Path) -> str:
    """The format that keeps an image's own, judged by its extension (HEIC/HEIF becomes JPEG)."""
    return _TARGET_FOR_SUFFIX.get(path.suffix.lower(), "jpeg")


def resize_images_generator(
    paths: PathInput | Sequence[PathInput],
    config: ImageResizeConfig | Callable[[str], ImageResizeConfig],
    *,
    recursive: bool = False,
    force: bool = False,
    extensions: Iterable[str] = IMAGE_EXTENSIONS,
    on_file_progress: FileProgress | None = None,
    output: PathInput | None = None,
    output_dir: PathInput | None = None,
    dry_run: bool = False,
) -> Iterator[YieldType]:
    """
    Shrinks every image found, writing ``<stem>.resized.<ext>`` next to it, under `output_dir`, or
    to `output` for a single image.

    `config` is either one resize config for every image, or a function that returns the config
    for a format name. The function is called with each image's own format (see
    :func:`source_target`), so images keep their format and extension; HEIC/HEIF becomes JPEG.
    """

    def config_for(path: pathlib.Path) -> ImageResizeConfig:
        return config(source_target(path)) if callable(config) else config

    def name_for(p: pathlib.Path, stem: str) -> str:
        target = config_for(p).target
        ext = (
            p.suffix if p.suffix.lower() in _SAME_FORMAT.get(target, {TARGET_SUFFIX[target]}) else TARGET_SUFFIX[target]
        )
        return output_name(stem, ext, "resized")

    return run_batch(
        paths,
        extensions=extensions,
        recursive=recursive,
        force=force,
        name_for=name_for,
        process=lambda src, dst, progress: convert(src, dst, config_for(src), on_progress=progress),
        success_message="Resized to {name}",
        on_file_progress=on_file_progress,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
    )


def resize_images(
    paths: PathInput | Sequence[PathInput],
    config: ImageResizeConfig | Callable[[str], ImageResizeConfig],
    *,
    show_progress: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Like :func:`resize_images_generator`, but runs to completion and returns a summary."""
    return consume_generator(
        resize_images_generator(paths, config, **kwargs),
        show_progress,
        "Resizing",
        "resize",
        dry_run=kwargs.get("dry_run", False),
    )
