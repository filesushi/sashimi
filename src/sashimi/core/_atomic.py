# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import contextlib
import os
import pathlib
import secrets
import shutil
from typing import Iterator

from sashimi.errors import ProcessingError

PathLike = str | os.PathLike[str]

_TEMP_ATTEMPTS = 100


def _create_temp(temp_dir: pathlib.Path) -> pathlib.Path:
    """
    Creates an empty, uniquely named file with mode 0666 minus the umask, like any newly created
    file. The name is short, so any output name the filesystem allows can be written.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    for _ in range(_TEMP_ATTEMPTS):
        path = temp_dir / f".sashimi-{secrets.token_hex(6)}.partial"
        try:
            os.close(os.open(path, flags, 0o666))
        except FileExistsError:
            continue
        except OSError as e:
            raise ProcessingError(f"Cannot create a temporary file in {temp_dir}: {e}") from e
        return path
    raise ProcessingError(f"Cannot create a temporary file in {temp_dir}: no unused name found")


@contextlib.contextmanager
def atomic_output(output_path: PathLike, scratch_dir: PathLike | None = None) -> Iterator[pathlib.Path]:
    """
    Yields a temporary path to write to, and moves it onto `output_path` only if the block succeeds.

    The temporary file lives in `scratch_dir` if given, otherwise next to the output (so the final
    `os.replace` is atomic). On any failure, including KeyboardInterrupt, it is deleted, so a
    partial output is never left behind. Writers must be told the format explicitly: the temporary
    name doesn't carry the output's extension.
    """
    output = pathlib.Path(output_path)
    temp_dir = pathlib.Path(scratch_dir) if scratch_dir is not None else output.parent
    temp_path = _create_temp(temp_dir)
    try:
        yield temp_path
        try:
            os.replace(temp_path, output)
        except OSError:
            if scratch_dir is None:
                raise
            with atomic_output(output) as staged:
                shutil.copyfile(temp_path, staged)
    finally:
        temp_path.unlink(missing_ok=True)
