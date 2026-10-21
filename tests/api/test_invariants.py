# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Invariants every batch command keeps, with its default pattern and with custom ones: names
never depend on the batch, reruns write nothing (with and without an output folder), a command
processes every other command's outputs it takes exactly once and never its own, nothing changes
without -f, -f changes only what it reports, and "exists" points at the planned name.
Processing is stubbed, so these run in milliseconds.
"""

import hashlib
import importlib
import itertools
import pathlib
import shutil

import pytest

from sashimi import api
from sashimi.config import CONTACT_SHEET, IMAGE_EDIT, IMAGE_FRAMES, IMAGE_RESIZE

FIXTURE = (
    "photo.jpg", "photo.png", "IMG 01.JPEG", "anim.gif", "anim.webp", "clip.mp4", "clip.mkv", "song.mp3",
)  # fmt: skip


def _resize(target):
    return IMAGE_RESIZE.from_params(target=target, max_width=10)


def _edit(target):
    return IMAGE_EDIT.from_params(target=target, rotate=90)


def _frames(target):
    return IMAGE_FRAMES.from_params(target=target, speed=2)


COMMANDS = {
    "convert_webp": ("convert", lambda p, **kw: api.convert_files_generator(p, "webp", **kw)),
    "convert_jpeg": ("convert", lambda p, **kw: api.convert_files_generator(p, "jpeg", **kw)),
    "convert_mp3": ("convert", lambda p, **kw: api.convert_files_generator(p, "mp3", **kw)),
    "remux": ("remux", lambda p, **kw: api.remux_files_generator(p, "mkv", **kw)),
    "resize": ("resize", lambda p, **kw: api.resize_images_generator(p, _resize, **kw)),
    "rotate": ("rotate", lambda p, **kw: api.rotate_files_generator(p, 90, **kw)),
    "rotate_png": ("rotate", lambda p, **kw: api.rotate_files_generator(p, 90, output_format="png", **kw)),
    "extract": ("extract", lambda p, **kw: api.extract_generator(p, 1, 2, **kw)),
    "edit": ("edit", lambda p, **kw: api.edit_images_generator(p, _edit, **kw)),
    "frames": ("frames", lambda p, **kw: api.edit_frames_generator(p, _frames, **kw)),
    "strip": ("strip", lambda p, **kw: api.strip_files_generator(p, **kw)),
    "thumbs": ("thumbs", lambda p, **kw: api.make_thumbs_generator(p, **kw)),
    "animated_thumbs": (
        "animated_thumbs",
        lambda p, **kw: api.make_thumbs_generator(p, CONTACT_SHEET.from_params(animated=True), **kw),
    ),
    "preview": ("preview", lambda p, **kw: api.make_previews_generator(p, **kw)),
    "favicon": ("favicon", lambda p, **kw: api.make_favicon_packs_generator(p, **kw)),
}

SHORT = {
    "convert": "{input_stem}.{output_ext}",
    "remux": "{input_stem}.{output_ext}",
}
"""Each command's short pattern; the rest use ``{input_stem}.{role}.{output_ext}``."""

LITERAL = {
    "convert_webp": "{input_name}.webp",
    "convert_mp3": "{input_name}.mp3",
    "remux": "{input_name}.mkv",
    "rotate_png": "{input_name}.turned.png",
    "thumbs": "{input_name}.jpeg",
    "animated_thumbs": "{input_name}.anim.webp",
    "preview": "{input_name}.prev.webp",
    "favicon": "{input_name}.icons.zip",
}
"""A pattern that spells out the extension, for each command whose outputs all have one format."""


FOLDER = {
    "convert": "out/{input_name}.{output_ext}",
    "remux": "out/{input_name}.{output_ext}",
}
"""Each command's pattern with a folder inside each input's folder; the rest use ``out/{input_name}.{role}.{output_ext}``."""


def patterns(command):
    section = COMMANDS[command][0]
    found = {
        "default": None,
        "short": SHORT.get(section, "{input_stem}.{role}.{output_ext}"),
        "folder": FOLDER.get(section, "out/{input_name}.{role}.{output_ext}"),
    }
    if command in LITERAL:
        found["literal"] = LITERAL[command]
    return found


