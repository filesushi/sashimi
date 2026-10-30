# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""The extract batch API: typed errors before anything runs, the batch protocol, and cuts to the end."""

import pytest

from sashimi import probe
from sashimi.api.extract import extract, extract_generator
from sashimi.errors import InvalidOptionsError

from tests.conftest import needs_trusted_cuts, needs_ffmpeg


@pytest.mark.parametrize(
    "args, kwargs, message",
    [
        (("abc",), {}, "'abc' isn't a time. Use seconds (90), units (1m30s, 1h22m) or a clock time (00:01:30)."),
        ((5, 2), {}, "The end time must be after the start time."),
        ((1, 3), {"duration": 1}, "Give an end time or a length, not both."),
        ((1,), {"crf": 20, "mute": True}, "crf, mute only apply to an accurate cut."),
    ],
)
def test_bad_options_raise_before_anything_runs(tmp_path, args, kwargs, message):
    with pytest.raises(InvalidOptionsError) as e:
        extract_generator(tmp_path, *args, **kwargs)
    assert e.value.user_message == message


def test_missing_and_unsupported_paths_are_results(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    report = extract([tmp_path / "missing.mkv", tmp_path / "notes.txt"], 1, show_progress=False)
    assert [(r["status"], r["message"]) for r in report["results"]] == [
        ("error", "Path does not exist"),
        ("skipped", "extract works on videos, not .txt files."),
    ]
    assert report["command"] == "extract"


@needs_ffmpeg
@needs_trusted_cuts
def test_cut_to_the_end_and_by_length(video_blob, tmp_path):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    tail = extract(tmp_path / "clip.mkv", 4, show_progress=False)["results"][0]
    assert tail["status"] == "success" and tail["output"] == str(tmp_path / "clip.mkv.extracted.mkv")
    assert probe(tmp_path / "clip.mkv.extracted.mkv").duration == pytest.approx(2.0, abs=0.1)
    exact = extract(
        tmp_path / "clip.mkv", duration="1.5s", accurate=True, output=tmp_path / "exact.mp4", show_progress=False
    )
    assert exact["results"][0]["message"] == (
        "Extracted 1.5s to exact.mp4 (lossy: re-encoded as H.264 (CRF 23), sound re-encoded as AAC at 128 kb/s)"
    )
    assert "mp4" in probe(tmp_path / "exact.mp4").container_names


@needs_ffmpeg
def test_a_late_keyframe_is_explained(video_blob, tmp_path):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    message = extract(tmp_path / "clip.mkv", 4.6, show_progress=False)["results"][0]["message"]
    assert message.startswith("Extracted 2.0s to clip.mkv.extracted.mkv (1.4s requested).")
