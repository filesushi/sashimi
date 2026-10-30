# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import json

import pytest
import typer
from PIL import Image
from typer.testing import CliRunner

from sashimi.api._naming import COMMANDS
from sashimi.cli import _output, settings
from sashimi.cli.main import app

runner = CliRunner()
CLI = typer.main.get_command(app).commands  # type: ignore[attr-defined]
NAMED = sorted({"animated_thumbs": "thumbs"}.get(command, command) for command in COMMANDS)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    settings.load_raw_config.cache_clear()
    yield
    settings.load_raw_config.cache_clear()


def touch(tmp_path, *names):
    for name in names:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x")


def results(result):
    return [(r["file"], r["status"], r["output"], r["message"]) for r in json.loads(result.stdout)["results"]]


def options(command):
    return {name for param in CLI[command].params for name in param.opts + param.secondary_opts}


def fake_transform(src, dst, config, **kwargs):
    open(dst, "wb").close()


@pytest.mark.parametrize("command", NAMED)
def test_every_named_command_takes_a_pattern_and_answers_questions(command):
    flags = options(command)
    assert {"--output-name", "-y", "--yes"} <= flags
    assert not {"--keep-extension", "--no-keep-extension", "--format", "--include-outputs"} & flags


def test_sheets_in_a_folder_count_as_existing(tmp_path):
    touch(tmp_path, "video.mp4", "video.mp4.jpeg", "new.mkv")
    (tmp_path / "sashimi.toml").write_text('[thumbs]\noutput_name = "{input_name}.jpeg"\n')
    planned = runner.invoke(app, ["thumbs", ".", "-n", "--json"])
    assert planned.exit_code == 0, planned.output
    assert results(planned) == [
        ("new.mkv", "success", "new.mkv.jpeg", "Would write new.mkv.jpeg"),
        ("video.mp4", "exists", "video.mp4.jpeg", "Output already exists: video.mp4.jpeg"),
    ]
    animated = results(runner.invoke(app, ["thumbs", ".", "-a", "-n", "--json"]))
    assert [output for _, _, output, _ in animated] == ["new.mkv.animated-sheet.webp", "video.mp4.animated-sheet.webp"]


def test_sheets_in_a_folder_are_converted_like_any_image(tmp_path):
    touch(tmp_path, "video.mp4", "video.mp4.jpeg")
    (tmp_path / "sashimi.toml").write_text('[thumbs]\noutput_name = "{input_name}.jpeg"\n')
    report = json.loads(runner.invoke(app, ["convert", ".", "webp", "-n", "-y", "--json"]).stdout)
    assert [r["file"] for r in report["results"]] == ["video.mp4", "video.mp4.jpeg"]
    assert report["outputs_left_out"] == []
    sheets = json.loads(runner.invoke(app, ["thumbs", ".", "-n", "--json"]).stdout)
    assert [(r["file"], r["status"]) for r in sheets["results"]] == [("video.mp4", "exists")]


def test_flags_beat_sashimi_toml(tmp_path):
    touch(tmp_path, "a.png")
    (tmp_path / "sashimi.toml").write_text('[convert]\noutput_name = "{input_stem}.from-toml.{output_ext}"\n')
    assert results(runner.invoke(app, ["convert", "a.png", "webp", "-n", "--json"]))[0][2] == "a.from-toml.webp"
    pattern = ["--output-name", "{input_stem}-{input_ext}.{output_ext}"]
    assert results(runner.invoke(app, ["convert", "a.png", "webp", "-n", "--json", *pattern]))[0][2] == "a-png.webp"


def test_clashes_are_skipped_with_the_pattern_named(tmp_path):
    touch(tmp_path, "x.mp4", "x.mkv")
    for extra in ([], ["-f"]):
        clash = runner.invoke(app, ["thumbs", ".", "-n", "--json", "--output-name", "{input_stem}.jpg", *extra])
        assert clash.exit_code == 0
        rows = results(clash)
        assert [(status, output, text) for _, status, output, text in rows] == [
            ("skipped", None, "Clashes with x.mp4: both would be named x.jpg."),
            ("skipped", None, "Clashes with x.mkv: both would be named x.jpg."),
        ]
        note = json.loads(clash.stdout)["clash_note"]
        assert note.startswith("2 files were skipped because the pattern {input_stem}.jpg (--output-name)")
        assert "-f" not in note
    named = runner.invoke(app, ["thumbs", "x.mp4", "x.mkv", "-n", "--output-name", "{input_stem}.jpg"])
    assert named.exit_code == 1
    text = runner.invoke(app, ["thumbs", ".", "-n", "--output-name", "{input_stem}.jpg"], env={"COLUMNS": "300"})
    flat = " ".join(text.stdout.split())
    assert "Skipped (name clash): x.mkv: Clashes with x.mp4: both would be named x.jpg." in flat
    assert "2 files were skipped because the pattern {input_stem}.jpg (--output-name)" in flat
    wrong = runner.invoke(app, ["thumbs", "x.mp4", "-n", "--json", "--output-name", "{input_name}.png"])
    assert wrong.exit_code == 2 and "ends in .png, but the output is JPEG" in " ".join(wrong.stderr.split())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.mkv", "x.mp4"]


