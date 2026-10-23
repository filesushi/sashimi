# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
One test per bug found in real use, and per rule the owner settled from one, run the way it was
found: CLI commands one after another in one folder. A command processes other commands'
outputs, each exactly once, and never its own. Video processing is stubbed; images are real.
"""

import importlib
import os
import json
import pathlib

import pytest
from PIL import Image
from typer.testing import CliRunner

from sashimi.cli import settings
from sashimi.cli.main import app

runner = CliRunner()

STEM_ARGS = ["--output-name", "{input_stem}.{output_ext}"]
"""Names conversions after the input's stem, as ``photo.webp``."""
STEM_JPG = "{input_stem}.jpg"
"""Names a sheet after the video's stem, as ``clip.jpg``."""


def stub(src, dst, *args, **kwargs):
    Image.new("RGB", (8, 8), "blue").save(dst, format="JPEG")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    settings.load_raw_config.cache_clear()
    for module, name in (
        ("sashimi.api.thumbs", "render"),
        ("sashimi.api.transform", "transform"),
        ("sashimi.core.video", "convert"),
    ):
        monkeypatch.setattr(importlib.import_module(module), name, stub)
    yield
    settings.load_raw_config.cache_clear()


def sashimi(*args):
    result = runner.invoke(app, list(args), env={"COLUMNS": "1000"})
    assert result.exit_code == 0, result.output
    return result


def files(folder):
    return sorted(p.name for p in folder.iterdir() if p.is_file())


def report(*args):
    return json.loads(sashimi(*args, "--json").stdout)


def written(result):
    return sorted(
        (pathlib.PurePath(r["file"]).as_posix(), pathlib.PurePath(r["output"]).as_posix())
        for r in result["results"]
        if r["status"] == "success"
    )


def test_convert_then_convert_converts_the_first_outputs_once(tmp_path):
    Image.new("RGB", (16, 8), "red").save(tmp_path / "photo.png")
    sashimi("convert", ".", "webp")
    assert written(report("convert", ".", "jpeg")) == [
        ("photo.png", "photo.png.jpeg"),
        ("photo.png.webp", "photo.png.webp.jpeg"),
    ]
    assert written(report("convert", ".", "jpeg")) == []
    assert files(tmp_path) == ["photo.png", "photo.png.jpeg", "photo.png.webp", "photo.png.webp.jpeg"]


def test_thumbs_then_convert_converts_the_sheets_once(tmp_path):
    (tmp_path / "x.mp4").write_bytes(b"video")
    sashimi("thumbs", ".")
    converted = report("convert", ".", "webp", "-y")
    assert written(converted) == [("x.mp4", "x.mp4.webp"), ("x.mp4.sheet.jpg", "x.mp4.sheet.jpg.webp")]
    assert converted["outputs_left_out"] == []
    assert written(report("convert", ".", "webp", "-y")) == []


def test_rotate_then_thumbs_makes_one_sheet_per_video(tmp_path):
    (tmp_path / "x.mp4").write_bytes(b"video")
    sashimi("rotate", ".", "90")
    assert written(report("thumbs", ".")) == [
        ("x.mp4", "x.mp4.sheet.jpg"),
        ("x.mp4.rotated.mp4", "x.mp4.rotated.mp4.sheet.jpg"),
    ]
    assert written(report("thumbs", ".")) == []


def test_resize_edit_convert_converts_each_file_once(tmp_path):
    Image.new("RGB", (40, 20), "red").save(tmp_path / "photo.jpg")
    sashimi("resize", ".", "-w", "20")
    sashimi("edit", ".", "--rotate", "90")
    pngs = written(report("convert", ".", "png"))
    assert pngs == [
        ("photo.jpg", "photo.jpg.png"),
        ("photo.jpg.edited.jpg", "photo.jpg.edited.jpg.png"),
        ("photo.jpg.resized.jpg", "photo.jpg.resized.jpg.png"),
        ("photo.jpg.resized.jpg.edited.jpg", "photo.jpg.resized.jpg.edited.jpg.png"),
    ]
    assert written(report("convert", ".", "png")) == []
    assert len(files(tmp_path)) == 8


def owners_layout(tmp_path):
    videos = tmp_path / "New folder"
    (videos / "sheets").mkdir(parents=True)
    for number in (5, 6, 7):
        (videos / f"Bluebeard-{number}.mkv").write_bytes(b"video")
        Image.new("RGB", (8, 8)).save(videos / "sheets" / f"Bluebeard-{number}.mkv.jpeg", format="JPEG")
    return videos


def test_the_owners_layout_converts_videos_and_sheets_after_asking(tmp_path):
    videos = owners_layout(tmp_path)
    asked = runner.invoke(app, ["convert", "New folder", "webp", "-r"])
    assert asked.exit_code == 2
    flat = " ".join(asked.stderr.split())
    assert "New folder contains 3 images and 3 videos, which convert handles differently." in flat
    assert "Pass -y to convert all 6 to WEBP" in flat
    assert sorted(p.name for p in videos.rglob("*") if p.is_file()) == [
        "Bluebeard-5.mkv", "Bluebeard-5.mkv.jpeg", "Bluebeard-6.mkv", "Bluebeard-6.mkv.jpeg",
        "Bluebeard-7.mkv", "Bluebeard-7.mkv.jpeg",
    ]  # fmt: skip
    done = report("convert", "New folder", "webp", "-r", "-y")
    assert [pathlib.PurePath(output).name for _, output in written(done)] == [
        "Bluebeard-5.mkv.webp", "Bluebeard-6.mkv.webp", "Bluebeard-7.mkv.webp",
        "Bluebeard-5.mkv.jpeg.webp", "Bluebeard-6.mkv.jpeg.webp", "Bluebeard-7.mkv.jpeg.webp",
    ]  # fmt: skip
    assert written(report("convert", "New folder", "webp", "-r", "-y")) == []


