# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the benchmark manifest, suite builder, per-machine persistence, and resume."""

import json
from pathlib import Path

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