def test_a_template_clash_in_sashimi_toml_names_the_file(tmp_path):
    touch(tmp_path, "x.mp4", "x.mkv")
    (tmp_path / "sashimi.toml").write_text('[thumbs]\noutput_name = "{input_stem}.jpg"\n')
    report = json.loads(runner.invoke(app, ["thumbs", ".", "-n", "--json"]).stdout)
    assert f"(output_name in [thumbs] of {tmp_path / 'sashimi.toml'})" in report["clash_note"]
    assert all(str(tmp_path) not in r["message"] for r in report["results"])


@pytest.mark.parametrize(
    "args, message",
    [
        (["convert", "a.png", "webp", "-o", "b.webp", "--output-name", "{input_stem}.{output_ext}"], "-o/--output"),
        (["convert", "a.png", "webp", "--output-name", "{input_stem}.{role}.{output_ext}"], "can't use {role}"),
        (["convert", "a.png", "webp", "--keep-extension"], "No such option"),
        (["thumbs", "a.mp4", "--output-name", "[x]{nam}"], "'[x]{nam}' has an unknown field {nam}"),
        (["thumbs", "a.mp4", "--output-name", "{name}.jpg"], "unknown field {name} (did you mean {input_name}?)"),
        (["thumbs", "a.mp4", "--output-name", "/sheets/{input_name}.jpg"], "is a full path"),
        (["thumbs", "a.mp4", "--output-name", "../{input_name}.jpg"], "has a . or .. folder"),
        (["thumbs", "a.mp4", "--format", "png"], "No such option"),
    ],
)
def test_refused_patterns(tmp_path, args, message):
    touch(tmp_path, "a.png", "a.mp4")
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert message in " ".join(result.stderr.split())