def test_converting_the_sheets_folder_converts_only_the_sheets(tmp_path):
    videos = owners_layout(tmp_path)
    done = report("convert", str(videos / "sheets"), "webp")
    assert [pathlib.PurePath(output).name for _, output in written(done)] == [
        "Bluebeard-5.mkv.jpeg.webp", "Bluebeard-6.mkv.jpeg.webp", "Bluebeard-7.mkv.jpeg.webp",
    ]  # fmt: skip


@pytest.mark.parametrize("out", ["sheets", "a/b/sheets"])
def test_thumbs_into_an_output_folder_twice_writes_nothing_new(tmp_path, out):
    sashimi("config", "set", "thumbs.output_name", "{input_name}.jpeg")
    for name in ("x.mp4", "sub/y.mkv"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"video")
    first = report("thumbs", ".", "-r", "--output-dir", out)
    assert [pathlib.PurePath(output).name for _, output in written(first)] == ["y.mkv.jpeg", "x.mp4.jpeg"]
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    again = report("thumbs", ".", "-r", "--output-dir", out)
    assert {r["status"] for r in again["results"]} == {"exists"}
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()) == before


def test_resizing_again_without_the_output_folder_leaves_the_earlier_outputs_out(tmp_path):
    (tmp_path / "photos").mkdir()
    Image.new("RGB", (40, 20), "red").save(tmp_path / "photos" / "photo.jpg")
    sashimi("resize", "photos", "-w", "20", "--output-dir", "resized")
    again = report("resize", ".", "-r", "-w", "20")
    photo = pathlib.Path("photos") / "photo.jpg"
    assert written(again) == [(str(photo), str(photo.with_name("photo.jpg.resized.jpg")))]
    earlier = pathlib.Path("resized") / "photo.jpg.resized.jpg"
    assert again["outputs_left_out"] == [{"file": str(earlier), "made_from": str(photo)}]


def test_an_original_is_never_reported_as_an_earlier_output(tmp_path):
    (tmp_path / "x.mkv").write_bytes(b"mkv")
    (tmp_path / "x.mp4").write_bytes(b"the user's own mp4")
    result = json.loads(sashimi("convert", ".", "mp4", *STEM_ARGS, "--json").stdout)["results"]
    assert [(r["file"], r["status"]) for r in result] == [("x.mkv", "exists")]
    assert result[0]["message"] != "Output already exists: x.mp4"
    assert (tmp_path / "x.mp4").read_bytes() == b"the user's own mp4"


def test_a_photo_is_never_reported_as_a_sheet(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"video")
    Image.new("RGB", (8, 8)).save(tmp_path / "clip.jpg")
    photo = (tmp_path / "clip.jpg").read_bytes()
    result = json.loads(sashimi("thumbs", ".", "--output-name", STEM_JPG, "--json").stdout)["results"]
    assert [(r["file"], r["status"]) for r in result] == [("clip.mp4", "exists")]
    assert result[0]["message"] != "Output already exists: clip.jpg"
    assert (tmp_path / "clip.jpg").read_bytes() == photo


def test_rerunning_resize_never_resizes_its_own_outputs(tmp_path):
    Image.new("RGB", (40, 20), "red").save(tmp_path / "photo.jpg")
    for _ in range(3):
        sashimi("resize", ".", "-w", "10")
    assert len(files(tmp_path)) == 2
    assert not [name for name in files(tmp_path) if name.count("resized") > 1]


def test_frames_after_edit_writes_its_own_file(tmp_path):
    frames = [Image.new("RGB", (8, 8), (i * 60, 0, 0)) for i in range(4)]
    frames[0].save(tmp_path / "anim.gif", save_all=True, append_images=frames[1:], duration=80, loop=0)
    sashimi("edit", "anim.gif", "--rotate", "90")
    edited = {name: (tmp_path / name).read_bytes() for name in files(tmp_path) if name != "anim.gif"}
    result = json.loads(sashimi("frames", "anim.gif", "--drop", "1", "--json").stdout)["results"]
    assert result[0]["status"] == "success"
    assert all((tmp_path / name).read_bytes() == data for name, data in edited.items())
    assert len(files(tmp_path)) == 3


@pytest.mark.parametrize("out", [".", ".."])
def test_an_output_folder_that_holds_the_searched_folder_still_finds_the_files(tmp_path, out):
    (tmp_path / "videos" / "sub").mkdir(parents=True)
    (tmp_path / "videos" / "sub" / "x.mp4").write_bytes(b"video")
    os.chdir(tmp_path / "videos")
    first = report("thumbs", ".", "-r", "--output-dir", out)
    assert [pathlib.PurePath(output).name for _, output in written(first)] == ["x.mp4.sheet.jpg"]
    assert {r["status"] for r in report("thumbs", ".", "-r", "--output-dir", out)["results"]} == {"exists"}


