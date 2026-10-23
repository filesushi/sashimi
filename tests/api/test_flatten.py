# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""flatten: planned in full, deterministic, never overwrites, keeps symbolic links working."""

import importlib
import json
import os
import pathlib

import pytest
from typer.testing import CliRunner

from sashimi.api.flatten import flatten, plan_flatten
from sashimi.cli.main import app
from sashimi.errors import InvalidOptionsError

runner = CliRunner()


def tree(root: pathlib.Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def listing(root: pathlib.Path) -> dict[str, str]:
    found = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            found[rel] = "-> " + os.readlink(path)
        elif path.is_file():
            found[rel] = path.read_text()
        else:
            found[rel] = "/"
    return found


def moves(plan):
    return [(s.relative_to(plan.root).as_posix(), d.name) for s, d in plan.moves]


def test_moves_files_up_and_removes_empty_folders(tmp_path):
    tree(tmp_path, {"top.txt": "t", "a/one.txt": "1", "a/b/two.txt": "2", "c/three.txt": "3"})
    (tmp_path / "empty" / "deeper").mkdir(parents=True)
    report = flatten(tmp_path)
    assert listing(tmp_path) == {"one.txt": "1", "three.txt": "3", "top.txt": "t", "two.txt": "2"}
    assert (report["command"], report["dry_run"], report["summary"]["success"]) == ("flatten", False, 3)
    removed = [pathlib.Path(p).relative_to(tmp_path).as_posix() for p in report["removed_dirs"]]
    assert removed == ["a/b", "empty/deeper", "a", "c", "empty"]


def test_clash_names_are_planned_before_anything_moves(tmp_path):
    tree(tmp_path, {"x.txt": "root", "a/x.txt": "a", "c/a_x.txt": "c", "d/e/x.txt": "de", "b/x.txt": "b"})
    plan = plan_flatten(tmp_path)
    assert moves(plan) == [
        ("a/x.txt", "a_x_1.txt"),
        ("b/x.txt", "b_x.txt"),
        ("c/a_x.txt", "a_x.txt"),
        ("d/e/x.txt", "d_e_x.txt"),
    ]
    flatten(tmp_path)
    assert listing(tmp_path) == {"a_x.txt": "c", "a_x_1.txt": "a", "b_x.txt": "b", "d_e_x.txt": "de", "x.txt": "root"}


def test_names_clash_regardless_of_case(tmp_path):
    tree(tmp_path, {"Photo.JPG": "root", "a/photo.jpg": "a"})
    assert moves(plan_flatten(tmp_path)) == [("a/photo.jpg", "a_photo.jpg")]


def test_dry_run_changes_nothing_and_reports_the_plan(tmp_path):
    tree(tmp_path, {"x.txt": "r", "a/x.txt": "a"})
    before = listing(tmp_path)
    report = flatten(tmp_path, dry_run=True)
    assert listing(tmp_path) == before
    assert report["dry_run"] is True and report["renamed"] == [str(tmp_path / "a" / "x.txt")]
    assert report["results"] == [
        {
            "file": str(tmp_path / "a" / "x.txt"),
            "status": "success",
            "message": "Would move to a_x.txt (renamed: x.txt is taken)",
            "output": str(tmp_path / "a_x.txt"),
        }
    ]
    assert report["removed_dirs"] == [str(tmp_path / "a")]


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need privileges on Windows")
def test_symbolic_links_and_their_targets_stay_put(tmp_path):
    tree(tmp_path, {"d/real.txt": "target", "c/inside.txt": "c", "e/linked.txt": "e", "g/free.txt": "g"})
    (tmp_path / "f").mkdir()
    (tmp_path / "f" / "link.txt").symlink_to("../d/real.txt")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "folder").symlink_to("../c")
    (tmp_path / "top").symlink_to("e/linked.txt")
    report = flatten(tmp_path)
    assert listing(tmp_path) == {
        "a": "/",
        "a/folder": "-> ../c",
        "c": "/",
        "c/inside.txt": "c",
        "d": "/",
        "d/real.txt": "target",
        "e": "/",
        "e/linked.txt": "e",
        "f": "/",
        "f/link.txt": "-> ../d/real.txt",
        "free.txt": "g",
        "top": "-> e/linked.txt",
    }
    assert (tmp_path / "f" / "link.txt").read_text() == "target"
    assert (tmp_path / "a" / "folder" / "inside.txt").read_text() == "c"
    assert (tmp_path / "top").read_text() == "e"
    statuses = {pathlib.Path(r["file"]).relative_to(tmp_path).as_posix(): r["status"] for r in report["results"]}
    assert statuses == {
        "g/free.txt": "success",
        "a/folder": "skipped",
        "c/inside.txt": "skipped",
        "d/real.txt": "skipped",
        "e/linked.txt": "skipped",
        "f/link.txt": "skipped",
    }


def test_a_file_appearing_at_the_destination_is_never_overwritten(tmp_path, monkeypatch):
    tree(tmp_path, {"a/x.txt": "a"})
    flatten_module = importlib.import_module("sashimi.api.flatten")
    real_plan = flatten_module.plan_flatten

    def racing_plan(root):
        plan = real_plan(root)
        (tmp_path / "x.txt").write_text("someone else's")
        return plan

    monkeypatch.setattr(flatten_module, "plan_flatten", racing_plan)
    report = flatten(tmp_path)
    assert (tmp_path / "x.txt").read_text() == "someone else's"
    assert (tmp_path / "a" / "x.txt").read_text() == "a"
    assert report["summary"]["errors"] == 1 and report["removed_dirs"] == []
    assert report["results"][0]["message"] == "x.txt appeared while flattening; left in place."


def test_flatten_raises_typed_errors(tmp_path):
    with pytest.raises(InvalidOptionsError) as e:
        flatten(tmp_path / "missing")
    assert e.value.user_message == "The folder to flatten doesn't exist."
    (tmp_path / "file").write_text("x")
    with pytest.raises(InvalidOptionsError) as e:
        plan_flatten(tmp_path / "file")
    assert e.value.user_message == "The path to flatten isn't a folder."


def test_cli_dry_run_json_and_confirmation(tmp_path):
    tree(tmp_path, {"x.txt": "r", "a/x.txt": "a"})
    result = runner.invoke(app, ["flatten", str(tmp_path), "--dry-run", "--json"])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert (report["command"], report["dry_run"], report["summary"]["success"]) == ("flatten", True, 1)
    assert (tmp_path / "a" / "x.txt").exists()
    declined = runner.invoke(app, ["flatten", str(tmp_path)], input="n\n")
    assert declined.exit_code == 1 and (tmp_path / "a" / "x.txt").exists()
    assert "Proceed?" in declined.stderr and "Proceed?" not in declined.stdout
    done = runner.invoke(app, ["flatten", str(tmp_path), "-f"])
    assert done.exit_code == 0 and (tmp_path / "a_x.txt").read_text() == "a"
    assert "Moved 1 files (1 renamed), left 0 in place, 0 errors; removed 1 empty subdirectories." in " ".join(
        done.stdout.split()
    )