def test_a_broken_pattern_in_sashimi_toml_stops_only_its_own_command(tmp_path):
    touch(tmp_path, "a.mp4", "a.png")
    (tmp_path / "sashimi.toml").write_text('[thumbs]\noutput_name = "{nme}.jpg"\n')
    for args in (["thumbs", "a.mp4", "-n"], ["thumbs", ".", "-n"]):
        result = runner.invoke(app, args, env={"COLUMNS": "1000"})
        assert result.exit_code == 2
        assert f"output_name in [thumbs] of {tmp_path / 'sashimi.toml'}" in " ".join(result.stderr.split())
        assert "{nme}" in result.stderr
    for args in (["convert", "a.png", "webp", "-n"], ["thumbs", "a.mp4", "-n", "--animated"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        assert "{nme}" not in result.output


def test_config_set_writes_patterns(tmp_path):
    assert runner.invoke(app, ["config", "set", "thumbs.output_name", "{input_name}.jpeg"]).exit_code == 0
    assert runner.invoke(app, ["config", "set", "convert.output_name", "{input_stem}.{output_ext}"]).exit_code == 0
    text = (tmp_path / "home" / "sashimi.toml").read_text()
    assert 'output_name = "{input_name}.jpeg"' in text and "[convert]" in text
    refused = runner.invoke(app, ["config", "set", "convert.output_name", "{input_stem}.{role}.{output_ext}"])
    assert refused.exit_code == 2 and "{role}" in refused.stderr
    old = runner.invoke(app, ["config", "set", "remux.output_name", "{name}.mkv"])
    assert old.exit_code == 2 and "did you mean {input_name}" in old.stderr
    assert runner.invoke(app, ["config", "set", "convert.quality", "80"]).exit_code == 2
    assert runner.invoke(app, ["config", "set", "flatten.output_name", "{input_name}"]).exit_code == 2
    touch(tmp_path, "video.mp4")
    planned = results(runner.invoke(app, ["thumbs", "video.mp4", "-n", "--json"]))
    assert planned[0][2] == "video.mp4.jpeg"


def test_rerunning_a_command_on_a_folder_makes_nothing_new(tmp_path):
    Image.new("RGB", (40, 20), "red").save(tmp_path / "photo.jpg")
    first = runner.invoke(app, ["resize", ".", "-w", "10", "--json"])
    assert results(first)[0][:3] == ("photo.jpg", "success", "photo.jpg.resized.jpg")
    for _ in range(2):
        again = runner.invoke(app, ["resize", ".", "-w", "10", "--json"])
        assert again.exit_code == 0
        assert results(again) == [
            ("photo.jpg", "exists", "photo.jpg.resized.jpg", "Output already exists: photo.jpg.resized.jpg")
        ]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["photo.jpg", "photo.jpg.resized.jpg"]


def test_short_names_from_a_pattern(tmp_path):
    Image.new("RGB", (40, 20), "red").save(tmp_path / "photo.jpg")
    short = ["--output-name", "{input_stem}.{role}.{output_ext}", "--json"]
    result = runner.invoke(app, ["rotate", "photo.jpg", "90", "--output-format", "png", *short])
    assert results(result)[0][2] == "photo.rotated.png"
    with Image.open(tmp_path / "photo.rotated.png") as rotated:
        assert (rotated.format, rotated.size) == ("PNG", (20, 40))


@pytest.mark.parametrize("typed", ["jpeg", "jpg"])
def test_the_format_is_written_as_typed(tmp_path, typed):
    Image.new("RGB", (8, 8)).save(tmp_path / "a.png")
    assert results(runner.invoke(app, ["convert", "a.png", typed, "-n", "--json"]))[0][2] == f"a.png.{typed}"
    resized = runner.invoke(app, ["resize", "a.png", "-w", "4", "--output-format", typed, "-n", "--json"])
    assert results(resized)[0][2] == f"a.png.resized.{typed}"
    edited = runner.invoke(app, ["edit", "a.png", "--rotate", "90", "--output-format", typed, "-n", "--json"])
    assert results(edited)[0][2] == f"a.png.edited.{typed}"


def mixed(tmp_path):
    Image.new("RGB", (40, 20), "red").save(tmp_path / "photo.jpg")
    frames = [Image.new("RGB", (8, 8), (i * 60, 0, 0)) for i in range(3)]
    frames[0].save(tmp_path / "anim.gif", save_all=True, append_images=frames[1:], duration=50, loop=0)
    touch(tmp_path, "clip.mp4")


def test_a_mixed_folder_stops_without_a_terminal(tmp_path):
    mixed(tmp_path)
    result = runner.invoke(app, ["rotate", ".", "90"])
    assert result.exit_code == 2
    flat = " ".join(result.stderr.split())
    assert "This folder contains 2 images and 1 video, which rotate handles differently." in flat
    assert "Pass -y to rotate all 3" in flat
    assert sorted(p.name for p in tmp_path.iterdir()) == ["anim.gif", "clip.mp4", "photo.jpg"]


def test_a_mixed_folder_dry_run_shows_the_counts(tmp_path):
    mixed(tmp_path)
    result = runner.invoke(app, ["rotate", ".", "90", "-n"])
    assert result.exit_code == 0
    assert "This folder contains 2 images and 1 video; rotate all 3" in " ".join(result.stderr.split())
    report = json.loads(runner.invoke(app, ["rotate", ".", "90", "-n", "--json"]).stdout)
    assert (report["kinds"], report["routes"]) == ({"image": 2, "video": 1}, {"edit": 2, "transform": 1})


@pytest.mark.parametrize("answer", ["-y", "--yes", "-f"])
def test_yes_and_force_answer_the_question(tmp_path, answer, monkeypatch):
    mixed(tmp_path)
    monkeypatch.setattr("sashimi.api.transform.transform", fake_transform)
    result = runner.invoke(app, ["rotate", ".", "90", answer, "--json"])
    assert result.exit_code == 0, result.output
    assert sorted(r["output"] for r in json.loads(result.stdout)["results"]) == [
        "anim.gif.rotated.gif", "clip.mp4.rotated.mp4", "photo.jpg.rotated.jpg",
    ]  # fmt: skip


@pytest.mark.parametrize("reply, code", [("y\n", 0), ("n\n", 1), ("\n", 1)])
def test_in_a_terminal_the_question_is_asked(tmp_path, monkeypatch, reply, code):
    mixed(tmp_path)
    monkeypatch.setattr(_output, "_stdin_is_terminal", lambda: True)
    monkeypatch.setattr("sashimi.api.transform.transform", fake_transform)
    result = runner.invoke(app, ["rotate", ".", "90"], input=reply)
    assert result.exit_code == code
    assert "This folder contains 2 images and 1 video; rotate all 3? [y/N]" in " ".join(result.stderr.split())
    assert (tmp_path / "photo.jpg.rotated.jpg").exists() == (code == 0)


def test_one_route_never_asks(tmp_path):
    mixed(tmp_path)
    images = runner.invoke(app, ["rotate", ".", "90", "--output-format", "png", "--json"])
    assert images.exit_code == 0, images.output
    assert [r["output"] for r in json.loads(images.stdout)["results"]] == [
        "anim.gif.rotated.png",
        "photo.jpg.rotated.png",
    ]
    assert runner.invoke(app, ["edit", ".", "--rotate", "90", "-n"]).exit_code == 0
    assert runner.invoke(app, ["convert", ".", "mp4", "-n"]).exit_code == 0


def test_unsupported_files_say_what_the_command_takes(tmp_path):
    mixed(tmp_path)
    resize = runner.invoke(app, ["resize", "clip.mp4", "-w", "5"], env={"COLUMNS": "300"})
    assert "resize works on images and animations; videos aren't supported yet." in resize.stdout
    thumbs = runner.invoke(app, ["thumbs", "photo.jpg", "--json"])
    assert results(thumbs)[0][3] == "thumbs works on videos, not .jpg files."
    rotate = runner.invoke(app, ["rotate", "clip.mp4", "--output-format", "png", "--json"])
    assert results(rotate)[0][3] == "PNG is an image format, so only images are rotated; rotate videos in another run."


def test_problems_come_first_and_are_never_hidden(tmp_path):
    for name in ("a", "b", "c"):
        Image.new("RGB", (8, 8)).save(tmp_path / f"{name}.png")
    (tmp_path / "z.png").write_bytes(b"not an image")
    (tmp_path / "b.png.webp").write_bytes(b"old")
    listing = runner.invoke(app, ["convert", ".", "webp", "-n", "-l", "2"], env={"COLUMNS": "200"})
    lines = listing.stdout.splitlines()
    assert lines[0].startswith("Exists (Skipped): b.png") and lines[1].startswith("Plan:")
    assert "(2 success not shown)" in listing.stdout
    table = runner.invoke(app, ["convert", ".", "webp", "-l", "1"], env={"COLUMNS": "200"})
    rows = [line for line in table.stdout.splitlines() if ".png" in line]
    assert len(rows) == 1 and "z.png" in rows[0] and "Skipped" in rows[0]
    assert "(1 exists, 2 success not shown)" in " ".join(table.stdout.split())


def test_flatten_takes_yes(tmp_path):
    touch(tmp_path, "sub/a.txt")
    assert runner.invoke(app, ["flatten", ".", "-y"]).exit_code == 0
    assert (tmp_path / "a.txt").exists()


def two_kinds(tmp_path):
    Image.new("RGB", (40, 20), "red").save(tmp_path / "photo.jpg")
    touch(tmp_path, "clip.mp4")


@pytest.mark.parametrize(
    "args, verb",
    [
        (["rotate", ".", "90"], "rotate both"),
        (["convert", ".", "webp"], "convert both to WEBP"),
        (["strip", "."], "remove metadata from both"),
    ],
)
def test_two_files_are_both_in_the_question_the_error_and_the_dry_run(tmp_path, monkeypatch, args, verb):
    two_kinds(tmp_path)
    stopped = " ".join(runner.invoke(app, args).stderr.split())
    assert f"Pass -y to {verb}, or run in a terminal to be asked." in stopped
    planned = " ".join(runner.invoke(app, [*args, "-n"]).stderr.split())
    assert f"This folder contains 1 image and 1 video; {verb} (without -y, sashimi asks first)." in planned
    monkeypatch.setattr(_output, "_stdin_is_terminal", lambda: True)
    asked = runner.invoke(app, args, input="n\n")
    assert asked.exit_code == 1
    assert f"This folder contains 1 image and 1 video; {verb}? [y/N]" in " ".join(asked.stderr.split())
    assert "all 2" not in stopped + planned + asked.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["clip.mp4", "photo.jpg"]


@pytest.mark.parametrize("count, every", [(1, "it"), (2, "both"), (3, "all 3"), (67, "all 67")])
def test_every_count_has_its_words(count, every):
    assert _output.every(count) == every


def rotate_clash(tmp_path, *names):
    for name in names:
        touch(tmp_path, name)
    runner.invoke(app, ["config", "set", "rotate.output_name", "{input_stem}.{role}.{output_ext}"])
    return str(tmp_path / "home" / "sashimi.toml")


@pytest.mark.parametrize("extra", [[], ["-n"], ["-f"]])
def test_a_clash_names_the_pattern_and_its_file_once_at_the_end(tmp_path, monkeypatch, extra):
    toml = rotate_clash(tmp_path, "x.mkv", "x.mp4")
    monkeypatch.setattr("sashimi.api.transform.transform", fake_transform)
    result = runner.invoke(app, ["rotate", ".", "90", *extra], env={"COLUMNS": "400"})
    assert result.exit_code == 0, result.output
    out = " ".join(result.stdout.split())
    assert out.count(toml) == 1
    assert out.count("{input_stem}.{role}.{output_ext}") == 1
    assert "Clashes with x.mp4: both would be named x.rotated.mp4." in out
    assert "Clashes with x.mkv: both would be named x.rotated.mp4." in out
    note = (
        f"2 files were skipped because the pattern {{input_stem}}.{{role}}.{{output_ext}} "
        f"(output_name in [rotate] of {toml}) gives them the same name as another input's output; "
        "keep {input_name} or {input_ext} in it to tell them apart."
    )
    assert out.rstrip().endswith(note)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["home", "x.mkv", "x.mp4"]


def test_a_clash_from_the_flag_says_so_once(tmp_path):
    touch(tmp_path, "x.mp4", "x.mkv", "y.mp4")
    result = runner.invoke(app, ["thumbs", ".", "-n", "--output-name", "{input_stem}.jpg"], env={"COLUMNS": "400"})
    out = " ".join(result.stdout.split())
    assert out.count("(--output-name)") == 1
    assert out.count("{input_stem}.jpg") == 1
    assert out.endswith(
        "2 files were skipped because the pattern {input_stem}.jpg (--output-name) gives them the same name as "
        "another input's output; keep {input_name} or {input_ext} in it to tell them apart."
    )


def test_the_json_report_carries_the_clash_note(tmp_path):
    toml = rotate_clash(tmp_path, "x.mkv", "x.mp4")
    report = json.loads(runner.invoke(app, ["rotate", ".", "90", "-n", "--json"]).stdout)
    assert [r["message"] for r in report["results"]] == [
        "Clashes with x.mp4: both would be named x.rotated.mp4.",
        "Clashes with x.mkv: both would be named x.rotated.mp4.",
    ]
    assert report["clash_note"] == (
        f"2 files were skipped because the pattern {{input_stem}}.{{role}}.{{output_ext}} "
        f"(output_name in [rotate] of {toml}) gives them the same name as another input's output; "
        "keep {input_name} or {input_ext} in it to tell them apart."
    )
    touch(tmp_path, "y.mp4")
    (tmp_path / "x.mkv").unlink()
    assert json.loads(runner.invoke(app, ["rotate", ".", "90", "-n", "--json"]).stdout)["clash_note"] is None


def test_a_case_only_clash_under_a_pattern_isnt_blamed_on_it(tmp_path):
    touch(tmp_path, "Clip.mp4", "clip.mp4")
    if len(list(tmp_path.iterdir())) < 2:
        pytest.skip("case-insensitive file system")
    result = runner.invoke(app, ["thumbs", ".", "-n", "--output-name", "{input_name}.jpeg"], env={"COLUMNS": "400"})
    out = " ".join(result.stdout.split())
    assert "Clashes with clip.mp4: both would be written as Clip.mp4.jpeg, one file on Windows and macOS." in out
    assert "(--output-name)" not in out
    report = json.loads(
        runner.invoke(app, ["thumbs", ".", "-n", "--json", "--output-name", "{input_name}.jpeg"]).stdout
    )
    assert report["clash_note"] is None


@pytest.mark.parametrize("command", NAMED)
def test_output_name_help_says_how_to_keep_a_pattern(command):
    text = " ".join(runner.invoke(app, [command, "-h"], env={"COLUMNS": "400"}).stdout.replace("│", " ").split())
    assert f"For this run only; keep one with sashimi config set {command}.output_name '" in text