def test_a_file_named_inside_the_output_folder_is_processed(tmp_path):
    (tmp_path / "sheets").mkdir()
    (tmp_path / "sheets" / "x.mp4").write_bytes(b"video")
    done = report("thumbs", str(pathlib.Path("sheets") / "x.mp4"), "--output-dir", "sheets")
    assert written(done) == [(str(pathlib.Path("sheets/x.mp4")), str(pathlib.Path("sheets/x.mp4.sheet.jpg")))]


def test_an_output_file_must_end_in_its_formats_extension(tmp_path):
    Image.new("RGB", (16, 8), "red").save(tmp_path / "photo.jpg")
    result = runner.invoke(app, ["convert", "photo.jpg", "webp", "-o", "out.png", "--json"])
    assert result.exit_code == 1
    row = json.loads(result.stdout)["results"][0]
    assert (row["status"], row["message"]) == ("error", "out.png must end in .webp, since the output is WEBP.")
    assert not (tmp_path / "out.png").exists()
    assert written(report("convert", "photo.jpg", "webp", "-o", "out.WEBP")) == [("photo.jpg", "out.WEBP")]


def test_an_output_file_and_an_output_folder_together_are_refused(tmp_path):
    Image.new("RGB", (16, 8), "red").save(tmp_path / "photo.png")
    result = runner.invoke(app, ["convert", "photo.png", "webp", "-o", "x.webp", "--output-dir", "out"])
    assert result.exit_code == 2
    assert "-o/--output names the output file, so leave out --output-dir." in " ".join(result.stderr.split())
    assert files(tmp_path) == ["photo.png"]


def test_flatten_asks_before_removing_empty_folders(tmp_path):
    (tmp_path / "keep" / "empty").mkdir(parents=True)
    result = runner.invoke(app, ["flatten", "keep"], input="n\n")
    assert result.exit_code == 1
    assert "removes 1 empty subdirectory" in " ".join(result.stderr.split())
    assert (tmp_path / "keep" / "empty").is_dir()


def test_messages_say_which_folder_an_output_is_in(tmp_path):
    (tmp_path / "New folder").mkdir()
    (tmp_path / "New folder" / "Bluebeard-5.mkv").write_bytes(b"video")
    sashimi("thumbs", "New folder", "--output-dir", "sheets")
    again = report("thumbs", "New folder", "--output-dir", "sheets")
    shown = str(pathlib.Path("sheets") / "Bluebeard-5.mkv.sheet.jpg")
    assert again["results"][0]["message"] == f"Output already exists: {shown}"
    dry = report("thumbs", "New folder", "--output-dir", "sheets", "-f", "-n")
    assert dry["results"][0]["message"] == f"Would replace {shown} (the existing file)"
    here = report("thumbs", "New folder")
    assert here["results"][0]["message"] == "Contact sheet created: Bluebeard-5.mkv.sheet.jpg"


@pytest.mark.parametrize("command", ["info", "metadata"])
def test_a_folder_given_to_info_or_metadata_is_called_a_folder(tmp_path, command):
    (tmp_path / "videos").mkdir()
    result = runner.invoke(app, [command, "videos"])
    assert result.exit_code == 1
    assert f"videos is a folder; {command} shows one file at a time." in " ".join(result.stderr.split())


@pytest.mark.parametrize(
    "angle, flip, said",
    [("90", "none", "Rotated to"), ("0", "horizontal", "Flipped to"), ("180", "vertical", "Rotated and flipped to")],
)
def test_rotate_says_what_it_did(tmp_path, angle, flip, said):
    Image.new("RGB", (16, 8), "red").save(tmp_path / "photo.jpg")
    row = report("rotate", "photo.jpg", angle, "--flip", flip)["results"][0]
    assert row["message"] == f"{said} photo.jpg.rotated.jpg"


