# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Batch rotation and flipping: photos and animations through the image editor, videos losslessly where they can be."""

import os
import pathlib
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from sashimi.api._batch import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, Batch, FileProgress, PathInput
from sashimi.api._batch import run_batch
from sashimi.api._consume import consume_generator
from sashimi.api._kinds import kind_of, unsupported
from sashimi.api._naming import EXTENSIONS, NameTemplate, OutputFormat, check_output_ext, naming, output_format
from sashimi.api.resize import source_target
from sashimi.config import (
    IMAGE_EDIT,
    VIDEO_TRANSFORM,
    ImageEditConfig,
    Limits,
    MovTransformConfig,
    Mp4TransformConfig,
    TransformConfig,
)
from sashimi.config._base import normalize_target
from sashimi.core import edit as image_edit
from sashimi.core.probe import probe
from sashimi.core.video import rotation_container, transform
from sashimi.errors import InvalidOptionsError, SashimiError

__all__ = [
    "ROTATE_EXTENSIONS",
    "rotate_files",
    "rotate_files_generator",
    "transform_videos",
    "transform_videos_generator",
]

ROTATE_EXTENSIONS = IMAGE_EXTENSIONS + VIDEO_EXTENSIONS
"""The extensions :func:`rotate_files_generator` picks up: images (animated GIFs and WebPs included) and videos."""

ROTATE_ACCEPTS = "images, animations and videos"

MOV_EXTENSIONS = frozenset({".mov"})
"""Videos :func:`rotate_files_generator` writes as MOV by default (see :func:`sashimi.video.rotation_container`)."""


def rotate_files_generator(
    paths: PathInput | Sequence[PathInput],
    rotate: int = 90,
    flip: str = "none",
    *,
    output_format: str | None = None,
    image_config: ImageEditConfig | Callable[[str], ImageEditConfig] | None = None,
    video_config: TransformConfig | None = None,
    recursive: bool = False,
    force: bool = False,
    max_workers: int | None = None,
    extensions: Iterable[str] | None = None,
    on_file_progress: FileProgress | None = None,
    output: PathInput | None = None,
    output_dir: PathInput | None = None,
    dry_run: bool = False,
    limits: Limits | None = None,
    output_name: str | NameTemplate | None = None,
) -> Batch:
    """
    Rotates (clockwise, by `rotate` degrees) and/or flips every photo, animation and video
    found, routed by its kind: images through :func:`sashimi.image.edit`, the same code as
    :func:`~sashimi.api.edit_images` with ``rotate`` (every frame of an animated GIF or WebP),
    and videos through :func:`sashimi.video.transform`, re-encoded. Each output is
    ``{input_name}.rotated.{output_ext}`` next to its input (``photo.jpg.rotated.jpg``,
    ``clip.mkv.rotated.mp4``, ``clip.mov.rotated.mov``), under `output_dir`, or `output` for a single file.

    `output_format` chooses the format, and with it the files taken: an image format (``png``,
    ``jpg``, …) rotates images only, saved in it (``photo.jpg.rotated.png``, without the loss of
    re-saving a JPEG), and a video format (``mp4``, ``webm``, ``mov``) rotates videos only. It is
    the extension as given (``jpeg`` or ``jpg``). Without it, images keep their own format (HEIC
    becomes JPEG, saved at high quality), MOV videos stay MOV (a lossless rotation then keeps
    every stream MOV holds, PCM sound included) unless MP4 keeps more of them (Dolby Vision
    settings, which FFmpeg writes only into MP4: :func:`sashimi.video.rotation_container`), and
    other videos become MP4; an MP4 `video_config` gives its options to MOV videos too.

    `image_config` (one config, or a function from format name to config) and `video_config` set
    the save and encoding options. Their ``rotate`` and ``flip`` must match, and an image config
    can't crop or change colours (that's :func:`~sashimi.api.edit_images`). `output_name` is
    as for every batch function (see :mod:`sashimi.api`).
    The plan's routes are ``edit`` and ``transform``, so a folder holding both is "mixed".
    """
    base = VIDEO_TRANSFORM.from_params(target="mp4", rotate=rotate, flip=flip)
    turn, mirror = base.rotate, base.flip
    typed = output_format.strip() if output_format is not None else None
    fmt = normalize_target(typed) if typed is not None else None
    if fmt is not None and fmt not in IMAGE_EDIT.targets and fmt not in VIDEO_TRANSFORM.targets:
        images = ", ".join(IMAGE_EDIT.targets)
        raise InvalidOptionsError(
            f"rotate can't save {fmt.upper()}; choose an image format ({images}), mp4, webm or mov."
        )
    only = "image" if fmt in IMAGE_EDIT.targets else "video" if fmt in VIDEO_TRANSFORM.targets else None
    if fmt is not None:
        check_output_ext(typed, (fmt,))

    def matches(config: ImageEditConfig | TransformConfig, what: str) -> None:
        if (config.rotate, config.flip) != (turn, mirror):
            raise InvalidOptionsError(
                f"The {what} options rotate by {config.rotate}° and flip {config.flip}, not {turn}° and {mirror}."
            )
        if fmt is not None and only == what and config.target != fmt:
            raise InvalidOptionsError(f"The {what} options make {config.target.upper()}, not {fmt.upper()}.")

    if video_config is not None:
        matches(video_config, "video")
    video_cfg = video_config or VIDEO_TRANSFORM.from_params(
        target=fmt if only == "video" else "mp4", rotate=turn, flip=mirror
    )
    as_mov = (
        MovTransformConfig.model_validate(video_cfg.model_dump(exclude={"target"}))
        if fmt is None and isinstance(video_cfg, Mp4TransformConfig)
        else None
    )

    chosen: dict[str, TransformConfig] = {}

    def video_config_for(path: pathlib.Path) -> TransformConfig:
        if as_mov is None or path.suffix.lower() not in MOV_EXTENSIONS:
            return video_cfg
        key = os.path.abspath(path)
        if key not in chosen:
            assert isinstance(video_cfg, Mp4TransformConfig)
            try:
                kept = rotation_container(probe(path, limits=limits), video_cfg, as_mov)
            except SashimiError:
                kept = "mov"
            chosen[key] = as_mov if kept == "mov" else video_cfg
        return chosen[key]

    image_cfgs: dict[str, ImageEditConfig] = {}

    def image_config_for(path: pathlib.Path) -> ImageEditConfig:
        target = str(fmt) if only == "image" else source_target(path)
        if target not in image_cfgs:
            if image_config is None:
                chosen = IMAGE_EDIT.from_params(target=target, rotate=turn, flip=mirror)
            else:
                chosen = image_config(target) if callable(image_config) else image_config
                matches(chosen, "image")
                if chosen.crops or chosen.adjusts:
                    raise InvalidOptionsError("rotate only rotates and flips; crop and adjust colours with edit.")
            image_cfgs[target] = chosen
        return image_cfgs[target]

    if image_config is not None and not callable(image_config):
        image_config_for(pathlib.Path("image.png"))

    def route(path: pathlib.Path) -> str:
        return "edit" if kind_of(path) == "image" else "transform"

    def target_for(path: pathlib.Path) -> str:
        return image_config_for(path).target if route(path) == "edit" else video_config_for(path).target

    def process(src: pathlib.Path, dst: pathlib.Path, progress: Callable[[float, str], None] | None) -> Any:
        if route(src) == "edit":
            return image_edit.edit(src, dst, image_config_for(src), limits=limits, on_progress=progress)
        return transform(src, dst, video_config_for(src), max_workers=max_workers, limits=limits, on_progress=progress)

    def reject_reason(path: pathlib.Path) -> str:
        kind = kind_of(path)
        if only is not None and kind in ("image", "video") and kind != only:
            what = "an image format" if only == "image" else "a video format"
            return f"{str(fmt).upper()} is {what}, so only {only}s are rotated; rotate {kind}s in another run."
        return unsupported("rotate", ROTATE_ACCEPTS, path)

    if extensions is None:
        extensions = IMAGE_EXTENSIONS if only == "image" else VIDEO_EXTENSIONS if only == "video" else ROTATE_EXTENSIONS
    fixed = _spelled(fmt, typed) if fmt is not None else None
    names = naming("rotate", lambda p: _spelled(target_for(p), typed, p), output_name, fixed=fixed)
    return run_batch(
        paths,
        names=names,
        extensions=extensions,
        recursive=recursive,
        force=force,
        process=process,
        success_message=_done(turn, mirror),
        accepts=ROTATE_ACCEPTS,
        route=route,
        on_file_progress=on_file_progress,
        reject_reason=reject_reason,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
    )


