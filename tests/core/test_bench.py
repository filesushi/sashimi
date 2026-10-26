# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the benchmark manifest, suite builder, per-machine persistence, and resume."""

import ast
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts.bench import manifest, run, tasks


def test_manifest_covers_all_20_fixtures():
    assert len(manifest.FIXTURES) == 20
    derf = [f for f in manifest.FIXTURES if f.category == "derf"]
    noise = [f for f in manifest.FIXTURES if f.category == "synthetic_noise"]
    assert len(derf) == 14
    assert len(noise) == 6
    assert len({f.rel_path for f in manifest.FIXTURES}) == 20
    assert len({f.sha256 for f in manifest.FIXTURES}) == 20
    assert all(f.size_bytes > 0 and f.width > 0 and f.height > 0 and f.fps > 0 for f in manifest.FIXTURES)


def test_every_suite_builds_valid_cases():
    for suite in tasks.SUITES:
        cases = tasks.build_suite_cases(suite, default_workers=2)
        assert cases, suite
        for case in cases:
            assert case.suite == suite
            assert case.task_name in tasks.TASKS
            assert case.rel_path in manifest.FIXTURES_BY_PATH
            assert case.workers >= 1


def test_machine_detection_respects_environment_override(monkeypatch):
    monkeypatch.setenv("SASHIMI_BENCH_MACHINE", "ubuntu-laptop-xps13")
    assert run.detect_machine_id() == "ubuntu-laptop-xps13"


def test_save_and_compare_reports_grouped_by_machine(tmp_path: Path):
    base_env = {
        "machine": "wsl2-desktop-ryzen3600",
        "timestamp_utc": "2026-10-25T14:00:00+00:00",
        "sashimi_version": "0.6.1",
        "git": {"commit": "abcdef1234", "dirty": False},
        "python_version": "3.14.6",
        "pillow_version": "11.3.0",
        "ffmpeg_version": "ffmpeg version 8.1.2",
        "platform": "Linux-WSL2",
        "is_wsl2": True,
        "cpu_model": "AMD Ryzen 5 3600",
        "cpu_count_logical": 12,
        "ram_gb": 47.0,
        "default_workers": 2,
        "load_avg_start": [0.2, 0.3, 0.4],
        "load_avg_end": [0.5, 0.4, 0.4],
    }
    report_a = {
        "schema": "sashimi.benchmark.v1",
        "status": "completed",
        "environment": base_env,
        "summary": {
            "total": 1,
            "completed": 1,
            "passed": 1,
            "failed": 0,
            "suites": ["smoke"],
            "total_wall_s": 1.2,
            "total_cpu_s": 2.1,
        },
        "results": [
            {
                "suite": "smoke",
                "task": "gif-diff-bayer",
                "category": "gif-methods",
                "rel_path": "derf/FourPeople_1280x720_60_h264.mp4",
                "clip": "FourPeople",
                "input_codec": "h264",
                "workers": 2,
                "ok": True,
                "wall_s": 1.2,
                "cpu_total_s": 2.1,
                "cpu_efficiency": 1.75,
                "peak_rss_mb": 105.0,
                "output_bytes": 3_500_000,
                "output_sha256": "deadbeef",
                "output_summary": "GIF 480x270 60f",
                "route": "encode",
            }
        ],
    }
    json_a, md_a = run.save_report(report_a, tmp_path)
    assert json_a.parent == tmp_path / "wsl2-desktop-ryzen3600"
    assert (tmp_path / "wsl2-desktop-ryzen3600" / "latest.json").is_file()
    assert (tmp_path / "wsl2-desktop-ryzen3600" / "LATEST.md").is_file()
    assert "wsl2-desktop-ryzen3600" in md_a.read_text(encoding="utf-8")

    report_b = json.loads(json.dumps(report_a))
    report_b["environment"]["machine"] = "ubuntu-server-xeon"
    report_b["results"][0]["wall_s"] = 0.9
    json_b, _ = run.save_report(report_b, tmp_path)
    assert json_b.parent == tmp_path / "ubuntu-server-xeon"

    diff_md = run.compare_reports(json_a, json_b)
    assert "wsl2-desktop-ryzen3600" in diff_md
    assert "ubuntu-server-xeon" in diff_md
    assert "-25.0%" in diff_md
    assert "yes" in diff_md


ROOT = Path(__file__).resolve().parent.parent.parent
POSIX_ONLY = frozenset({"resource", "fcntl", "pwd", "grp", "termios", "pty", "tty", "crypt", "syslog", "spwd", "nis"})
"""Standard modules that don't exist on Windows, where the desktop app and CI's Windows jobs run."""