def test_an_empty_search_says_what_the_command_works_on(tmp_path):
    (tmp_path / "videos" / "sub").mkdir(parents=True)
    (tmp_path / "videos" / "sub" / "x.mp4").write_bytes(b"video")
    result = runner.invoke(app, ["thumbs", "videos"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "Found no files thumbs works on (videos); -r searches subfolders too." in result.stdout


def test_an_earlier_output_in_a_pattern_folder_is_reported_as_one(tmp_path):
    Image.new("RGB", (40, 20), "red").save(tmp_path / "poster.jpg")
    sashimi("resize", ".", "-w", "20", "--output-name", "small/{input_name}")
    again = report("resize", ".", "-r", "-w", "20", "--output-name", "small/{input_name}")
    shown = str(pathlib.Path("small") / "poster.jpg")
    assert [r["message"] for r in again["results"]] == [f"Output already exists: {shown}"]
    assert again["outputs_left_out"] == [{"file": shown, "made_from": "poster.jpg"}]


def bluebeard(tmp_path):
    folder = tmp_path / "Videos" / "New folder"
    folder.mkdir(parents=True)
    for n in (5, 6, 7):
        (folder / f"Bluebeard-{n}.mkv").write_bytes(b"video")
    return folder


def test_a_setting_saved_inside_a_folder_applies_from_anywhere(tmp_path):
    folder = bluebeard(tmp_path)
    os.chdir(folder)
    sashimi("config", "set", "thumbs.output_name", "sheets/{input_name}.jpeg")
    sheets = [str(pathlib.Path("New folder", "sheets", f"Bluebeard-{n}.mkv.jpeg")) for n in (5, 6, 7)]
    os.chdir(folder.parent)
    assert [r["output"] for r in report("thumbs", "New folder", "-n")["results"]] == sheets
    assert [r["output"] for r in report("thumbs", "New folder", "-r", "-n")["results"]] == sheets
    os.chdir(tmp_path)
    planned = report("thumbs", str(folder), "-n")["results"]
    assert [r["output"] for r in planned] == [str(folder / "sheets" / f"Bluebeard-{n}.mkv.jpeg") for n in (5, 6, 7)]
    os.chdir(folder.parent)
    assert report("thumbs", "New folder", "-r")["summary"]["success"] == 3
    assert report("thumbs", "New folder", "-r")["summary"]["exists"] == 3
    assert files(folder) == ["Bluebeard-5.mkv", "Bluebeard-6.mkv", "Bluebeard-7.mkv"]


def test_config_set_and_unset_say_which_file_they_changed(tmp_path):
    user = tmp_path / "home" / "sashimi.toml"
    said = " ".join(sashimi("config", "set", "thumbs.cols", "4").stdout.split())
    assert f"in {user}" in said and "used wherever you run sashimi" in said
    said = " ".join(sashimi("config", "set", "--project", "thumbs.cols", "5").stdout.split())
    assert f"in {tmp_path / 'sashimi.toml'}" in said and f"run sashimi in {tmp_path} or a folder inside it" in said
    said = " ".join(sashimi("config", "set", "thumbs.cols", "6").stdout.split())
    assert f"Note: {tmp_path / 'sashimi.toml'} sets thumbs.cols too (5)" in said
    said = " ".join(sashimi("config", "unset", "thumbs.cols").stdout.split())
    assert f"Removed thumbs.cols from {user}" in said and "sets thumbs.cols (5)" in said
    said = " ".join(sashimi("config", "unset", "--project", "thumbs.cols").stdout.split())
    assert f"Removed thumbs.cols from {tmp_path / 'sashimi.toml'}" in said


def test_config_set_never_overwrites_a_file_that_isnt_toml(tmp_path):
    user = tmp_path / "home" / "sashimi.toml"
    user.parent.mkdir()
    user.write_text('[thumbs]\ncols = 4\noutput_name = "{input_name}.jpeg\n')
    result = runner.invoke(app, ["config", "set", "thumbs.rows", "2"])
    assert result.exit_code == 2 and "isn't valid TOML" in " ".join(result.stderr.split())
    assert user.read_text() == '[thumbs]\ncols = 4\noutput_name = "{input_name}.jpeg\n'


def photos(tmp_path, *names):
    for name in names:
        Image.new("RGB", (16, 8), "red").save(tmp_path / name)


def test_resize_convert_and_rotate_take_several_files(tmp_path):
    photos(tmp_path, "a.jpg", "b.jpg", "c.png")
    assert written(report("resize", "a.jpg", "b.jpg", "-w", "8")) == [
        ("a.jpg", "a.jpg.resized.jpg"),
        ("b.jpg", "b.jpg.resized.jpg"),
    ]
    assert written(report("convert", "a.jpg", "c.png", "webp")) == [("a.jpg", "a.jpg.webp"), ("c.png", "c.png.webp")]
    assert written(report("rotate", "a.jpg", "b.jpg", "180")) == [
        ("a.jpg", "a.jpg.rotated.jpg"),
        ("b.jpg", "b.jpg.rotated.jpg"),
    ]
    (tmp_path / "x.mp4").write_bytes(b"video")
    (tmp_path / "y.mp4").write_bytes(b"video")
    assert written(report("rotate", "x.mp4", "y.mp4")) == [
        ("x.mp4", "x.mp4.rotated.mp4"),
        ("y.mp4", "y.mp4.rotated.mp4"),
    ]


@pytest.mark.parametrize(
    "args, said",
    [
        (["convert", "webp"], "End with the format to convert to, e.g. sashimi convert webp jpeg"),
        (["rotate", "a.jpg", "90"], "90 is an angle and also a folder here"),
        (["convert", "a.jpg", "b.jpg"], "End with the format to convert to"),
        (["convert", "a.jpg"], "End with the format to convert to"),
        (["convert", "jpeg"], "Name the files or folders to convert, then the format"),
        (["rotate", "180"], "Name the files or folders to rotate, then the angle"),
        (["rotate", "a.jpg", "45"], "45 isn't a file here, nor an angle rotate takes (0, 90, 180 or 270)"),
        (["convert", "a.jpg", "jpeg", "--output-format", "png"], "Give the format once"),
        (["rotate", "a.jpg", "180", "--angle", "90"], "Give the angle once"),
    ],
)
def test_names_that_look_like_formats_or_angles_are_never_guessed(tmp_path, args, said):
    photos(tmp_path, "a.jpg", "b.jpg")
    (tmp_path / "webp").write_bytes(b"not a format")
    (tmp_path / "90").mkdir()
    result = runner.invoke(app, args)
    assert result.exit_code == 2 and said in " ".join(result.stderr.split()), result.output
    assert files(tmp_path) == ["a.jpg", "b.jpg", "webp"]


def test_a_glob_that_names_a_folder_like_the_angle_or_format_still_takes_the_last_word(tmp_path):
    photos(tmp_path, "a.jpg", "b.jpg")
    for folder in ("90", "png"):
        (tmp_path / folder).mkdir()
    rotated = runner.invoke(app, ["rotate", "90", "a.jpg", "b.jpg", "png", "90", "--json"])
    assert rotated.exit_code == 0 and "won't guess" not in rotated.stderr
    assert written(json.loads(rotated.stdout)) == [("a.jpg", "a.jpg.rotated.jpg"), ("b.jpg", "b.jpg.rotated.jpg")]
    converted = runner.invoke(app, ["convert", "90", "a.jpg", "b.jpg", "png", "png", "--json"])
    assert converted.exit_code == 0 and "Note" not in converted.stderr
    assert written(json.loads(converted.stdout)) == [("a.jpg", "a.jpg.png"), ("b.jpg", "b.jpg.png")]


def test_messages_show_a_long_list_of_files_as_its_first_and_an_ellipsis(tmp_path):
    photos(tmp_path, "a.jpg", "b.jpg", "c.jpg")
    (tmp_path / "90").mkdir()
    result = runner.invoke(app, ["rotate", "a.jpg", "b.jpg", "c.jpg", "90"])
    said = " ".join(result.stderr.split())
    assert result.exit_code == 2 and "(sashimi rotate a.jpg … --angle 90)" in said and "b.jpg" not in said


def test_the_last_argument_is_the_format_even_when_a_folder_has_its_name(tmp_path):
    photos(tmp_path, "a.png", "b.png")
    sashimi("convert", "a.png", "b.png", "webp", "--output-dir", "webp")
    rerun = runner.invoke(app, ["convert", "a.png", "b.png", "webp", "--output-dir", "webp", "--json"])
    assert rerun.exit_code == 0 and json.loads(rerun.stdout)["summary"]["exists"] == 2
    note = " ".join(rerun.stderr.split())
    assert "Note: webp is taken as the format" in note and "sashimi convert a.png b.png ./webp webp" in note
    assert files(tmp_path / "webp") == ["a.png.webp", "b.png.webp"]


def test_names_that_look_like_formats_or_angles_can_be_written_both_ways(tmp_path):
    photos(tmp_path, "a.jpg")
    (tmp_path / "webp").mkdir()
    photos(tmp_path / "webp", "b.png")
    (tmp_path / "90").mkdir()
    photos(tmp_path / "90", "c.jpg")
    assert written(report("convert", "a.jpg", "--output-format", "webp")) == [("a.jpg", "a.jpg.webp")]
    assert written(report("convert", "./webp", "jpeg")) == [("webp/b.png", "webp/b.png.jpeg")]
    assert written(report("convert", "webp", "--output-format", "gif")) == [
        ("webp/b.png", "webp/b.png.gif"),
        ("webp/b.png.jpeg", "webp/b.png.jpeg.gif"),
    ]
    assert written(report("rotate", "a.jpg", "--angle", "180")) == [("a.jpg", "a.jpg.rotated.jpg")]
    assert written(report("rotate", "./90")) == [("90/c.jpg", "90/c.jpg.rotated.jpg")]
    assert written(report("rotate", "90", "-n")) == []
    assert report("rotate", "90", "-n")["results"][0]["status"] == "exists"


def test_a_named_file_already_in_the_format_is_not_an_error(tmp_path):
    photos(tmp_path, "a.jpg")
    (tmp_path / "x.mkv").write_bytes(b"video")
    for args in (["convert", "a.jpg", "jpeg"], ["convert", "a.jpg", "jpg"], ["remux", "x.mkv"]):
        result = runner.invoke(app, [*args, "--json"])
        assert result.exit_code == 0, args
        done = json.loads(result.stdout)
        assert done["results"][0]["status"] == "skipped" and done["results"][0]["message"].startswith("Already")
        assert done["already_done"] == [args[1]]
    (tmp_path / "notes.txt").write_text("hi")
    assert runner.invoke(app, ["convert", "notes.txt", "jpeg"]).exit_code == 1


@pytest.mark.parametrize("command", ["thumbs", "preview"])
def test_width_has_a_short_flag_everywhere(tmp_path, command):
    (tmp_path / "x.mp4").write_bytes(b"video")
    long = report(command, "x.mp4", "--width", "640", "-n")
    assert report(command, "x.mp4", "-w", "640", "-n") == {**long, "summary": {**long["summary"]}}


def test_frames_with_nothing_to_change_is_refused(tmp_path):
    frames = [Image.new("RGB", (8, 8), colour) for colour in ("red", "blue")]
    frames[0].save(tmp_path / "anim.gif", save_all=True, append_images=frames[1:], duration=100)
    result = runner.invoke(app, ["frames", "anim.gif"])
    assert result.exit_code == 2 and "Nothing to change: give --keep or --drop" in " ".join(result.stderr.split())
    assert runner.invoke(app, ["frames", "anim.gif", "--speed", "1"]).exit_code == 2
    assert files(tmp_path) == ["anim.gif"]
    assert written(report("frames", "anim.gif", "--output-format", "webp")) == [("anim.gif", "anim.gif.frames.webp")]


def test_a_conversions_suggested_short_pattern_works(tmp_path):
    shown = json.loads(sashimi("config", "defaults", "convert", "--json").stdout)[0]["fields"][0]
    assert "{role}" not in shown["accepted"]
    short = "{input_stem}.{output_ext}"
    assert f"{short} gives short names" in shown["description"]
    sashimi("convert", ".", "webp", "--output-name", short, "-n")


PROCESSING = {
    "convert": ("sashimi.api.convert", "convert_files_generator", ["convert", "photo.png", "webp"]),
    "remux": ("sashimi.api.remux", "remux_files_generator", ["remux", "clip.mp4"]),
    "resize": ("sashimi.api.resize", "resize_images_generator", ["resize", "photo.png", "-w", "4"]),
    "rotate": ("sashimi.api.transform", "rotate_files_generator", ["rotate", "photo.png", "90"]),
    "extract": ("sashimi.api.extract", "extract_generator", ["extract", "clip.mp4", "1", "2"]),
    "edit": ("sashimi.api.edit", "edit_images_generator", ["edit", "photo.png", "--rotate", "90"]),
    "frames": ("sashimi.api.edit", "edit_frames_generator", ["frames", "photo.png", "--speed", "2"]),
    "strip": ("sashimi.api.strip", "strip_files_generator", ["strip", "photo.png"]),
    "thumbs": ("sashimi.api.thumbs", "make_thumbs_generator", ["thumbs", "clip.mp4"]),
    "animated_thumbs": ("sashimi.api.thumbs", "make_thumbs_generator", ["thumbs", "clip.mp4", "--animated"]),
    "preview": ("sashimi.api.preview", "make_previews_generator", ["preview", "clip.mp4"]),
    "favicon": ("sashimi.api.favicon", "make_favicon_packs_generator", ["favicon", "photo.png"]),
}
"""Every processing command: where its batch function is, and a run of it."""


def test_every_processing_command_is_listed():
    from sashimi.api._naming import COMMANDS

    assert set(PROCESSING) == set(COMMANDS)


def capture(monkeypatch, command, effect):
    module, name, args = PROCESSING[command]
    seen = {}

    def batch(*arguments, **kwargs):
        seen.update(kwargs)
        return effect()

    monkeypatch.setattr(importlib.import_module(module), name, batch)
    Image.new("RGB", (8, 8), "red").save("photo.png")
    pathlib.Path("clip.mp4").write_bytes(b"video")
    return args, seen


@pytest.mark.parametrize("command", sorted(PROCESSING))
def test_every_processing_command_runs_with_the_limits_in_sashimi_toml(tmp_path, monkeypatch, command):
    sashimi("config", "set", "limits.max_pixels", "1234")
    args, seen = capture(monkeypatch, command, lambda: iter([0]))
    runner.invoke(app, args)
    assert seen.get("limits") is not None and seen["limits"].max_pixels == 1234, command


def test_strip_keeps_to_the_pixel_limit_in_sashimi_toml(tmp_path):
    Image.new("RGB", (20, 20), "red").save(tmp_path / "photo.png")
    sashimi("config", "set", "limits.max_pixels", "100")
    result = runner.invoke(app, ["strip", "photo.png", "--json"])
    assert json.loads(result.stdout)["results"][0]["status"] == "error"
    assert files(tmp_path) == ["photo.png"]


@pytest.mark.parametrize("command", sorted(PROCESSING))
def test_every_processing_commands_refusals_keep_bracketed_text(tmp_path, monkeypatch, command):
    from sashimi.errors import InvalidOptionsError

    def refuse():
        raise InvalidOptionsError("The pattern [draft] {input_name}.jpeg can't be used.")

    args, _ = capture(monkeypatch, command, refuse)
    result = runner.invoke(app, args)
    assert result.exit_code == 2, (command, result.output)
    assert "[draft] {input_name}.jpeg" in " ".join(result.stderr.split()), command


def test_rotate_refusals_keep_bracketed_text(tmp_path):
    Image.new("RGB", (8, 8), "red").save(tmp_path / "p.png")
    result = runner.invoke(
        app, ["rotate", "p.png", "90", "--output-format", "png", "--output-name", "[draft] {input_name}.jpeg"]
    )
    assert result.exit_code == 2 and "[draft] {input_name}.jpeg" in " ".join(result.stderr.split())


def test_thumbs_settings_errors_keep_bracketed_text(tmp_path, monkeypatch):
    from sashimi.errors import InvalidOptionsError

    def refuse(*args, **kwargs):
        raise InvalidOptionsError("The setting [x] can't be used.")

    (tmp_path / "clip.mp4").write_bytes(b"video")
    monkeypatch.setattr(settings, "resolve_sheet", refuse)
    result = runner.invoke(app, ["thumbs", "clip.mp4"])
    assert result.exit_code == 2 and "[x]" in result.stderr


@pytest.mark.parametrize(
    "key", ["resize.output_dir", "thumbs.output_dir", "thumbs.emoji_dir", "thumbs.header_font_path"]
)
@pytest.mark.parametrize(
    "value, kept", [("2024", "2024"), ("2024-10-21", "2024-10-21"), ("true", "true"), ('"2024"', "2024")]
)
def test_config_set_keeps_folder_and_file_names_as_typed(tmp_path, key, value, kept):
    sashimi("config", "set", key, value)
    section, option = key.split(".")
    shown = json.loads(sashimi("config", "show", "--json").stdout)["settings"]
    assert shown[section][option] == kept


def test_a_kept_output_folder_named_like_a_year_is_used(tmp_path):
    Image.new("RGB", (8, 8), "red").save(tmp_path / "photo.png")
    sashimi("config", "set", "resize.output_dir", "2024")
    sashimi("resize", "photo.png", "-w", "4")
    assert files(tmp_path / "2024") == ["photo.png.resized.png"]


def output_formats():
    import typer

    found = []
    for name, command in typer.main.get_command(app).commands.items():  # type: ignore[attr-defined]
        if command.hidden:
            continue
        for param in command.params:
            if "--output-format" in (param.opts or []):
                found += [(name, choice) for choice in param.type.choices]
    return found


@pytest.mark.parametrize("command, fmt", output_formats())
def test_config_defaults_takes_every_format_a_command_writes(tmp_path, command, fmt):
    sashimi("config", "defaults", command, fmt)


def test_config_defaults_rotate_shows_only_the_image_options_rotate_has(tmp_path):
    tables = json.loads(sashimi("config", "defaults", "rotate", "jpeg", "--json").stdout)
    fields = {field["field"]: field for table in tables[1:] for field in table["fields"]}
    assert set(fields) == {"rotate", "flip", "metadata", "quality"}
    assert fields["quality"]["default"] == 92 and fields["rotate"]["default"] == 90


def test_a_dot_in_a_patterns_folder_is_not_the_extension(tmp_path):
    Image.new("RGB", (8, 8), "red").save(tmp_path / "q.png")
    result = runner.invoke(app, ["convert", "q.png", "jpeg", "--output-name", "{input_stem}.d/sheet", "--json"])
    message = json.loads(result.stdout)["results"][0]["message"]
    assert message == "sheet must end in .jpeg or .jpg, since the output is JPEG."


def tree(tmp_path):
    for folder in ("photos", "photos/sub"):
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "red").save(tmp_path / folder / "x.jpg")


@pytest.mark.parametrize("extra", [["photos/sub"], ["photos/x.jpg", "photos/sub"]])
def test_naming_a_subfolder_too_doesnt_move_outputs(tmp_path, extra):
    tree(tmp_path)
    alone = written(report("resize", "photos", "-r", "-w", "4", "--output-dir", "out1", "-n"))
    glob = written(report("resize", "photos", *extra, "-r", "-w", "4", "--output-dir", "out1", "-n"))
    assert (
        alone
        == glob
        == [("photos/sub/x.jpg", "out1/sub/x.jpg.resized.jpg"), ("photos/x.jpg", "out1/x.jpg.resized.jpg")]
    )


@pytest.mark.parametrize("stem", ["a" * 230, "写" * 75, "写" * 80])
def test_a_long_name_the_dry_run_accepts_is_written(tmp_path, stem):
    Image.new("RGB", (8, 8), "red").save(tmp_path / f"{stem}.png")
    planned = json.loads(runner.invoke(app, ["strip", f"{stem}.png", "-n", "--json"]).stdout)["results"][0]
    result = runner.invoke(app, ["strip", f"{stem}.png", "--json"])
    done = json.loads(result.stdout)["results"][0]
    assert planned["status"] == done["status"], (planned, done)
    if done["status"] == "success":
        assert (tmp_path / f"{stem}.png.stripped.png").is_file()
    else:
        assert "255 bytes" in planned["message"]
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]


