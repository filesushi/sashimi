# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Batch conversion of images, videos and audio, routed by each file's kind and the target format."""

import pathlib
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Literal, get_args

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
from sashimi.api._naming import ALSO_NAMED, EXTENSIONS, NameTemplate, check_output_ext, naming, output_format
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
from sashimi.core.probe import probe
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

Target = Literal[
    "jpeg",
    "png",
    "webp",
    "gif",
    "bmp",
    "tiff",
    "avif",
    "apng",
    "ico",
    "webm",
    "mp4",
    "mp3",
    "m4a",
    "wav",
    "ogg",
    "flac",
]
TARGET_SUFFIX = {target: f".{EXTENSIONS[target][0]}" for target in get_args(Target)}
_STILL_CONVERTED = {"mp4": frozenset({".m4v"}), "apng": frozenset({".png"})}
"""
Extensions of a target's format that converting to it still takes: an .m4v is converted to an
.mp4, and a .png (usually a still picture) to an animated PNG.
"""
IMAGE_TARGETS = frozenset(IMAGE_CONVERT.targets) | {"ico"}
VIDEO_TARGETS = frozenset(VIDEO_CONVERT.targets)
AUDIO_TARGETS = frozenset(AUDIO_CONVERT.targets)

Processor = Callable[[pathlib.Path, pathlib.Path, Callable[[float, str], None] | None], None]


def own_extensions(target: str) -> frozenset[str]:
    """The extensions of files already in `target`'s format, which converting to it leaves alone."""
    own = frozenset(f".{ext}" for ext in (*EXTENSIONS[target], *ALSO_NAMED.get(target, ())))
    return own - _STILL_CONVERTED.get(target, frozenset())


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
    own = own_extensions(target)
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


def _unreadable(path: pathlib.Path, target: str) -> str | None:
    """
    Why a file named like `target`'s format isn't one sashimi can read (an empty or damaged file),
    or None when it reads it, so "Already MP4" is said only of files that are.
    """
    return _read_own(path, target)[0]


def _read_own(path: pathlib.Path, target: str) -> tuple[str | None, bool]:
    """:func:`_unreadable`'s answer, and whether the file is HDR video (which a conversion to its own format tone-maps)."""
    from sashimi.core._hdr import is_hdr
    from sashimi.errors import SashimiError

    try:
        if target in IMAGE_TARGETS:
            from sashimi.core.edit import frames_info

            frames_info(path)
            return None, False
        info = probe(path)
    except SashimiError as e:
        return getattr(e, "user_message", None) or "This file isn't one sashimi can read.", False
    if target in AUDIO_TARGETS and not info.has_audio:
        return "This file doesn't look like audio we can read.", False
    if target in VIDEO_TARGETS and not info.has_video:
        return video.NOT_A_VIDEO, False
    return None, target in ("mp4", "webm") and is_hdr(info)


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

    def process(src: pathlib.Path, dst: pathlib.Path, progress: Callable[[float, str], None] | None) -> Any:
        route = route_for(target, src)
        if route == "audio" and audio_cfg:
            return audio.convert(src, dst, audio_cfg, max_workers=max_workers, limits=limits, on_progress=progress)
        if route == "video" and video_cfg:
            return video.convert(src, dst, video_cfg, max_workers=max_workers, limits=limits, on_progress=progress)
        if route == "ico":
            return image.to_ico(src, dst, ico_config, limits=limits, on_progress=progress)
        if route == "image" and image_cfg:
            return image.convert(src, dst, image_cfg, limits=limits, on_progress=progress)
        raise UnsupportedMediaError(f"Can't convert {src.suffix or 'this file'} to {target.upper()}.")

    own_format = own_extensions(target)
    check_output_ext(output_ext, (target,))
    spelled = output_ext or (typed.lower() if typed.lower() in EXTENSIONS.get(target, ()) else None)
    fmt = output_format(target, typed=spelled)
    names = naming("convert", lambda _: fmt, output_name, fixed=fmt)
    accepts = _accepts(target)

    readable: dict[pathlib.Path, tuple[str | None, bool]] = {}
    tone_maps = video_cfg is not None and not getattr(video_cfg, "keep_hdr", False)

    def unreadable(path: pathlib.Path) -> str | None:
        if path not in readable:
            readable[path] = _read_own(path, target)
        return readable[path][0]

    def tone_mapped(path: pathlib.Path) -> bool:
        return path.suffix.lower() in own_format and unreadable(path) is None and tone_maps and readable[path][1]

    def is_already(path: pathlib.Path) -> bool:
        return path.suffix.lower() in own_format and unreadable(path) is None and not tone_mapped(path)

    def reject_reason(path: pathlib.Path) -> str:
        if path.suffix.lower() in own_format:
            return unreadable(path) or f"Already {target.upper()}"
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
        already=is_already,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
        takes_named=tone_mapped,
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
        own = own_extensions(config.target)
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
