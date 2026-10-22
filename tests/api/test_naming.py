# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pathlib

import pytest
from PIL import Image

from sashimi import api
from sashimi.api import _naming
from sashimi.api._naming import NameRefused, NameTemplate, check_name, output_format, source_format
from sashimi.config import CONTACT_SHEET, IMAGE_EDIT, IMAGE_FRAMES, IMAGE_RESIZE
from sashimi.errors import InvalidOptionsError


def _resize(target):
    return IMAGE_RESIZE.from_params(target=target, max_width=10)


def _edit(target):
    return IMAGE_EDIT.from_params(target=target, rotate=90)


def _frames(target):
    return IMAGE_FRAMES.from_params(target=target, speed=2)


GENERATORS = {
    "convert": lambda paths, **kw: api.convert_files_generator(paths, "webp", **kw),
    "convert_jpeg": lambda paths, **kw: api.convert_files_generator(paths, "jpeg", **kw),
    "convert_jpg": lambda paths, **kw: api.convert_files_generator(paths, "jpg", **kw),
    "remux": lambda paths, **kw: api.remux_files_generator(paths, "mkv", **kw),
    "resize": lambda paths, **kw: api.resize_images_generator(paths, _resize, **kw),
    "resize_webp": lambda paths, **kw: api.resize_images_generator(paths, _resize("webp"), **kw),
    "rotate": lambda paths, **kw: api.rotate_files_generator(paths, 90, **kw),
    "rotate_png": lambda paths, **kw: api.rotate_files_generator(paths, 90, output_format="png", **kw),
    "extract": lambda paths, **kw: api.extract_generator(paths, 1, 2, **kw),
    "edit": lambda paths, **kw: api.edit_images_generator(paths, _edit, **kw),
    "frames": lambda paths, **kw: api.edit_frames_generator(paths, _frames, **kw),
    "frames_gif": lambda paths, **kw: api.edit_frames_generator(paths, _frames("gif"), **kw),
    "strip": lambda paths, **kw: api.strip_files_generator(paths, **kw),
    "thumbs": lambda paths, **kw: api.make_thumbs_generator(paths, **kw),
    "animated_thumbs": lambda paths, **kw: api.make_thumbs_generator(
        paths, CONTACT_SHEET.from_params(animated=True), **kw
    ),
    "preview": lambda paths, **kw: api.make_previews_generator(paths, **kw),
    "favicon": lambda paths, **kw: api.make_favicon_packs_generator(paths, **kw),
}

DEFAULT_NAMES = [
    ("convert", "photo.png", "photo.png.webp"),
    ("convert_jpeg", "photo.png", "photo.png.jpeg"),
    ("convert_jpg", "photo.png", "photo.png.jpg"),
    ("remux", "clip.mp4", "clip.mp4.mkv"),
    ("resize", "photo.png", "photo.png.resized.png"),
    ("resize", "photo.JPEG", "photo.JPEG.resized.JPEG"),
    ("resize", "photo.heic", "photo.heic.resized.jpg"),
    ("resize_webp", "photo.png", "photo.png.resized.webp"),
    ("rotate", "clip.mp4", "clip.mp4.rotated.mp4"),
    ("rotate", "clip.m4v", "clip.m4v.rotated.m4v"),
    ("rotate", "clip.mkv", "clip.mkv.rotated.mp4"),
    ("rotate", "photo.jpg", "photo.jpg.rotated.jpg"),
    ("rotate", "anim.gif", "anim.gif.rotated.gif"),
    ("rotate_png", "photo.jpg", "photo.jpg.rotated.png"),
    ("extract", "clip.mkv", "clip.mkv.extracted.mkv"),
    ("extract", "clip.avi", "clip.avi.extracted.mkv"),
    ("edit", "photo.jpg", "photo.jpg.edited.jpg"),
    ("edit", "photo.heic", "photo.heic.edited.jpg"),
    ("frames", "anim.webp", "anim.webp.frames.webp"),
    ("frames_gif", "anim.webp", "anim.webp.frames.gif"),
    ("strip", "clip.mov", "clip.mov.stripped.mov"),
    ("thumbs", "video.mp4", "video.mp4.sheet.jpg"),
    ("animated_thumbs", "video.mp4", "video.mp4.animated-sheet.webp"),
    ("preview", "video.mp4", "video.mp4.preview.webp"),
    ("favicon", "logo.png", "logo.png.favicons.zip"),
]