def photos(tmp_path, *names):
    for name in names:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "red").save(tmp_path / name)


def shell_glob(folder):
    return sorted(str(p) for p in pathlib.Path(folder).iterdir() if not p.name.startswith("."))


def plain(*args):
    result = runner.invoke(app, list(args), env={"COLUMNS": "400"}, terminal_width=400)
    return result, " ".join(result.stdout.split())


def test_a_glob_into_one_output_folder_says_how_to_keep_the_layout(tmp_path):
    photos(tmp_path, "photos/x.jpg", "photos/y.jpg", "photos/sub/x.jpg")
    for dry in ([], ["-n"]):
        result, text = plain("resize", *shell_glob("photos"), "-w", "4", "--output-dir", "out", *dry)
        assert result.exit_code == 1
        assert (
            "2 files from different folders were skipped because they'd meet in out. To keep each folder's layout "
            "there, name the folder that holds them, with -r: "
            f"sashimi resize photos -r -w 4 --output-dir out{' -n' if dry else ''}"
        ) in text
        assert "-f" not in text.split("To keep")[1]
    sashimi("resize", "photos", "-r", "-w", "4", "--output-dir", "out")
    assert sorted(p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*.jpg")) == [
        "sub/x.jpg.resized.jpg",
        "x.jpg.resized.jpg",
        "y.jpg.resized.jpg",
    ]


