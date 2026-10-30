# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the manifest-driven corpus benchmark: task selection, outcomes, checks and recording."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from scripts.bench import corpus, run
from scripts.bench.corpus import CorpusEntry


def _entry(path, kind, categories=(), size=1, **facts):
    return CorpusEntry(path=path, kind=kind, categories=tuple(categories), facts=facts, size=size)


STILL = _entry("img/a.jpg", "image", ["gps"], 10, width=40, height=30, format="JPEG", frames=1)
GIF = _entry("img/b.gif", "image", ["animated-gif"], 5, width=20, height=20, format="GIF", frames=5)
APNG = _entry("img/c.png", "image", ["apng"], 6, width=20, height=20, format="PNG", frames=4)
CLIP = _entry("vid/d.mp4", "video", [], 100, width=64, height=48, duration=4.0, audio=["aac"])
MUTE = _entry("vid/e.webm", "video", ["no-audio", "location"], 50, width=64, height=48, duration=2.0, audio=[])
FILM = _entry("vid/f.mkv", "video", ["long"], 1000, width=64, height=48, duration=3600.0, audio=["aac"])


def _tasks(entry):
    return {case.task for case in corpus.select_cases([entry])}


def test_load_corpus_keeps_images_and_videos_and_joins_test_notes(tmp_path: Path):
    (tmp_path / "synthetic").mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {"path": "img/a.jpg", "kind": "image", "categories": ["gps"], "facts": {"width": 4}, "size": 3},
                {"path": "synthetic/vid/b.ts", "kind": "video", "categories": [], "facts": {}, "size": 9},
                {"path": "vid/sidecar.raw", "kind": "other"},
            ]
        ),
        encoding="utf-8",
    )
    notes = tmp_path / "synthetic" / "manifest.json"
    notes.write_text(json.dumps([{"path": "vid/b.ts", "tests": "MPEG-TS edge case"}]), encoding="utf-8")
    entries = corpus.load_corpus(manifest, notes)
    assert [e.path for e in entries] == ["img/a.jpg", "synthetic/vid/b.ts"]
    assert entries[0].categories == ("gps",) and entries[0].facts == {"width": 4}
    assert entries[1].tests == "MPEG-TS edge case" and entries[0].tests == ""
    assert not entries[1].readable


def test_tasks_are_chosen_by_kind_facts_and_categories():
    still, gif, apng = _tasks(STILL), _tasks(GIF), _tasks(APNG)
    assert "frames-info" in still and "probe" not in still
    assert not {t for t in still if t.startswith(("anim-", "vid-"))}
    assert {"anim-convert", "anim-to-mp4", "anim-frames-drop", "anim-frames-speed"} <= gif
    assert "anim-convert" in apng and "anim-to-mp4" not in apng
    clip, mute = _tasks(CLIP), _tasks(MUTE)
    assert "vid-to-mp3" in clip and "vid-to-mp3" not in mute
    assert "vid-strip-location" in mute and "vid-strip-location" not in clip
    assert "probe" in clip and "frames-info" not in clip
    assert not {t for t in clip if t.startswith(("img-", "anim-"))}


def test_cases_run_images_first_then_smaller_files_and_filter_by_globs():
    cases = corpus.select_cases([FILM, CLIP, STILL, GIF])
    order = list(dict.fromkeys(case.path for case in cases))
    assert order == ["img/b.gif", "img/a.jpg", "vid/d.mp4", "vid/f.mkv"]
    only = corpus.select_cases([FILM, CLIP], task_glob="vid-to-*", path_glob="vid/d*")
    assert {c.path for c in only} == {"vid/d.mp4"} and all(c.task.startswith("vid-to-") for c in only)