SIBLINGS = {
    "convert": ("x.png", "x.jpg"),
    "convert_jpeg": ("x.png", "x.gif"),
    "convert_jpg": ("x.png", "x.gif"),
    "remux": ("x.mp4", "x.mov"),
    "resize": ("x.png", "x.jpg"),
    "resize_webp": ("x.png", "x.jpg"),
    "rotate": ("x.mp4", "x.mkv"),
    "rotate_png": ("x.jpg", "x.png"),
    "extract": ("x.mkv", "x.mp4"),
    "edit": ("x.png", "x.heic"),
    "frames": ("x.gif", "x.webp"),
    "frames_gif": ("x.gif", "x.webp"),
    "strip": ("x.mov", "x.mp4"),
    "thumbs": ("x.mp4", "x.mkv"),
    "animated_thumbs": ("x.mp4", "x.mkv"),
    "preview": ("x.mp4", "x.mkv"),
    "favicon": ("x.png", "x.jpg"),
}


def plan(command, paths, **kwargs):
    """Each input's (status, output name or None, message) from a dry run."""
    items = list(GENERATORS[command](paths, dry_run=True, **kwargs))
    return {
        p.name: (status, out.name if out else None, message)
        for p, status, message, out in items[1:]
        if status != "starting"
    }


def touch(*paths):
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


@pytest.mark.parametrize("command, source, expected", DEFAULT_NAMES)
def test_default_names(tmp_path, command, source, expected):
    touch(tmp_path / source)
    assert plan(command, tmp_path / source)[source][:2] == ("success", expected)


def test_every_command_has_a_default_name():
    assert set(_naming.COMMANDS) <= set(GENERATORS)


def test_default_output_names():
    assert {command: api.default_output_name(command) for command in _naming.COMMANDS} == {
        command: "{input_name}.{output_ext}" if command in ("convert", "remux") else "{input_name}.{role}.{output_ext}"
        for command in _naming.COMMANDS
    }


@pytest.mark.parametrize("command", sorted(SIBLINGS))
def test_a_name_never_depends_on_the_rest_of_the_batch(tmp_path, command):
    name, sibling = SIBLINGS[command]
    folder, alone_dir = tmp_path / "videos", tmp_path / "alone"
    touch(folder / name, folder / sibling, folder / "other" / "y.bin", alone_dir / name)
    alone = plan(command, alone_dir / name)[name]
    assert alone[0] == "success" and alone[1]
    runs = [
        plan(command, folder / name),
        plan(command, [folder / name, folder / sibling]),
        plan(command, folder),
        plan(command, tmp_path, recursive=True),
        plan(command, alone_dir),
    ]
    for run in runs:
        assert run[name][:2] == alone[:2], (command, run)


@pytest.mark.parametrize("command, a, b", [("convert", "x.png", "x.jpg"), ("remux", "x.mp4", "x.mov")])
def test_two_outputs_with_one_name_are_both_skipped(tmp_path, command, a, b):
    touch(tmp_path / a, tmp_path / b)
    pattern = NameTemplate("{input_stem}.{output_ext}", "output_name in [convert] of /home/user/sashimi.toml")
    for force in (False, True):
        together = plan(command, tmp_path, output_name=pattern, force=force)
        assert together[a][0] == together[b][0] == "skipped"
        assert together[a][1] is None
        message = together[a][2]
        assert message.startswith(f"Clashes with {b}: both would be named ")
        assert "-f" not in message and "force" not in message
    batch = GENERATORS[command](tmp_path, output_name=pattern)
    assert {item.source.name for item in batch.plan().clashes} == {a, b}
    note = batch.plan().clash_note
    assert note is not None and note.startswith("2 files were skipped because the pattern ")
    assert "{input_stem}.{output_ext} (output_name in [convert] of /home/user/sashimi.toml)" in note
    assert "-f" not in note and "force" not in note
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([a, b])


@pytest.mark.parametrize("command, source, expected", DEFAULT_NAMES)
def test_rerunning_on_a_folder_reports_exists_and_plans_nothing_new(tmp_path, command, source, expected):
    touch(tmp_path / source, tmp_path / expected)
    again = plan(command, tmp_path)
    assert again == {source: ("exists", expected, f"Output already exists: {expected}")}