def test_the_hint_keeps_options_where_they_were_and_quotes_the_folder(tmp_path):
    photos(tmp_path, "my photos/x.jpg", "my photos/sub/x.jpg")
    _, text = plain("resize", "-w", "4", *shell_glob("my photos"), "--output-dir", "out", "-n")
    assert "with -r: sashimi resize -w 4 'my photos' -r --output-dir out -n" in text


def test_the_hint_for_convert_keeps_the_format_last(tmp_path):
    photos(tmp_path, "photos/x.jpg", "photos/sub/x.jpg")
    _, text = plain("convert", *shell_glob("photos"), "webp", "--output-dir", "out", "-n")
    assert "with -r: sashimi convert photos -r webp --output-dir out -n" in text


def test_a_recursive_run_is_not_told_to_add_r(tmp_path):
    photos(tmp_path, "photos/x.jpg", "photos/sub/x.jpg", "photos/sub/deeper/z.jpg")
    _, text = plain("resize", *shell_glob("photos"), "-r", "-w", "4", "--output-dir", "out", "-n")
    assert "name the folder that holds them instead: sashimi resize photos -r -w 4 --output-dir out -n" in text


def test_two_folders_get_a_command_only_when_the_folder_holding_both_holds_nothing_more(tmp_path):
    photos(tmp_path, "a/x.jpg", "b/x.jpg")
    _, text = plain("resize", "a", "b", "-w", "4", "--output-dir", "out", "-n")
    assert "with -r: sashimi resize . -r -w 4 --output-dir out -n" in text
    photos(tmp_path, "c/z.jpg")
    _, text = plain("resize", "a", "b", "-w", "4", "--output-dir", "out", "-n")
    assert "2 files from different folders were skipped because they'd meet in out." in text
    assert (
        "To keep them apart, run each folder on its own with its own --output-dir. Naming . with -r would keep "
        "each folder's layout too, but it wouldn't process the same files (-n shows which it would)."
    ) in text
    assert "sashimi resize ." not in text


