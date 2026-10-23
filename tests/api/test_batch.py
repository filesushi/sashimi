# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pathlib
import sys

import pytest

from sashimi.api import _batch, _naming
from sashimi.api.extract import parse_time_to_seconds
from sashimi.errors import (
    MediaTooShortError,
    NoAudioTrackError,
    InvalidOptionsError,
    ProcessingError,
    UnsupportedMediaError,
)


@pytest.mark.parametrize(
    "error, expected",
    [
        (UnsupportedMediaError("Not a video."), ("skipped", "Not a video.")),
        (NoAudioTrackError("No audio."), ("skipped", "No audio.")),
        (MediaTooShortError("Too short."), ("skipped", "Too short.")),
        (InvalidOptionsError("Bad option."), ("error", "Bad option.")),
        (
            ProcessingError("ffmpeg exited with code 1: boom\ncommand: ..."),
            ("error", "Failed: ffmpeg exited with code 1: boom"),
        ),
        (RuntimeError("x"), ("error", "Failed: RuntimeError: x")),
    ],
)
def test_errors_are_classified_by_type(error, expected):
    assert _batch.classify(error) == expected


def names(pattern, target="webp", command="convert"):
    """A batch's naming from a pattern, as a batch function builds it."""
    return _naming.naming(command, lambda p: _naming.output_format(target, p), pattern)


def test_discover_does_not_resolve_symlinks(tmp_path):
    blob = tmp_path / "store" / "original.blob"
    blob.parent.mkdir()
    blob.write_bytes(b"x")
    link = tmp_path / "clip.mp4"
    link.symlink_to(blob)

    files, rejected = _batch.discover([link, tmp_path / "missing.mp4"], [".mp4"], recursive=False)

    assert files == [link]
    assert rejected == [(tmp_path / "missing.mp4", "error", "Path does not exist", None)]


def test_run_batch_protocol_and_existing_outputs(tmp_path):
    for name in ("a.mp4", "b.mp4", "c.mp4", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "b.mp4.out").write_bytes(b"old")

    def process(src: pathlib.Path, dst: pathlib.Path, progress) -> None:
        if src.name == "c.mp4":
            raise UnsupportedMediaError("Not a video.")
        dst.write_bytes(b"new")

    results = list(
        _batch.run_batch(
            [tmp_path, tmp_path / "notes.txt"],
            names=names("{input_name}.out", "out"),
            extensions=[".mp4"],
            recursive=False,
            force=False,
            process=process,
            success_message="made {name}",
            accepts="videos",
        )
    )

    assert results[0] == 3
    finals = [(p.name, status, msg, out and out.name) for p, status, msg, out in results[1:] if status != "starting"]
    assert finals == [
        ("notes.txt", "skipped", "convert works on videos, not .txt files.", None),
        ("a.mp4", "success", "made a.mp4.out", "a.mp4.out"),
        ("b.mp4", "exists", "Output already exists: b.mp4.out", "b.mp4.out"),
        ("c.mp4", "skipped", "Not a video.", "c.mp4.out"),
    ]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "a.mp4",
        "a.mp4.out",
        "b.mp4",
        "b.mp4.out",
        "c.mp4",
        "notes.txt",
    ]


@pytest.mark.parametrize(
    "text, seconds",
    [
        ("00:24:09", 1449),
        ("24:09", 1449),
        ("1:22:45.5", 4965.5),
        ("42s", 42),
        ("1h22m", 4920),
        ("1.5h", 5400),
        ("90", 90),
    ],
)
def test_parse_time_to_seconds(text, seconds):
    assert parse_time_to_seconds(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "1:2:3:4", "5s abc", "1:xx"])
def test_parse_time_to_seconds_rejects(text):
    with pytest.raises(InvalidOptionsError) as e:
        parse_time_to_seconds(text)
    assert "00:01:30" in e.value.user_message


