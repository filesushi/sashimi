# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pathlib
import sys
import types

from sashimi.api._consume import consume_generator


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
        yield (missing, "error", "Path does not exist")
        yield (txt, "skipped", "Not a video file")
        yield (a, "starting", "Processing...")
        yield (a, "exists", "Contact sheet already exists")
        yield (b, "starting", "Processing...")
        yield (b, "skipped", "No video track found in container.")

    payload = consume_generator(gen(), show_progress=True, desc="test")

    assert sum(updates) == 2
    assert payload["summary"]["errors"] == 1
    assert payload["summary"]["skipped"] == 2
    assert len(payload["results"]) == 4
