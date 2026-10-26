#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Reproducible benchmark harness for sashimi over the ``data/`` test-media corpus.

Usage examples::

    uv run python -m scripts.bench.run --verify
    uv run python -m scripts.bench.run --list
    uv run python -m scripts.bench.run --machine wsl2-desktop-ryzen3600
    uv run python -m scripts.bench.run --suite sheets --suite gif-methods
    uv run python -m scripts.bench.run --compare scripts/bench/results/wsl2-desktop-ryzen3600/latest.json ...

Saved benchmark reports are grouped by machine under ``scripts/bench/results/<machine>/``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image

import sashimi

try:
    import resource
except ImportError:
    resource = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.bench.manifest import (  # noqa: E402
    FIXTURES_BY_PATH,
    rebuild_missing,
    sha256_file,
    verify_all,
)
from scripts.bench.tasks import (  # noqa: E402
    DEFAULT_SUITES,
    SUITES,
    TASKS,
    BenchCase,
    build_suite_cases,
)

DEFAULT_DATA_DIR = ROOT / "data"
DEFAULT_RESULTS_DIR = ROOT / "scripts" / "bench" / "results"
KNOWN_MACHINES = (
    "wsl2-desktop-ryzen3600",
    "ubuntu-laptop-xps13",
    "ubuntu-server-xeon",
)
MAXRSS_BYTES = 1 if sys.platform == "darwin" else 1024
"""Bytes per unit of ``ru_maxrss``: bytes on macOS, kilobytes on Linux and the BSDs."""


def rusage() -> tuple[float, float, float, float] | None:
    """
    User and system CPU seconds of this process and its finished children together, and the peak
    resident memory of this process and of its largest child, in MiB; None where the ``resource``
    module doesn't exist (Windows), whose results then record CPU time and memory as unavailable.
    """
    if resource is None:
        return None
    me = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (
        me.ru_utime + children.ru_utime,
        me.ru_stime + children.ru_stime,
        me.ru_maxrss * MAXRSS_BYTES / (1024 * 1024),
        children.ru_maxrss * MAXRSS_BYTES / (1024 * 1024),
    )