PATTERN_CASES = [(command, kind) for command in COMMANDS for kind in patterns(command)]


def made(src, dst, *args, **kwargs):
    pathlib.Path(dst).write_bytes(b"made from " + pathlib.Path(src).name.encode())


@pytest.fixture(autouse=True)
def stub_processing(monkeypatch):
    for module, name in (
        ("sashimi.core.image", "convert"),
        ("sashimi.core.image", "to_ico"),
        ("sashimi.core.video", "convert"),
        ("sashimi.core.audio", "convert"),
        ("sashimi.core.edit", "edit"),
        ("sashimi.core.edit", "select_frames"),
        ("sashimi.api.resize", "convert"),
        ("sashimi.api.transform", "transform"),
        ("sashimi.api.extract", "trim"),
        ("sashimi.api.remux", "trim"),
        ("sashimi.api.strip", "strip"),
        ("sashimi.api.thumbs", "render"),
        ("sashimi.api.preview", "preview"),
        ("sashimi.api.favicon", "favicon_pack"),
    ):
        monkeypatch.setattr(importlib.import_module(module), name, made)
    extract = importlib.import_module("sashimi.api.extract")
    monkeypatch.setattr(extract, "_clip_message", lambda config, source, output: "Extracted to {name}")


def folder(path, names=FIXTURE):
    path.mkdir(parents=True, exist_ok=True)
    for name in names:
        (path / name).write_bytes(name.encode())
    return path


def run(command, paths, *, pattern=None, **kwargs):
    """The results as (source, status, message, output) tuples."""
    section, make = COMMANDS[command]
    items = list(make(paths, output_name=pattern, **kwargs))
    return [item for item in items[1:] if item[1] != "starting"]


def planned(command, paths, **kwargs):
    batch = COMMANDS[command][1](paths, **kwargs)
    return {
        item.source.name: item.output.relative_to(item.source.parent).as_posix() if item.output else None
        for item in batch.plan().files
    }


