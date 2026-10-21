# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Batch conversion of images, videos and audio, routed by each file's kind and the target format."""

import pathlib
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Literal

from sashimi.api._batch import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    Batch,
    FileProgress,
    PathInput,
    run_batch,
)
from sashimi.api._kinds import kind_of
from sashimi.api._naming import EXTENSIONS, NameTemplate, check_output_ext, naming, output_format
from sashimi.api._consume import consume_generator
from sashimi.config import (
    AUDIO_CONVERT,
    IMAGE_CONVERT,
    VIDEO_CONVERT,
    AudioConvertConfig,
    IcoConfig,
    ImageConvertConfig,
    JpegImageConfig,
    Limits,
    PngImageConfig,
    VideoConvertConfig,
)
from sashimi.core import audio, image, video
from sashimi.config._base import normalize_target
from sashimi.errors import InvalidOptionsError, UnsupportedMediaError

__all__ = [
    "convert_files",
    "convert_files_generator",
    "convert_images",
    "convert_images_generator",
    "to_jpeg",
    "route_for",
    "to_png",
]

Target = Literal["jpeg", "png", "webp", "gif", "bmp", "tiff", "ico", "webm", "mp4", "mp3", "m4a", "wav", "ogg", "flac"]
TARGET_SUFFIX = {
    "jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif", "bmp": ".bmp", "tiff": ".tif", "ico": ".ico",
    "webm": ".webm", "mp4": ".mp4", "mp3": ".mp3", "m4a": ".m4a", "wav": ".wav", "ogg": ".ogg", "flac": ".flac",
}  # fmt: skip
_SAME_FORMAT = {"jpeg": {".jpg", ".jpeg"}, "tiff": {".tif", ".tiff"}, "ogg": {".ogg", ".opus", ".oga"}}
IMAGE_TARGETS = frozenset(IMAGE_CONVERT.targets) | {"ico"}
VIDEO_TARGETS = frozenset(VIDEO_CONVERT.targets)
AUDIO_TARGETS = frozenset(AUDIO_CONVERT.targets)

ROUTE_MESSAGES = {
    "copy": "Converted to {name} (video and audio copied, not re-encoded)",
    "copy_video": "Converted to {name} (video copied, audio re-encoded)",
}
"""Success messages for the MP4 routes that skip a re-encode (see :func:`sashimi.video.plan_mp4_route`)."""

Processor = Callable[[pathlib.Path, pathlib.Path, Callable[[float, str], None] | None], None]


def _kind(path: pathlib.Path) -> str | None:
    return kind_of(path)


def _source_extensions(target: str) -> list[str]:
    """File extensions that can be converted to `target`, excluding files already in that format."""
    candidates: list[str] = []
    if target in IMAGE_TARGETS:
        candidates += IMAGE_EXTENSIONS
    if target in VIDEO_TARGETS:
        candidates += VIDEO_EXTENSIONS
        if target in ("webm", "mp4"):
            candidates.append(".gif")
    if target in AUDIO_TARGETS:
        candidates += VIDEO_EXTENSIONS + AUDIO_EXTENSIONS
    own = _SAME_FORMAT.get(target, {TARGET_SUFFIX[target]})
    return sorted({ext for ext in candidates if ext not in own})


def _accepts(target: str) -> str:
    """What converting to `target` works on, in words."""
    if target in AUDIO_TARGETS:
        return "videos and audio files"
    if target in ("webm", "mp4"):
        return "videos and GIFs"
    if target in VIDEO_TARGETS:
        return "images and videos"
    return "images"


