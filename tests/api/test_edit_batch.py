# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Batch editing and frame selection, and dry runs in the batch machinery."""

import pytest
from PIL import Image

from sashimi.api import _batch, edit_frames, edit_images
from sashimi.config import IMAGE_EDIT, IMAGE_FRAMES, GifFramesConfig, PngEditConfig
from sashimi.errors import InvalidOptionsError


def test_edit_images_keeps_formats_and_crops_each_to_a_shape(tmp_path):
    Image.new("RGB", (400, 300), "red").save(tmp_path / "wide.jpg")
    Image.new("RGB", (100, 300), "blue").save(tmp_path / "tall.PNG")
    report = edit_images(
        tmp_path, lambda fmt: IMAGE_EDIT.from_params({"rotate": 90}, target=fmt), crop_aspect="1:1", show_progress=False
    )
    assert [row["output"] for row in report["results"]] == [
        str(tmp_path / "tall.edited.PNG"),
        str(tmp_path / "wide.edited.jpg"),
    ]
    with Image.open(tmp_path / "tall.edited.PNG") as tall, Image.open(tmp_path / "wide.edited.jpg") as wide:
        assert (tall.format, tall.size, wide.format, wide.size) == ("PNG", (100, 100), "JPEG", (300, 300))


def test_crop_shape_and_box_are_exclusive(tmp_path):
    with pytest.raises(InvalidOptionsError):
        edit_images(tmp_path, PngEditConfig(crop_width=5), crop_aspect="1:1", show_progress=False)
    with pytest.raises(InvalidOptionsError):
        edit_images(tmp_path, PngEditConfig(), crop_aspect="square", show_progress=False)


def test_edit_frames_drops_frames(tmp_path):
    frames = [Image.new("RGB", (8, 8), (index * 60, 0, 0)) for index in range(4)]
    frames[0].save(tmp_path / "a.gif", save_all=True, append_images=frames[1:], duration=80, loop=0)
    report = edit_frames(tmp_path / "a.gif", GifFramesConfig(keep=(0, 1, 2)), drop=[1], show_progress=False)
    assert report["summary"]["success"] == 1
    with Image.open(tmp_path / "a.edited.gif") as out:
        assert out.n_frames == 2
    everything = edit_frames(tmp_path / "a.gif", GifFramesConfig(), drop=range(4), force=True, show_progress=False)
    assert everything["results"][0]["message"] == "That would drop every frame; keep at least one."
    with pytest.raises(InvalidOptionsError):
        edit_frames(tmp_path, IMAGE_FRAMES.from_params({}, target="gif"), drop=[-1], show_progress=False)


def test_run_batch_dry_run_never_processes(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "b.png").write_bytes(b"x")
    (tmp_path / "b.webp").write_bytes(b"old")

    def process(src, dst, progress):
        raise AssertionError("processed during a dry run")

    for force, expected in (
        (False, ("exists", "Output already exists: b.webp")),
        (True, ("success", "Would replace b.webp")),
    ):
        items = list(
            _batch.run_batch(
                tmp_path,
                extensions=[".png"],
                recursive=False,
                force=force,
                name_for=lambda path, stem: _batch.output_name(stem, "webp"),
                process=process,
                success_message="made {name}",
                output_dir=None,
                dry_run=True,
            )
        )
        finals = [(p.name, status, message) for p, status, message, _ in items[1:] if status != "starting"]
        assert finals == [("a.png", "success", "Would write a.webp"), ("b.png", *expected)]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.png", "b.png", "b.webp"]


def test_dry_run_creates_no_output_folder(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    list(
        _batch.run_batch(
            tmp_path / "a.png",
            extensions=[".png"],
            recursive=False,
            force=False,
            name_for=lambda path, stem: stem + ".out",
            process=lambda src, dst, progress: None,
            success_message="ok",
            output_dir=tmp_path / "out",
            dry_run=True,
        )
    )
    assert not (tmp_path / "out").exists()
