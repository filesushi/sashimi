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
    assert rejected == [(tmp_path / "missing.mp4", "error", "Path does not exist")]


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
            output_for=lambda p: p.with_name(p.name + ".out"),
            process=process,
            success_message="made {name}",
        )
    )

    assert results[0] == 3
    finals = [(p.name, status, msg) for p, status, msg in results[1:] if status != "starting"]
    assert finals == [
        ("notes.txt", "skipped", "Unsupported file type"),
        ("a.mp4", "success", "made a.mp4.out"),
        ("b.mp4", "exists", "Output already exists: b.mp4.out"),
        ("c.mp4", "skipped", "Not a video."),
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


@pytest.mark.parametrize("text", ["", "abc", "1:2:3:4"])
def test_parse_time_to_seconds_rejects(text):
    with pytest.raises(ValueError):
        parse_time_to_seconds(text)


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
            output_for=lambda p: p.with_suffix(".out"),
            process=process,
            success_message="ok",
            on_file_progress=lambda path, fraction, stage: seen.append((path.name, fraction, stage)),
        )
    )
    assert seen == [("a.mp4", 0.5, "encoding")]
