# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Shared batch machinery: file discovery, output naming, planning, the result protocol, and error
classification.

Output naming policy, used by every batch function:

- **A name depends only on its own input, the command and the settings,** never on the other
  files in the batch. A rerun, a bigger batch or a subfolder plans the same name for a file, so
  "exists" means the same thing every time.
- Each command names its outputs with a pattern (``output_name``; see
  :class:`~sashimi.api.NameTemplate`). The fields: ``{input_name}``, the input's file name
  (``clip.mp4``); ``{input_stem}``, the name without its extension (``clip``); ``{input_ext}``,
  the extension without the dot (``mp4``); ``{role}``, what the output is (``sheet``); and
  ``{output_ext}``, the output's extension (``jpg``).
- **The default is the same for every command,** ``{input_name}.{role}.{output_ext}``:
  ``clip.mp4.sheet.jpg``, ``clip.mp4.animated-sheet.webp``, ``clip.mp4.preview.webp``,
  ``logo.png.favicons.zip``, ``photo.jpg.resized.jpg``, ``photo.jpg.rotated.png`` ("this is
  photo.jpg, rotated, saved as PNG"), ``clip.mkv.extracted.mkv``, ``photo.jpg.edited.jpg``,
  ``anim.gif.frames.gif`` and ``clip.mov.stripped.mov``. Conversions have no role, so theirs
  is ``{input_name}.{output_ext}``: ``photo.png.webp`` (``convert``), ``clip.mp4.mkv``
  (``remux``).
- **``{output_ext}``** is the extension the caller chose for the format (``output_ext``, or
  ``convert``'s target as given: ``jpeg`` writes ``.jpeg``, ``jpg`` writes ``.jpg``); else, for
  an output in the input's own format, the input's extension as written (``photo.JPEG`` keeps
  ``.JPEG``); else sashimi's standard one (``.jpg`` for JPEG, ``.tif`` for TIFF).
- A pattern may spell out the extension instead (``{input_name}.jpeg`` for ``clip.mp4.jpeg``
  sheets), which must name the output's format: when every output has one format, a pattern
  that doesn't is refused before anything runs, and otherwise that file gets an ``error``.
- Every name is checked before anything runs: a single file name (no ``/`` or ``\\``), valid on
  Windows and POSIX, at most 255 characters, and ending in an extension of the output's format.
  A name that fails is that file's ``error`` result.
- A pattern may start with folders, which are inside each input's own folder:
  ``sheets/{input_name}.jpeg`` writes ``Show/S01/sheets/clip.mkv.jpeg`` for ``Show/S01/clip.mkv``,
  wherever the batch runs from. Folders are created as needed.
- Outputs go into each input's folder, or under ``output_dir`` (one folder, relative to the
  current folder unless absolute, keeping the folders below a directory that was searched, with
  the pattern's folders below that), or to the single ``output`` path given for a single input,
  which replaces the pattern and must end in an extension of the output's format.
- Messages name an output by its file name when it's next to its input, and otherwise by its
  path as built from the caller's own paths (``sheets/clip.mkv.jpeg``), so it can be found.

Searching directories:

- **A run never processes its own outputs.** A file is left out when this command, with these
  settings (its pattern and output format), would give that name to another file of the same
  run, found in a search or named (a shell glob such as ``*.jpg`` names earlier outputs too), wherever in the search the two are (an earlier run may
  have used ``output_dir``): ``photo.jpg.resized.jpg`` for ``resize``, ``photo.png.webp`` for
  ``convert`` to WebP. So rerunning a command never processes what it made before, and the
  rerun reports those outputs as ``exists``. A name that is just another file's stem with a
  new extension (``photo.webp`` under ``{input_stem}.{output_ext}``) counts only in that
  file's folder. Two files that would each be named after the other both count as originals.
- **Everything else found is processed,** other commands' outputs included: ``convert . webp``
  after ``thumbs .`` converts the sheets, and ``convert . jpeg`` after ``convert . webp``
  writes ``photo.png.webp.jpeg``. When an original is deleted, its outputs are processed like
  any file. A file named on its own is always processed, whatever its name.
- Files a search finds inside ``output_dir`` are left out (:attr:`BatchPlan.in_output_dir`),
  unless ``output_dir`` is a searched directory or contains one: outputs then sit beside their
  inputs' copies of the folder layout and are recognised by name as above.

Planning and writing:

- **The plan is made before anything is written** (:meth:`Batch.plan`): each file's output,
  what will happen to it, and its kind and route, so a caller can show it and ask first.
- **Two outputs with one name are both skipped,** never renamed: when two inputs would be
  written to the same file (compared as a case-insensitive filesystem would), each gets a
  ``skipped`` result naming the other inputs and the shared name, with or without ``force``.
  When the pattern caused it, :attr:`BatchPlan.clash_note` names the pattern and where it was
  set, once.
- **An existing output is ``exists``** and left alone unless ``force``. The message says what
  sashimi knows: the input itself (a pattern that writes in place), another input, a name a
  pattern gives originals too, or an earlier output. Without ``force`` the output is moved into
  place only if the name is still free, so a file created meanwhile is never replaced.
- **``force`` replaces whatever is there,** the input itself included (an in-place edit), and
  every replacement is reported. Outputs are written to a staged file first and moved into
  place, so a failure leaves the original as it was. When an output replaces another input of
  the same run, that input is processed first.
- With ``dry_run`` nothing is processed or written: each file's result says what would be
  written (``success``, "Would write …" or "Would replace …") or why not (``exists``,
  ``error``, ``skipped``), from the plan alone, without opening any input.
"""

import functools
import logging
import os
import pathlib
import secrets
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass

from sashimi.api._kinds import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, count_text, kind_of, unsupported
from sashimi.api._naming import NameRefused, NameTemplate, Naming, endings
from sashimi.api._outputs import own_outputs
from sashimi.errors import InvalidOptionsError, MediaTooShortError, ProcessingError, UnsupportedMediaError
from sashimi.errors import UserInputError

__all__ = [
    "AUDIO_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "VIDEO_EXTENSIONS",
    "Batch",
    "BatchPlan",
    "PlannedFile",
    "run_batch",
]

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

FileProgress = Callable[[pathlib.Path, float, str], None]
"""Per-file progress: ``(input_path, fraction, stage)``."""
Process = Callable[[pathlib.Path, pathlib.Path, "Callable[[float, str], None] | None"], "str | None"]
"""
Makes one output: ``(input, output, on_progress)``. It may return a success message, in which
``{name}`` stands for the output's file name, to use instead of the batch's own.
"""


@dataclass(frozen=True)
class PlannedFile:
    """
    What a batch will do with one input. `action` is ``write`` or ``replace`` (it will be
    processed), or ``exists``, ``skipped`` or ``error`` (it won't be, and `message` says why).
    `kind` is ``image``, ``video`` or ``audio``; `route` names the code that will process it
    (``edit``, ``video``, …). `clash` is True when it was skipped because another input's
    output has the same name.
    """

    source: pathlib.Path
    action: str
    message: str
    output: pathlib.Path | None = None
    kind: str | None = None
    route: str | None = None
    clash: bool = False


@dataclass(frozen=True)
class BatchPlan:
    """
    Everything a batch will do, worked out before anything is written: each input's
    :class:`PlannedFile`, the inputs refused outright (missing, or a type the command doesn't
    take), and the run's own earlier outputs that were left out, each with the file it was made
    from. `searched` is True when a directory was searched. `template` is the pattern in use
    (None when ``output`` names the one output), and `default_template` whether it's the
    command's own. `clash_note` says once which pattern, and where it was set, gave inputs the
    same output name; it's None when no clash was the pattern's doing. `in_output_dir` lists the
    files a search found inside ``output_dir``, which were left out. `accepts` says what the
    command works on ("videos"), and `recursive` whether subfolders were searched, so a caller
    can explain an empty plan. `already_done` lists the files named directly that were skipped
    because they are already what the command makes (``Already JPEG``), so a caller can treat
    them like existing outputs rather than as problems.
    """

    command: str
    files: tuple[PlannedFile, ...]
    rejected: tuple[ResultTuple, ...] = ()
    left_out: tuple[tuple[pathlib.Path, pathlib.Path], ...] = ()
    searched: bool = False
    template: NameTemplate | None = None
    default_template: bool = True
    clash_note: str | None = None
    in_output_dir: tuple[pathlib.Path, ...] = ()
    accepts: str = ""
    recursive: bool = False
    already_done: tuple[pathlib.Path, ...] = ()

    @property
    def to_process(self) -> tuple[PlannedFile, ...]:
        """The files that will be processed (``write`` or ``replace``)."""
        return tuple(item for item in self.files if item.action in ("write", "replace"))

    @property
    def routes(self) -> dict[str, int]:
        """How many files each route will process."""
        counts: dict[str, int] = {}
        for item in self.to_process:
            counts[item.route or "file"] = counts.get(item.route or "file", 0) + 1
        return counts

    @property
    def kinds(self) -> dict[str, int]:
        """How many files of each kind (``image``, ``video``, ``audio``) will be processed."""
        counts: dict[str, int] = {}
        for item in self.to_process:
            counts[item.kind or "file"] = counts.get(item.kind or "file", 0) + 1
        return counts

    @property
    def mixed(self) -> bool:
        """True when the files to process take more than one route, so a caller should ask before a folder run."""
        return len(self.routes) > 1

    def describe(self) -> str:
        """The files to process, counted in words: ``44 videos and 23 images``."""
        return count_text(self.kinds)

    @property
    def clashes(self) -> tuple[PlannedFile, ...]:
        """The files skipped because their outputs would have had the same name."""
        return tuple(item for item in self.files if item.clash)


class Batch(Iterator[YieldType]):
    """
    A batch run. Iterate it for the results (the protocol in :data:`YieldType`); call
    :meth:`plan` first to see what it will do, for example to ask before a folder of mixed
    file types is processed. Nothing is planned or written until one of them is called.
    """

    def __init__(self, planner: Callable[[], BatchPlan], executor: Callable[[BatchPlan], Iterator[YieldType]]):
        self._planner = planner
        self._executor = executor
        self._plan: BatchPlan | None = None
        self._run: Iterator[YieldType] | None = None

    def plan(self) -> BatchPlan:
        """
        The plan: found files, names, and what will happen to each, from the filesystem as it is
        now. Raises :class:`~sashimi.errors.InvalidOptionsError` for options that can't work.
        """
        if self._plan is None:
            self._plan = self._planner()
        return self._plan

    def __iter__(self) -> "Batch":
        return self

    def __next__(self) -> YieldType:
        if self._run is None:
            self._run = self._executor(self.plan())
        return next(self._run)


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
    Expands paths into a sorted list of files with matching extensions, plus the rejected
    inputs. A file reached twice (named and found in a folder, or by two spellings) is listed once. `reject_reason` explains why an
    explicitly named file was skipped (default "Unsupported file type").
    """
    wanted = {ext.lower() for ext in extensions}
    files: dict[str, pathlib.Path] = {}
    rejected: list[ResultTuple] = []
    for path in paths:
        if not path.exists():
            rejected.append((path, "error", "Path does not exist", None))
        elif path.is_dir():
            for candidate in path.rglob("*") if recursive else path.glob("*"):
                if candidate.is_file() and candidate.suffix.lower() in wanted:
                    files.setdefault(os.path.abspath(candidate), candidate)
        elif path.suffix.lower() in wanted:
            files.setdefault(os.path.abspath(path), path)
        else:
            rejected.append((path, "skipped", reject_reason(path) if reject_reason else "Unsupported file type", None))
    logger.info(f"Found {len(files)} files to process.")
    return sorted(files.values()), rejected


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


def _inside(path: pathlib.Path, folder: str) -> bool:
    return os.path.abspath(path).startswith(folder + os.sep)


def _at_or_above(folder: str, root: pathlib.Path) -> bool:
    """True when `folder` (absolute) is `root` or one of the folders that contain it."""
    absolute = os.path.abspath(root)
    return absolute == folder or absolute.startswith(folder.rstrip(os.sep) + os.sep)


def shown(source: pathlib.Path, target: pathlib.Path) -> str:
    """
    How messages name `target`, an output of `source`: its file name when it's in the input's
    folder, else its path as built from the caller's paths (``sheets/clip.mkv.jpeg``).
    """
    return target.name if _key(target.parent) == _key(source.parent) else str(target)


def _folder_for(file: pathlib.Path, roots: list[pathlib.Path], output_dir: pathlib.Path | None) -> pathlib.Path:
    if output_dir is None:
        return file.parent
    for root in roots:
        if file.is_relative_to(root):
            return output_dir / file.parent.relative_to(root)
    return output_dir


def _left_in_output_dir(found: pathlib.Path, roots: list[pathlib.Path], named: set[str], output_dir: str) -> bool:
    """True when a search found `found` inside `output_dir` (absolute), so it isn't an input."""
    if os.path.abspath(found) in named or not _inside(found, output_dir):
        return False
    containing = [root for root in roots if _inside(found, os.path.abspath(root))]
    return not any(_at_or_above(output_dir, root) for root in containing)


def _joined(labels: list[str]) -> str:
    return " and ".join(labels) if len(labels) <= 2 else ", ".join(labels[:-1]) + " and " + labels[-1]


def _by_pattern(members: list[pathlib.Path], names: Naming) -> bool:
    """True when the pattern, not the input names, gives `members` one output name."""
    return not names.default and len({m.name.casefold() for m in members}) == len(members)


def _clash_message(source: pathlib.Path, members: list[pathlib.Path], target: pathlib.Path, names: Naming) -> str:
    distinct = len({m.name for m in members}) == len(members)
    others = [m.name if distinct else str(m) for m in members if m != source]
    every = "both" if len(members) == 2 else f"all {len(members)}"
    if _by_pattern(members, names):
        return f"Clashes with {_joined(others)}: {every} would be named {shown(source, target)}."
    written = shown(source, target) if distinct else str(target)
    case = len({m.name.casefold() for m in members}) < len({m.name for m in members})
    where = ", one file on Windows and macOS" if case else ""
    return f"Clashes with {_joined(others)}: {every} would be written as {written}{where}."


def clash_note(count: int, template: NameTemplate) -> str:
    """The sentence that blames `template` once for the `count` files its names made clash."""
    files = "1 file was" if count == 1 else f"{count} files were"
    them = "it" if count == 1 else "them"
    return (
        f"{files} skipped because the pattern {template.described()} gives {them} the same name as another "
        "input's output; keep {input_name} or {input_ext} in it to tell them apart."
    )


def _targets(
    files: list[pathlib.Path],
    names: Naming,
    roots: Sequence[pathlib.Path],
    output_dir: pathlib.Path | None,
) -> tuple[dict[pathlib.Path, pathlib.Path], dict[pathlib.Path, str]]:
    """Each input's planned output path, and the inputs whose name can't be used, with why."""
    ordered_roots = sorted(roots, key=lambda r: len(r.parts), reverse=True)
    planned: dict[pathlib.Path, pathlib.Path] = {}
    refused: dict[pathlib.Path, str] = {}
    for source in files:
        try:
            planned[source] = _folder_for(source, ordered_roots, output_dir) / names.name_for(source)
        except NameRefused as e:
            refused[source] = e.message
    return planned, refused


def plan_outputs(
    files: list[pathlib.Path],
    names: Naming,
    *,
    roots: Sequence[pathlib.Path] = (),
    output: pathlib.Path | None = None,
    output_dir: pathlib.Path | None = None,
) -> dict[pathlib.Path, pathlib.Path | tuple[str, str]]:
    """
    Every input's output path, or the ``(status, message)`` explaining why it gets none: its
    name can't be used (``error``), or another input would be written to the same file
    (``skipped``, both of them, each message naming the other files and the shared name). Names
    come from `names` alone, so they never depend on the other files. Pure: it doesn't look at
    the filesystem.
    """
    return _plan_outputs(files, names, roots=roots, output=output, output_dir=output_dir)[0]


def _plan_outputs(
    files: list[pathlib.Path],
    names: Naming,
    *,
    roots: Sequence[pathlib.Path],
    output: pathlib.Path | None,
    output_dir: pathlib.Path | None,
) -> tuple[dict[pathlib.Path, pathlib.Path | tuple[str, str]], int]:
    if output is not None:
        if len(files) > 1:
            raise InvalidOptionsError("An output file name can only be given for a single input file.")
        return {source: _checked_output(source, output, names) for source in files}, 0
    planned, refused = _targets(files, names, roots, output_dir)
    result: dict[pathlib.Path, pathlib.Path | tuple[str, str]] = {s: ("error", m) for s, m in refused.items()}
    groups: dict[str, list[pathlib.Path]] = {}
    for source, target in planned.items():
        groups.setdefault(_key(target), []).append(source)
    by_pattern = 0
    for members in groups.values():
        members.sort()
        if len(members) > 1 and _by_pattern(members, names):
            by_pattern += len(members)
        for source in members:
            if len(members) > 1:
                result[source] = ("skipped", _clash_message(source, members, planned[members[0]], names))
            else:
                result[source] = planned[source]
    return {source: result[source] for source in files}, by_pattern


def _checked_output(source: pathlib.Path, output: pathlib.Path, names: Naming) -> pathlib.Path | tuple[str, str]:
    """`output`, or an ``error`` when its extension doesn't name the format `source` is written in."""
    try:
        fmt = names.format_for(source)
    except NameRefused as e:
        return ("error", e.message)
    if output.suffix[1:].lower() not in fmt.accepted:
        return ("error", f"{output.name} must end in {endings(fmt)}, since the output is {fmt.label}.")
    return output


def _exists_message(
    source: pathlib.Path, target: pathlib.Path, names: Naming, inputs: set[str], by_output: bool
) -> str:
    where = shown(source, target)
    if _key(target) == _key(source):
        how = "-o names the input" if by_output else "the name pattern writes in place"
        return f"{where} already exists (it's the input; {how})."
    if _key(target) in inputs:
        return f"{where} already exists (it's also one of the inputs)."
    beside = _key(target.parent) == _key(source.parent)
    if not by_output and beside and pathlib.PurePath(target.name).stem.casefold() == source.stem.casefold():
        return (
            f"{where} already exists (sashimi can't tell whether an earlier run made it: "
            f"the pattern {names.template.described()} gives outputs an original's name)."
        )
    return f"Output already exists: {where}"


def _replace_note(source: pathlib.Path, target: pathlib.Path) -> str:
    return "the input itself" if _key(target) == _key(source) else "the existing file"


def _folder_message(source: pathlib.Path, target: pathlib.Path) -> str:
    return f"{shown(source, target)} is a folder, so it was not replaced."


def _order(items: list[PlannedFile]) -> list[PlannedFile]:
    """
    Puts each input that another input's output will replace before that other input, so it's
    read before it's replaced. Inputs that would replace each other in a loop are skipped.
    """
    by_key = {_key(item.source): index for index, item in enumerate(items)}
    writes = {
        index: by_key[_key(item.output)]
        for index, item in enumerate(items)
        if item.output is not None
        and item.action == "replace"
        and _key(item.output) in by_key
        and _key(item.output) != _key(item.source)
        and items[by_key[_key(item.output)]].action in ("write", "replace")
    }
    if not writes:
        return items
    ordered: list[int] = []
    state: dict[int, str] = {}
    looped: set[int] = set()

    def visit(index: int, path: list[int]) -> None:
        if state.get(index) == "done":
            return
        if state.get(index) == "active":
            looped.update(path[path.index(index) :])
            return
        state[index] = "active"
        if index in writes:
            visit(writes[index], [*path, index])
        state[index] = "done"
        ordered.append(index)

    for index in range(len(items)):
        visit(index, [])
    result = []
    for index in ordered:
        item = items[index]
        if index in looped:
            others = sorted(items[i].source.name for i in looped if i != index)
            item = PlannedFile(
                item.source,
                "skipped",
                f"{item.source.name}'s output would replace {' and '.join(others)}, whose output would replace it, "
                "so neither was written.",
                item.output,
                item.kind,
                item.route,
            )
        result.append(item)
    return result


def _plan(
    paths: PathInput | Sequence[PathInput],
    *,
    names: Naming,
    extensions: Iterable[str],
    recursive: bool,
    force: bool,
    route: Callable[[pathlib.Path], str] | None,
    reject_reason: Callable[[pathlib.Path], str],
    already: Callable[[pathlib.Path], bool] | None,
    accepts: str,
    output: PathInput | None,
    output_dir: PathInput | None,
) -> BatchPlan:
    sources = as_paths(paths)
    files, rejected = discover(sources, extensions, recursive, reject_reason)
    done = tuple(path for path, status, _, _ in rejected if status == "skipped" and already and already(path))
    out_dir = pathlib.Path(output_dir) if output_dir is not None else None
    roots = [p for p in sources if p.is_dir()]
    named = {os.path.abspath(p) for p in sources if not p.is_dir()}
    in_output_dir: list[pathlib.Path] = []
    if out_dir is not None and output is None:
        outputs = os.path.abspath(out_dir)
        in_output_dir = [f for f in files if _left_in_output_dir(f, roots, named, outputs)]
        files = [f for f in files if f not in set(in_output_dir)]
    left_out: list[tuple[pathlib.Path, pathlib.Path]] = []
    if output is None:
        made = own_outputs(files, names)
        left_out = [(f, made[f]) for f in files if f in made]
        dropped = {f for f, _ in left_out}
        files = [f for f in files if f not in dropped]
    target_for, by_pattern = _plan_outputs(
        files, names, roots=roots, output=pathlib.Path(output) if output is not None else None, output_dir=out_dir
    )
    inputs = {_key(f) for f in files}
    items: list[PlannedFile] = []
    for source in files:
        kind = kind_of(source)
        chosen = route(source) if route is not None else kind
        planned = target_for[source]
        if isinstance(planned, tuple):
            status, message = planned
            items.append(PlannedFile(source, status, message, None, kind, chosen, clash=status == "skipped"))
            continue
        if os.path.lexists(planned):
            if planned.is_dir():
                items.append(PlannedFile(source, "error", _folder_message(source, planned), planned, kind))
            elif force:
                message = f"Would replace {shown(source, planned)} ({_replace_note(source, planned)})"
                items.append(PlannedFile(source, "replace", message, planned, kind, chosen))
            else:
                message = _exists_message(source, planned, names, inputs, output is not None)
                items.append(PlannedFile(source, "exists", message, planned, kind, chosen))
        else:
            items.append(PlannedFile(source, "write", f"Would write {shown(source, planned)}", planned, kind, chosen))
    return BatchPlan(
        names.command,
        tuple(_order(items) if force else items),
        tuple(rejected),
        tuple(left_out),
        searched=bool(roots),
        template=names.template if output is None else None,
        default_template=names.default,
        clash_note=clash_note(by_pattern, names.template) if by_pattern else None,
        in_output_dir=tuple(in_output_dir),
        accepts=accepts,
        recursive=recursive,
        already_done=done,
    )


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
    Runs `process` into a staged file next to `output`, then moves it into place: over whatever
    is there with `force` (the input itself included), else only if `output` is still free.
    Returns whether the output was written, and `process`'s message.
    """
    staged = output.with_name(f".{output.name}.{secrets.token_hex(6)}.staged")
    try:
        message = process(source, staged, progress)
        if force:
            os.replace(staged, output)
            return True, message
        return _publish(staged, output), message
    finally:
        staged.unlink(missing_ok=True)


def _execute(
    plan: BatchPlan,
    *,
    names: Naming,
    process: Process,
    success_message: str,
    force: bool,
    dry_run: bool,
    on_file_progress: FileProgress | None,
    by_output: bool,
) -> Iterator[YieldType]:
    yield len(plan.files)
    yield from plan.rejected
    inputs = {_key(item.source) for item in plan.files}
    for item in plan.files:
        source, target = item.source, item.output
        yield (source, "starting", "Processing...", target)
        if item.action not in ("write", "replace") or target is None:
            yield (source, item.action, item.message, target)
            continue
        existed = os.path.lexists(target)
        if existed and not force:
            yield (source, "exists", _exists_message(source, target, names, inputs, by_output), target)
            continue
        if existed and target.is_dir():
            yield (source, "error", _folder_message(source, target), target)
            continue
        where = shown(source, target)
        if dry_run:
            verb = f"replace {where} ({_replace_note(source, target)})" if existed else f"write {where}"
            yield (source, "success", f"Would {verb}", target)
            continue
        progress = functools.partial(on_file_progress, source) if on_file_progress is not None else None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            written, custom = _write(process, source, target, progress, force)
        except Exception as e:
            logger.debug(f"{source}: {e!r}")
            status, message = classify(e)
            yield (source, status, message, target)
        else:
            if not written:
                yield (source, "exists", _exists_message(source, target, names, inputs, by_output), target)
                continue
            message = (custom if isinstance(custom, str) else success_message).format(name=where)
            if existed:
                message += f" (replaced {_replace_note(source, target)})"
            yield (source, "success", message, target)


def run_batch(
    paths: PathInput | Sequence[PathInput],
    *,
    names: Naming,
    extensions: Iterable[str],
    recursive: bool,
    force: bool,
    process: Process,
    success_message: str,
    accepts: str,
    route: Callable[[pathlib.Path], str] | None = None,
    on_file_progress: FileProgress | None = None,
    reject_reason: Callable[[pathlib.Path], str] | None = None,
    already: Callable[[pathlib.Path], bool] | None = None,
    output: PathInput | None = None,
    output_dir: PathInput | None = None,
    dry_run: bool = False,
) -> Batch:
    """
    Runs ``process(input, output, on_progress)`` over discovered files, naming outputs with
    `names` (see the module's policy). `accepts` says what the command takes ("videos"), for
    files named directly that it doesn't, unless `reject_reason` explains them;
    `already` says which of those are already what the command makes
    (:attr:`BatchPlan.already_done`). `route` names the code each file goes through, so the plan can count them. `output` names the one output when
    exactly one file is found (InvalidOptionsError otherwise), and must end in an extension of the
    output's format; `output_dir` collects outputs in one folder (relative to the current folder
    unless absolute), created as needed, keeping the subfolders of searched directories, and
    files a search finds already inside it aren't picked up as inputs. Folders a pattern starts
    with are inside each input's folder, below `output_dir` when it's given.
    A directory search leaves out the run's own earlier outputs. Existing outputs are reported as
    ``exists`` unless `force`. With `dry_run`, `process` never runs and no folder is created:
    each file gets the result the plan predicts.
    """
    label = "thumbs" if names.command == "animated_thumbs" else names.command

    def rejected(path: pathlib.Path) -> str:
        return reject_reason(path) if reject_reason is not None else unsupported(label, accepts, path)

    planner = functools.partial(
        _plan,
        paths,
        names=names,
        extensions=list(extensions),
        recursive=recursive,
        force=force,
        route=route,
        reject_reason=rejected,
        already=already,
        accepts=accepts,
        output=output,
        output_dir=output_dir,
    )
    executor = functools.partial(
        _execute,
        names=names,
        process=process,
        success_message=success_message,
        force=force,
        dry_run=dry_run,
        on_file_progress=on_file_progress,
        by_output=output is not None,
    )
    return Batch(planner, executor)