def test_a_named_earlier_output_is_processed_on_its_own_but_not_beside_its_original(tmp_path):
    touch(tmp_path / "a.png", tmp_path / "a.png.resized.png")
    alone = plan("resize", [tmp_path / "a.png.resized.png"])
    assert alone["a.png.resized.png"][:2] == ("success", "a.png.resized.png.resized.png")
    for paths in ([tmp_path, tmp_path / "a.png.resized.png"], [tmp_path / "a.png", tmp_path / "a.png.resized.png"]):
        named = plan("resize", paths)
        assert "a.png.resized.png" not in named
        assert named["a.png"][:2] == ("exists", "a.png.resized.png")


def test_the_owners_three_runs(tmp_path, monkeypatch):
    def render(src, dst, config, **kwargs):
        pathlib.Path(dst).write_bytes(b"sheet of " + pathlib.Path(src).name.encode())

    monkeypatch.setattr("sashimi.api.thumbs.render", render)
    videos = tmp_path / "Videos"
    touch(videos / "x.mkv", videos / "x.mp4")

    def run(paths):
        report = api.make_thumbs(paths, show_progress=False)
        return {
            (pathlib.Path(r["file"]).name, r["status"], r["output"] and pathlib.Path(r["output"]).name)
            for r in report["results"]
        }

    assert run(videos) == {("x.mkv", "success", "x.mkv.sheet.jpg"), ("x.mp4", "success", "x.mp4.sheet.jpg")}
    touch(videos / "y.mp4")
    assert run(videos) == {
        ("x.mkv", "exists", "x.mkv.sheet.jpg"),
        ("x.mp4", "exists", "x.mp4.sheet.jpg"),
        ("y.mp4", "success", "y.mp4.sheet.jpg"),
    }
    touch(videos / "sub" / "x.mp4")
    (videos / "sub" / "x.mp4.sheet.jpg").write_bytes((videos / "x.mp4.sheet.jpg").read_bytes())
    assert run(videos / "sub") == {("x.mp4", "exists", "x.mp4.sheet.jpg")}
    assert run(videos / "x.mp4") == {("x.mp4", "exists", "x.mp4.sheet.jpg")}
    assert sorted(p.name for p in videos.iterdir() if p.is_file()) == [
        "x.mkv", "x.mkv.sheet.jpg", "x.mp4", "x.mp4.sheet.jpg", "y.mp4", "y.mp4.sheet.jpg",
    ]  # fmt: skip


def test_a_pattern_makes_existing_sheets_count(tmp_path):
    touch(tmp_path / "video.mp4", tmp_path / "video.mp4.jpeg", tmp_path / "new.mkv")
    planned = plan("thumbs", tmp_path, output_name="{input_name}.jpeg")
    assert planned == {
        "new.mkv": ("success", "new.mkv.jpeg", "Would write new.mkv.jpeg"),
        "video.mp4": ("exists", "video.mp4.jpeg", "Output already exists: video.mp4.jpeg"),
    }


@pytest.mark.parametrize(
    "command, pattern, source, expected",
    [
        ("convert", "{input_stem}-{input_ext}.{output_ext}", "a.png", "a-png.webp"),
        ("convert", "{input_stem}.{output_ext}", "a.png", "a.webp"),
        ("thumbs", "{input_stem} [{role}].jpg", "a.mp4", "a [sheet].jpg"),
        ("resize", "small/{input_name}", "a.png", "a.png"),
        ("resize", "{{{input_stem}}}.{output_ext}", "a.png", "{a}.png"),
        ("resize", "{input_stem}.{role}.{output_ext}", "a.png", "a.resized.png"),
        ("rotate_png", "{input_stem}.{role}.{output_ext}", "a.jpg", "a.rotated.png"),
        ("strip", "{input_name}.clean.{input_ext}", "a.MOV", "a.MOV.clean.MOV"),
    ],
)
def test_patterns(tmp_path, command, pattern, source, expected):
    touch(tmp_path / source)
    if expected is None:
        with pytest.raises(InvalidOptionsError):
            plan(command, tmp_path, output_name=pattern)
        return
    assert plan(command, tmp_path, output_name=pattern)[source][:2] == ("success", expected)