def test_the_test_suite_and_the_harness_import_without_resource():
    code = (
        "import sys; sys.modules['resource'] = None; import pytest; "
        "import scripts.bench.run as run; assert run.rusage() is None; "
        "sys.exit(pytest.main(['--collect-only', '-q', '-p', 'no:cacheprovider', '-o', 'log_cli=false', 'tests']))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    assert " collected" in result.stdout.strip().splitlines()[-1]


def _guarded(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.Try) and any(
            isinstance(h.type, ast.Name) and h.type.id in ("ImportError", "ModuleNotFoundError") for h in node.handlers
        ):
            return True
    return False


def test_no_code_imports_a_posix_only_module_unguarded():
    found = []
    for folder in ("src", "tests", "scripts"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names = [node.module.split(".")[0]]
                if set(names) & POSIX_ONLY and not _guarded(node, parents):
                    found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert found == []


class FakeResource:
    RUSAGE_SELF, RUSAGE_CHILDREN = 0, 1

    def getrusage(self, who):
        return SimpleNamespace(ru_utime=1.5, ru_stime=0.5, ru_maxrss=(100 if who == 0 else 300) * 1024 * 1024)


def test_peak_memory_is_in_mib_on_every_platform(monkeypatch):
    monkeypatch.setattr(run, "resource", FakeResource())
    monkeypatch.setattr(run, "MAXRSS_BYTES", 1)
    assert run.rusage() == (3.0, 1.0, 100.0, 300.0)
    monkeypatch.setattr(run, "MAXRSS_BYTES", 1024)
    assert run.rusage() == (3.0, 1.0, 100.0 * 1024, 300.0 * 1024)
    monkeypatch.setattr(run, "resource", None)
    assert run.rusage() is None


def _record(**extra):
    return {
        "suite": "smoke",
        "task": "gif-diff-bayer",
        "category": "gif-methods",
        "rel_path": "derf/FourPeople_1280x720_60_h264.mp4",
        "clip": "FourPeople",
        "input_codec": "h264",
        "workers": 2,
        "ok": True,
        "wall_s": 1.2,
        "output_bytes": 10,
        "output_sha256": "x",
        "output_summary": "GIF",
        **extra,
    }


def test_reports_without_cpu_or_memory_render_as_unavailable(tmp_path: Path):
    env = {
        "machine": "windows-desktop",
        "timestamp_utc": "2026-10-25T14:00:00+00:00",
        "sashimi_version": "0.7.0",
        "git": {"commit": "abc", "dirty": False},
        "python_version": "3.14.6",
        "pillow_version": "11.3.0",
        "ffmpeg_version": "ffmpeg version 8.1.2",
        "platform": "Windows-11",
        "is_wsl2": False,
        "cpu_model": "x",
        "cpu_count_logical": 8,
        "ram_gb": None,
        "default_workers": 2,
        "load_avg_start": [0.0, 0.0, 0.0],
    }
    result = _record(cpu_total_s=None, cpu_efficiency=None, peak_rss_mb=None)
    report = {
        "environment": env,
        "summary": {"total": 1, "passed": 1, "total_wall_s": 1.2, "total_cpu_s": None},
        "results": [result],
    }
    md = run.render_markdown_report(report)
    assert "| 1.20 | n/a | n/a | n/a |" in md
    assert "(n/a CPU)" in md
    json_a, _ = run.save_report(report, tmp_path)
    assert "| n/a | n/a | n/a / n/a |" in run.compare_reports(json_a, json_a)


def test_resume_continues_the_same_report_after_midnight(tmp_path: Path, monkeypatch, capsys):
    started = {
        "machine": "m",
        "timestamp_utc": "2026-10-25T23:30:00+00:00",
        "sashimi_version": "0.7.0",
        "git": {"commit": "abc", "dirty": False},
        "python_version": "3.14.6",
        "pillow_version": "11.3.0",
        "ffmpeg_version": "ffmpeg version 8.1.2",
        "platform": "Linux",
        "is_wsl2": False,
        "cpu_model": "x",
        "cpu_count_logical": 8,
        "ram_gb": 16.0,
        "default_workers": 2,
        "load_avg_start": [3.0, 3.0, 3.0],
    }
    cases = tasks.build_suite_cases("smoke", default_workers=2)
    first = cases[0]
    done = _record(task=first.task_name, rel_path=first.rel_path, workers=first.workers, cpu_total_s=2.0)
    partial = {
        "schema": "sashimi.benchmark.v1",
        "status": "running",
        "environment": started,
        "summary": {"total": len(cases), "passed": 1, "total_wall_s": 100.0, "total_cpu_s": 2.0},
        "results": [done],
    }
    run.save_report(partial, tmp_path)
    next_day = {**started, "timestamp_utc": "2026-10-26T00:10:00+00:00", "load_avg_start": [0.1, 0.1, 0.1]}
    monkeypatch.setattr(run, "collect_environment", lambda machine, workers: dict(next_day))
    measured = []

    def execute(case, data_dir):
        measured.append((case.task_name, case.rel_path, case.workers))
        return _record(task=case.task_name, rel_path=case.rel_path, workers=case.workers, cpu_total_s=1.0)

    monkeypatch.setattr(run, "execute_case", execute)
    assert run.main(["--suite", "smoke", "--machine", "m", "--resume", "--results-dir", str(tmp_path)]) == 0
    saved = sorted(p.name for p in (tmp_path / "m").iterdir())
    assert saved == ["2026-10-25_sashimi-0.7.0.json", "2026-10-25_sashimi-0.7.0.md", "LATEST.md", "latest.json"]
    report = json.loads((tmp_path / "m" / "2026-10-25_sashimi-0.7.0.json").read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    assert report["environment"]["timestamp_utc"] == started["timestamp_utc"]
    assert report["environment"]["load_avg_start"] == [3.0, 3.0, 3.0]
    assert measured == [(c.task_name, c.rel_path, c.workers) for c in cases[1:]]
    assert report["summary"]["passed"] == len(cases)


def test_resume_picks_the_newest_report_of_the_version_and_tag(tmp_path: Path):
    folder = tmp_path / "m"
    folder.mkdir()
    for name in (
        "2026-10-24_sashimi-0.7.0.json",
        "2026-10-25_sashimi-0.7.0.json",
        "2026-10-26_sashimi-0.7.0_methods.json",
        "2026-10-27_sashimi-0.6.1.json",
        "latest.json",
    ):
        (folder / name).write_text("{}", encoding="utf-8")
    assert run.resume_target("m", "0.7.0", tmp_path).name == "2026-10-25_sashimi-0.7.0.json"
    assert run.resume_target("m", "0.7.0", tmp_path, "methods").name == "2026-10-26_sashimi-0.7.0_methods.json"
    assert run.resume_target("m", "0.8.0", tmp_path) is None
    assert run.resume_target("other", "0.7.0", tmp_path) is None