def test_long_inputs_are_measured_on_a_stretch():
    task = corpus.TASKS_BY_NAME
    assert task["vid-to-webm"].argv(FILM)[-4:] == ["--start", "900.0", "--duration", "60"]
    assert task["vid-to-webm"].argv(CLIP) == ["convert", "{input}", "webm"]
    assert task["vid-to-mp4"].argv(FILM) == ["convert", "{input}", "mp4"]
    assert task["vid-rotate"].setup(FILM) == "excerpt" and task["vid-rotate"].setup(CLIP) is None
    assert corpus.section(FILM) == (1080.0, 10.0)
    assert corpus.section(CLIP) == (1.2, 1.6)
    assert corpus.section(_entry("vid/x.mkv", "video")) == (1.0, 3.0)
    assert task["vid-extract-accurate"].argv(CLIP)[-1] == "--accurate"
    assert task["anim-frames-drop"].argv(GIF) == ["frames", "{input}", "--drop", "2,4"]
    assert task["anim-convert"].argv(GIF)[-1] == "webp" and task["anim-convert"].argv(APNG)[-1] == "gif"


def test_cli_arguments_name_the_input_the_output_folder_and_json(tmp_path: Path):
    src, out = tmp_path / "a b.jpg", tmp_path / "out"
    args = corpus.cli_argv(corpus.TASKS_BY_NAME["img-rotate"], STILL, src, out)
    assert args == ["rotate", str(src), "90", "--output-dir", str(out), "--json"]
    assert corpus.cli_argv(corpus.TASKS_BY_NAME["cli-info"], STILL, src, out) == ["info", str(src), "--json"]


def _batch(status, message="", success=0, already=0):
    return {
        "exit_code": 0 if status in ("success", "already") else 1,
        "errors": [],
        "cli_report": {
            "summary": {"success": success},
            "already_done": already,
            "results": [{"status": status, "message": message, "output": "x"}],
        },
    }


def test_outcomes_read_like_a_user_sees_them():
    assert corpus.outcome(_batch("success", success=1)) == "success"
    assert corpus.outcome(_batch("skipped", "Already JPEG", already=1)) == "already"
    assert corpus.outcome(_batch("skipped", "thumbs works on videos, not .jpg files.")) == "refused"
    assert corpus.outcome(_batch("error", "Failed: ffmpeg exited 1")) == "failed"
    limited = _batch("error", "This image is too large to process.")
    limited["errors"] = [{"type": "MediaTooLargeError", "user_error": True, "limit": "max_pixels"}]
    assert corpus.outcome(limited) == "refused" and corpus.refused_by_limit(limited)
    assert corpus.message(limited) == "This image is too large to process."
    crash = _batch("error", "Failed: KeyError: 'x'")
    crash["errors"] = [{"type": "KeyError", "user_error": False, "traceback": "..."}]
    assert corpus.outcome(crash) == "failed" and not corpus.refused_by_limit(crash)
    assert corpus.outcome({"outcome": "timeout"}) == "timeout"
    assert corpus.outcome({"outcome": "crash"}) == "crash"
    assert corpus.outcome({"exit_code": None, "errors": [], "returned": {"width": 1}}) == "success"
    info_error = {"exit_code": 1, "errors": [], "cli_report": None, "stderr_tail": "Error: not media."}
    assert corpus.outcome(info_error) == "refused"
    assert corpus.outcome({**info_error, "stderr_tail": "Traceback (most recent call last):"}) == "failed"
    assert corpus.outcome({"exit_code": 0, "errors": [], "cli_report": {"width": 3}}) == "success"


def _ok(output):
    record = _batch("success", success=1)
    record["outputs"] = {"x": {"readable": True, "bytes": 10, **output}}
    return record


