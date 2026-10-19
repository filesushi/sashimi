# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Batch conversion of images, videos and audio, routed by each file's kind and the target format."""

import pathlib
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any, Literal

from sashimi.api._batch import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    FileProgress,
    PathInput,
    YieldType,
    run_batch,
)
from sashimi.api._consume import consume_generator
from sashimi.config import (
    AUDIO_CONVERT,
    IMAGE_CONVERT,
    VIDEO_CONVERT,
    AudioConvertConfig,
    IcoConfig,
    ImageConvertConfig,
    JpegImageConfig,
    PngImageConfig,
    VideoConvertConfig,
)
from sashimi.core import audio, image, video
from sashimi.errors import UnsupportedMediaError

__all__ = [
    "Target",
    "TARGET_SUFFIX",
    "convert_files",
    "convert_files_generator",
    "convert_images",
    "convert_images_generator",
    "to_jpeg",
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

Processor = Callable[[pathlib.Path, pathlib.Path, Callable[[float, str], None] | None], None]


def _kind(path: pathlib.Path) -> str | None:
    suffix = path.suffix.lower()
    for kind, extensions in (("image", IMAGE_EXTENSIONS), ("video", VIDEO_EXTENSIONS), ("audio", AUDIO_EXTENSIONS)):
        if suffix in extensions:
            return kind
    return None


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
) -> Iterator[YieldType]:
    """
    Converts every file found to `target`, writing ``<stem><suffix>`` next to each input.

    Each file is handled by its kind (from its extension): images go through
    :func:`sashimi.image.convert` (or :func:`~sashimi.image.to_ico`), videos (to GIF, WebP, WebM
    or MP4) and animated GIFs (to WebM or MP4) through :func:`sashimi.video.convert`, and audio targets through
    :func:`sashimi.audio.convert`. Pass a config per kind to set options; its ``target`` must match.
    Kinds without a config use that target's defaults. Files already in the target format aren't
    picked up unless `extensions` says so.
    """
    target = {"jpg": "jpeg", "tif": "tiff"}.get(str(target).lower(), str(target).lower())  # type: ignore[assignment]
    if target not in TARGET_SUFFIX:
        raise ValueError(f"Unsupported target format: {target!r}")
    for config in (image_config, video_config, audio_config):
        if config is not None and config.target != target:
            raise ValueError(f"{type(config).__name__} makes {config.target}, not {target}")
    image_cfg = image_config or (IMAGE_CONVERT.from_params(target=target) if target in IMAGE_CONVERT.targets else None)
    video_cfg = video_config or (VIDEO_CONVERT.from_params(target=target) if target in VIDEO_TARGETS else None)
    audio_cfg = audio_config or (AUDIO_CONVERT.from_params(target=target) if target in AUDIO_TARGETS else None)

    def process(src: pathlib.Path, dst: pathlib.Path, progress: Callable[[float, str], None] | None) -> None:
        kind = _kind(src)
        if target in AUDIO_TARGETS and kind in ("video", "audio") and audio_cfg:
            audio.convert(src, dst, audio_cfg, max_workers=max_workers, on_progress=progress)
        elif (
            target in VIDEO_TARGETS
            and (kind == "video" or (kind == "image" and target in ("webm", "mp4")))
            and video_cfg
        ):
            video.convert(src, dst, video_cfg, max_workers=max_workers, on_progress=progress)
        elif target == "ico" and kind == "image":
            image.to_ico(src, dst, ico_config, on_progress=progress)
        elif kind == "image" and image_cfg:
            image.convert(src, dst, image_cfg, on_progress=progress)
        else:
            raise UnsupportedMediaError(f"Can't convert {src.suffix or 'this file'} to {target.upper()}.")

    suffix = TARGET_SUFFIX[target]
    own_format = _SAME_FORMAT.get(target, {suffix})

    def reject_reason(path: pathlib.Path) -> str:
        if path.suffix.lower() in own_format:
            return f"Already {target.upper()}"
        return f"Can't convert {path.suffix or 'this file'} to {target.upper()}"

    return run_batch(
        paths,
        extensions=extensions if extensions is not None else _source_extensions(target),
        recursive=recursive,
        force=force,
        output_for=lambda p: p.with_suffix(suffix),
        process=process,
        success_message="Converted to {name}",
        on_file_progress=on_file_progress,
        reject_reason=reject_reason,
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
        convert_files_generator(paths, target, **kwargs), show_progress, f"Converting to {target.upper()}"
    )


def convert_images_generator(
    paths: PathInput | Sequence[PathInput],
    config: ImageConvertConfig,
    *,
    recursive: bool = False,
    force: bool = False,
    extensions: Iterable[str] | None = None,
) -> Iterator[YieldType]:
    """Image-only conversion: every image found is converted to `config.target`."""
    if extensions is None:
        own = _SAME_FORMAT.get(config.target, {TARGET_SUFFIX[config.target]})
        extensions = [ext for ext in IMAGE_EXTENSIONS if ext not in own]
    return convert_files_generator(
        paths, config.target, image_config=config, recursive=recursive, force=force, extensions=extensions
    )


def convert_images(
    paths: PathInput | Sequence[PathInput],
    config: ImageConvertConfig,
    *,
    recursive: bool = False,
    force: bool = False,
    show_progress: bool = True,
    extensions: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Like :func:`convert_images_generator`, but runs to completion and returns a summary."""
    gen = convert_images_generator(paths, config, recursive=recursive, force=force, extensions=extensions)
    return consume_generator(gen, show_progress, f"Converting to {config.target.upper()}")


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
