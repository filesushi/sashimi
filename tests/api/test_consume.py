# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pathlib
import sys
import types

from sashimi.api._consume import build_report, consume_generator


def test_rejected_inputs_are_reported_but_do_not_advance_progress(monkeypatch):
    updates = []

    class FakeTqdm:
        def __init__(self, total, desc):
            self.total = total

        def update(self, n):
            updates.append(n)

        def set_postfix_str(self, s):
            pass

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "tqdm", types.ModuleType("tqdm"))
    monkeypatch.setitem(sys.modules, "tqdm.auto", types.SimpleNamespace(tqdm=FakeTqdm))

    a, b, missing, txt = (pathlib.Path(n) for n in ("a.mp4", "b.mp4", "missing.mp4", "notes.txt"))

    def gen():
        yield 2
        yield (missing, "error", "Path does not exist", None)
        yield (txt, "skipped", "Not a video file", None)
        yield (a, "starting", "Processing...", None)
        yield (a, "exists", "Contact sheet already exists", pathlib.Path("a.sheet.jpg"))
        yield (b, "starting", "Processing...", None)
        yield (b, "skipped", "No video track found in container.", None)

    payload = consume_generator(gen(), show_progress=True, desc="test", command="thumbs")

    assert sum(updates) == 2
    assert (payload["schema"], payload["command"]) == (1, "thumbs")
    assert payload["summary"]["errors"] == 1
    assert payload["summary"]["skipped"] == 2
    assert len(payload["results"]) == 4
    assert payload["results"][2] == {
        "file": "a.mp4",
        "status": "exists",
        "message": "Contact sheet already exists",
        "output": "a.sheet.jpg",
        "fidelity": None,
    }


def test_report_shape():
    report = build_report("convert", 1, [(pathlib.Path("a.png"), "success", "ok", pathlib.Path("a.webp"))], 2.0)
    assert list(report) == ["schema", "command", "dry_run", "summary", "results"]
    assert report["dry_run"] is False
    planned = build_report("convert", 0, [], 0.0, dry_run=True, renamed=[])
    assert list(planned) == ["schema", "command", "dry_run", "renamed", "summary", "results"] and planned["dry_run"]
    assert list(report["summary"]) == [
        "total_found", "success", "exists", "skipped", "errors", "time_taken_seconds", "avg_time_seconds",
    ]  # fmt: skip
    assert report["summary"]["avg_time_seconds"] == 2.0