def test_checks_flag_outputs_that_differ_from_what_the_task_gives():
    task = corpus.TASKS_BY_NAME
    src = {"display": [40, 30], "width": 40, "height": 30, "frames": 1}
    assert corpus.assess(task["img-to-png"], STILL, src, _ok({"format": "PNG", "display": [40, 30]})) == []
    lost = corpus.assess(task["img-to-png"], STILL, src, _ok({"format": "PNG", "display": [30, 40]}))
    assert lost == ["shown as 30x40, source shows as 40x30"]
    assert corpus.assess(task["img-rotate"], STILL, src, _ok({"display": [30, 40]})) == []
    assert corpus.assess(task["img-edit"], STILL, src, _ok({"display": [30, 30]})) == []
    assert corpus.assess(task["img-edit"], STILL, src, _ok({"display": [40, 30]}))
    assert corpus.assess(task["img-resize"], STILL, src, _ok({"display": [40, 30]})) == []
    assert corpus.assess(task["img-to-webp"], STILL, src, _ok({"format": "PNG", "display": [40, 30]}))
    assert corpus.assess(task["img-to-png"], STILL, src, {**_batch("success", success=1), "outputs": {}}) == [
        "success reported but no output written"
    ]
    gif_src = {"display": [20, 20], "frames": 5, "duration_ms": 500}
    half = _ok({"display": [20, 20], "frames": 3, "duration_ms": 300})
    assert corpus.assess(task["anim-frames-drop"], GIF, gif_src, half) == []
    assert corpus.assess(task["anim-frames-speed"], GIF, gif_src, _ok({"frames": 5, "duration_ms": 250})) == []
    slow = corpus.assess(task["anim-frames-speed"], GIF, gif_src, _ok({"frames": 5, "duration_ms": 500}))
    assert slow == ["plays 500 ms, expected about 250 ms"]
    strip = corpus.assess(task["img-strip-all"], STILL, src, _ok({"width": 40, "height": 30, "gps": True}))
    assert strip == ["location still present after strip"]


def test_checks_of_video_outputs_allow_for_keyframes_and_rounding():
    task = corpus.TASKS_BY_NAME
    src = {"display": [64, 48], "width": 64, "height": 48, "duration": 4.0, "streams": ["video", "audio"]}
    good = {"container": "mov,mp4", "display": [64, 48], "width": 64, "height": 48, "duration": 4.02}
    assert corpus.assess(task["vid-to-mp4"], CLIP, src, _ok(good)) == []
    assert corpus.assess(task["vid-to-mp4"], CLIP, src, _ok({**good, "duration": 2.0})) == [
        "lasts 2.00 s, expected 4.00 s"
    ]
    broken = {**good, "decode_errors": 2, "decode_first_errors": ["[h264] error while decoding MB 3 4"]}
    assert corpus.assess(task["vid-to-mp4"], CLIP, src, _ok(broken))
    rounding = ["[null] Application provided invalid, non monotonically increasing dts to muxer in stream 0: 4 >= 4"]
    assert (
        corpus.assess(task["vid-to-mp4"], CLIP, src, _ok({**good, "decode_errors": 1, "decode_first_errors": rounding}))
        == []
    )
    copy = {"container": "matroska", "width": 64, "height": 48, "duration": 2.4}
    keyframes = _entry("vid/d.mp4", "video", width=64, height=48, duration=4.0, max_keyframe_gap=1.0)
    assert corpus.assess(task["vid-extract"], keyframes, src, _ok(copy)) == []
    assert corpus.assess(task["vid-extract"], keyframes, src, _ok({**copy, "duration": 3.5}))
    assert corpus.assess(task["vid-rotate"], CLIP, src, _ok({**good, "display": [48, 64]})) == []
    assert corpus.assess(task["vid-rotate"], CLIP, src, _ok({**good, "display": [64, 48]}))
    gif = {"format": "GIF", "display": [64, 48], "duration_ms": 4000}
    assert corpus.assess(task["vid-to-gif"], CLIP, src, _ok(gif)) == []
    film_src = {**src, "duration": 3600.0}
    stretch = {"container": "matroska", "display": [64, 48], "duration": 60.0}
    assert corpus.assess(task["vid-to-webm"], FILM, film_src, _ok(stretch)) == []
    excerpt = {**_ok({**good, "display": [48, 64], "duration": 60.0}), "input_used": "lossless excerpt, 60 s"}
    assert corpus.assess(task["vid-rotate"], FILM, film_src, excerpt) == []
    assert corpus.normalised_costs(FILM, {**excerpt, "task": "vid-rotate", "cpu_s": 30.0})["cpu_s_per_input_s"] == 0.5


