# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Files from different folders that ``output_dir`` gathers into one folder clash; the plan says so
(:attr:`BatchPlan.folder_clash`), with the folder that holds them all and whether searching it
would process the same files, so a caller can say how to keep each folder's layout.
"""

import os
import pathlib

import pytest
from PIL import Image

from sashimi import api
from sashimi.api import _batch
from sashimi.config import IMAGE_RESIZE


def image(*paths):
    for path in paths:
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "red").save(path, format="PNG" if path.suffix == ".png" else "JPEG")


def resize(paths, **kwargs):
    config = lambda target: IMAGE_RESIZE.from_params(target=target, max_width=4)  # noqa: E731
    return api.resize_images_generator(paths, config, **kwargs).plan()


@pytest.fixture
def here(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def glob(folder):
    """The paths a shell's ``folder/*`` names: every entry but the hidden ones, sorted."""
    return sorted(str(p) for p in pathlib.Path(folder).iterdir() if not p.name.startswith("."))


def test_a_shell_glob_of_a_folder_and_its_subfolder(here):
    image("photos/x.jpg", "photos/y.jpg", "photos/sub/x.jpg")
    plan = resize(glob("photos"), output_dir="out")
    assert {item.source for item in plan.clashes} == {pathlib.Path("photos/x.jpg"), pathlib.Path("photos/sub/x.jpg")}
    clash = plan.folder_clash
    assert clash is not None
    assert set(clash.files) == {pathlib.Path("photos/x.jpg"), pathlib.Path("photos/sub/x.jpg")}
    assert clash.output_dir == pathlib.Path("out")
    assert clash.holder == pathlib.Path("photos")
    assert clash.same_files is True
    assert plan.clash_note is None


def test_the_holder_takes_the_runs_files_and_keeps_each_folders_layout(here):
    image("photos/x.jpg", "photos/y.jpg", "photos/sub/x.jpg")
    plan = resize(["photos"], recursive=True, output_dir="out")
    assert plan.folder_clash is None and not plan.clashes
    assert {item.output for item in plan.files} == {
        pathlib.Path("out/x.jpg.resized.jpg"),
        pathlib.Path("out/y.jpg.resized.jpg"),
        pathlib.Path("out/sub/x.jpg.resized.jpg"),
    }


@pytest.mark.parametrize("extra", ["photos/.hidden.jpg", "photos/sub/deeper/z.jpg"])
def test_the_holder_is_not_offered_when_it_holds_more(here, extra):
    image("photos/x.jpg", "photos/sub/x.jpg", extra)
    clash = resize(glob("photos"), output_dir="out").folder_clash
    assert clash is not None and clash.holder == pathlib.Path("photos")
    assert clash.same_files is False


def test_a_deeper_folder_counts_when_the_run_searched_subfolders(here):
    image("photos/x.jpg", "photos/sub/x.jpg", "photos/sub/deeper/z.jpg")
    clash = resize(glob("photos"), recursive=True, output_dir="out").folder_clash
    assert clash is not None and clash.same_files is True


def test_two_sibling_folders(here):
    image("a/x.jpg", "b/x.jpg")
    clash = resize(["a", "b"], output_dir="out").folder_clash
    assert clash is not None and clash.holder == pathlib.Path(".") and clash.same_files is True
    image("c/z.jpg")
    clash = resize(["a", "b"], output_dir="out").folder_clash
    assert clash is not None and clash.holder == pathlib.Path(".") and clash.same_files is False


def test_earlier_outputs_in_the_output_folder_dont_count_against_the_holder(here):
    image("a/x.jpg", "b/x.jpg", "out/y.jpg.resized.jpg", "out/a/x.jpg.resized.jpg")
    clash = resize(["a", "b"], output_dir="out").folder_clash
    assert clash is not None and clash.same_files is True


def test_absolute_paths_give_an_absolute_holder(here):
    image("a/x.jpg", "b/x.jpg")
    clash = resize([here / "a", here / "b"], output_dir=here / "out").folder_clash
    assert clash is not None and clash.holder == here


