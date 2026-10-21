# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pathlib
import time
from collections.abc import Iterable, Iterator
from typing import Any

from sashimi.api._batch import Batch, BatchPlan, ResultTuple, YieldType

REPORT_SCHEMA = 1
"""Version of the report layout below; it changes only when a key is renamed or removed."""
STATUSES = ("success", "error", "exists", "skipped")


def build_report(
    command: str,
    total_found: int,
    results: Iterable[ResultTuple],
    seconds: float,
    *,
    dry_run: bool = False,
    plan: BatchPlan | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """
    The report every batch operation returns, and the CLI prints with ``--json``::

        {"schema": 1, "command": "convert", "dry_run": false,
         "kinds": {"video": 44, "image": 23}, "routes": {"transform": 44, "edit": 23},
         "outputs_left_out": [{"file", "made_from"}, ...], "in_output_dir": ["...", ...],
         "already_done": ["...", ...],
         "clash_note": "..." or null,
         "summary": {"total_found", "success", "exists", "skipped", "errors", "time_taken_seconds", "avg_time_seconds"},
         "results": [{"file", "status", "message", "output"}, ...]}

    ``dry_run`` is true when nothing was written and the results say what would have been.
    With the batch's `plan`, ``kinds`` and ``routes`` count the files it planned to process,
    ``outputs_left_out`` lists the command's own earlier outputs the run left out,
    each with the file it was made from, ``in_output_dir`` the files found inside ``output_dir``
    and left out, ``already_done`` the files named directly that are already what the command
    makes (``Already JPEG``; skipped, but not a problem), and ``clash_note`` names the pattern that gave inputs
    one output name, and where it was set. `extra` keys are added at the top level (``flatten`` adds
    ``renamed`` and ``removed_dirs``).
    """
    rows = list(results)
    counts = {status: 0 for status in STATUSES}
    for _, status, _, _ in rows:
        counts[status] = counts.get(status, 0) + 1
    success = counts["success"]
    return {
        "schema": REPORT_SCHEMA,
        "command": command,
        "dry_run": dry_run,
        **(
            {
                "kinds": plan.kinds,
                "routes": plan.routes,
                "outputs_left_out": [{"file": str(f), "made_from": str(s)} for f, s in plan.left_out],
                "in_output_dir": [str(f) for f in plan.in_output_dir],
                "already_done": [str(f) for f in plan.already_done],
                "clash_note": plan.clash_note,
            }
            if plan is not None
            else {}
        ),
        **extra,
        "summary": {
            "total_found": total_found,
            "success": success,
            "exists": counts["exists"],
            "skipped": counts["skipped"],
            "errors": counts["error"],
            "time_taken_seconds": round(seconds, 2),
            "avg_time_seconds": round(seconds / success, 2) if success else 0.0,
        },
        "results": [
            {"file": str(path), "status": status, "message": message, "output": str(out) if out else None}
            for path, status, message, out in rows
        ],
    }


def consume_generator(
    gen: Iterator[YieldType], show_progress: bool, desc: str, command: str = "", *, dry_run: bool = False
) -> dict[str, Any]:
    """Consumes a batch-operation generator, showing an optional tqdm bar, and returns :func:`build_report`'s payload."""
    start_time = time.perf_counter()
    try:
        first = next(gen)
    except StopIteration:
        first = 0
    total_files = first if isinstance(first, int) else 0

    results: list[ResultTuple] = []
    started: set[pathlib.Path] = set()

    pbar = None
    if show_progress and total_files > 0:
        try:
            from tqdm.auto import tqdm  # type: ignore[import-untyped]

            pbar = tqdm(total=total_files, desc=desc)
        except ImportError:
            pass

    try:
        for item in gen:
            if not (isinstance(item, tuple) and len(item) == 4):
                continue
            file_path, status, _, _ = item
            if status == "starting":
                started.add(file_path)
                if pbar:
                    pbar.set_postfix_str(f"Processing: {file_path.name[:25]}")
                continue
            results.append(item)
            if pbar and file_path in started:
                pbar.update(1)
    finally:
        if pbar:
            pbar.close()

    plan = gen.plan() if isinstance(gen, Batch) else None
    elapsed = time.perf_counter() - start_time
    return build_report(command or desc, total_files, results, elapsed, dry_run=dry_run, plan=plan)