def test_reports_resume_by_version_and_tag_and_summarise_outcomes(tmp_path: Path):
    folder = tmp_path / "m"
    folder.mkdir()
    for name in ("2026-10-28_sashimi-0.7.0_corpus.json", "2026-10-29_sashimi-0.7.0_corpus_x.json", "latest.json"):
        (folder / name).write_text("{}", encoding="utf-8")
    assert corpus.resume_path(tmp_path, "m", "0.7.0", None).name == "2026-10-28_sashimi-0.7.0_corpus.json"
    assert corpus.resume_path(tmp_path, "m", "0.7.0", "x").name == "2026-10-29_sashimi-0.7.0_corpus_x.json"
    assert corpus.resume_path(tmp_path, "m", "0.8.0", None) is None
    results = [
        {"task": "t", "path": "a", "mode": "default", "result": "refused", "cpu_s": 1.0, "suspects": []},
        {"task": "t", "path": "a", "mode": "capability", "result": "success", "cpu_s": 2.0, "suspects": []},
        {"task": "t", "path": "b", "mode": "default", "result": "success", "cpu_s": 0.5, "suspects": ["odd"]},
    ]
    summary = corpus.summarise(results)
    assert summary["by_task"] == {"t": {"refused": 1, "success": 1, "suspect": 1}}
    assert summary["total_cpu_s"] == 3.5


def test_power_state_and_tree_memory_are_recorded():
    state = corpus.power_state()
    assert set(state) == {"supplies_online", "power_profile", "energy_performance_preference"}
    proc = subprocess.Popen([sys.executable, "-c", "import time; x = bytearray(30_000_000); time.sleep(1.5)"])
    with corpus.TreeMemory(proc.pid, interval=0.05) as memory:
        proc.wait()
    if memory.available:
        assert memory.peak_mb is not None and memory.peak_mb > 20


@pytest.fixture
def small_corpus(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    (data / "img").mkdir(parents=True)
    Image.new("RGBA", (64, 48), (200, 10, 10, 128)).save(data / "img" / "alpha.png")
    frames = [Image.new("RGB", (32, 32), (i * 50, 255 - i * 50, 0)) for i in range(5)]
    frames[0].save(data / "img" / "anim.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    Image.new("1", (9000, 8000)).save(data / "img" / "big.png")
    manifest = data / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {"path": "img/alpha.png", "kind": "image", "categories": ["transparency"], "size": 1,
                 "facts": {"width": 64, "height": 48, "format": "PNG", "frames": 1}},
                {"path": "img/anim.gif", "kind": "image", "categories": ["animated-gif"], "size": 2,
                 "facts": {"width": 32, "height": 32, "format": "GIF", "frames": 5}},
                {"path": "img/big.png", "kind": "image", "categories": ["huge-image"], "size": 3,
                 "facts": {"width": 9000, "height": 8000, "format": "PNG", "frames": 1}},
            ]
        ),
        encoding="utf-8",
    )  # fmt: skip
    return manifest