def test_run_batch_reports_per_file_progress(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    seen = []

    def process(src, dst, progress):
        progress(0.5, "encoding")
        dst.write_bytes(b"y")

    list(
        _batch.run_batch(
            tmp_path / "a.mp4",
            names=names("{input_stem}.out", "out"),
            extensions=[".mp4"],
            recursive=False,
            force=False,
            process=process,
            success_message="ok",
            accepts="videos",
            on_file_progress=lambda path, fraction, stage: seen.append((path.name, fraction, stage)),
        )
    )
    assert seen == [("a.mp4", 0.5, "encoding")]


STEM = names("{input_stem}.{output_ext}")


def test_plan_outputs_skips_inputs_that_would_share_a_name(tmp_path):
    a_png, a_jpeg, b_png = tmp_path / "a.png", tmp_path / "a.jpeg", tmp_path / "b.png"
    plan = _batch.plan_outputs([a_jpeg, a_png, b_png], STEM)
    assert plan == {
        a_jpeg: ("skipped", "Clashes with a.png: both would be named a.webp."),
        a_png: ("skipped", "Clashes with a.jpeg: both would be named a.webp."),
        b_png: tmp_path / "b.webp",
    }


def test_plan_outputs_names_three_or_more_inputs(tmp_path):
    files = [tmp_path / name for name in ("a.gif", "a.jpg", "a.png")]
    plan = _batch.plan_outputs(files, STEM)
    assert [message for _, message in plan.values()] == [
        "Clashes with a.jpg and a.png: all 3 would be named a.webp.",
        "Clashes with a.gif and a.png: all 3 would be named a.webp.",
        "Clashes with a.gif and a.jpg: all 3 would be named a.webp.",
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="A.png and a.png are one path on Windows")
def test_plan_outputs_compares_names_case_insensitively(tmp_path):
    upper, lower = tmp_path / "A.png", tmp_path / "a.png"
    plan = _batch.plan_outputs([upper, lower], names(None))
    written = "both would be written as A.png.webp, one file on Windows and macOS."
    assert plan[upper] == ("skipped", f"Clashes with a.png: {written}")
    assert plan[lower] == ("skipped", f"Clashes with A.png: {written}")


def test_plan_outputs_may_target_an_input(tmp_path):
    source, other = tmp_path / "a.png", tmp_path / "b.webp"
    plan = _batch.plan_outputs([source, other], STEM)
    assert plan == {source: tmp_path / "a.webp", other: tmp_path / "b.webp"}


def test_plan_outputs_refuses_unusable_names(tmp_path):
    plan = _batch.plan_outputs([tmp_path / "con.png", tmp_path / "ok.png"], names("NUL.webp"))
    assert plan[tmp_path / "con.png"] == plan[tmp_path / "ok.png"]
    assert plan[tmp_path / "ok.png"] == ("error", "NUL.webp can't be used as a file name on Windows.")


def test_plan_outputs_reports_names_shared_under_one_output_dir(tmp_path):
    first, second = tmp_path / "x" / "a.png", tmp_path / "y" / "a.png"
    plan = _batch.plan_outputs([first, second], STEM, output_dir=tmp_path / "out")
    written = f"both would be written as {tmp_path / 'out' / 'a.webp'}."
    assert plan[first] == ("skipped", f"Clashes with {second}: {written}")
    assert plan[second] == ("skipped", f"Clashes with {first}: {written}")


def test_plan_outputs_mirrors_searched_folders(tmp_path):
    root = tmp_path / "in"
    nested, named = root / "sub" / "a.png", tmp_path / "b.png"
    plan = _batch.plan_outputs([nested, named], STEM, roots=[root], output_dir=tmp_path / "out")
    assert plan == {nested: tmp_path / "out" / "sub" / "a.webp", named: tmp_path / "out" / "b.webp"}


def test_rerunning_on_a_folder_reports_exists_and_makes_nothing_new(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")

    def run():
        items = _batch.run_batch(
            tmp_path,
            names=names("{input_name}.{role}.{output_ext}", "png", "resize"),
            extensions=[".png"],
            recursive=False,
            force=False,
            process=lambda src, dst, progress: dst.write_bytes(b"y"),
            success_message="made {name}",
            accepts="images",
        )
        return [(p.name, status) for p, status, _, _ in list(items)[1:] if status != "starting"]

    assert run() == [("a.png", "success")]
    assert run() == [("a.png", "exists")]
    assert run() == [("a.png", "exists")]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.png", "a.png.resized.png"]


def test_a_file_reached_twice_is_processed_once(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    spellings = [tmp_path, tmp_path / "a.png", tmp_path / "sub" / ".." / "a.png"]
    files, _ = _batch.discover(spellings, [".png"], recursive=False)
    assert files == [tmp_path / "a.png"]


def test_single_output_needs_a_single_input(tmp_path):
    assert _batch.plan_outputs([tmp_path / "a.png"], STEM, output=tmp_path / "x.webp") == {
        tmp_path / "a.png": tmp_path / "x.webp"
    }
    with pytest.raises(InvalidOptionsError):
        _batch.plan_outputs([tmp_path / "a.png", tmp_path / "b.png"], STEM, output=tmp_path / "x.bin")


def _run(tmp_path, process, pattern="{input_stem}.{output_ext}", **kwargs):
    items = list(
        _batch.run_batch(
            tmp_path,
            names=names(pattern),
            extensions=[".png"],
            recursive=kwargs.pop("recursive", False),
            force=kwargs.pop("force", False),
            process=process,
            success_message="made {name}",
            accepts="images",
            **kwargs,
        )
    )
    return [(p.name, status, msg) for p, status, msg, _ in items[1:] if status != "starting"]


def test_force_replaces_and_says_so(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "a.webp").write_bytes(b"old")
    results = _run(tmp_path, lambda src, dst, progress: dst.write_bytes(b"new"), force=True)
    assert results == [("a.png", "success", "made a.webp (replaced the existing file)")]
    assert (tmp_path / "a.webp").read_bytes() == b"new"


def test_force_edits_in_place_through_a_staged_file(tmp_path):
    (tmp_path / "a.png").write_bytes(b"old")
    seen = []

    def process(src, dst, progress):
        seen.append(dst.name)
        dst.write_bytes(src.read_bytes() + b"+edit")

    in_place = names("{input_name}", "png", "edit")
    batch = _batch.run_batch(
        tmp_path, names=in_place, extensions=[".png"], recursive=False, force=False, process=process,
        success_message="made {name}", accepts="images",
    )  # fmt: skip
    finals = [r for r in list(batch)[1:] if r[1] != "starting"]
    assert finals[0][1:3] == ("exists", "a.png already exists (it's the input; the name pattern writes in place).")
    batch = _batch.run_batch(
        tmp_path, names=in_place, extensions=[".png"], recursive=False, force=True, process=process,
        success_message="made {name}", accepts="images",
    )  # fmt: skip
    finals = [r for r in list(batch)[1:] if r[1] != "starting"]
    assert finals[0][1:3] == ("success", "made a.png (replaced the input itself)")
    assert (tmp_path / "a.png").read_bytes() == b"old+edit"
    assert seen[0].endswith(".staged") and sorted(p.name for p in tmp_path.iterdir()) == ["a.png"]


def test_force_processes_an_input_before_another_output_replaces_it(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "out").mkdir()
    (tmp_path / "x" / "a.jpg").write_bytes(b"a")
    (tmp_path / "out" / "a.png").write_bytes(b"out a")
    made = []

    def process(src, dst, progress):
        made.append((src.parent.name, src.read_bytes()))
        dst.write_bytes(b"from " + src.read_bytes())

    def swap(path):
        return _naming.output_format("png" if path.suffix == ".jpg" else "jpeg", path)

    batch = _batch.run_batch(
        [tmp_path / "x" / "a.jpg", tmp_path / "out" / "a.png"],
        names=_naming.naming("edit", swap, "{input_stem}.{output_ext}"),
        extensions=[".jpg", ".png"], recursive=False, force=True, process=process, success_message="made {name}",
        accepts="images", output_dir=tmp_path / "out",
    )  # fmt: skip
    finals = [(p.parent.name, status) for p, status, _, _ in list(batch)[1:] if status != "starting"]
    assert finals == [("out", "success"), ("x", "success")]
    assert made == [("out", b"out a"), ("x", b"a")]
    assert (tmp_path / "out" / "a.jpg").read_bytes() == b"from out a"
    assert (tmp_path / "out" / "a.png").read_bytes() == b"from a"


def test_a_named_output_of_another_named_input_is_left_out(tmp_path):
    (tmp_path / "a.png").write_bytes(b"a")
    (tmp_path / "ax.png").write_bytes(b"ax")
    batch = _batch.run_batch(
        [tmp_path / "a.png", tmp_path / "ax.png"], names=names("{input_stem}x.{output_ext}", "png", "edit"),
        extensions=[".png"], recursive=False, force=True, process=lambda *a: None, success_message="made {name}",
        accepts="images",
    )  # fmt: skip
    plan = batch.plan()
    assert plan.left_out == ((tmp_path / "ax.png", tmp_path / "a.png"),)
    assert [(item.source.name, item.action) for item in plan.files] == [("a.png", "replace")]


def test_force_skips_inputs_that_would_replace_each_other(tmp_path):
    for name in ("a.jpg", "a.png"):
        (tmp_path / name).write_bytes(name.encode())

    def swap(path):
        return _naming.output_format("png" if path.suffix == ".jpg" else "jpeg", path)

    naming = _naming.naming("edit", swap, "{input_stem}.{output_ext}")
    batch = _batch.run_batch(
        [tmp_path / "a.jpg", tmp_path / "a.png"], names=naming, extensions=[".jpg", ".png"], recursive=False,
        force=True, process=lambda src, dst, progress: dst.write_bytes(b"new"), success_message="made {name}",
        accepts="images",
    )  # fmt: skip
    finals = {p.name: (status, message) for p, status, message, _ in list(batch)[1:] if status != "starting"}
    assert {status for status, _ in finals.values()} == {"skipped"}
    assert "whose output would replace it" in finals["a.jpg"][1]
    assert (tmp_path / "a.jpg").read_bytes() == b"a.jpg" and (tmp_path / "a.png").read_bytes() == b"a.png"


def test_without_force_a_file_appearing_meanwhile_is_kept(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")

    def process(src, dst, progress):
        (tmp_path / "a.webp").write_bytes(b"someone else's")
        dst.write_bytes(b"new")

    results = _run(tmp_path, process)
    assert results[0][:2] == ("a.png", "exists") and results[0][2].startswith("a.webp already exists")
    assert (tmp_path / "a.webp").read_bytes() == b"someone else's"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.png", "a.webp"]


def test_failed_process_leaves_nothing(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")

    def process(src, dst, progress):
        dst.write_bytes(b"half")
        raise UnsupportedMediaError("Not an image.")

    assert _run(tmp_path, process) == [("a.png", "skipped", "Not an image.")]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.png"]


def test_output_dir_is_created_and_not_searched(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.png").write_bytes(b"x")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "old.png").write_bytes(b"x")
    results = _run(
        tmp_path, lambda src, dst, progress: dst.write_bytes(b"y"), recursive=True, output_dir=tmp_path / "out"
    )
    assert results == [("a.png", "success", f"made {tmp_path / 'out' / 'sub' / 'a.webp'}")]
    assert (tmp_path / "out" / "sub" / "a.webp").read_bytes() == b"y"


def test_process_can_name_its_own_success(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")

    def process(src, dst, progress):
        dst.write_bytes(b"y")
        return "Wrote 3 frames to {name}"

    assert _run(tmp_path, process) == [("a.png", "success", "Wrote 3 frames to a.webp")]
