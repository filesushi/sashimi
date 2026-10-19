# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pathlib
import time
from typing import Any, Dict, Iterator, Set

from sashimi.api._batch import YieldType


def consume_generator(gen: Iterator[YieldType], show_progress: bool, desc: str) -> Dict[str, Any]:
    """Consumes a batch-operation generator, showing an optional tqdm bar, and returns a summary payload."""
    start_time = time.perf_counter()
    try:
        total_files = next(gen)
    except StopIteration:
        total_files = 0

    results = []
    counts = {"success": 0, "error": 0, "exists": 0, "skipped": 0}
    started: Set[pathlib.Path] = set()

    pbar = None
    if show_progress and isinstance(total_files, int) and total_files > 0:
        try:
            from tqdm.auto import tqdm  # type: ignore[import-untyped]

            pbar = tqdm(total=total_files, desc=desc)
        except ImportError:
            pass

    try:
        for item in gen:
            if not (isinstance(item, tuple) and len(item) == 3):
                continue
            file_path, status, message = item

            if status == "starting":
                started.add(file_path)
                if pbar:
                    pbar.set_postfix_str(f"Processing: {file_path.name[:25]}")
                continue

            counts[status] = counts.get(status, 0) + 1
            results.append({"file": str(file_path), "status": status, "message": message})

            if pbar and file_path in started:
                pbar.update(1)
    finally:
        if pbar:
            pbar.close()

    time_taken = time.perf_counter() - start_time
    return {
        "summary": {
            "total_found": total_files,
            "success": counts["success"],
            "exists": counts["exists"],
            "skipped": counts["skipped"],
            "errors": counts["error"],
            "time_taken_seconds": round(time_taken, 2),
            "avg_time_seconds": round(time_taken / counts["success"], 2) if counts["success"] > 0 else 0.0,
        },
        "results": results,
    }
