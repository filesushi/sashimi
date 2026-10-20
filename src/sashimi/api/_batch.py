# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Shared batch machinery: file discovery, output naming, collision handling, the result protocol,
and error classification.

Output naming policy, used by every batch function:

- a conversion writes ``<stem>.<ext>`` (``photo.png`` → ``photo.webp``, ``clip.mp4`` → ``clip.mkv``);
- an output derived from the input writes ``<stem>.<role>.<ext>``: ``clip.sheet.jpg``,
  ``clip.animated-sheet.webp``, ``clip.preview.gif``, ``photo.resized.jpg``, ``clip.rotated.mp4``,
  ``clip.extracted.mkv``, ``logo.favicons.zip``, ``photo.edited.jpg`` (edits and frame selection);
- outputs go next to their inputs, or under ``output_dir`` (keeping the folders below a directory
  that was searched), or to the single ``output`` path given for a single input;
- when two inputs would be written to the same file (``a.png`` and ``a.jpeg`` → ``a.webp``), each
  keeps its own extension in the name instead (``a.png.webp``, ``a.jpeg.webp``);
- an output that would replace one of the batch's own inputs is an error, even with ``force``;
- an existing output is reported as ``exists`` and left alone unless ``force``. Without ``force``
  the output is moved into place only if the name is still free, so a file created meanwhile is
  never replaced. With ``force`` it is replaced, and the result says so;
- with ``dry_run`` nothing is processed or written: each file's result says what would be
  written (``success``, "Would write …" or "Would replace …") or why not (``exists``,
  ``error``, ``skipped``), from the plan alone, without opening any input.