def route_for(target: str, path: pathlib.Path) -> str | None:
    """
    The code that converts `path` to `target`: ``image`` (:func:`sashimi.image.convert`), ``ico``
    (:func:`sashimi.image.to_ico`), ``video`` (:func:`sashimi.video.convert`, animated GIFs to
    MP4 or WebM included) or ``audio`` (:func:`sashimi.audio.convert`, from video or audio), or
    None when it can't be converted.
    """
    kind = kind_of(path)
    if target in AUDIO_TARGETS and kind in ("video", "audio"):
        return "audio"
    if target in VIDEO_TARGETS and (kind == "video" or (kind == "image" and target in ("webm", "mp4"))):
        return "video"
    if target == "ico" and kind == "image":
        return "ico"
    if kind == "image" and target in IMAGE_CONVERT.targets:
        return "image"
    return None


def convert_files_generator(
    paths: PathInput | Sequence[PathInput],
    target: Target,
    *,
    image_config: ImageConvertConfig | None = None,
    ico_config: IcoConfig | None = None,
    video_config: VideoConvertConfig | None = None,
    audio_config: AudioConvertConfig | None = None,
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
    output_ext: str | None = None,
) -> Batch:
    """
    Converts every file found to `target`, writing ``{input_name}.{output_ext}`` next to each
    input (``photo.png`` → ``photo.png.webp``), or under `output_dir`, or to `output` for a
    single file. The extension is `output_ext` if given, else `target` as given (``jpeg`` writes
    ``photo.png.jpeg``, ``jpg`` writes ``photo.png.jpg``). `output_name` sets the pattern (``{input_stem}.{output_ext}`` for
    ``photo.webp``; see :mod:`sashimi.api`).

    Each file is handled by its kind (from its extension; see :func:`route_for`): images go through
    :func:`sashimi.image.convert` (or :func:`~sashimi.image.to_ico`), videos (to GIF, WebP, WebM
    or MP4) and animated GIFs (to WebM or MP4) through :func:`sashimi.video.convert`, and audio targets through
    :func:`sashimi.audio.convert`. Pass a config per kind to set options; its ``target`` must match.
    Kinds without a config use that target's defaults. Files already in the target format aren't
    picked up unless `extensions` says so. `limits` (default ``Limits()``) is passed to each converter.
    """
    typed = str(target).strip()
    target = normalize_target(typed)
    if target not in TARGET_SUFFIX:
        raise InvalidOptionsError(f"Can't convert to {str(target).upper()}.")
    for config in (image_config, video_config, audio_config):
        if config is not None and config.target != target:
            raise InvalidOptionsError(f"These options make {config.target.upper()}, not {target.upper()}.")
    image_cfg = image_config or (IMAGE_CONVERT.from_params(target=target) if target in IMAGE_CONVERT.targets else None)
    video_cfg = video_config or (VIDEO_CONVERT.from_params(target=target) if target in VIDEO_TARGETS else None)
    audio_cfg = audio_config or (AUDIO_CONVERT.from_params(target=target) if target in AUDIO_TARGETS else None)

    def process(src: pathlib.Path, dst: pathlib.Path, progress: Callable[[float, str], None] | None) -> str | None:
        route = route_for(target, src)
        if route == "audio" and audio_cfg:
            audio.convert(src, dst, audio_cfg, max_workers=max_workers, limits=limits, on_progress=progress)
        elif route == "video" and video_cfg:
            route = video.convert(src, dst, video_cfg, max_workers=max_workers, limits=limits, on_progress=progress)
            return ROUTE_MESSAGES.get(route)
        elif route == "ico":
            image.to_ico(src, dst, ico_config, limits=limits, on_progress=progress)
        elif route == "image" and image_cfg:
            image.convert(src, dst, image_cfg, limits=limits, on_progress=progress)
        else:
            raise UnsupportedMediaError(f"Can't convert {src.suffix or 'this file'} to {target.upper()}.")
        return None

    suffix = TARGET_SUFFIX[target]
    own_format = _SAME_FORMAT.get(target, {suffix})
    check_output_ext(output_ext, (target,))
    spelled = output_ext or (typed.lower() if typed.lower() in EXTENSIONS.get(target, ()) else None)
    fmt = output_format(target, typed=spelled)
    names = naming("convert", lambda _: fmt, output_name, fixed=fmt)
    accepts = _accepts(target)

    def reject_reason(path: pathlib.Path) -> str:
        if path.suffix.lower() in own_format:
            return f"Already {target.upper()}"
        return f"Can't convert {path.suffix or 'this file'} to {target.upper()}; converting to {target.upper()} works on {accepts}."

    return run_batch(
        paths,
        names=names,
        extensions=extensions if extensions is not None else _source_extensions(target),
        recursive=recursive,
        force=force,
        process=process,
        success_message="Converted to {name}",
        accepts=accepts,
        route=lambda path: route_for(target, path) or "unsupported",
        on_file_progress=on_file_progress,
        reject_reason=reject_reason,
        already=lambda path: path.suffix.lower() in own_format,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
    )