@pytest.mark.parametrize(
    "pattern, message",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("{nam}.jpg", "unknown field {nam}; the fields are {input_name} (clip.mp4), {input_stem}"),
        ("{name}.jpg", "unknown field {name} (did you mean {input_name}?)"),
        ("{stem}.jpg", "unknown field {stem} (did you mean {input_stem}?)"),
        ("{ext}.jpg", "unknown field {ext} (did you mean {input_ext}?)"),
        ("{input_name}.{format}", "unknown field {format} (did you mean {output_ext}?)"),
        ("{input_name!r}.jpg", "unknown field {input_name!r}"),
        ("{input_name:>9}.jpg", "unknown field {input_name:>9}"),
        ("{0}.jpg", "unknown field {0}"),
        ("{input_name.upper}.jpg", "unknown field {input_name.upper}"),
        ("{input_name", "unmatched brace"),
        ("input_name}", "unmatched brace"),
        ("../{input_name}.jpg", "has a . or .. folder; a pattern's folders are inside each input's folder"),
        ("..\\{input_name}.jpg", "has a . or .. folder"),
        ("{input_name}?.jpg", "'?'"),
        ("{input_name}:sheet.jpg", "':'"),
        ("{input_name}\t.jpg", "'\\t'"),
    ],
)
def test_invalid_patterns(pattern, message):
    with pytest.raises(InvalidOptionsError) as e:
        NameTemplate(pattern)
    assert message in e.value.user_message


def test_role_is_refused_for_conversions(tmp_path):
    with pytest.raises(InvalidOptionsError, match="role"):
        api.convert_files_generator(tmp_path, "webp", output_name="{input_stem}.{role}.{output_ext}")


@pytest.mark.parametrize(
    "command, pattern",
    [("thumbs", "{input_name}.png"), ("convert", "{input_name}.jpg"), ("favicon", "{input_name}.png")],
)
def test_a_literal_extension_of_another_format_is_refused_before_anything_runs(tmp_path, command, pattern):
    with pytest.raises(InvalidOptionsError, match="ends in"):
        GENERATORS[command](tmp_path, output_name=NameTemplate(pattern, "--output-name"))


def test_a_literal_extension_is_checked_per_file_when_formats_differ(tmp_path):
    touch(tmp_path / "a.jpg", tmp_path / "b.png")
    planned = plan("strip", tmp_path, output_name="{input_stem}.clean.jpg")
    assert planned["a.jpg"][:2] == ("success", "a.clean.jpg")
    assert planned["b.png"][0] == "error" and "must end in .png" in planned["b.png"][2]


@pytest.mark.parametrize(
    "name, source, problem",
    [
        ("a.mp4.png", "a.mp4", "must end in .jpg or .jpeg, since the output is JPEG"),
        ("a.mp4", "a.mp4", "must end in .jpg or .jpeg"),
        ("CON.jpg", "con-video.mp4", "Windows"),
        ("nul.sheet.jpg", "x.mp4", "Windows"),
        ("a.jpg.", "a.mp4", "Windows"),
        ("a" * 252 + ".jpg", "a.mp4", "at most 255 bytes"),
        ("é" * 130 + ".jpg", "a.mp4", "at most 255 bytes"),
        ("a|b.jpg", "a.mp4", "'|'"),
        (".", "a.mp4", "isn't a file name"),
    ],
)
def test_names_that_are_refused(name, source, problem):
    with pytest.raises(NameRefused) as e:
        check_name(name, pathlib.PurePosixPath(source), output_format("jpeg"))
    assert problem in e.value.message


@pytest.mark.parametrize(
    "name, source",
    [
        ("a.mp4.jpeg", "a.mp4"),
        ("a.mp4.JPG", "a.mp4"),
        ("Show: Ep 1.mkv.sheet.jpg", "Show: Ep 1.mkv"),
        ("con.mp4.sheet.jpg", "con.mp4"),
        ("{x}.jpg", "{x}.mp4"),
        (".hidden.mp4.sheet.jpg", ".hidden.mp4"),
        ("a" * 251 + ".jpg", "a.mp4"),
    ],
)
def test_names_that_are_accepted(name, source):
    check_name(name, pathlib.PurePosixPath(source), output_format("jpeg"))


@pytest.mark.parametrize("path_type", [pathlib.PureWindowsPath, pathlib.PurePosixPath])
def test_rendering_uses_only_the_file_name(path_type):
    source = path_type(
        "C:\\Videos\\Holiday 2025.MKV" if path_type is pathlib.PureWindowsPath else "/home/user/Videos/Holiday 2025.MKV"
    )
    fmt = output_format("jpeg")
    template = NameTemplate("{input_name}.{role}.{output_ext}")
    assert template.render(source, "sheet", fmt.extension) == "Holiday 2025.MKV.sheet.jpg"
    assert NameTemplate("{input_stem}_{input_ext}.jpeg").render(source, "sheet", "jpg") == "Holiday 2025_MKV.jpeg"
    check_name("Holiday 2025.MKV.sheet.jpg", source, fmt)