def snapshot(path):
    return {p.relative_to(path): hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*") if p.is_file()}


def every_file(path):
    return {p for p in path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("command, kind", PATTERN_CASES)
def test_determinism(tmp_path, command, kind):
    pattern = patterns(command)[kind]
    root = folder(tmp_path / "root" / "files")
    folder(tmp_path / "root" / "files" / "nested")
    everything = planned(command, root, output_name=pattern)
    assert everything, command
    for name, output in everything.items():
        alone = folder(tmp_path / "alone" / name, [name])
        sibling = next(n for n in FIXTURE if n != name)
        runs = [
            planned(command, alone, output_name=pattern),
            planned(command, alone / name, output_name=pattern),
            planned(command, [root / name, root / sibling], output_name=pattern),
            planned(command, tmp_path / "root", output_name=pattern, recursive=True),
        ]
        for found in runs:
            if output is not None and found.get(name) is not None:
                assert found[name] == output, (command, kind, name, found)


@pytest.mark.parametrize("command, kind", PATTERN_CASES)
def test_a_second_identical_run_writes_nothing(tmp_path, command, kind):
    pattern = patterns(command)[kind]
    root = folder(tmp_path / "files")
    first = run(command, root, pattern=pattern)
    assert kind != "default" or any(status == "success" for _, status, _, _ in first), first
    before = snapshot(root)
    second = run(command, root, pattern=pattern)
    assert snapshot(root) == before
    assert all(status in ("exists", "skipped") for _, status, _, _ in second), second


CONFIGURATIONS = {
    "default": lambda command: None,
    "short": lambda command: patterns(command)["short"],
    "literal": lambda command: LITERAL.get(command),
    "folder": lambda command: patterns(command)["folder"],
}
"""The pattern each command runs with in a configuration (None: its default)."""


def own_names(command, pattern, root):
    """Every path `command` would write for a file in `root`, each file planned on its own."""
    names = set()
    for path in every_file(root):
        batch = COMMANDS[command][1](path, output_name=pattern)
        names |= {item.output for item in batch.plan().files if item.output is not None}
    return names


def takes(command, pattern, path):
    """Whether `command` processes `path` when it's named on its own."""
    plan = COMMANDS[command][1](path, output_name=pattern).plan()
    return bool(plan.files)


@pytest.mark.parametrize("configuration", sorted(CONFIGURATIONS))
@pytest.mark.parametrize("first, second", list(itertools.product(COMMANDS, repeat=2)))
def test_a_command_processes_other_outputs_once_and_never_its_own(tmp_path, configuration, first, second):
    root = folder(tmp_path / "files")
    originals = set(snapshot(root))
    one, two = CONFIGURATIONS[configuration](first), CONFIGURATIONS[configuration](second)
    run(first, root, pattern=one)
    outputs = {root / name for name in set(snapshot(root)) - originals}
    own = own_names(second, two, root)
    results = run(second, root, pattern=two, recursive=True)
    sources = [source for source, _, _, _ in results]
    assert len(sources) == len(set(sources)), (first, second)
    expected = {path for path in outputs if path not in own and takes(second, two, path)}
    assert set(sources) & outputs == expected, (first, second, sorted(p.name for p in set(sources) ^ expected))
    if (first, one) == (second, two):
        assert not expected
    after = snapshot(root)
    run(second, root, pattern=two, recursive=True)
    assert snapshot(root) == after, (first, second)


def test_other_commands_outputs_are_processed_under_default_patterns(tmp_path):
    root = folder(tmp_path / "files", ["photo.png", "clip.mp4"])
    run("thumbs", root)
    run("resize", root)
    converted = {source.name: out.name for source, status, _, out in run("convert_webp", root) if status == "success"}
    assert converted == {
        "photo.png": "photo.png.webp",
        "photo.png.resized.png": "photo.png.resized.png.webp",
        "clip.mp4": "clip.mp4.webp",
        "clip.mp4.sheet.jpg": "clip.mp4.sheet.jpg.webp",
        "clip.mp4.sheet.jpg.resized.jpg": "clip.mp4.sheet.jpg.resized.jpg.webp",
    }
    jpeg = {source.name for source, status, _, _ in run("convert_jpeg", root) if status == "success"}
    assert "photo.png.webp" in jpeg and "photo.png.resized.png.webp" in jpeg
    before = snapshot(root)
    assert all(status in ("exists", "skipped") for _, status, _, _ in run("convert_jpeg", root))
    assert snapshot(root) == before
    resized = sorted(source.name for source, status, _, _ in run("resize", root) if status == "success")
    assert resized == [
        "clip.mp4.sheet.jpg.resized.jpg.webp",
        "clip.mp4.sheet.jpg.resized.jpg.webp.jpeg",
        "clip.mp4.sheet.jpg.webp",
        "clip.mp4.sheet.jpg.webp.jpeg",
        "clip.mp4.webp",
        "clip.mp4.webp.jpeg",
        "photo.png.jpeg",
        "photo.png.resized.png.jpeg",
        "photo.png.resized.png.webp",
        "photo.png.resized.png.webp.jpeg",
        "photo.png.webp",
        "photo.png.webp.jpeg",
    ]


OUTPUT_DIRS = {
    "subfolder": lambda tmp: (tmp / "files", tmp / "files" / "out"),
    "nested": lambda tmp: (tmp / "files", tmp / "files" / "a" / "b" / "out"),
    "sibling": lambda tmp: (tmp / "files", tmp / "out"),
}


@pytest.mark.parametrize("where", sorted(OUTPUT_DIRS))
@pytest.mark.parametrize("command, kind", PATTERN_CASES)
def test_a_rerun_with_an_output_folder_writes_nothing(tmp_path, command, kind, where):
    pattern = patterns(command)[kind]
    root, out = OUTPUT_DIRS[where](tmp_path)
    folder(root)
    folder(root / "sub", ["photo.jpg", "clip.mp4", "anim.gif"])
    first = run(command, root, pattern=pattern, output_dir=out, recursive=True)
    assert kind != "default" or any(status == "success" for _, status, _, _ in first), first
    before = snapshot(tmp_path)
    second = run(command, root, pattern=pattern, output_dir=out, recursive=True)
    assert snapshot(tmp_path) == before
    assert all(status in ("exists", "skipped") for _, status, _, _ in second), second


@pytest.mark.parametrize("where", sorted(OUTPUT_DIRS))
@pytest.mark.parametrize("command, kind", PATTERN_CASES)
def test_outputs_in_an_output_folder_are_the_commands_own_when_searched(tmp_path, command, kind, where):
    pattern = patterns(command)[kind]
    root, out = OUTPUT_DIRS[where](tmp_path)
    folder(root)
    run(command, root, pattern=pattern, output_dir=out)
    made = {path for path in out.rglob("*") if path.is_file()}
    searched = tmp_path if where == "sibling" else root
    results = run(command, searched, pattern=pattern, recursive=True)
    processed = {source for source, _, _, _ in results}
    stems = {pathlib.PurePath(name).stem.casefold() for name in FIXTURE}
    leaked = {path for path in processed & made if pathlib.PurePath(path.name).stem.casefold() not in stems}
    assert not leaked, (command, kind, sorted(p.name for p in leaked))


@pytest.mark.parametrize("command, kind", PATTERN_CASES)
def test_nothing_changes_without_force_and_force_changes_only_what_it_reports(tmp_path, command, kind):
    pattern = patterns(command)[kind]
    root = folder(tmp_path / "files")
    run(command, root, pattern=pattern)
    for name in ("photo.webp", "photo.jpg.webp", "clip.jpg", "photo.resized.jpg", "song.mp3.mp3"):
        (root / name).write_bytes(b"the user's own " + name.encode())
    before = snapshot(root)
    run(command, root, pattern=pattern)
    middle = snapshot(root)
    assert {path: middle.get(path) for path in before} == before
    results = run(command, root, pattern=pattern, force=True)
    after = snapshot(root)
    changed = {path for path in middle if middle[path] != after.get(path)}
    replaced = {out.relative_to(root) for _, status, message, out in results if out and "(replaced" in message}
    assert changed <= replaced, (command, kind, changed - replaced)
    assert set(before) <= set(after)


@pytest.mark.parametrize("command, kind", PATTERN_CASES)
def test_exists_points_at_the_planned_name(tmp_path, command, kind):
    pattern = patterns(command)[kind]
    root = folder(tmp_path / "files")
    run(command, root, pattern=pattern)
    names = planned(command, root, output_name=pattern)
    for source, status, message, out in run(command, root, pattern=pattern):
        if status == "exists":
            assert out is not None and out.relative_to(source.parent).as_posix() == names[source.name], (
                source,
                message,
            )
            assert out.exists()


def test_force_edits_in_place_and_says_so(tmp_path):
    root = folder(tmp_path / "files", ["photo.jpg", "clip.mov"])
    kept = run("strip", root, pattern="{input_name}")
    assert {status for _, status, _, _ in kept} == {"exists"}
    assert all("it's the input" in message for _, _, message, _ in kept)
    assert (root / "photo.jpg").read_bytes() == b"photo.jpg"
    replaced = run("strip", root, pattern="{input_name}", force=True)
    assert all(message.endswith("(replaced the input itself)") for _, _, message, _ in replaced)
    assert (root / "photo.jpg").read_bytes() == b"made from photo.jpg"
    assert sorted(p.name for p in root.iterdir()) == ["clip.mov", "photo.jpg"]


def test_a_deleted_originals_outputs_are_processed_like_any_file(tmp_path):
    root = folder(tmp_path / "files", ["photo.png"])
    run("convert_webp", root)
    (root / "photo.png").unlink()
    results = run("resize", root)
    assert [(source.name, status) for source, status, _, _ in results] == [("photo.png.webp", "success")]
    shutil.rmtree(root)
