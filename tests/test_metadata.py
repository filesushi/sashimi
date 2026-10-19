# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Package metadata: licence, authors, project URLs, version and what the sdist leaves out."""

import re
import tomllib
from pathlib import Path

import sashimi

PROJECT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
REPOSITORY = "https://github.com/filesushi/sashimi"


def test_pep_639_licence_metadata():
    project = PYPROJECT["project"]
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE", "AUTHORS"]
    assert not [c for c in project["classifiers"] if c.startswith("License ::")]
    assert PYPROJECT["build-system"]["requires"] == ["hatchling>=1.27"]


def test_authors_and_urls():
    project = PYPROJECT["project"]
    assert project["authors"] == [{"name": "The FileSushi Authors", "email": "admin@filesushi.com"}]
    urls = project["urls"]
    assert set(urls) == {"Homepage", "Repository", "Changelog", "Issues"}
    assert all(url == REPOSITORY or url.startswith(f"{REPOSITORY}/") for url in urls.values())


def test_sdist_leaves_out_internal_docs():
    assert {"/CLAUDE.md", "/docs/FILESUSHI_HANDBACK.md"} <= set(
        PYPROJECT["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
    )


def test_core_metadata_version_is_pinned():
    targets = PYPROJECT["tool"]["hatch"]["build"]["targets"]
    assert targets["wheel"]["core-metadata-version"] == targets["sdist"]["core-metadata-version"] == "2.4"


def test_changelog_starts_with_this_version():
    changelog = (PROJECT / "CHANGELOG.md").read_text(encoding="utf-8")
    first = re.search(r"^## (\S+)", changelog, re.MULTILINE)
    assert first is not None and first.group(1) == sashimi.__version__
