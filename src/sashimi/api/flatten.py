# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Flattening a folder: every file in its subfolders moves into it, planned in full before anything moves."""

import logging
import os
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any, NoReturn

from sashimi.api._batch import PathInput, ResultTuple
from sashimi.api._consume import build_report
from sashimi.errors import InvalidOptionsError, ProcessingError

__all__ = ["FlattenPlan", "flatten", "plan_flatten"]

logger = logging.getLogger("sashimi")

SYMLINK_SKIPPED = "Symbolic link: left where it is, so its target stays valid."
SPECIAL_SKIPPED = "Not a regular file: left where it is."
LINK_TARGET_SKIPPED = "A symbolic link points to it: left where it is, so the link keeps working."


@dataclass
class FlattenPlan:
    """
    What :func:`flatten` will do: each move as ``(source, destination)`` in order, the entries
    left in place with the reason, and the folders that end up empty and are removed (deepest first).
    """

    root: pathlib.Path
    moves: list[tuple[pathlib.Path, pathlib.Path]] = field(default_factory=list)
    renamed: set[pathlib.Path] = field(default_factory=set)
    skipped: list[tuple[pathlib.Path, str]] = field(default_factory=list)
    empty_dirs: list[pathlib.Path] = field(default_factory=list)


def _key(name: str) -> str:
    """Names compare as a case-insensitive filesystem would, so a plan never relies on case alone."""
    return name.casefold()


def plan_flatten(source_dir: PathInput) -> FlattenPlan:
    """
    Plans a flatten without changing anything.

    - Regular files below `source_dir` move into it. Symbolic links (to files or folders) are
      left where they are, since moving a relative link would break it, and so are sockets,
      FIFOs and devices. Linked folders aren't followed.
    - Files and folders that a symbolic link anywhere in `source_dir` points to stay where they
      are too, so every link keeps working. (Links from outside `source_dir` can't be seen.)
    - A file keeps its name if no entry in `source_dir` has it and no file earlier in path order
      claimed it. Otherwise it is renamed ``<folder>_<subfolder>_<name>``, then
      ``<folder>_<subfolder>_<stem>_<n><ext>`` if that is taken too. All plain names are claimed
      before any renamed one, so the result doesn't depend on the order files are found in.
    - Folders left empty are removed; folders still holding a skipped entry stay.

    Raises :class:`~sashimi.errors.InvalidOptionsError` if `source_dir` isn't a folder.
    """
    root = pathlib.Path(source_dir)
    if not root.exists():
        raise InvalidOptionsError("The folder to flatten doesn't exist.", detail=str(root))
    if not root.is_dir():
        raise InvalidOptionsError("The path to flatten isn't a folder.", detail=str(root))

    plan = FlattenPlan(root)
    candidates: list[pathlib.Path] = []
    folders: list[pathlib.Path] = []
    links: list[pathlib.Path] = []

    def fail(error: OSError) -> NoReturn:
        raise ProcessingError(f"Cannot read {error.filename}: {error.strerror}") from error

    for dirpath, dirnames, filenames in os.walk(root, onerror=fail):
        folder = pathlib.Path(dirpath)
        dirnames.sort()
        for name in sorted(dirnames):
            path = folder / name
            if path.is_symlink():
                links.append(path)
                if folder != root:
                    plan.skipped.append((path, SYMLINK_SKIPPED))
            else:
                folders.append(path)
        for name in sorted(filenames):
            path = folder / name
            if path.is_symlink():
                links.append(path)
                if folder != root:
                    plan.skipped.append((path, SYMLINK_SKIPPED))
            elif folder == root:
                continue
            elif path.is_file():
                candidates.append(path)
            else:
                plan.skipped.append((path, SPECIAL_SKIPPED))

    real_root = pathlib.Path(os.path.realpath(root))
    targets = set()
    for link in links:
        target = pathlib.Path(os.path.realpath(link))
        if target != real_root and target.is_relative_to(real_root):
            targets.add(target)

    def linked(path: pathlib.Path) -> bool:
        real = pathlib.Path(os.path.realpath(path))
        return any(real == target or real.is_relative_to(target) for target in targets)

    files: list[pathlib.Path] = []
    for path in candidates:
        if targets and linked(path):
            plan.skipped.append((path, LINK_TARGET_SKIPPED))
        else:
            files.append(path)
    plan.skipped.sort(key=lambda entry: entry[0].relative_to(root).parts)

    try:
        taken = {_key(name) for name in os.listdir(root)}
    except OSError as e:
        fail(e)
    files.sort(key=lambda p: p.relative_to(root).parts)
    destination: dict[pathlib.Path, pathlib.Path] = {}
    clashing: list[pathlib.Path] = []
    for path in files:
        if _key(path.name) in taken:
            clashing.append(path)
        else:
            taken.add(_key(path.name))
            destination[path] = root / path.name
    for path in clashing:
        prefix = "_".join(path.parent.relative_to(root).parts)
        candidate = f"{prefix}_{path.name}"
        counter = 1
        while _key(candidate) in taken:
            candidate = f"{prefix}_{path.stem}_{counter}{path.suffix}"
            counter += 1
        taken.add(_key(candidate))
        destination[path] = root / candidate
        plan.renamed.add(path)
    plan.moves = [(path, destination[path]) for path in files]

    kept = [path for path, _ in plan.skipped]
    plan.empty_dirs = sorted(
        (
            folder
            for folder in folders
            if not any(entry.is_relative_to(folder) for entry in kept) and not (targets and linked(folder))
        ),
        key=lambda p: (-len(p.parts), p),
    )
    return plan


