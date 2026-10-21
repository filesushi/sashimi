# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Recognising a run's own earlier outputs: the files a directory search found that this command,
with these settings, would make from another file found in the same run.
"""

import os
import pathlib
from collections.abc import Iterable

from sashimi.api._naming import NameRefused, Naming


def _key(folder: pathlib.Path) -> str:
    return os.path.normcase(os.path.abspath(folder)).casefold()


def _folder(path: pathlib.Path) -> str:
    return _key(path.parent)


def _same(a: pathlib.Path, b: pathlib.Path) -> bool:
    return os.path.normcase(os.path.abspath(a)).casefold() == os.path.normcase(os.path.abspath(b)).casefold()


def own_outputs(files: Iterable[pathlib.Path], names: Naming) -> dict[pathlib.Path, pathlib.Path]:
    """
    The files among `files` whose name is the one `names` gives another of `files`, each with
    that file: ``photo.jpg.resized.jpg`` → ``photo.jpg`` for ``resize``, wherever in the search
    each of them is, since ``output_dir`` may have put outputs elsewhere. When the pattern starts
    with folders (``sheets/{input_name}.jpeg``), the output must be in folders of those names.
    Names are compared as a case-insensitive filesystem would. A name that is only another
    file's stem with a new extension (``photo.webp`` from ``photo.png`` under
    ``{input_stem}.{output_ext}``) counts only in that file's own folder (or the pattern's
    folders inside it), because such names are common for unrelated originals elsewhere. Two
    files that would each be named after the other both count as originals.
    """
    files = list(files)
    named: dict[str, list[tuple[pathlib.Path, tuple[str, ...]]]] = {}
    for source in files:
        try:
            *folders, name = names.name_for(source).split("/")
        except NameRefused:
            continue
        named.setdefault(name.casefold(), []).append((source, tuple(folder.casefold() for folder in folders)))

    def sources_of(path: pathlib.Path) -> list[pathlib.Path]:
        found = []
        for source, folders in named.get(path.name.casefold(), ()):
            if _same(source, path):
                continue
            parent = path.parent
            if folders:
                if tuple(part.casefold() for part in parent.parts[-len(folders) :]) != folders:
                    continue
                for _ in folders:
                    parent = parent.parent
            plain = pathlib.PurePath(path.name).stem.casefold() == source.stem.casefold()
            if plain and _folder(source) != _key(parent):
                continue
            found.append(source)
        return found

    sources = {path: sources_of(path) for path in files}
    left_out: dict[pathlib.Path, pathlib.Path] = {}
    for path, found in sources.items():
        made_from = sorted(source for source in found if not any(_same(path, s) for s in sources.get(source, ())))
        if made_from:
            left_out[path] = made_from[0]
    return left_out
