# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pathlib
import time
from collections.abc import Iterable, Iterator
from typing import Any

from sashimi.api._batch import ResultTuple, YieldType

REPORT_SCHEMA = 1
"""Version of the report layout below; it changes only when a key is renamed or removed."""
STATUSES = ("success", "error", "exists", "skipped")


def build_report(
    command: str, total_found: int, results: Iterable[ResultTuple], seconds: float, **extra: Any
) -> dict[str, Any]:
    """
    The report every batch operation returns, and the CLI prints with ``--json``::

        {"schema": 1, "command": "convert",
         "summary": {"total_found", "success", "exists", "skipped", "errors", "time_taken_seconds", "avg_time_seconds"},
         "results": [{"file", "status", "message", "output"}, ...]}

    `extra` keys are added at the top level (``flatten`` adds ``dry_run``).
    """
    rows = list(results)
    counts = {status: 0 for status in STATUSES}
    for _, status, _, _ in rows:
        counts[status] = counts.get(status, 0) + 1
    success = counts["success"]
    return {
        "schema": REPORT_SCHEMA,
        "command": command,
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


def consume_generator(gen: Iterator[YieldType], show_progress: bool, desc: str, command: str = "") -> dict[str, Any]:
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

    return build_report(command or desc, total_files, results, time.perf_counter() - start_time)
