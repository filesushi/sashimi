# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pathlib
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from sashimi.api._batch import IMAGE_EXTENSIONS, Batch, FileProgress, PathInput, run_batch
from sashimi.api._consume import consume_generator
from sashimi.api._kinds import kind_of, unsupported
from sashimi.api._naming import NameTemplate, check_output_ext, naming, output_format
from sashimi.config import ImageResizeConfig, Limits
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
    ".jfif": "jpeg",
    ".jpe": "jpeg",
    ".jif": "jpeg",
    ".apng": "apng",
    ".avif": "avif",
    ".ico": "png",
    ".tga": "png",
    ".psd": "png",
}


def source_target(path: pathlib.Path) -> str:
    """
    The format that keeps an image's own, judged by its extension, or comes closest: HEIC/HEIF
    become JPEG; ICO, TGA and PSD become PNG. A ``.png`` that is an animated PNG (it has an
    ``acTL`` chunk before its picture data) keeps ``apng``, so it stays animated.
    """
    target = _TARGET_FOR_SUFFIX.get(path.suffix.lower(), "jpeg")
    if target == "png" and _animated_png(path):
        return "apng"
    return target


def _animated_png(path: pathlib.Path) -> bool:
    """True when the PNG file at `path` declares an animation (``acTL`` before ``IDAT``); reads only its first chunks."""
    try:
        with open(path, "rb") as f:
            head = f.read(1 << 16)
    except OSError:
        return False
    found = head.find(b"acTL")
    return found > 0 and (head.find(b"IDAT") < 0 or found < head.find(b"IDAT"))


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
    limits: Limits | None = None,
    output_name: str | NameTemplate | None = None,
    output_ext: str | None = None,
) -> Batch:
    """
    Shrinks every image found, writing ``{input_name}.resized.{output_ext}`` next to it
    (``a.png.resized.png``; ``a.png.resized.webp`` in WebP), under `output_dir`, or to `output`
    for a single image. `output_ext` spells the extension when a format is chosen (``jpeg`` or
    ``jpg``), and `output_name` sets the pattern, as for every batch
    function (see :mod:`sashimi.api`).

    `config` is either one resize config for every image, or a function that returns the config
    for a format name. The function is called with each image's own format (see
    :func:`source_target`), so images keep their format and extension; HEIC/HEIF becomes JPEG.
    """

    def config_for(path: pathlib.Path) -> ImageResizeConfig:
        return config(source_target(path)) if callable(config) else config

    fixed = None
    if not callable(config):
        check_output_ext(output_ext, (config.target,))
        fixed = output_format(config.target, typed=output_ext)
    names = naming("resize", lambda p: output_format(config_for(p).target, p, output_ext), output_name, fixed=fixed)

    def process(src: pathlib.Path, dst: pathlib.Path, progress: Callable[[float, str], None] | None) -> Any:
        chosen = config_for(src)
        return convert(src, dst, chosen, limits=limits, on_progress=progress)

    def reject_reason(path: pathlib.Path) -> str:
        if kind_of(path) == "video":
            return "resize works on images and animations; videos aren't supported yet."
        return unsupported("resize", "images and animations", path)

    return run_batch(
        paths,
        names=names,
        extensions=extensions,
        recursive=recursive,
        force=force,
        process=process,
        success_message="Resized to {name}",
        accepts="images and animations",
        route=lambda _: "image",
        on_file_progress=on_file_progress,
        reject_reason=reject_reason,
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
