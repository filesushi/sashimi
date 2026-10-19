# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Shared batch machinery: file discovery, the result protocol, and error classification."""

import functools
import logging
import pathlib
from collections.abc import Callable, Iterable, Iterator, Sequence

from sashimi.errors import MediaTooShortError, ProcessingError, UnsupportedMediaError, UserInputError

logger = logging.getLogger("sashimi")

PathInput = str | pathlib.Path
ResultTuple = tuple[pathlib.Path, str, str]
YieldType = int | ResultTuple
"""
Batch generators yield the number of files to process first, then any rejected inputs
(missing paths, wrong extensions) as final results, then for each file a
``(path, "starting", ...)`` tuple followed by a result whose status is one of
``success | error | exists | skipped``.
"""

VIDEO_EXTENSIONS = (
    ".mp4", ".avi", ".mpg", ".ogm", ".wmv", ".asf", ".mkv", ".mov", ".m4v", ".flv", ".ts", ".webm", ".mpeg", ".3gp",
)  # fmt: skip
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif")
AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg", ".opus", ".oga", ".flac", ".m4a", ".aac", ".weba")

FileProgress = Callable[[pathlib.Path, float, str], None]
"""Per-file progress: ``(input_path, fraction, stage)``."""
Process = Callable[[pathlib.Path, pathlib.Path, "Callable[[float, str], None] | None"], None]


def as_paths(paths: PathInput | Sequence[PathInput]) -> list[pathlib.Path]:
    """Accepts one path or several. Paths are used as given: symlinks are not resolved."""
    if isinstance(paths, (str, pathlib.Path)):
        paths = [paths]
    return [pathlib.Path(p) for p in paths]


def discover(
    paths: list[pathlib.Path],
    extensions: Iterable[str],
    recursive: bool,
    reject_reason: Callable[[pathlib.Path], str] | None = None,
) -> tuple[list[pathlib.Path], list[ResultTuple]]:
    """
    Expands paths into a sorted list of files with matching extensions, plus rejected inputs.
    `reject_reason` explains why an explicitly named file was skipped (default "Unsupported file type").
    """
    wanted = {ext.lower() for ext in extensions}
    files: set[pathlib.Path] = set()
    rejected: list[ResultTuple] = []
    for path in paths:
        if not path.exists():
            rejected.append((path, "error", "Path does not exist"))
        elif path.is_dir():
            for candidate in path.rglob("*") if recursive else path.glob("*"):
                if candidate.is_file() and candidate.suffix.lower() in wanted:
                    files.add(candidate)
        elif path.suffix.lower() in wanted:
            files.add(path)
        else:
            rejected.append((path, "skipped", reject_reason(path) if reject_reason else "Unsupported file type"))
    logger.info(f"Found {len(files)} files to process.")
    return sorted(files), rejected


def classify(error: Exception) -> tuple[str, str]:
    """Maps an exception to a batch status and message, by type."""
    if isinstance(error, (UnsupportedMediaError, MediaTooShortError)):
        return "skipped", error.user_message
    if isinstance(error, UserInputError):
        return "error", error.user_message
    if isinstance(error, ProcessingError):
        return "error", f"Failed: {str(error).splitlines()[0]}"
    return "error", f"Failed: {type(error).__name__}: {error}"


def run_batch(
    paths: PathInput | Sequence[PathInput],
    *,
    extensions: Iterable[str],
    recursive: bool,
    force: bool,
    output_for: Callable[[pathlib.Path], pathlib.Path],
    process: Process,
    success_message: str,
    on_file_progress: FileProgress | None = None,
    reject_reason: Callable[[pathlib.Path], str] | None = None,
) -> Iterator[YieldType]:
    """
    Runs ``process(input, output, on_progress)`` over discovered files. `output_for` names each
    output (typically next to its input); existing outputs are reported as ``exists`` unless `force`.
    """
    files, rejected = discover(as_paths(paths), extensions, recursive, reject_reason)
    yield len(files)
    yield from rejected
    for file_path in files:
        yield (file_path, "starting", "Processing...")
        output = output_for(file_path)
        if not force and output.exists():
            yield (file_path, "exists", f"Output already exists: {output.name}")
            continue
        progress = functools.partial(on_file_progress, file_path) if on_file_progress is not None else None
        try:
            process(file_path, output, progress)
        except Exception as e:
            logger.debug(f"{file_path}: {e!r}")
            status, message = classify(e)
            yield (file_path, status, message)
        else:
            yield (file_path, "success", success_message.format(name=output.name))
