# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Licensing: SPDX headers on every hand-written source file, and the files that name the copyright holder."""

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
HEADER = "# SPDX-FileCopyrightText: 2026 The FileSushi Authors\n# SPDX-License-Identifier: Apache-2.0\n"
HEADED_SUFFIXES = {".py", ".sh", ".toml"}
HEADED_NAMES = {"Dockerfile"}


def project_files() -> list[Path]:
    git = shutil.which("git")
    if git is None or not (PROJECT / ".git").exists():
        pytest.skip("needs a git checkout")
    listing = subprocess.run(
        [git, "-C", str(PROJECT), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    return [PROJECT / name for name in listing if (PROJECT / name).is_file()]


def headed_files() -> list[Path]:
    return [path for path in project_files() if path.suffix in HEADED_SUFFIXES or path.name in HEADED_NAMES]


def test_source_files_start_with_spdx_header():
    missing = []
    for path in headed_files():
        text = path.read_text(encoding="utf-8")
        if text.startswith("#!"):
            text = text.split("\n", 1)[1]
        if not text.startswith(HEADER):
            missing.append(path.relative_to(PROJECT).as_posix())
    assert not missing, f"missing the SPDX header: {missing}"


def test_header_check_covers_the_source_tree():
    names = {path.relative_to(PROJECT).as_posix() for path in headed_files()}
    assert {
        "pyproject.toml",
        "scripts/check.sh",
        "scripts/sandbox_check.sh",
        "scripts/sandbox/Dockerfile",
        "scripts/sandbox/driver.py",
        "src/sashimi/__init__.py",
        "tests/conftest.py",
    } <= names
    assert {
        path.relative_to(PROJECT).as_posix()
        for directory in ("src", "tests", "scripts")
        for path in (PROJECT / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    } <= names


def test_authors_names_the_copyright_holders():
    text = (PROJECT / "AUTHORS").read_text(encoding="utf-8")
    assert text.startswith("The FileSushi Authors are the copyright holders of sashimi.")
    assert "Dorsal Hub LTD" in text.splitlines()


def test_license_is_the_unmodified_apache_text():
    text = (PROJECT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License\n                           Version 2.0, January 2004" in text
    assert "Copyright [yyyy] [name of copyright owner]" in text