def _done(turn: int, mirror: str) -> str:
    """The success message for a rotation and/or flip."""
    if not turn % 360:
        return "Flipped to {name}"
    return "Rotated to {name}" if mirror == "none" else "Rotated and flipped to {name}"


def _spelled(target: str, typed: str | None, source: pathlib.Path | None = None) -> OutputFormat:
    """The output format for `target`, spelled `typed` when that names it (``jpeg`` or ``jpg``)."""
    spelled = typed if typed is not None and typed.lower() in EXTENSIONS.get(target, (target,)) else None
    return output_format(target, source, spelled)


def rotate_files(
    paths: PathInput | Sequence[PathInput],
    rotate: int = 90,
    flip: str = "none",
    *,
    show_progress: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Like :func:`rotate_files_generator`, but runs to completion and returns a summary."""
    return consume_generator(
        rotate_files_generator(paths, rotate, flip, **kwargs),
        show_progress,
        "Rotating",
        "rotate",
        dry_run=kwargs.get("dry_run", False),
    )


def transform_videos_generator(
    paths: PathInput | Sequence[PathInput],
    config: TransformConfig,
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
    Rotates/flips every video found, and only videos (:func:`rotate_files_generator` takes
    images too), writing ``{input_name}.rotated.{output_ext}`` next to it (``a.mkv.rotated.mp4``),
    under `output_dir`, or to `output` for a single video. `output_name` is as for
    every batch function (see :mod:`sashimi.api`).
    """
    fmt = output_format(config.target)
    names = naming("rotate", lambda p: output_format(config.target, p), output_name, fixed=fmt)
    return run_batch(
        paths,
        names=names,
        extensions=extensions,
        recursive=recursive,
        force=force,
        process=lambda src, dst, progress: transform(
            src, dst, config, max_workers=max_workers, limits=limits, on_progress=progress
        ),
        success_message=_done(config.rotate, config.flip),
        accepts="videos",
        route=lambda _: "transform",
        on_file_progress=on_file_progress,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
    )


def transform_videos(
    paths: PathInput | Sequence[PathInput],
    config: TransformConfig,
    *,
    show_progress: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Like :func:`transform_videos_generator`, but runs to completion and returns a summary."""
    return consume_generator(
        transform_videos_generator(paths, config, **kwargs),
        show_progress,
        "Rotating",
        "rotate",
        dry_run=kwargs.get("dry_run", False),
    )
