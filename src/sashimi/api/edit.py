# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Batch image editing and frame selection, keeping each image's own format."""

import pathlib
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from sashimi.api._batch import IMAGE_EXTENSIONS, Batch, FileProgress, PathInput, run_batch
from sashimi.api._consume import consume_generator
from sashimi.api._naming import NameTemplate, check_output_ext, naming, output_format
from sashimi.api.resize import source_target
from sashimi.config import FramesConfig, ImageEditConfig, Limits
from sashimi.core import edit as core
from sashimi.errors import InvalidOptionsError

__all__ = ["ANIMATION_EXTENSIONS", "edit_frames", "edit_frames_generator", "edit_images", "edit_images_generator"]

ANIMATION_EXTENSIONS = (".gif", ".webp")
"""The extensions :func:`edit_frames_generator` picks up: animated GIF and WebP."""

EditConfigs = ImageEditConfig | Callable[[str], ImageEditConfig]
FramesConfigs = FramesConfig | Callable[[str], FramesConfig]


def edit_images_generator(
    paths: PathInput | Sequence[PathInput],
    config: EditConfigs,
    *,
    crop_aspect: str | None = None,
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
    Edits every image found (see :func:`sashimi.image.edit`), writing
    ``{input_name}.edited.{output_ext}`` next to it (``photo.jpg.edited.jpg``), under
    `output_dir`, or to `output` for a single image.

    `config` is one edit config for every image, or a function that returns the config for a
    format name; it is called with each image's own format (see
    :func:`~sashimi.api.source_target`), so images keep their format and extension (HEIC/HEIF
    becomes JPEG). `crop_aspect` ("16:9", "1:1", …) crops each image to the largest centred box of
    that shape after rotating, instead of a crop box in the config; giving both raises
    InvalidOptionsError. `output_ext` spells the extension when one format is chosen (``jpeg``
    or ``jpg``), and `output_name` sets the pattern, as for every batch
    function (see :mod:`sashimi.api`).
    """
    if crop_aspect is not None:
        core.aspect_crop(16, 9, crop_aspect)

    def config_for(path: pathlib.Path) -> ImageEditConfig:
        chosen = config(source_target(path)) if callable(config) else config
        if crop_aspect is not None and chosen.crops:
            raise InvalidOptionsError("Give a crop box or a crop shape, not both.")
        return chosen

    if crop_aspect is not None and not callable(config) and config.crops:
        raise InvalidOptionsError("Give a crop box or a crop shape, not both.")

    def process(src: pathlib.Path, dst: pathlib.Path, progress: Callable[[float, str], None] | None) -> None:
        chosen = config_for(src)
        if crop_aspect is not None:
            info = core.frames_info(src, limits=limits)
            width, height = (info.height, info.width) if chosen.rotate in (90, 270) else (info.width, info.height)
            left, top, crop_width, crop_height = core.aspect_crop(width, height, crop_aspect)
            box = {"crop_left": left, "crop_top": top, "crop_width": crop_width, "crop_height": crop_height}
            chosen = type(chosen).model_validate({**chosen.model_dump(), **box})
        core.edit(src, dst, chosen, limits=limits, on_progress=progress)

    fixed = None
    if not callable(config):
        check_output_ext(output_ext, (config.target,))
        fixed = output_format(config.target, typed=output_ext)
    names = naming("edit", lambda p: output_format(config_for(p).target, p, output_ext), output_name, fixed=fixed)
    return run_batch(
        paths,
        names=names,
        extensions=extensions,
        recursive=recursive,
        force=force,
        process=process,
        success_message="Edited: {name}",
        accepts="images and animations",
        route=lambda _: "edit",
        on_file_progress=on_file_progress,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
    )


def edit_images(
    paths: PathInput | Sequence[PathInput],
    config: EditConfigs,
    *,
    show_progress: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Like :func:`edit_images_generator`, but runs to completion and returns a summary."""
    gen = edit_images_generator(paths, config, **kwargs)
    return consume_generator(gen, show_progress, "Editing", "edit", dry_run=kwargs.get("dry_run", False))


def edit_frames_generator(
    paths: PathInput | Sequence[PathInput],
    config: FramesConfigs,
    *,
    drop: Iterable[int] = (),
    recursive: bool = False,
    force: bool = False,
    extensions: Iterable[str] = ANIMATION_EXTENSIONS,
    on_file_progress: FileProgress | None = None,
    output: PathInput | None = None,
    output_dir: PathInput | None = None,
    dry_run: bool = False,
    limits: Limits | None = None,
    output_name: str | NameTemplate | None = None,
) -> Batch:
    """
    Keeps some frames of every animated GIF and WebP found and changes its speed (see
    :func:`sashimi.image.select_frames`), writing ``{input_name}.frames.{output_ext}`` next to
    it (``anim.gif.frames.gif``), under `output_dir`, or to `output` for a single animation.
    Images that aren't animated are skipped.

    `config` is one frames config, or a function from format name (``gif`` or ``webp``) to
    config, so each animation keeps its format. `drop` lists frames (numbered from 0) to leave
    out of each animation, on top of ``config.keep``; frames an animation doesn't have, or
    dropping every frame, are errors for that file. `output_name` sets the pattern, as for
    every batch function (see :mod:`sashimi.api`).
    """
    dropped = frozenset(drop)
    if any(frame < 0 for frame in dropped):
        raise InvalidOptionsError("Frame numbers can't be negative.")

    def config_for(path: pathlib.Path) -> FramesConfig:
        target = "webp" if path.suffix.lower() == ".webp" else "gif"
        return config(target) if callable(config) else config

    def process(src: pathlib.Path, dst: pathlib.Path, progress: Callable[[float, str], None] | None) -> None:
        chosen = config_for(src)
        if dropped:
            total = core.frames_info(src, limits=limits).frames
            if max(dropped) >= total:
                raise InvalidOptionsError(
                    f"Some of the frames to drop aren't in this animation, which has {total} frames."
                )
            keep = [frame for frame in (chosen.keep or range(total)) if frame not in dropped]
            if not keep:
                raise InvalidOptionsError("That would drop every frame; keep at least one.")
            chosen = type(chosen).model_validate({**chosen.model_dump(), "keep": keep})
        core.select_frames(src, dst, chosen, limits=limits, on_progress=progress)

    fixed = None if callable(config) else output_format(config.target)
    names = naming("frames", lambda p: output_format(config_for(p).target, p), output_name, fixed=fixed)
    return run_batch(
        paths,
        names=names,
        extensions=extensions,
        recursive=recursive,
        force=force,
        process=process,
        success_message="Frames saved: {name}",
        accepts="animated GIFs and WebPs",
        route=lambda _: "frames",
        on_file_progress=on_file_progress,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
    )


def edit_frames(
    paths: PathInput | Sequence[PathInput],
    config: FramesConfigs,
    *,
    show_progress: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Like :func:`edit_frames_generator`, but runs to completion and returns a summary."""
    gen = edit_frames_generator(paths, config, **kwargs)
    return consume_generator(gen, show_progress, "Frames", "frames", dry_run=kwargs.get("dry_run", False))