def test_a_kept_output_folder_gets_the_hint_without_the_flag(tmp_path):
    photos(tmp_path, "photos/x.jpg", "photos/sub/x.jpg")
    sashimi("config", "set", "resize.output_dir", "out")
    _, text = plain("resize", *shell_glob("photos"), "-w", "4", "-n")
    assert "they'd meet in out. To keep each folder's layout there, name the folder that holds them, with -r: " in text
    assert "sashimi resize photos -r -w 4 -n" in text


def test_the_json_report_has_the_folder_clash(tmp_path):
    photos(tmp_path, "photos/x.jpg", "photos/sub/x.jpg")
    result = runner.invoke(app, ["resize", *shell_glob("photos"), "-w", "4", "--output-dir", "out", "-n", "--json"])
    clash = json.loads(result.stdout)["folder_clash"]
    clash["files"] = [pathlib.PurePath(name).as_posix() for name in clash["files"]]
    assert clash == {
        "files": ["photos/sub/x.jpg", "photos/x.jpg"],
        "output_dir": "out",
        "holder": "photos",
        "same_files": True,
    }


def test_no_folder_hint_for_a_pattern_clash_a_case_clash_or_no_output_folder(tmp_path):
    photos(tmp_path, "a/x.jpg", "b/x.png")
    _, text = plain(
        "convert", "a", "b", "webp", "--output-dir", "out", "--output-name", "{input_stem}.{output_ext}", "-n"
    )
    assert "because the pattern" in text and "different folders" not in text
    photos(tmp_path, "c/x.jpg", "d/x.jpg")
    _, text = plain("resize", "c", "d", "-w", "4", "-n")
    assert "different folders" not in text and "Skipped" not in text
    photos(tmp_path, "e/x.jpg", "e/X.jpg")
    if len(list((tmp_path / "e").iterdir())) == 2:
        _, text = plain("resize", "e", "-w", "4", "--output-dir", "out", "-n")
        assert "Windows and macOS" in text and "different folders" not in text


def test_the_suggested_command_is_one_line_however_narrow_the_terminal(tmp_path):
    photos(tmp_path, "photos/x.jpg", "photos/sub/x.jpg")
    args = ["resize", *shell_glob("photos"), "-w", "4", "--output-dir", "out", "-n"]
    result = runner.invoke(app, args, env={"COLUMNS": "40"}, terminal_width=40)
    assert "  sashimi resize photos -r -w 4 --output-dir out -n" in result.stdout.splitlines()


def test_a_clash_with_both_causes_ends_with_both_lines(tmp_path):
    photos(tmp_path, "a/x.jpg", "a/x.png", "b/x.jpg")
    args = ["convert", "a", "b", "webp", "--output-dir", "out", "--output-name", "{input_stem}.{output_ext}", "-n"]
    _, text = plain(*args)
    assert "3 files were skipped because the pattern {input_stem}.{output_ext} (--output-name)" in text
    assert (
        "Files from different folders also meet in out. To keep each folder's layout there, name the folder that "
        "holds them, with -r: sashimi convert . -r webp --output-dir out --output-name '{input_stem}.{output_ext}' -n"
    ) in text