def _move(source: pathlib.Path, target: pathlib.Path) -> None:
    """Moves a file without ever replacing an existing one (FileExistsError if `target` exists)."""
    try:
        os.link(source, target)
    except FileExistsError:
        raise
    except OSError:
        if os.name == "nt":
            os.rename(source, target)
            return
        if os.path.lexists(target):
            raise FileExistsError(target) from None
        os.rename(source, target)
        return
    try:
        os.unlink(source)
    except OSError:
        os.unlink(target)
        raise


def flatten(source_dir: PathInput, *, dry_run: bool = False) -> dict[str, Any]:
    """
    Moves every regular file in `source_dir`'s subfolders into `source_dir`, as
    :func:`plan_flatten` plans it, then removes the folders left empty. With `dry_run`, nothing
    changes and the report says what would happen.

    Files are moved one by one, never replacing anything: a file that appeared at a destination
    after planning is reported as an error and left where it was. Returns the batch report
    (see :func:`sashimi.api.build_report`): one result per file or skipped entry, with
    ``dry_run``, ``renamed`` (the files moved under a new name) and ``removed_dirs`` added at the
    top level.
    """
    began = time.perf_counter()
    plan = plan_flatten(source_dir)
    verb = "Would move" if dry_run else "Moved"
    results: list[ResultTuple] = []
    failed: list[pathlib.Path] = []
    for source, target in plan.moves:
        note = f" (renamed: {source.name} is taken)" if source in plan.renamed else ""
        if not dry_run:
            try:
                _move(source, target)
            except FileExistsError:
                failed.append(source)
                results.append((source, "error", f"{target.name} appeared while flattening; left in place.", target))
                continue
            except OSError as e:
                failed.append(source)
                results.append((source, "error", f"Couldn't move: {e.strerror or e}", target))
                continue
            logger.info(f"Moved {source.relative_to(plan.root)} -> {target.name}")
        results.append((source, "success", f"{verb} to {target.name}{note}", target))
    results += [(path, "skipped", reason, None) for path, reason in plan.skipped]

    removed: list[pathlib.Path] = []
    for folder in plan.empty_dirs:
        if any(path.is_relative_to(folder) for path in failed):
            continue
        if not dry_run:
            try:
                folder.rmdir()
            except OSError as e:
                logger.info(f"Kept {folder}: {e.strerror}")
                continue
        removed.append(folder)
    return build_report(
        "flatten",
        len(plan.moves) + len(plan.skipped),
        results,
        time.perf_counter() - began,
        dry_run=dry_run,
        renamed=[str(path) for path in sorted(plan.renamed)],
        removed_dirs=[str(folder) for folder in removed],
    )