"""

import functools
import logging
import os
import pathlib
import secrets
from collections.abc import Callable, Iterable, Iterator, Sequence

from sashimi.errors import InvalidOptionsError, MediaTooShortError, ProcessingError, UnsupportedMediaError
from sashimi.errors import UserInputError

logger = logging.getLogger("sashimi")

PathInput = str | os.PathLike[str]
ResultTuple = tuple[pathlib.Path, str, str, pathlib.Path | None]
YieldType = int | ResultTuple
"""
Batch generators yield the number of files to process first, then any rejected inputs
(missing paths, wrong extensions) as final results, then for each file a
``(path, "starting", message, output)`` tuple followed by a result
``(path, status, message, output)`` whose status is one of ``success | error | exists | skipped``.
``output`` is the file written (or that would have been), or ``None`` when there is none.
"""

VIDEO_EXTENSIONS = (
    ".mp4", ".avi", ".mpg", ".ogm", ".wmv", ".asf", ".mkv", ".mov", ".m4v", ".flv", ".ts", ".webm", ".mpeg", ".3gp",
)  # fmt: skip
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif")
AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg", ".opus", ".oga", ".flac", ".m4a", ".aac", ".weba")

FileProgress = Callable[[pathlib.Path, float, str], None]
"""Per-file progress: ``(input_path, fraction, stage)``."""
Process = Callable[[pathlib.Path, pathlib.Path, "Callable[[float, str], None] | None"], "str | None"]
"""
Makes one output: ``(input, output, on_progress)``. It may return a success message, in which
``{name}`` stands for the output's file name, to use instead of the batch's own.
"""
NameFor = Callable[[pathlib.Path, str], str]
"""Names an output file from its input and the stem to use (the input's stem, or its whole name after a collision)."""


def output_name(stem: str, extension: str, role: str | None = None) -> str:
    """The naming policy: ``<stem>.<ext>`` for a conversion, ``<stem>.<role>.<ext>`` for a derived output."""
    ext = extension.lstrip(".")
    return f"{stem}.{role}.{ext}" if role else f"{stem}.{ext}"


def as_paths(paths: PathInput | Sequence[PathInput]) -> list[pathlib.Path]:
    """Accepts one path or several. Paths are used as given: symlinks are not resolved."""
    if isinstance(paths, (str, os.PathLike)):
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
            rejected.append((path, "error", "Path does not exist", None))
        elif path.is_dir():
            for candidate in path.rglob("*") if recursive else path.glob("*"):
                if candidate.is_file() and candidate.suffix.lower() in wanted:
                    files.add(candidate)
        elif path.suffix.lower() in wanted:
            files.add(path)
        else:
            rejected.append((path, "skipped", reject_reason(path) if reject_reason else "Unsupported file type", None))
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
    if isinstance(error, OSError):
        return "error", f"Failed: {error.strerror or error}"
    return "error", f"Failed: {type(error).__name__}: {error}"


def _key(path: pathlib.Path) -> str:
    """Compares paths the way a case-insensitive filesystem would, so plans are safe on every OS."""
    return os.path.normcase(os.path.abspath(path)).casefold()


def _folder_for(file: pathlib.Path, roots: list[pathlib.Path], output_dir: pathlib.Path | None) -> pathlib.Path:
    if output_dir is None:
        return file.parent
    for root in roots:
        if file.is_relative_to(root):
            return output_dir / file.parent.relative_to(root)
    return output_dir


def plan_outputs(
    files: list[pathlib.Path],
    name_for: NameFor,
    *,
    roots: Sequence[pathlib.Path] = (),
    output: pathlib.Path | None = None,
    output_dir: pathlib.Path | None = None,
) -> dict[pathlib.Path, pathlib.Path | str]:
    """
    Decides every output before anything is written. Returns each input's output path, or the
    message explaining why it gets none (another input already claims that name, or the output
    would replace one of the inputs). Pure: it doesn't look at the filesystem.
    """
    if output is not None:
        if len(files) != 1:
            raise InvalidOptionsError("An output file name can only be given for a single input file.")
        return {files[0]: output}
    ordered_roots = sorted(roots, key=lambda r: len(r.parts), reverse=True)
    planned = {f: _folder_for(f, ordered_roots, output_dir) / name_for(f, f.stem) for f in files}

    def groups(mapping: dict[pathlib.Path, pathlib.Path]) -> dict[str, list[pathlib.Path]]:
        found: dict[str, list[pathlib.Path]] = {}
        for source, target in mapping.items():
            found.setdefault(_key(target), []).append(source)
        return found

    for members in groups(planned).values():
        if len(members) > 1:
            for source in members:
                planned[source] = _folder_for(source, ordered_roots, output_dir) / name_for(source, source.name)

    inputs = {_key(f) for f in files}
    result: dict[pathlib.Path, pathlib.Path | str] = {}
    for members in groups(planned).values():
        first, *others = sorted(members)
        for source in [first, *others]:
            target = planned[source]
            if _key(target) in inputs:
                result[source] = f"Writing {target.name} would replace one of the input files, so it was not written."
            elif source is not first:
                result[source] = f"Another input is already being written to {target.name}."
            else:
                result[source] = target
    return result


def _publish(staged: pathlib.Path, output: pathlib.Path) -> bool:
    """Moves `staged` onto `output` only if `output` doesn't exist. Returns False if it does."""
    try:
        os.link(staged, output)
    except FileExistsError:
        return False
    except OSError:
        if os.name == "nt":
            try:
                os.rename(staged, output)
            except FileExistsError:
                return False
            return True
        if os.path.lexists(output):
            return False
        os.replace(staged, output)
        return True
    staged.unlink()
    return True


def _write(
    process: Process,
    source: pathlib.Path,
    output: pathlib.Path,
    progress: "Callable[[float, str], None] | None",
    force: bool,
) -> tuple[bool, str | None]:
    """
    Runs `process` so that, unless `force`, a file appearing at `output` meanwhile is never
    replaced. Returns whether the output was written, and `process`'s message.
    """
    if force:
        return True, process(source, output, progress)
    staged = output.with_name(f".{output.name}.{secrets.token_hex(6)}.staged")
    try:
        message = process(source, staged, progress)
        return _publish(staged, output), message
    finally:
        staged.unlink(missing_ok=True)


def run_batch(
    paths: PathInput | Sequence[PathInput],
    *,
    extensions: Iterable[str],
    recursive: bool,
    force: bool,
    name_for: NameFor,
    process: Process,
    success_message: str,
    on_file_progress: FileProgress | None = None,
    reject_reason: Callable[[pathlib.Path], str] | None = None,
    output: PathInput | None = None,
    output_dir: PathInput | None = None,
    dry_run: bool = False,
) -> Iterator[YieldType]:
    """
    Runs ``process(input, output, on_progress)`` over discovered files, naming outputs with
    `name_for` (see the module's naming policy). `output` names the one output when exactly one
    file is found (InvalidOptionsError otherwise); `output_dir` collects outputs in one folder,
    created as needed, and files already inside it aren't picked up as inputs. Existing outputs
    are reported as ``exists`` unless `force`. With `dry_run`, `process` never runs and no folder
    is created: each file gets the result the plan predicts.
    """
    sources = as_paths(paths)
    files, rejected = discover(sources, extensions, recursive, reject_reason)
    if output_dir is not None:
        outputs = os.path.abspath(output_dir)
        files = [f for f in files if not os.path.abspath(f).startswith(outputs + os.sep)]
    plan = plan_outputs(
        files,
        name_for,
        roots=[p for p in sources if p.is_dir()],
        output=pathlib.Path(output) if output is not None else None,
        output_dir=pathlib.Path(output_dir) if output_dir is not None else None,
    )
    yield len(files)
    yield from rejected
    for file_path in files:
        target = plan[file_path]
        if isinstance(target, str):
            yield (file_path, "starting", "Processing...", None)
            yield (file_path, "error", target, None)
            continue
        yield (file_path, "starting", "Processing...", target)
        existed = os.path.lexists(target)
        if existed and not force:
            yield (file_path, "exists", f"Output already exists: {target.name}", target)
            continue
        if existed and target.is_dir():
            yield (file_path, "error", f"{target.name} is a folder, so it was not replaced.", target)
            continue
        if dry_run:
            yield (file_path, "success", f"Would {'replace' if existed else 'write'} {target.name}", target)
            continue
        progress = functools.partial(on_file_progress, file_path) if on_file_progress is not None else None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            written, custom = _write(process, file_path, target, progress, force)
        except Exception as e:
            logger.debug(f"{file_path}: {e!r}")
            status, message = classify(e)
            yield (file_path, status, message, target)
        else:
            if not written:
                yield (file_path, "exists", f"Output already exists: {target.name}", target)
                continue
            message = (custom if isinstance(custom, str) else success_message).format(name=target.name)
            yield (file_path, "success", f"{message} (replaced the existing file)" if existed else message, target)