def convert_files(
    paths: PathInput | Sequence[PathInput],
    target: Target,
    *,
    show_progress: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Like :func:`convert_files_generator`, but runs to completion and returns a summary."""
    return consume_generator(
        convert_files_generator(paths, target, **kwargs),
        show_progress,
        f"Converting to {target.upper()}",
        "convert",
        dry_run=kwargs.get("dry_run", False),
    )


def convert_images_generator(
    paths: PathInput | Sequence[PathInput],
    config: ImageConvertConfig,
    *,
    recursive: bool = False,
    force: bool = False,
    extensions: Iterable[str] | None = None,
    output_dir: PathInput | None = None,
    dry_run: bool = False,
    limits: Limits | None = None,
    output_name: str | NameTemplate | None = None,
) -> Batch:
    """Image-only conversion: every image found is converted to `config.target`."""
    if extensions is None:
        own = _SAME_FORMAT.get(config.target, {TARGET_SUFFIX[config.target]})
        extensions = [ext for ext in IMAGE_EXTENSIONS if ext not in own]
    return convert_files_generator(
        paths,
        config.target,
        output_ext=EXTENSIONS[config.target][0],
        image_config=config,
        recursive=recursive,
        force=force,
        extensions=extensions,
        output_dir=output_dir,
        dry_run=dry_run,
        limits=limits,
        output_name=output_name,
    )


def convert_images(
    paths: PathInput | Sequence[PathInput],
    config: ImageConvertConfig,
    *,
    recursive: bool = False,
    force: bool = False,
    show_progress: bool = True,
    extensions: Iterable[str] | None = None,
    output_dir: PathInput | None = None,
    dry_run: bool = False,
    limits: Limits | None = None,
    output_name: str | NameTemplate | None = None,
) -> dict[str, Any]:
    """Like :func:`convert_images_generator`, but runs to completion and returns a summary."""
    gen = convert_images_generator(
        paths,
        config,
        recursive=recursive,
        force=force,
        extensions=extensions,
        output_dir=output_dir,
        dry_run=dry_run,
        limits=limits,
        output_name=output_name,
    )
    return consume_generator(gen, show_progress, f"Converting to {config.target.upper()}", "convert", dry_run=dry_run)


def to_jpeg(
    paths: PathInput | Sequence[PathInput],
    *,
    recursive: bool = False,
    force: bool = False,
    show_progress: bool = True,
    **options: Any,
) -> dict[str, Any]:
    """Shortcut for ``convert_images(paths, JpegImageConfig.from_params(options))``."""
    config = JpegImageConfig.from_params(options)
    return convert_images(paths, config, recursive=recursive, force=force, show_progress=show_progress)


def to_png(
    paths: PathInput | Sequence[PathInput],
    *,
    recursive: bool = False,
    force: bool = False,
    show_progress: bool = True,
    **options: Any,
) -> dict[str, Any]:
    """Shortcut for ``convert_images(paths, PngImageConfig.from_params(options))``."""
    config = PngImageConfig.from_params(options)
    return convert_images(paths, config, recursive=recursive, force=force, show_progress=show_progress)
