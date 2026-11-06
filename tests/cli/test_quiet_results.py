# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
A run over several files prints its summary and lists only what needs attention; ``-v`` lists every
file, and a run over one file, ``--dry-run`` and ``--json`` are as they were. Nothing a file holds or
is called reaches the terminal as a control character, on any route.
"""

import ast
import io
import json
import os
import pathlib
import re
import struct
import subprocess
import sys

import pytest
from PIL import Image
from typer.testing import CliRunner

import sashimi
from sashimi.cli import _output, settings
from sashimi.cli.main import app
from sashimi.core import _ffmpeg, video
from sashimi.config import VIDEO_CONVERT, CopyTrimConfig
from sashimi.core.fidelity import UNCHECKED, fidelity
from tests.conftest import needs_ffmpeg
from tests.core import test_hostile_metadata as hostile

runner = CliRunner()
ROW = re.compile(r"^(Success|Exists|Skipped|Error|Plan)\b", re.M)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, no_live_log_of_sashimi):
    monkeypatch.delenv(_ffmpeg.ENVIRONMENT, raising=False)
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    settings.load_raw_config.cache_clear()
    _ffmpeg.use_setting(None)
    yield tmp_path
    sashimi.set_ffmpeg_paths()
    _ffmpeg.use_setting(None)
    settings.load_raw_config.cache_clear()
    _output.set_verbosity(0)


def invoke(*args: str, code: int = 0):
    result = runner.invoke(app, list(args), env={"COLUMNS": "200"})
    assert result.exit_code == code, result.output
    return result.output


def photos(folder: pathlib.Path, count: int, name: str = "p{:03}.png") -> None:
    folder.mkdir(exist_ok=True)
    for number in range(count):
        Image.new("RGB", (8, 8), (number % 250, 0, 0)).save(folder / name.format(number))


def rows(output: str) -> list[str]:
    return ROW.findall(_flat(output))


def _flat(output: str) -> str:
    """Rows of a table as ``Status`` at a line's start, as when they don't fit one."""
    lines = []
    for line in output.splitlines():
        cells = [cell.strip() for cell in line.strip("│ ").split("│")]
        lines.append(cells[1] if len(cells) == 3 and cells[1] != "Status" else line)
    return "\n".join(lines)


IMAGE_RUNS = {
    "convert": ("convert", "in", "webp"),
    "resize": ("resize", "in", "--width", "4"),
    "rotate": ("rotate", "in", "90"),
    "edit": ("edit", "in", "--rotate", "90"),
    "strip": ("strip", "in"),
    "favicon": ("favicon", "in"),
}


@pytest.mark.parametrize("command", sorted(IMAGE_RUNS))
def test_a_run_and_its_rerun_print_no_rows_per_file(tmp_path, command):
    photos(tmp_path / "in", 4)
    fresh = invoke(*IMAGE_RUNS[command])
    assert "summary" in fresh and not rows(fresh), fresh
    again = invoke(*IMAGE_RUNS[command])
    assert "summary" in again and not rows(again), again
    assert "Showing the first" not in again and "Output already exists" not in again
    verbose = runner.invoke(app, ["-v", *IMAGE_RUNS[command]], env={"COLUMNS": "200"}).output
    assert rows(verbose).count("Exists") == 4, verbose


def test_frames_reruns_quietly(tmp_path):
    (tmp_path / "in").mkdir()
    frames = [Image.new("RGB", (8, 8), (number * 80, 0, 0)) for number in range(3)]
    for name in ("a.gif", "b.gif"):
        frames[0].save(tmp_path / "in" / name, save_all=True, append_images=frames[1:], duration=100)
    invoke("frames", "in", "--speed", "2")
    again = invoke("frames", "in", "--speed", "2")
    assert "summary" in again and not rows(again), again


@needs_ffmpeg
@pytest.mark.parametrize(
    "args",
    [
        ("thumbs", "in"),
        ("thumbs", "in", "--animated"),
        ("preview", "in"),
        ("remux", "in", "--output-format", "mkv"),
    ],
    ids=lambda args: " ".join(args[:1] + args[2:3]),
)
def test_video_commands_rerun_quietly(tmp_path, args, silent_video_blob):
    (tmp_path / "in").mkdir()
    for name in ("a.mp4", "b.mp4"):
        (tmp_path / "in" / name).write_bytes(silent_video_blob.read_bytes())
    invoke(*args)
    again = invoke(*args)
    assert "summary" in again and not rows(again), again


def test_an_error_among_many_is_listed_and_the_rest_are_not(tmp_path):
    photos(tmp_path / "in", 120)
    invoke("convert", "in", "webp")
    for number in range(100, 120):
        (tmp_path / "in" / f"p{number:03}.png.webp").unlink()
    (tmp_path / "in" / "p105.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    output = invoke("convert", "in", "webp", code=0)
    found = rows(output)
    assert found == ["Skipped"], output
    assert "p105.png" in output and "p110.png" not in output and "p001.png" not in output


def test_a_named_refusal_is_listed_and_fails(tmp_path):
    photos(tmp_path, 2)
    (tmp_path / "notes.txt").write_text("x")
    output = invoke("resize", "p000.png", "p001.png", "notes.txt", "--width", "4", code=1)
    assert rows(output) == ["Skipped"] and "notes.txt" in output


def test_a_file_already_in_the_format_is_counted_not_listed(tmp_path):
    photos(tmp_path, 2)
    Image.new("RGB", (8, 8)).save(tmp_path / "a.jpg")
    output = invoke("convert", "p000.png", "p001.png", "a.jpg", "jpg")
    assert not rows(output), output
    assert re.search(r"Skipped:\s+1", output)
    verbose = invoke("-v", "convert", "p000.png", "p001.png", "a.jpg", "jpg", "-f")
    assert "Skipped" in rows(verbose)


def test_a_folder_of_files_with_nothing_to_do_is_counted_not_listed(tmp_path):
    (tmp_path / "in").mkdir()
    for name in ("a.bmp", "b.bmp"):
        Image.new("RGB", (8, 8)).save(tmp_path / "in" / name)
    Image.new("RGB", (8, 8)).save(tmp_path / "in" / "c.png")
    output = invoke("strip", "in")
    assert not rows(output), output
    assert re.search(r"Skipped:\s+2", output)


def test_a_named_file_with_nothing_to_do_is_listed_and_fails(tmp_path):
    Image.new("RGB", (8, 8)).save(tmp_path / "a.bmp")
    Image.new("RGB", (8, 8)).save(tmp_path / "c.png")
    output = invoke("strip", "a.bmp", "c.png", code=1)
    assert rows(output) == ["Skipped"] and "a.bmp" in output


def test_a_name_clash_is_listed(tmp_path):
    photos(tmp_path, 2)
    output = invoke("convert", ".", "webp", "--output-name", "same.webp", "-y")
    assert rows(output) == ["Skipped", "Skipped"], output
    assert "name clash" in output


def result(status: str, *, path: str = "a.mov", **told):
    from sashimi.api._batch import FileResult

    made = fidelity("picture", **told) if status == "success" else None
    return FileResult(pathlib.Path(path), status, "message", None, made)


@pytest.mark.parametrize(
    "command, told, listed",
    [
        ("convert", {"lost": ["another audio track"]}, True),
        ("convert", {"lost": ["GoPro GPS track"]}, True),
        ("convert", {"lost": ["chapters"]}, True),
        ("convert", {"lost": ["subtitles"]}, True),
        ("convert", {"lost": ["animation (20 frames)"]}, True),
        ("convert", {"lost": ["2 more pages"]}, True),
        ("convert", {"changed": ["tone-mapped from HLG"]}, True),
        ("strip", {"notes": ["The motion photo's video was removed: it holds metadata"]}, True),
        ("convert", {"changed": ["sashimi couldn't check this output against its source"], "checked": False}, True),
        ("thumbs", {"changed": ["tone-mapped from HLG"], "lost": ["the source's metadata"]}, False),
        ("preview", {"lost": ["the source's metadata"]}, False),
        ("convert", {"lost": ["sound"]}, False),
        ("convert", {"lost": ["EXIF", "colour profile"]}, False),
        ("convert", {"changed": ["saved as WebP at quality 85"]}, False),
        ("strip", {"removed": ["EXIF"], "notes": ["The motion photo's video was kept, without its metadata"]}, False),
        ("convert", {}, False),
    ],
)
def test_which_successes_need_attention(command, told, listed):
    runner_ = _output.BatchRunner(command, "T", False)
    assert runner_._needs_attention(result("success", **told), set(), set()) is listed


LISTING_COMMANDS = ("convert", "resize", "rotate", "edit", "frames", "strip", "extract", "remux")
ROUTINE_LOSSES = ("extra images (such as a gain map)", "Apple metadata track", "3 Apple metadata tracks")
WANTED_LOSSES = (
    "another audio track",
    "2 another video tracks",
    "GoPro telemetry track (GPS)",
    "timecode track",
    "a data track (unknown)",
    "chapters",
    "subtitles",
    "cover art",
    "an attached file",
    "animation (20 frames)",
    "2 more pages",
    "motion photo video",
)


def attention(command: str, **told) -> bool:
    return _output.BatchRunner(command, "T", False)._needs_attention(result("success", **told), set(), set())


@pytest.mark.parametrize("command", LISTING_COMMANDS)
@pytest.mark.parametrize("routine", ROUTINE_LOSSES)
def test_a_routine_loss_alone_earns_no_row(command, routine):
    assert not attention(command, lost=["IPTC", routine, "data after the picture"])


@pytest.mark.parametrize("command", LISTING_COMMANDS)
@pytest.mark.parametrize("routine", ROUTINE_LOSSES)
@pytest.mark.parametrize("wanted", WANTED_LOSSES)
def test_a_wanted_loss_keeps_its_row_beside_a_routine_one(command, routine, wanted):
    assert attention(command, lost=[routine, wanted])
    assert attention(command, lost=[wanted, routine])


@pytest.mark.parametrize("command", LISTING_COMMANDS)
@pytest.mark.parametrize("routine", ROUTINE_LOSSES)
def test_tone_mapping_and_unchecked_results_keep_their_row_beside_a_routine_loss(command, routine):
    assert attention(command, lost=[routine], changed=["tone-mapped from HLG"])
    assert attention(command, lost=[routine], changed=[UNCHECKED], checked=False)


def gain_map_photo(path: pathlib.Path, *, trailer: bytes = b"") -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 50, 50)).save(buffer, "JPEG")
    data = buffer.getvalue()
    payload = b"MPF\x00II*\x00" + struct.pack("<I", 8)
    path.write_bytes(data[:2] + b"\xff\xe2" + struct.pack(">H", len(payload) + 2) + payload + data[2:] + trailer)


def lost_by_file(*args: str) -> dict[str, list[str]]:
    report = json.loads(invoke(*args, "--json"))
    return {pathlib.Path(item["file"]).name: item["fidelity"]["lost"] for item in report["results"]}


GAIN_MAP_RUNS = {
    "convert": ("convert", "in", "webp"),
    "resize": ("resize", "in", "--width", "4"),
    "edit": ("edit", "in", "--rotate", "90", "--output-format", "png"),
}


@pytest.mark.parametrize("command", sorted(GAIN_MAP_RUNS))
def test_a_folder_of_gain_map_photos_lists_nothing(tmp_path, command):
    (tmp_path / "in").mkdir()
    for name in ("a.jpg", "b.jpg"):
        gain_map_photo(tmp_path / "in" / name)
    args = GAIN_MAP_RUNS[command]
    output = invoke(*args)
    assert not rows(output), output
    assert re.search(r"Success:\s+2", output)
    for lost in lost_by_file(*args, "-f").values():
        assert "extra images (such as a gain map)" in lost
    verbose = runner.invoke(app, ["-v", *args, "-f"], env={"COLUMNS": "200"}).output
    assert rows(verbose) == ["Success", "Success"], verbose


def test_a_real_loss_among_gain_map_photos_is_the_only_row(tmp_path):
    (tmp_path / "in").mkdir()
    gain_map_photo(tmp_path / "in" / "gain.jpg")
    gain_map_photo(tmp_path / "in" / "motion.jpg", trailer=b"MicroVideo" + b"\x01" * 64)
    pages = [Image.new("RGB", (8, 8), (number * 80, 0, 0)) for number in range(3)]
    pages[0].save(tmp_path / "in" / "pages.tif", save_all=True, append_images=pages[1:])
    output = invoke("convert", "in", "webp", "-y")
    assert rows(output) == ["Success", "Success"], output
    assert "gain.jpg" not in output and "motion.jpg" in output and "pages.tif" in output
    again = invoke("convert", "in", "webp", "-y")
    assert not rows(again), again


def test_one_gain_map_photo_keeps_its_line(tmp_path):
    gain_map_photo(tmp_path / "a.jpg")
    assert invoke("convert", "a.jpg", "webp").startswith("Success: a.jpg")


@pytest.fixture
def unknown_tracks_read_as_apples(monkeypatch):
    named = video._track_name

    def as_apples(stream):
        return video._DATA_TRACKS["mebx"] if stream.codec == "unknown" else named(stream)

    monkeypatch.setattr(video, "_track_name", as_apples)


def clip_with_data_tracks(path: pathlib.Path, *tags: str) -> None:
    blob = path.parent / "blob.bin"
    blob.write_bytes(bytes(range(256)) * 16)
    plain = path.parent / f"plain-{path.name}"
    make = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=64x64:r=10:d=1"]
    subprocess.run([*make, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(plain)], check=True)
    command = ["ffmpeg", "-v", "error", "-y", "-i", str(plain)]
    for _ in tags:
        command += ["-f", "data", "-i", str(blob)]
    command += ["-map", "0"]
    for number, tag in enumerate(tags):
        command += ["-map", str(number + 1), f"-tag:d:{number}", tag]
    subprocess.run([*command, "-c", "copy", "-f", "mov", str(path)], check=True)
    plain.unlink()
    blob.unlink()


APPLE_RUNS = {
    "convert": ("convert", "in", "mp4"),
    "remux": ("remux", "in"),
    "extract": ("extract", "in", "--duration", "0.5", "--output-format", "mp4"),
    "rotate": ("rotate", "in", "90"),
}


@needs_ffmpeg
@pytest.mark.parametrize("command", sorted(APPLE_RUNS))
def test_a_folder_of_iphone_clips_lists_nothing(tmp_path, command, unknown_tracks_read_as_apples):
    (tmp_path / "in").mkdir()
    clip_with_data_tracks(tmp_path / "in" / "a.mov", "data", "data")
    clip_with_data_tracks(tmp_path / "in" / "b.mov", "data")
    args = APPLE_RUNS[command]
    output = invoke(*args, "-y")
    assert not rows(output), output
    lost = lost_by_file(*args, "-f")
    assert lost == {"a.mov": ["2 Apple metadata tracks"], "b.mov": ["Apple metadata track"]}


@needs_ffmpeg
def test_a_gopro_gps_track_keeps_its_row_among_iphone_clips(tmp_path, unknown_tracks_read_as_apples):
    (tmp_path / "in").mkdir()
    clip_with_data_tracks(tmp_path / "in" / "iphone.mov", "data")
    clip_with_data_tracks(tmp_path / "in" / "gopro.mov", "gpmd", "data")
    output = invoke("remux", "in", "-y")
    assert rows(output) == ["Success"], output
    assert "gopro.mov" in output and "iphone.mov" not in output


def test_one_file_keeps_its_line(tmp_path):
    photos(tmp_path, 1)
    invoke("convert", "p000.png", "webp")
    assert invoke("convert", "p000.png", "webp").startswith("Exists: p000.png")


def test_a_dry_run_still_lists_everything(tmp_path):
    photos(tmp_path / "in", 3)
    assert rows(invoke("convert", "in", "webp", "--dry-run")) == ["Plan"] * 3
    invoke("convert", "in", "webp")
    assert rows(invoke("convert", "in", "webp", "--dry-run")) == ["Exists"] * 3


def test_the_json_report_lists_everything(tmp_path):
    photos(tmp_path / "in", 3)
    invoke("convert", "in", "webp")
    report = json.loads(invoke("convert", "in", "webp", "--json"))
    assert [item["status"] for item in report["results"]] == ["exists"] * 3


def test_exit_codes_are_unchanged_by_listing_less(tmp_path):
    photos(tmp_path / "in", 2)
    (tmp_path / "in" / "x.png").write_bytes(b"not a png")
    invoke("convert", "in", "webp")
    invoke("convert", "in/x.png", "in/p000.png", "webp", code=1)


@pytest.mark.parametrize("before", [True, False])
def test_verbose_goes_before_or_after_the_command(tmp_path, before):
    photos(tmp_path / "in", 3)
    invoke("convert", "in", "webp")
    args = ("-v", "convert", "in", "webp") if before else ("convert", "in", "webp", "-v")
    assert rows(invoke(*args)) == ["Exists"] * 3


def test_verbose_still_cuts_at_the_limit_and_says_so(tmp_path):
    photos(tmp_path / "in", 5)
    invoke("convert", "in", "webp")
    output = invoke("convert", "in", "webp", "-v", "--limit-output", "2")
    assert rows(output) == ["Exists"] * 2
    assert "Showing the first 2 of 5" in output


def test_the_limit_cuts_attention_rows_and_says_so(tmp_path):
    (tmp_path / "in").mkdir()
    for number in range(5):
        (tmp_path / "in" / f"x{number}.png").write_bytes(b"not a png")
    output = invoke("convert", "in", "webp", "--limit-output", "2")
    assert rows(output) == ["Skipped"] * 2
    assert "Showing the first 2 of 5" in output


def test_no_hint_about_verbose_in_default_output(tmp_path):
    photos(tmp_path / "in", 2)
    assert "-v" not in invoke("convert", "in", "webp")


def test_help_says_what_verbose_and_the_limit_do():
    for args in (["--help"], ["convert", "--help"]):
        text = " ".join(runner.invoke(app, args, env={"COLUMNS": "200"}).output.split())
        assert "every file" in text
    assert "every file up to this" in " ".join(
        runner.invoke(app, ["convert", "--help"], env={"COLUMNS": "200"}).output.split()
    )


def sashimi_cli(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    code = "from sashimi.cli import main; main()"
    env = {**os.environ, "HOME": str(cwd), "COLUMNS": "120", "PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, "-c", code, *args], cwd=cwd, capture_output=True, env=env, timeout=120)


control_names = pytest.mark.skipif(sys.platform == "win32", reason="Windows file names can't hold control characters")
ESCAPES = re.compile(rb"\x1b[\]\[(]0;pwned|\x1b\]0|\x9b31m|\xc2\x9b|\xe2\x80\xae")


@pytest.mark.parametrize(
    "name",
    ["png icc escapes", "jpeg comment escapes", "png text escapes", "png text name of escapes", "gif comment escapes"],
)
@pytest.mark.parametrize("flags", [(), ("--all",), ("--json",)], ids=["plain", "all", "json"])
def test_the_metadata_command_passes_no_escapes(tmp_path, name, flags):
    path = hostile.write(tmp_path, name, hostile.HOSTILE[name])
    done = sashimi_cli("metadata", path.name, *flags, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert b"\x1b" not in done.stdout and not ESCAPES.search(done.stdout)
    assert b"\x1b" not in done.stderr


def test_batch_rows_and_strip_pass_no_escapes(tmp_path):
    path = hostile.write(tmp_path, "png icc escapes", hostile.HOSTILE["png icc escapes"])
    done = sashimi_cli("strip", path.name, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert b"\x1b" not in done.stdout + done.stderr


@control_names
def test_a_file_name_with_escapes_is_safe_and_json_keeps_it(tmp_path):
    name = "a\x1b]0;pwned\x07\u202eb.png"
    Image.new("RGB", (8, 8)).save(tmp_path / "plain.png")
    (tmp_path / name).write_bytes((tmp_path / "plain.png").read_bytes())
    for args in (("convert", name, "webp"), ("info", name), ("metadata", name), ("convert", ".", "webp", "-v")):
        done = sashimi_cli(*args, cwd=tmp_path)
        assert b"\x1b" not in done.stdout + done.stderr and b"\xe2\x80\xae" not in done.stdout, args
    done = sashimi_cli("convert", name, "webp", "--json", cwd=tmp_path)
    assert b"\x1b" not in done.stdout
    report = json.loads(done.stdout)
    assert report["results"][0]["file"].endswith(name)


@control_names
def test_several_files_with_escapes_print_a_safe_progress_and_table(tmp_path):
    for number in range(3):
        Image.new("RGB", (8, 8)).save(tmp_path / f"a\x1b]0;pwned\x07{number}.png")
    (tmp_path / "bad\x1b[31m.png").write_bytes(b"not a png")
    done = sashimi_cli("convert", ".", "webp", cwd=tmp_path)
    assert b"\x1b" not in done.stdout + done.stderr
    assert b"bad [31m.png" in done.stdout


def source_labels() -> set[str]:
    """Every label the fidelity code can give a thing an output lacks, read from its source, singular and plural."""
    from sashimi.core import fidelity as module

    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    found: set[str] = set()

    def spelled(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.Name) and isinstance(getattr(module, node.id, None), str):
            return [getattr(module, node.id)]
        if isinstance(node, ast.IfExp):
            return [*spelled(node.body), *spelled(node.orelse)]
        if isinstance(node, ast.JoinedStr):
            texts = [""]
            for part in node.values:
                options = spelled(part.value) if isinstance(part, ast.FormattedValue) else spelled(part)
                texts = [text + option for text in texts for option in (options or ["1", "2"])]
            return texts
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_count":
            return [module._count(number, *spelled(node.args[1])) for number in (1, 2)]
        return []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add":
            label = next((word.value for word in node.keywords if word.arg == "label"), None)
            found.update(spelled(label if label is not None else node.args[0]))
    found.update(name for name in module._CARRIER_NAMES.values() if name)
    found.update(module._PNG_NAMES.values())
    found.update(name.removeprefix("the ") for name in video._DATA_TRACKS.values())
    return found


LABELS = {
    "1 more page": True,
    "1 more pages": True,
    "2 more page": True,
    "2 more pages": True,
    "animation (1 frames)": True,
    "animation (2 frames)": True,
    "another video track": True,
    "2 more video tracks": True,
    "another audio track": True,
    "2 more audio tracks": True,
    "subtitles": True,
    "an attached file": True,
    "2 attached files": True,
    "motion photo video": True,
    "another picture (such as a stereo pair's other eye)": True,
    "GoPro telemetry track (GPS)": True,
    "timecode track": True,
    "GoPro file-description track": True,
    "camera-motion track": True,
    "Sony real-time metadata track": True,
    "the photo's Depth item": True,
    "another item the photo listed": True,
    "extra images (such as a gain map)": False,
    "embedded thumbnail": False,
    "Apple metadata track": False,
    "EXIF": False,
    "XMP": False,
    "extended XMP": False,
    "IPTC": False,
    "Photoshop data": False,
    "comments": False,
    "PNG text": False,
    "tags": False,
    "location": False,
    "colour profile": False,
    "resolution (DPI)": False,
    "gamma and colour-space tags": False,
    "modification time": False,
    "background colour": False,
    "significant bits": False,
    "data after the picture": False,
    "display rotation": False,
    "Dolby Vision settings": False,
    "other data (1)": False,
    "other data (2)": False,
    "other data (1 chunk)": False,
    "other data (2 chunk)": False,
    "other data (GIF text)": False,
    "resolution 1": False,
    "1 2": False,
    "1": False,
}


def test_every_label_the_fidelity_code_can_give_is_decided():
    assert source_labels() - set(LABELS) == set()


@pytest.mark.parametrize("label", sorted(LABELS))
@pytest.mark.parametrize("command", ["convert", "remux", "strip"])
def test_each_label_has_the_row_it_should(command, label):
    assert attention(command, lost=[label]) is LABELS[label]


def mpo_pair(path: pathlib.Path) -> None:
    left, right = Image.new("RGB", (16, 16), (200, 0, 0)), Image.new("RGB", (16, 16), (0, 0, 200))
    left.save(path, format="MPO", save_all=True, append_images=[right])


def test_a_two_page_scan_keeps_its_row_among_others(tmp_path):
    Image.new("L", (8, 8)).save("two.tif", save_all=True, append_images=[Image.new("L", (8, 8))])
    Image.new("RGB", (8, 8)).save("plain.png")
    output = invoke("convert", ".", "jpeg")
    assert "two.tif" in output and "1 more page" in output and "plain.png" not in output


def test_a_stereo_pairs_lost_eye_keeps_its_row_and_a_gain_map_does_not(tmp_path):
    mpo_pair(tmp_path / "pair.jpg")
    gain_map_photo(tmp_path / "phone.jpg")
    output = invoke("convert", ".", "webp")
    assert "pair.jpg" in output and "another picture" in output and "animation" not in output
    assert "phone.jpg" not in output


def test_a_stereo_camera_files_own_extension_is_found_and_converted(tmp_path):
    mpo_pair(tmp_path / "pair.mpo")
    output = invoke("convert", ".", "webp")
    assert "pair.mpo" in output and "another picture" in output and (tmp_path / "pair.mpo.webp").exists()
    assert invoke("convert", ".", "webp").startswith("Exists: pair.mpo")
    assert invoke("strip", "pair.mpo").startswith("Success: pair.mpo")


def test_stripping_a_stereo_pair_keeps_both_eyes_and_says_nothing_lost(tmp_path):
    mpo_pair(tmp_path / "pair.jpg")
    done = sashimi.metadata.strip(tmp_path / "pair.jpg", tmp_path / "out.jpg", "remove_all")
    assert not done.fidelity.lost
    with Image.open(tmp_path / "out.jpg") as img:
        assert img.n_frames == 2


FORGED = "b\nError: forged.png"


def forged_name(folder: pathlib.Path) -> None:
    Image.new("RGB", (8, 8)).save(folder / "a.png")
    (folder / FORGED).write_bytes((folder / "a.png").read_bytes())


@control_names
@pytest.mark.parametrize(
    "args",
    [
        ("convert", ".", "webp", "-v"),
        ("convert", ".", "webp", "--dry-run"),
        ("convert", FORGED, "webp"),
        ("convert", FORGED, "bmp"),
        ("info", FORGED),
        ("metadata", FORGED),
        ("metadata", FORGED, "--all"),
        ("strip", FORGED),
        ("strip", ".", "-v"),
        ("resize", ".", "-W", "4", "-v"),
        ("rotate", ".", "90", "-v"),
        ("edit", ".", "--rotate", "90", "-v"),
    ],
    ids=lambda args: " ".join(args).replace(FORGED, "name"),
)
def test_a_line_break_in_a_file_name_cant_start_a_line(tmp_path, args):
    forged_name(tmp_path)
    done = sashimi_cli(*args, cwd=tmp_path)
    assert not re.search(rb"^\s*Error: forged", done.stdout + b"\n" + done.stderr, re.M), done.stdout


@control_names
def test_a_run_over_a_forged_name_that_fails_stays_on_one_row(tmp_path):
    (tmp_path / FORGED).write_bytes(b"not a picture")
    Image.new("RGB", (8, 8)).save(tmp_path / "a.png")
    done = sashimi_cli("convert", ".", "webp", cwd=tmp_path)
    assert not re.search(rb"^\s*Error: forged", done.stdout, re.M)
    assert b"b Error: forged.png" in done.stdout


@control_names
def test_json_reads_back_a_name_with_a_line_break(tmp_path):
    forged_name(tmp_path)
    done = sashimi_cli("convert", FORGED, "webp", "--json", cwd=tmp_path)
    assert json.loads(done.stdout)["results"][0]["file"].endswith(FORGED)
    done = sashimi_cli("metadata", FORGED, "--json", cwd=tmp_path)
    assert done.returncode == 0


def test_a_tag_with_line_breaks_stays_indented_under_its_field(tmp_path):
    exif = Image.Exif()
    exif[0x010E] = "first\nError: forged\r\nthird"
    Image.new("RGB", (8, 8)).save(tmp_path / "tagged.jpg", exif=exif)
    done = sashimi_cli("metadata", "tagged.jpg", "--all", cwd=tmp_path)
    assert done.returncode == 0
    assert not re.search(rb"^Error", done.stdout, re.M) and b"first" in done.stdout


def short_clips(folder: pathlib.Path, *lengths: float) -> None:
    for number, length in enumerate(lengths):
        make = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=64x64:r=10:d={length}"]
        subprocess.run([*make, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(folder / f"clip{number}.mp4")], check=True)


@needs_ffmpeg
@pytest.mark.parametrize(
    ("lengths", "line"),
    [((0.4,), None), ((0.4, 1.0, 0.8), "3 videos were too short"), ((0.4, 60.0), "1 video was too short")],
    ids=["one", "several", "one of two"],
)
def test_a_folder_of_short_clips_ends_with_one_line(tmp_path, lengths, line):
    short_clips(tmp_path, *lengths)
    output = invoke("thumbs", ".")
    assert rows(output) == [] or len(lengths) == 1
    if line is not None:
        assert line in output and "-v" in output and output.count("too short") == 1
    assert "so the sheet shows" not in output or len(lengths) == 1


@needs_ffmpeg
def test_a_short_clips_explanation_stays_with_it_under_verbose(tmp_path):
    short_clips(tmp_path, 0.4, 0.8)
    output = invoke("-v", "thumbs", ".")
    assert output.count("so the sheet shows") == 2 and "too short for the full grid" not in output


@needs_ffmpeg
def test_no_closing_line_when_the_grid_was_not_cut(tmp_path):
    short_clips(tmp_path, 60.0, 60.0)
    assert "too short" not in invoke("thumbs", ".")


HOSTILE_HANDLER = "\x1b]0;pwned\x07\x1b[31m\u202e\n" + "H" * 70_000


@needs_ffmpeg
@pytest.mark.parametrize(
    "route",
    [
        lambda clip, out: sashimi.metadata.strip(clip, out / "a.mov", "remove_location"),
        lambda clip, out: sashimi.metadata.strip(clip, out / "b.mov", "remove_all"),
        lambda clip, out: video.trim(clip, out / "c.mp4", CopyTrimConfig(container="mp4")),
        lambda clip, out: video.trim(clip, out / "e.mkv", CopyTrimConfig(container="mkv")),
        lambda clip, out: video.convert(clip, out / "d.mp4", VIDEO_CONVERT.from_params({}, target="mp4")),
        lambda clip, out: video.convert(clip, out / "f.webm", VIDEO_CONVERT.from_params({"crf": 40}, target="webm")),
    ],
    ids=["strip location", "strip all", "trim mp4", "trim mkv", "convert mp4", "convert webm"],
)
def test_a_tracks_own_name_reaches_fidelity_clean_and_capped(tmp_path, route):
    clip = tmp_path / "in.mov"
    clip_with_data_tracks(clip, "abcd")
    named = tmp_path / "named.mov"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(clip),
            "-map",
            "0",
            "-c",
            "copy",
            "-metadata:s:d:0",
            "handler_name=" + HOSTILE_HANDLER.replace("\x00", ""),
            "-f",
            "mov",
            str(named),
        ],
        check=True,
    )
    told = route(named, tmp_path).fidelity
    texts = [told.text, *told.changed, *told.lost, *told.removed, *told.adjusted, *told.kept, *told.notes]
    for text in texts:
        assert not re.search("[\x00-\x1f\x7f-\x9f\u202a-\u202e]", text)
    assert all(len(text) <= 500 for text in texts[1:])
    assert len(told.text) < 2000