def test_a_case_runs_in_its_own_process_and_records_cost_and_output(small_corpus: Path, tmp_path: Path):
    entries = {e.path: e for e in corpus.load_corpus(small_corpus)}
    case = corpus.CorpusCase("img-to-webp", "img/alpha.png")
    record = corpus.execute(case, entries[case.path], small_corpus.parent, tmp_path / "scratch", timeout=120)
    assert corpus.outcome(record) == "success", record
    if run.rusage() is None:
        assert "cpu_s" not in record and "python_rss_mb" not in record
    else:
        assert record["cpu_s"] is not None and record["cpu_s"] >= 0
    [(name, output)] = record["outputs"].items()
    assert name == "alpha.png.webp"
    assert output["format"] == "WEBP" and output["display"] == [64, 48] and output["transparency"]
    assert output["alpha_range"] == [128, 128]
    src = corpus.describe_source(entries[case.path], small_corpus.parent / case.path)
    assert corpus.assess(corpus.TASKS_BY_NAME["img-to-webp"], entries[case.path], src, record) == []
    drop = corpus.CorpusCase("anim-frames-drop", "img/anim.gif")
    record = corpus.execute(drop, entries[drop.path], small_corpus.parent, tmp_path / "scratch", timeout=120)
    assert corpus.outcome(record) == "success" and next(iter(record["outputs"].values()))["frames"] == 3
    gif_src = corpus.describe_source(entries[drop.path], small_corpus.parent / drop.path)
    assert gif_src["duration_ms"] == 500
    assert corpus.assess(corpus.TASKS_BY_NAME["anim-frames-drop"], entries[drop.path], gif_src, record) == []


@pytest.mark.parametrize("process_groups", [True, False], ids=["posix", "windows"])
def test_a_case_past_its_timeout_is_stopped_on_every_platform(
    small_corpus: Path, tmp_path: Path, monkeypatch, process_groups
):
    if not process_groups:
        monkeypatch.delattr(corpus.os, "killpg", raising=False)
    elif not hasattr(corpus.os, "killpg"):
        pytest.skip("process groups are POSIX only")
    entries = {e.path: e for e in corpus.load_corpus(small_corpus)}
    case = corpus.CorpusCase("img-to-webp", "img/alpha.png")
    record = corpus.execute(case, entries[case.path], small_corpus.parent, tmp_path / "scratch", timeout=0.01)
    assert corpus.outcome(record) == "timeout" and record["timeout_s"] == 0.01


def test_a_limit_refusal_is_run_again_with_every_limit_off(small_corpus: Path, tmp_path: Path):
    results = tmp_path / "results"
    argv = ["--corpus", str(small_corpus), "--results-dir", str(results), "--scratch-dir", str(tmp_path / "s")]
    assert run.main([*argv, "--machine", "m", "--task", "img-resize", "--clip", "img/big.png"]) == 0
    [saved] = (results / "m").glob("*_corpus.json")
    report = json.loads(saved.read_text(encoding="utf-8"))
    assert [(r["mode"], r["result"]) for r in report["results"]] == [("default", "refused"), ("capability", "success")]
    assert report["results"][0]["errors"][0]["limit"] == "max_pixels"
    assert "megapixels" in report["results"][0]["message"]
    assert report["status"] == "completed" and report["environment"]["power_end"] is not None
    assert "img/big.png" in report["sources"] and saved.with_suffix(".md").is_file()
    assert run.main([*argv, "--machine", "m", "--task", "img-resize", "--clip", "img/big.png", "--resume"]) == 0
    assert len(json.loads(saved.read_text(encoding="utf-8"))["results"]) == 2


def test_a_relative_scratch_folder_still_records_every_result(small_corpus: Path, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    argv = ["--corpus", str(small_corpus), "--results-dir", "results", "--scratch-dir", "scratch", "--machine", "m"]
    assert run.main([*argv, "--task", "img-to-webp", "--clip", "img/alpha.png"]) == 0
    [saved] = (tmp_path / "results" / "m").glob("*_corpus.json")
    [record] = json.loads(saved.read_text(encoding="utf-8"))["results"]
    assert record["result"] == "success" and record["outputs"]


def test_checking_an_image_leaves_pillows_own_limit_as_it_was(small_corpus: Path):
    before = Image.MAX_IMAGE_PIXELS
    assert corpus.describe_image(small_corpus.parent / "img" / "big.png")["width"] == 9000
    assert Image.MAX_IMAGE_PIXELS == before