def test_output_ext_is_typed_then_the_inputs_own_then_standard():
    assert output_format("jpeg", pathlib.PurePath("a.png"), "jpeg").extension == "jpeg"
    assert output_format("jpeg", pathlib.PurePath("a.JPG"), "jpeg").extension == "jpeg"
    assert output_format("jpeg", pathlib.PurePath("a.JPEG")).extension == "JPEG"
    assert output_format("jpeg", pathlib.PurePath("a.png")).extension == "jpg"
    assert output_format("tiff", pathlib.PurePath("a.png")).extension == "tif"
    assert output_format("mp4", pathlib.PurePath("a.m4v")).extension == "m4v"
    assert source_format(pathlib.PurePath("a.oga")).accepted == {"ogg", "opus", "oga"}
    with pytest.raises(NameRefused, match="isn't an extension for PNG"):
        output_format("png", None, "jpg")


def test_a_typed_format_is_the_extension(tmp_path):
    touch(tmp_path / "a.jpg", tmp_path / "v.mp4")
    assert plan("edit", tmp_path / "a.jpg", output_ext="jpeg")["a.jpg"][1] == "a.jpg.edited.jpeg"
    assert plan("thumbs", tmp_path / "v.mp4", output_ext="jpeg")["v.mp4"][1] == "v.mp4.sheet.jpeg"
    assert plan("thumbs", tmp_path / "v.mp4")["v.mp4"][1] == "v.mp4.sheet.jpg"
    with pytest.raises(InvalidOptionsError, match="isn't an extension for JPEG"):
        api.make_thumbs_generator(tmp_path, output_ext="png")


def test_edit_and_frames_outputs_never_meet(tmp_path):
    touch(tmp_path / "anim.gif")
    assert plan("edit", tmp_path)["anim.gif"][1] == "anim.gif.edited.gif"
    touch(tmp_path / "anim.gif.edited.gif")
    assert plan("frames", tmp_path / "anim.gif")["anim.gif"][:2] == ("success", "anim.gif.frames.gif")


def test_own_outputs_are_recognised_in_any_folder_and_in_any_case(tmp_path):
    touch(tmp_path / "photos" / "IMG_1.JPG", tmp_path / "out" / "deeper" / "img_1.jpg.RESIZED.jpg")
    batch = GENERATORS["resize"](tmp_path, recursive=True)
    assert [(f.name, s.name) for f, s in batch.plan().left_out] == [("img_1.jpg.RESIZED.jpg", "IMG_1.JPG")]
    assert [item.source.name for item in batch.plan().files] == ["IMG_1.JPG"]


def test_other_commands_outputs_and_other_settings_are_processed(tmp_path):
    touch(tmp_path / "photo.png", tmp_path / "photo.png.resized.png", tmp_path / "photo.png.jpeg")
    batch = GENERATORS["convert"](tmp_path)
    assert batch.plan().left_out == ()
    assert [item.source.name for item in batch.plan().files] == ["photo.png", "photo.png.jpeg", "photo.png.resized.png"]


def test_a_stem_only_name_counts_only_in_its_own_folder(tmp_path):
    pattern = "{input_stem}.{output_ext}"
    touch(tmp_path / "2023" / "IMG_0001.HEIC", tmp_path / "2024" / "IMG_0001.png", tmp_path / "2023" / "IMG_0002.HEIC")
    touch(tmp_path / "2023" / "IMG_0002.png")
    batch = GENERATORS["rotate_png"](tmp_path, recursive=True, output_name=pattern)
    assert [(f.parent.name, f.name) for f, _ in batch.plan().left_out] == [("2023", "IMG_0002.png")]
    assert ("2024", "IMG_0001.png") in [(i.source.parent.name, i.source.name) for i in batch.plan().files]