def test_a_holder_too_big_to_check_is_unknown(here, monkeypatch):
    image("a/x.jpg", "b/x.jpg", *(f"a/{n}.jpg" for n in range(5)))
    monkeypatch.setattr(_batch, "HOLDER_SEARCH_LIMIT", 3)
    clash = resize(["a", "b"], output_dir="out").folder_clash
    assert clash is not None and clash.same_files is None


def test_a_clash_the_pattern_made_has_only_the_pattern_note(here):
    image("a/x.jpg", "b/x.png")
    plan = api.convert_files_generator(
        ["a", "b"], "webp", output_dir="out", output_name="{input_stem}.{output_ext}"
    ).plan()
    assert len(plan.clashes) == 2
    assert plan.clash_note is not None
    assert plan.folder_clash is None


@pytest.mark.skipif(os.path.normcase("A") == "a", reason="needs a case-sensitive filesystem")
def test_a_case_only_clash_in_one_folder_has_no_folder_note(here):
    image("photos/x.jpg", "photos/X.jpg")
    if len(list(pathlib.Path("photos").iterdir())) < 2:
        pytest.skip("needs a case-sensitive filesystem")
    for kwargs in ({"output_dir": "out"}, {}):
        plan = resize(["photos"], **kwargs)
        assert len(plan.clashes) == 2
        assert plan.folder_clash is None


@pytest.mark.skipif(os.path.normcase("A") == "a", reason="needs a case-sensitive filesystem")
def test_folders_that_differ_only_in_case_have_no_folder_note(here):
    image("A/x.jpg", "a/x.jpg")
    if len(list(pathlib.Path(".").iterdir())) < 2:
        pytest.skip("needs a case-sensitive filesystem")
    plan = resize(["A", "a"], output_dir="out")
    assert len(plan.clashes) == 2
    assert plan.folder_clash is None


def test_no_output_folder_no_folder_note(here):
    image("a/x.jpg", "b/x.jpg", "c/x.jpg", "c/x.png")
    assert resize(["a", "b"]).folder_clash is None
    plan = api.convert_files_generator(["c"], "webp", output_name="{input_stem}.{output_ext}").plan()
    assert len(plan.clashes) == 2 and plan.folder_clash is None


def test_only_the_clashes_from_different_folders_are_counted(here):
    image("photos/x.jpg", "photos/sub/x.jpg", "photos/y.jpg")
    plan = resize(glob("photos"), output_dir="out")
    assert len(plan.folder_clash.files) == 2
    assert [item.action for item in plan.files if item.source == pathlib.Path("photos/y.jpg")] == ["write"]


def test_the_report_carries_the_folder_clash(here):
    image("photos/x.jpg", "photos/sub/x.jpg")
    config = lambda target: IMAGE_RESIZE.from_params(target=target, max_width=4)  # noqa: E731
    report = api.resize_images(glob("photos"), config, output_dir="out", dry_run=True)
    assert report["folder_clash"] == {
        "files": ["photos/sub/x.jpg", "photos/x.jpg"],
        "output_dir": "out",
        "holder": "photos",
        "same_files": True,
    }
    image("a/x.jpg")
    assert api.resize_images(["a"], config, output_dir="out", dry_run=True)["folder_clash"] is None


def test_a_clash_with_both_causes_gets_both_notes(here):
    image("a/x.jpg", "a/x.png", "b/x.jpg")
    plan = api.convert_files_generator(
        ["a", "b"], "webp", output_dir="out", output_name="{input_stem}.{output_ext}"
    ).plan()
    assert len(plan.clashes) == 3
    assert plan.clash_note is not None and plan.clash_note.startswith("3 files were skipped because the pattern ")
    assert plan.folder_clash is not None and len(plan.folder_clash.files) == 3
    alone = api.convert_files_generator(["a"], "webp", output_name="{input_stem}.{output_ext}").plan()
    assert alone.clash_note is not None and alone.folder_clash is None