def _shown(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def _with_unit(value: float | None, spec: str, unit: str) -> str:
    return "n/a" if value is None else format(value, spec) + unit


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown-cpu"


def _is_wsl() -> bool:
    try:
        release = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8", errors="replace").lower()
        return "microsoft" in release or "wsl" in release
    except OSError:
        return False


def _dmi_product() -> str:
    try:
        return Path("/sys/devices/virtual/dmi/id/product_name").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def detect_machine_id() -> str:
    """Detects the standardized machine identifier or falls back to a sanitized host slug."""
    env_machine = os.environ.get("SASHIMI_BENCH_MACHINE", "").strip()
    if env_machine:
        return env_machine
    cpu = _cpu_model()
    if "Ryzen 5 3600" in cpu and _is_wsl():
        return "wsl2-desktop-ryzen3600"
    if "Xeon" in cpu or "E-2386G" in cpu:
        return "ubuntu-server-xeon"
    product = _dmi_product()
    if "XPS" in product:
        return "ubuntu-laptop-xps13"
    host = platform.node().lower().replace(" ", "-") or "unknown-host"
    prefix = "wsl2" if _is_wsl() else platform.system().lower()
    return f"{prefix}-{host}"


def _ffmpeg_version_line() -> str:
    try:
        out = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        return out[0] if out else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _git_info() -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short=10", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"commit": "unknown", "dirty": False}


def _total_ram_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                return round(kb / (1024 * 1024), 1)
    except (OSError, ValueError):
        pass
    return None


def collect_environment(machine_id: str, default_workers: int) -> dict[str, Any]:
    """Collects machine, OS, CPU, RAM, Python, Pillow, FFmpeg, and sashimi metadata."""
    load_avg = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    return {
        "machine": machine_id,
        "timestamp_utc": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
        "sashimi_version": sashimi.__version__,
        "git": _git_info(),
        "python_version": platform.python_version(),
        "pillow_version": getattr(Image, "__version__", "unknown"),
        "ffmpeg_version": _ffmpeg_version_line(),
        "platform": platform.platform(),
        "is_wsl2": _is_wsl(),
        "cpu_model": _cpu_model(),
        "cpu_count_logical": os.cpu_count() or 1,
        "ram_gb": _total_ram_gb(),
        "default_workers": default_workers,
        "load_avg_start": [round(x, 2) for x in load_avg],
    }


def _describe_output(path: Path, ext: str) -> str:
    if ext == "json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if "container" in data:
            return f"probe:{data['container'].split(',')[0]} {data.get('width')}x{data.get('height')}"
        return f"metadata:{','.join(data.get('carriers', [])) or 'none'}"
    if ext in ("jpg", "jpeg", "png", "webp", "gif"):
        with Image.open(path) as img:
            frames = getattr(img, "n_frames", 1)
            anim = f" {frames}f" if frames > 1 else ""
            return f"{img.format} {img.width}x{img.height}{anim}"
    info = sashimi.probe(path)
    dims = f"{info.width}x{info.height}" if info.width else "audio"
    return f"{info.container.split(',')[0]} {dims} {info.duration or 0:.2f}s {','.join(info.codecs)}"


def run_worker_child(task_name: str, input_path: Path, workers: int) -> int:
    """Runs a single benchmark task inside a fresh child process and prints one JSON result line."""
    task = TASKS[task_name]
    with tempfile.TemporaryDirectory(prefix="sashimi-bench-") as tmp:
        scratch = Path(tmp)
        try:
            actual_input = task.setup(input_path, scratch, workers) if task.setup is not None else input_path
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps(
                    {
                        "ok": False,
                        "wall_s": 0.0,
                        "error_type": type(exc).__name__,
                        "error": f"setup failed: {exc}",
                    }
                )
            )
            return 1

        out_path = scratch / f"result.{task.ext}"
        before = rusage()
        t0 = time.perf_counter()
        try:
            returned = task.run(actual_input, out_path, workers)
            wall_s = time.perf_counter() - t0
        except Exception as exc:  # noqa: BLE001
            wall_s = time.perf_counter() - t0
            print(
                json.dumps(
                    {
                        "ok": False,
                        "wall_s": round(wall_s, 4),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
            )
            return 1

        after = rusage()
        user_s = sys_s = cpu_s = python_rss_mb = child_rss_mb = peak_rss_mb = combined_rss_mb = None
        if before is not None and after is not None:
            user_s = round(after[0] - before[0], 4)
            sys_s = round(after[1] - before[1], 4)
            cpu_s = round(user_s + sys_s, 4)
            python_rss_mb = round(after[2], 1)
            child_rss_mb = round(after[3], 1)
            peak_rss_mb = round(max(python_rss_mb, child_rss_mb), 1)
            combined_rss_mb = round(python_rss_mb + child_rss_mb, 1)

        out_bytes = out_path.stat().st_size
        out_sha256 = sha256_file(out_path)
        summary = _describe_output(out_path, task.ext)

        payload: dict[str, Any] = {
            "ok": True,
            "wall_s": round(wall_s, 4),
            "cpu_user_s": user_s,
            "cpu_sys_s": sys_s,
            "cpu_total_s": cpu_s,
            "cpu_efficiency": None if cpu_s is None else round(cpu_s / wall_s, 2) if wall_s > 0 else 0.0,
            "python_rss_mb": python_rss_mb,
            "child_rss_mb": child_rss_mb,
            "peak_rss_mb": peak_rss_mb,
            "combined_rss_mb": combined_rss_mb,
            "output_bytes": out_bytes,
            "output_sha256": out_sha256,
            "output_summary": summary,
        }
        if isinstance(returned, str) and task.ext != "json":
            payload["route"] = returned
        print(json.dumps(payload))
        return 0


def execute_case(case: BenchCase, data_dir: Path) -> dict[str, Any]:
    """Spawns an isolated Python child process to measure ``case``."""
    input_path = data_dir / case.rel_path
    spec = FIXTURES_BY_PATH.get(case.rel_path)
    task = TASKS[case.task_name]

    base_record: dict[str, Any] = {
        "suite": case.suite,
        "task": case.task_name,
        "category": task.category,
        "rel_path": case.rel_path,
        "clip": spec.clip if spec else Path(case.rel_path).stem,
        "input_codec": spec.codec if spec else "unknown",
        "input_res": f"{spec.width}x{spec.height}" if spec else "unknown",
        "input_fps": spec.fps if spec else None,
        "input_bytes": input_path.stat().st_size if input_path.is_file() else 0,
        "workers": case.workers,
    }
    if not input_path.is_file():
        return {**base_record, "ok": False, "error": f"Missing input file: {case.rel_path}"}

    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker-child",
            case.task_name,
            str(input_path),
            str(case.workers),
        ],
        capture_output=True,
        text=True,
    )
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        return {
            **base_record,
            "ok": False,
            "error": proc.stderr.strip() or f"Worker exited with code {proc.returncode}",
        }
    child_data = json.loads(lines[-1])
    return {**base_record, **child_data}