@pytest.mark.parametrize(
    "pattern, expected",
    [
        ("sheets/{input_name}.jpeg", "sheets/clip.mp4.jpeg"),
        ("sheets\\{input_name}.jpeg", "sheets/clip.mp4.jpeg"),
        ("thumbs/still/{input_stem}.{output_ext}", "thumbs/still/clip.jpg"),
        ("{input_stem}/sheet.jpg", "clip/sheet.jpg"),
    ],
)
def test_a_pattern_may_start_with_folders_inside_the_inputs_folder(tmp_path, pattern, expected):
    touch(tmp_path / "Show" / "clip.mp4")
    for paths, kwargs in (
        (tmp_path / "Show", {}),
        (tmp_path, {"recursive": True}),
        (tmp_path / "Show" / "clip.mp4", {}),
    ):
        files = GENERATORS["thumbs"](paths, output_name=pattern, **kwargs).plan().files
        assert [item.output for item in files] == [tmp_path / "Show" / pathlib.PurePosixPath(expected)]


@pytest.mark.parametrize(
    "pattern, message",
    [
        ("/sheets/{input_name}.jpg", "is a full path; a pattern's folders are inside each input's folder"),
        ("\\sheets\\{input_name}.jpg", "is a full path"),
        ("C:/sheets/{input_name}.jpg", "is a full path"),
        ("C:\\x\\{input_name}.jpg", "'C:\\x\\{input_name}.jpg' is a full path"),
        ("c:{input_name}.jpg", "':'"),
        ("~/sheets/{input_name}.jpg", "is a full path"),
        ("../{input_name}.jpg", "has a . or .. folder"),
        ("sheets/../{input_name}.jpg", "has a . or .. folder"),
        ("./{input_name}.jpg", "has a . or .. folder"),
        ("sheets//{input_name}.jpg", "has an empty folder name"),
        ("sheets/", "ends in a folder separator"),
        ("she:ets/{input_name}.jpg", "':'"),
    ],
)
def test_folders_that_leave_the_inputs_folder_are_refused(pattern, message):
    with pytest.raises(InvalidOptionsError) as e:
        NameTemplate(pattern)
    assert message in e.value.user_message


def test_a_folder_from_a_field_is_checked_like_a_name(tmp_path):
    touch(tmp_path / "x.mp4")
    planned = plan("strip", tmp_path, output_name="{input_ext}/{input_name}")
    assert planned["x.mp4"][0] == "success"
    refused = plan("thumbs", tmp_path, output_name="{input_ext}x/{input_stem}.jpg")
    assert refused["x.mp4"][0] == "success"
    for name in ("nul/x.jpg", "a./x.jpg", "a|b/x.jpg"):
        with pytest.raises(NameRefused, match="folder name|Windows"):
            check_name(name, pathlib.PurePosixPath("x.mp4"), output_format("jpeg"))


def test_a_field_value_with_a_backslash_never_makes_a_folder(tmp_path):
    source = tmp_path / "a\\b.mp4"
    touch(source)
    files = GENERATORS["thumbs"](tmp_path, output_name="{input_name}.jpeg").plan().files
    assert [item.output for item in files] == [tmp_path / "a\\b.mp4.jpeg"]


def test_outputs_in_pattern_folders_are_the_runs_own_on_a_rerun(tmp_path):
    for path in (tmp_path / "Show" / "S01" / "a.jpg", tmp_path / "Show" / "S02" / "b.png"):
        path.parent.mkdir(parents=True)
        Image.new("RGB", (16, 8), "red").save(path)
    for _ in range(2):
        list(GENERATORS["resize"](tmp_path / "Show", recursive=True, output_name="small/{input_name}"))
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()) == [
        "Show/S01/a.jpg", "Show/S01/small/a.jpg", "Show/S02/b.png", "Show/S02/small/b.png",
    ]  # fmt: skip
    plan_ = GENERATORS["resize"](tmp_path / "Show", recursive=True, output_name="small/{input_name}").plan()
    assert {item.source.name: item.action for item in plan_.files} == {"a.jpg": "exists", "b.png": "exists"}
    assert {path.relative_to(tmp_path).as_posix() for path, _ in plan_.left_out} == {
        "Show/S01/small/a.jpg",
        "Show/S02/small/b.png",
    }


def test_a_stem_name_in_another_folder_of_the_same_name_is_not_an_output(tmp_path):
    touch(tmp_path / "a" / "photo.png", tmp_path / "b" / "small" / "photo.png")
    plan_ = GENERATORS["resize"](tmp_path, recursive=True, output_name="small/{input_name}").plan()
    assert sorted(item.source.relative_to(tmp_path).as_posix() for item in plan_.files) == [
        "a/photo.png",
        "b/small/photo.png",
    ]
