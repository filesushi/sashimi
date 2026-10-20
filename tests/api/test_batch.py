# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pathlib

import pytest

from sashimi.api import _batch
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
            extensions=[".mp4"],
            recursive=False,
            force=False,
            name_for=lambda p, stem: p.name + ".out",
            process=process,
            success_message="made {name}",
        )
    )

    assert results[0] == 3
    finals = [(p.name, status, msg, out and out.name) for p, status, msg, out in results[1:] if status != "starting"]
    assert finals == [
        ("notes.txt", "skipped", "Unsupported file type", None),
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
            extensions=[".mp4"],
            recursive=False,
            force=False,
            name_for=lambda p, stem: stem + ".out",
            process=process,
            success_message="ok",
            on_file_progress=lambda path, fraction, stage: seen.append((path.name, fraction, stage)),
        )
    )
    assert seen == [("a.mp4", 0.5, "encoding")]


def _name(path, stem):
    return _batch.output_name(stem, "webp")


def test_output_name_policy():
    assert _batch.output_name("clip", "jpg") == "clip.jpg"
    assert _batch.output_name("clip", ".webp", "animated-sheet") == "clip.animated-sheet.webp"


def test_plan_outputs_keeps_extensions_when_inputs_collide(tmp_path):
    a_png, a_jpeg, b_png = tmp_path / "a.png", tmp_path / "a.jpeg", tmp_path / "b.png"
    plan = _batch.plan_outputs([a_jpeg, a_png, b_png], _name)
    assert plan == {a_jpeg: tmp_path / "a.jpeg.webp", a_png: tmp_path / "a.png.webp", b_png: tmp_path / "b.webp"}


def test_plan_outputs_compares_names_case_insensitively(tmp_path):
    upper, lower = tmp_path / "A.png", tmp_path / "a.png"
    plan = _batch.plan_outputs([upper, lower], _name)
    assert plan == {upper: tmp_path / "A.png.webp", lower: "Another input is already being written to a.png.webp."}


def test_plan_outputs_never_targets_an_input(tmp_path):
    source, other = tmp_path / "a.png", tmp_path / "a.resized.png"
    plan = _batch.plan_outputs([source, other], lambda p, stem: _batch.output_name(stem, "png", "resized"))
    assert plan[source] == "Writing a.resized.png would replace one of the input files, so it was not written."
    assert plan[other] == tmp_path / "a.resized.resized.png"


def test_plan_outputs_reports_names_still_shared(tmp_path):
    first, second = tmp_path / "x" / "a.png", tmp_path / "y" / "a.png"
    plan = _batch.plan_outputs([first, second], _name, output_dir=tmp_path / "out")
    assert plan[first] == tmp_path / "out" / "a.png.webp"
    assert plan[second] == "Another input is already being written to a.png.webp."


def test_plan_outputs_mirrors_searched_folders(tmp_path):
    root = tmp_path / "in"
    nested, named = root / "sub" / "a.png", tmp_path / "b.png"
    plan = _batch.plan_outputs([nested, named], _name, roots=[root], output_dir=tmp_path / "out")
    assert plan == {nested: tmp_path / "out" / "sub" / "a.webp", named: tmp_path / "out" / "b.webp"}


def test_single_output_needs_a_single_input(tmp_path):
    assert _batch.plan_outputs([tmp_path / "a.png"], _name, output=tmp_path / "x.bin") == {
        tmp_path / "a.png": tmp_path / "x.bin"
    }
    with pytest.raises(InvalidOptionsError):
        _batch.plan_outputs([tmp_path / "a.png", tmp_path / "b.png"], _name, output=tmp_path / "x.bin")


def _run(tmp_path, process, **kwargs):
    items = list(
        _batch.run_batch(
            tmp_path,
            extensions=[".png"],
            recursive=kwargs.pop("recursive", False),
            force=kwargs.pop("force", False),
            name_for=_name,
            process=process,
            success_message="made {name}",
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


def test_without_force_a_file_appearing_meanwhile_is_kept(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")

    def process(src, dst, progress):
        (tmp_path / "a.webp").write_bytes(b"someone else's")
        dst.write_bytes(b"new")

    assert _run(tmp_path, process) == [("a.png", "exists", "Output already exists: a.webp")]
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
    assert results == [("a.png", "success", "made a.webp")]
    assert (tmp_path / "out" / "sub" / "a.webp").read_bytes() == b"y"


def test_process_can_name_its_own_success(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")

    def process(src, dst, progress):
        dst.write_bytes(b"y")
        return "Wrote 3 frames to {name}"

    assert _run(tmp_path, process) == [("a.png", "success", "Wrote 3 frames to a.webp")]