def format_bytes(num_bytes: int) -> str:
    """Formats a byte count as a compact human-readable string."""
    if num_bytes >= 1_000_000:
        return f"{num_bytes / 1_000_000:.2f} MB"
    if num_bytes >= 1_000:
        return f"{num_bytes / 1_000:.1f} KB"
    return f"{num_bytes} B"


def render_markdown_report(report: dict[str, Any]) -> str:
    """Renders a complete benchmark report dictionary into GitHub-flavored Markdown."""
    env = report["environment"]
    results: list[dict[str, Any]] = report["results"]
    summary = report["summary"]

    lines: list[str] = [
        f"# sashimi Benchmark Report — `{env['machine']}`",
        "",
        "## Environment",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| **Machine ID** | `{env['machine']}` |",
        f"| **Timestamp (UTC)** | `{env['timestamp_utc']}` |",
        f"| **CPU** | {env['cpu_model']} ({env['cpu_count_logical']} logical threads) |",
        f"| **RAM** | {env.get('ram_gb', '?')} GiB |",
        f"| **Platform** | `{env['platform']}` (WSL2: `{env['is_wsl2']}`) |",
        f"| **sashimi** | `{env['sashimi_version']}` (git `{env['git']['commit']}`, dirty=`{env['git']['dirty']}`) |",
        f"| **FFmpeg** | `{env['ffmpeg_version']}` |",
        f"| **Python / Pillow** | `{env['python_version']}` / `{env['pillow_version']}` |",
        f"| **Default `max_workers`** | `{env['default_workers']}` |",
        f"| **Host load avg (start → end)** | `{env['load_avg_start']}` → `{env.get('load_avg_end', env['load_avg_start'])}` |",
        f"| **Total cases** | {summary['passed']}/{summary['total']} passed in {summary['total_wall_s']:.1f}s wall ({_with_unit(summary.get('total_cpu_s'), '.1f', 's')} CPU) |",
        "",
    ]

    suites_seen: list[str] = []
    for item in results:
        if item["suite"] not in suites_seen:
            suites_seen.append(item["suite"])

    for suite in suites_seen:
        suite_rows = [r for r in results if r["suite"] == suite]
        lines.extend(
            [
                f"## Suite: `{suite}`",
                "",
                "| Task | Input Clip | Codec | Workers | Wall (s) | CPU (s) | CPU/Wall | Peak RSS (MB) | Output Size | Output Details |",
                "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for r in suite_rows:
            if not r.get("ok"):
                lines.append(
                    f"| `{r['task']}` | `{r['clip']}` | `{r['input_codec']}` | {r['workers']} | "
                    f"FAIL | - | - | - | - | `{r.get('error', 'failed')}` |"
                )
                continue
            route_note = f" [{r['route']}]" if "route" in r else ""
            lines.append(
                f"| `{r['task']}` | `{r['clip']}` | `{r['input_codec']}` | {r['workers']} | "
                f"{r['wall_s']:.2f} | {_shown(r.get('cpu_total_s'), '.2f')} | "
                f"{_shown(r.get('cpu_efficiency'), '.2f')}{'' if r.get('cpu_efficiency') is None else 'x'} | {_shown(r.get('peak_rss_mb'), '.1f')} | "
                f"{format_bytes(r['output_bytes'])} | "
                f"{r['output_summary']}{route_note} |"
            )
        lines.append("")

    return "\n".join(lines)


def compare_reports(path_a: Path, path_b: Path) -> str:
    """Compares two saved JSON benchmark reports and returns a Markdown comparison table."""
    rep_a = json.loads(path_a.read_text(encoding="utf-8"))
    rep_b = json.loads(path_b.read_text(encoding="utf-8"))
    env_a = rep_a["environment"]
    env_b = rep_b["environment"]

    key_fn = lambda r: (r["suite"], r["task"], r["rel_path"], r["workers"])  # noqa: E731
    map_a = {key_fn(r): r for r in rep_a["results"] if r.get("ok")}
    map_b = {key_fn(r): r for r in rep_b["results"] if r.get("ok")}
    shared_keys = [k for k in map_a if k in map_b]

    lines = [
        "# Benchmark Comparison",
        "",
        f"- **Baseline (A):** `{path_a.name}` — machine `{env_a['machine']}`, sashimi `{env_a['sashimi_version']}` (`{env_a['git']['commit']}`), `{env_a['timestamp_utc']}`",
        f"- **Candidate (B):** `{path_b.name}` — machine `{env_b['machine']}`, sashimi `{env_b['sashimi_version']}` (`{env_b['git']['commit']}`), `{env_b['timestamp_utc']}`",
        "",
        "| Suite | Task | Clip | Workers | Wall A (s) | Wall B (s) | Δ Wall | CPU A (s) | CPU B (s) | RSS A/B (MB) | Identical Bytes |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for key in shared_keys:
        a = map_a[key]
        b = map_b[key]
        wa, wb = a["wall_s"], b["wall_s"]
        pct = ((wb - wa) / wa * 100.0) if wa > 0 else 0.0
        same_hash = "yes" if a.get("output_sha256") == b.get("output_sha256") else "DIFF"
        lines.append(
            f"| `{a['suite']}` | `{a['task']}` | `{a['clip']} ({a['input_codec']})` | {a['workers']} | "
            f"{wa:.2f} | {wb:.2f} | {pct:+.1f}% | {_shown(a.get('cpu_total_s'), '.2f')} | "
            f"{_shown(b.get('cpu_total_s'), '.2f')} | "
            f"{_shown(a.get('peak_rss_mb'), '.0f')} / {_shown(b.get('peak_rss_mb'), '.0f')} | {same_hash} |"
        )
    return "\n".join(lines)


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically writes ``content`` to ``path`` via a sibling temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def report_paths(
    machine_id: str, date_str: str, version: str, results_dir: Path, tag: str | None = None
) -> tuple[Path, Path]:
    """Returns the ``(json_path, md_path)`` for a benchmark run under ``results_dir/<machine>/``."""
    machine_dir = results_dir / machine_id
    machine_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{tag}" if tag else ""
    stem = f"{date_str}_sashimi-{version}{suffix}"
    return machine_dir / f"{stem}.json", machine_dir / f"{stem}.md"


RESUME_MUST_MATCH = ("python_version", "pillow_version", "ffmpeg_version", "git", "default_workers")
"""Environment fields a resumed run should share with the run it continues; a difference is warned about."""


def shown_path(path: Path) -> str:
    """`path` relative to the repository when it is inside it, else as given."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def resume_target(machine_id: str, version: str, results_dir: Path, tag: str | None = None) -> Path | None:
    """
    The saved report ``--resume`` continues: this machine's newest dated report for `version` and
    `tag`, whatever day it was started on (a run started before midnight UTC and resumed after it
    continues the same file), or None when there is none.
    """
    suffix = f"_{tag}" if tag else ""
    machine_dir = results_dir / machine_id
    if not machine_dir.is_dir():
        return None
    pattern = re.compile(r"\d{4}-\d{2}-\d{2}" + re.escape(f"_sashimi-{version}{suffix}.json"))
    found = sorted(path for path in machine_dir.iterdir() if pattern.fullmatch(path.name))
    return found[-1] if found else None


def save_report(report: dict[str, Any], results_dir: Path, tag: str | None = None) -> tuple[Path, Path]:
    """Atomically saves a benchmark run (complete or in-progress) under ``results_dir/<machine>/``."""
    env = report["environment"]
    json_path, md_path = report_paths(
        env["machine"],
        env["timestamp_utc"][:10],
        env["sashimi_version"],
        results_dir,
        tag=tag,
    )
    machine_dir = json_path.parent

    json_text = json.dumps(report, indent=2) + "\n"
    md_text = render_markdown_report(report)

    _atomic_write_text(json_path, json_text)
    _atomic_write_text(md_path, md_text)
    _atomic_write_text(machine_dir / "latest.json", json_text)
    _atomic_write_text(machine_dir / "LATEST.md", md_text)
    return json_path, md_path


def _filter_cases(
    cases: list[BenchCase],
    task_pattern: str | None,
    clip_pattern: str | None,
) -> list[BenchCase]:
    filtered = cases
    if task_pattern:
        filtered = [c for c in filtered if fnmatch.fnmatch(c.task_name, task_pattern)]
    if clip_pattern:
        filtered = [
            c
            for c in filtered
            if fnmatch.fnmatch(c.rel_path, clip_pattern) or fnmatch.fnmatch(Path(c.rel_path).stem, clip_pattern)
        ]
    return filtered


def build_parser() -> argparse.ArgumentParser:
    """Builds the CLI argument parser for ``scripts/bench/run.py``."""
    parser = argparse.ArgumentParser(
        prog="scripts/bench/run.py",
        description="Run reproducible sashimi benchmarks over data/ and group saved reports by machine.",
    )
    parser.add_argument(
        "--machine",
        default=None,
        help=(
            "Machine identifier slug for grouping results "
            f"(known: {', '.join(KNOWN_MACHINES)}; default: auto-detected)."
        ),
    )
    parser.add_argument(
        "--suite",
        action="append",
        choices=[*SUITES, "all", "default"],
        help="Benchmark suite(s) to run (repeatable; default: 'default' which runs all standard suites).",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Glob filter on task name (e.g. 'sheet-*', 'gif-*').",
    )
    parser.add_argument(
        "--clip",
        default=None,
        help="Glob filter on clip name or relative path (e.g. '*ducks*', '*noise*').",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Default max_workers passed to sashimi operations (default: 2).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Path to the benchmark test media directory (default: data/).",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help="Root directory where per-machine results are saved (default: scripts/bench/results/).",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="Optional label appended to the saved report filename.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Print results without writing to scripts/bench/results/<machine>/.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from an existing report for this machine/date/tag, skipping already-passed cases.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify all 20 fixtures in data/ against the manifest (size, SHA-256, sashimi.probe) and exit.",
    )
    parser.add_argument(
        "--rebuild-missing",
        action="store_true",
        help="Rebuild any missing files in data/ before running.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all benchmark suites, tasks, and manifest fixtures and exit.",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BASELINE_JSON", "CANDIDATE_JSON"),
        type=Path,
        help="Compare two saved JSON benchmark reports and print a Markdown diff table.",
    )
    parser.add_argument(
        "--worker-child",
        nargs=3,
        metavar=("TASK", "INPUT", "WORKERS"),
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.worker_child:
        task_name, input_str, workers_str = args.worker_child
        return run_worker_child(task_name, Path(input_str), int(workers_str))

    if args.compare:
        print(compare_reports(args.compare[0], args.compare[1]))
        return 0

    if args.list:
        print("Suites:", ", ".join(SUITES))
        print("\nTasks:")
        for name, spec in TASKS.items():
            print(f"  {name:28s} [{spec.category:11s}] {spec.description}")
        print("\nFixtures:")
        for rel_path, f in FIXTURES_BY_PATH.items():
            print(
                f"  {rel_path:48s} {f.width}x{f.height} {f.fps:g}fps {f.codec:4s} "
                f"{format_bytes(f.size_bytes):>10s} ({f.bytes_per_pixel:.2f} B/px)"
            )
        return 0

    if args.rebuild_missing:
        with tempfile.TemporaryDirectory(prefix="sashimi-rebuild-") as tmp:
            rebuilt = rebuild_missing(args.data_dir, Path(tmp))
            if rebuilt:
                print(f"Rebuilt {len(rebuilt)} missing fixture(s): {', '.join(rebuilt)}", file=sys.stderr)

    if args.verify:
        checks = verify_all(args.data_dir, check_hash=True, check_probe=True)
        failed = [c for c in checks if not c["ok"]]
        for c in checks:
            status = "OK" if c["ok"] else f"FAIL ({'; '.join(c['problems'])})"
            print(f"{c['rel_path']:50s} {status}")
        print(f"\nVerified {len(checks) - len(failed)}/{len(checks)} fixtures in {args.data_dir}.")
        return 1 if failed else 0

    machine_id = args.machine or detect_machine_id()
    requested_suites = args.suite or ["default"]
    expanded_suites: list[str] = []
    for s in requested_suites:
        if s in ("all", "default"):
            for ds in DEFAULT_SUITES:
                if ds not in expanded_suites:
                    expanded_suites.append(ds)
        elif s not in expanded_suites:
            expanded_suites.append(s)

    cases: list[BenchCase] = []
    for suite_name in expanded_suites:
        cases.extend(build_suite_cases(suite_name, default_workers=args.workers))
    cases = _filter_cases(cases, args.task, args.clip)
    if not cases:
        print("No benchmark cases matched the given filters.", file=sys.stderr)
        return 2

    env = collect_environment(machine_id, args.workers)
    target_json, target_md = report_paths(
        machine_id,
        env["timestamp_utc"][:10],
        env["sashimi_version"],
        args.results_dir,
        tag=args.tag,
    )

    cache: dict[tuple[str, str, int], dict[str, Any]] = {}
    prior_wall = 0.0
    resumed = resume_target(machine_id, env["sashimi_version"], args.results_dir, args.tag) if args.resume else None
    if resumed is not None:
        prior = json.loads(resumed.read_text(encoding="utf-8"))
        changed = [key for key in RESUME_MUST_MATCH if prior["environment"].get(key) != env.get(key)]
        if changed:
            print(f"Warning: resuming a run made with a different {', '.join(changed)}.", file=sys.stderr)
        env = prior["environment"]
        target_json = resumed
        prior_wall = float(prior.get("summary", {}).get("total_wall_s", 0.0))
        for prev in prior.get("results", []):
            if prev.get("ok"):
                cache[(prev["task"], prev["rel_path"], prev["workers"])] = prev
        print(
            f"Resuming from {shown_path(resumed)} ({len(cache)} unique completed case(s) loaded).",
            file=sys.stderr,
        )

    print(
        f"Running {len(cases)} benchmark case(s) on machine '{machine_id}' "
        f"(sashimi {env['sashimi_version']}, workers={args.workers})...",
        file=sys.stderr,
    )
    if not args.no_save:
        print(
            f"Live checkpointing after every case to: {shown_path(target_json)}",
            file=sys.stderr,
        )

    results: list[dict[str, Any]] = []
    seen_in_this_run: set[tuple[str, str, int]] = set()
    t_start = time.perf_counter()

    def _build_report(status: str) -> dict[str, Any]:
        elapsed = round(prior_wall + (time.perf_counter() - t_start), 2)
        total_cpu = (
            None
            if resource is None
            else round(sum(r.get("cpu_total_s") or 0.0 for r in results if r.get("ok") and not r.get("reused")), 2)
        )
        passed_now = sum(1 for r in results if r.get("ok"))
        load_now = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
        env["load_avg_end"] = [round(x, 2) for x in load_now]
        return {
            "schema": "sashimi.benchmark.v1",
            "status": status,
            "environment": env,
            "summary": {
                "total": len(cases),
                "completed": len(results),
                "passed": passed_now,
                "failed": len(results) - passed_now,
                "suites": expanded_suites,
                "total_wall_s": elapsed,
                "total_cpu_s": total_cpu,
            },
            "results": results,
        }

    for idx, case in enumerate(cases, 1):
        cache_key = (case.task_name, case.rel_path, case.workers)
        if cache_key in cache:
            is_duplicate_in_run = cache_key in seen_in_this_run
            seen_in_this_run.add(cache_key)
            rec = {**cache[cache_key], "suite": case.suite, "reused": is_duplicate_in_run}
            results.append(rec)
            label = "reused" if is_duplicate_in_run else "resumed"
            print(
                f"[{idx:3d}/{len(cases):3d}] {case.suite}/{case.task_name} on {rec['clip']} ({rec['input_codec']}, w={case.workers}): "
                f"{label} (wall={rec['wall_s']:.2f}s cpu={_with_unit(rec.get('cpu_total_s'), '.2f', 's')})",
                file=sys.stderr,
            )
            if not args.no_save and not is_duplicate_in_run:
                save_report(_build_report("running"), args.results_dir, tag=args.tag)
            continue

        rec = execute_case(case, args.data_dir)
        cache[cache_key] = rec
        seen_in_this_run.add(cache_key)
        results.append(rec)
        if rec.get("ok"):
            print(
                f"[{idx:3d}/{len(cases):3d}] {case.suite}/{case.task_name} on {rec['clip']} ({rec['input_codec']}, w={case.workers}): "
                f"wall={rec['wall_s']:.2f}s cpu={_with_unit(rec.get('cpu_total_s'), '.2f', 's')} "
                f"rss={_with_unit(rec.get('peak_rss_mb'), '.1f', 'MB')} "
                f"size={format_bytes(rec['output_bytes'])}",
                file=sys.stderr,
            )
        else:
            print(
                f"[{idx:3d}/{len(cases):3d}] {case.suite}/{case.task_name} on {case.rel_path}: FAILED ({rec.get('error')})",
                file=sys.stderr,
            )
        if not args.no_save:
            save_report(_build_report("running"), args.results_dir, tag=args.tag)

    report = _build_report("completed")
    passed = report["summary"]["passed"]

    if not args.no_save:
        json_path, md_path = save_report(report, args.results_dir, tag=args.tag)
        print(f"\nSaved JSON report:     {shown_path(json_path)}", file=sys.stderr)
        print(f"Saved Markdown report: {shown_path(md_path)}", file=sys.stderr)

    print(render_markdown_report(report))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
