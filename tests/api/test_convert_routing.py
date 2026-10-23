# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pytest
from PIL import Image

from sashimi.api.convert import _source_extensions, convert_files_generator
from sashimi.config import JpegImageConfig
from sashimi.errors import InvalidOptionsError

from tests.conftest import needs_ffmpeg


def finals(gen):
    items = list(gen)
    return [(p.name, status, msg) for p, status, msg, _ in items[1:] if status != "starting"]


def test_source_extensions_per_target():
    assert ".mp4" not in _source_extensions("mp4") and ".gif" in _source_extensions("mp4")
    assert ".gif" not in _source_extensions("gif") and ".mkv" in _source_extensions("gif")
    assert ".png" in _source_extensions("gif")
    assert {".mp4", ".png", ".gif"} <= set(_source_extensions("webp")) and ".webp" not in _source_extensions("webp")
    assert {".mp3", ".mkv"} <= set(_source_extensions("flac")) and ".flac" not in _source_extensions("flac")
    assert not {".jpg", ".jpeg"} & set(_source_extensions("jpeg"))


def test_skip_reasons(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "b.txt").write_bytes(b"x")
    assert finals(convert_files_generator([tmp_path / "a.mp3", tmp_path / "b.txt"], "mp3")) == [
        ("a.mp3", "skipped", "Already MP3"),
        ("b.txt", "skipped", "Can't convert .txt to MP3; converting to MP3 works on videos and audio files."),
    ]


def test_unknown_target():
    with pytest.raises(InvalidOptionsError):
        convert_files_generator(".", "docx")  # type: ignore[arg-type]


def test_config_must_match_the_target():
    with pytest.raises(InvalidOptionsError):
        convert_files_generator(".", "png", image_config=JpegImageConfig())


@needs_ffmpeg
def test_routes_each_kind(video_blob, audio_blob, tmp_path):
    (tmp_path / "clip.mkv").write_bytes(video_blob.read_bytes())
    (tmp_path / "song.mp3").write_bytes(audio_blob.read_bytes())
    Image.new("RGB", (20, 20), "blue").save(tmp_path / "pic.png")

    assert finals(convert_files_generator(tmp_path, "ogg", output_name="{input_stem}.{output_ext}")) == [
        ("clip.mkv", "success", "Converted to clip.ogg"),
        ("song.mp3", "success", "Converted to song.ogg"),
    ]
    assert sorted(finals(convert_files_generator(tmp_path, "gif", output_name="{input_stem}.{output_ext}"))) == [
        ("clip.mkv", "success", "Converted to clip.gif"),
        ("pic.png", "success", "Converted to pic.gif"),
    ]
    assert finals(convert_files_generator(tmp_path / "clip.gif", "mp4", output_name="{input_stem}.{output_ext}")) == [
        ("clip.gif", "success", "Converted to clip.mp4")
    ]
    assert sorted(
        finals(
            convert_files_generator(
                [tmp_path / "clip.mkv", tmp_path / "pic.png"], "webp", output_name="{input_stem}.{output_ext}"
            )
        )
    ) == [
        ("clip.mkv", "success", "Converted to clip.webp"),
        ("pic.png", "success", "Converted to pic.webp"),
    ]
    with Image.open(tmp_path / "clip.webp") as clip, Image.open(tmp_path / "pic.webp") as pic:
        assert clip.is_animated and not getattr(pic, "is_animated", False)
