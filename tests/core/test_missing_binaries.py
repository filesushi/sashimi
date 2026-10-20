# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""A missing or unrunnable ffmpeg/ffprobe is an internal error, never a "doesn't look like media" user error."""

import os
import shutil

import pytest

import sashimi
from sashimi import audio, sheets, video
from sashimi.api import convert_files_generator
from sashimi.config import CONTACT_SHEET, AUDIO_CONVERT, VIDEO_CONVERT, VIDEO_PREVIEW, VIDEO_TRIM
from sashimi.errors import BinaryNotFoundError, UserInputError

from tests.conftest import needs_ffmpeg

MISSING = os.path.join(os.sep, "definitely", "not", "installed")


@pytest.fixture(autouse=True)
def reset_paths():
    yield
    sashimi.set_ffmpeg_paths()


@pytest.fixture
def media(tmp_path):
    path = tmp_path / "original.blob"
    path.write_bytes(b"\x00" * 1024)
    return path


def operations(media, tmp_path):
    out = tmp_path / "result"
    return {
        "probe": lambda: sashimi.probe(media),
        "video.convert": lambda: video.convert(media, out, VIDEO_CONVERT.from_params({}, target="gif")),
        "video.trim": lambda: video.trim(media, out, VIDEO_TRIM.from_params({"end_seconds": 1}, container="mkv")),
        "audio.convert": lambda: audio.convert(media, out, AUDIO_CONVERT.from_params({}, target="mp3")),
        "sheets.render": lambda: sheets.render(media, out, CONTACT_SHEET.from_params({})),
        "video.preview": lambda: video.preview(media, out, VIDEO_PREVIEW.from_params({}, target="gif")),
    }


@pytest.mark.parametrize(
    "name", ["probe", "video.convert", "video.trim", "audio.convert", "sheets.render", "video.preview"]
)
def test_configured_ffprobe_that_does_not_exist(media, tmp_path, name):
    sashimi.set_ffmpeg_paths(ffmpeg=MISSING, ffprobe=MISSING)
    with pytest.raises(BinaryNotFoundError) as e:
        operations(media, tmp_path)[name]()
    assert not isinstance(e.value, UserInputError)
    assert MISSING in str(e.value)


def test_configured_ffprobe_that_cannot_be_run(media, tmp_path):
    not_executable = tmp_path / "ffprobe"
    not_executable.write_text("not a program\n")
    not_executable.chmod(0o644)
    sashimi.set_ffmpeg_paths(ffprobe=str(not_executable))
    with pytest.raises(BinaryNotFoundError):
        sashimi.probe(media)


def test_ffprobe_missing_from_path(media, monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(BinaryNotFoundError) as e:
        sashimi.probe(media)
    assert "'ffprobe' was not found on PATH" in str(e.value)


def test_batch_reports_a_missing_ffprobe_as_a_failure(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 1024)
    sashimi.set_ffmpeg_paths(ffprobe=MISSING)
    results = [item for item in convert_files_generator(clip, "gif") if isinstance(item, tuple)]
    assert results[-1][1] == "error" and MISSING in results[-1][2]


@needs_ffmpeg
@pytest.mark.parametrize("animated", [False, True])
def test_sheet_with_missing_ffmpeg_is_not_unreadable_frames(video_blob, tmp_path, animated):
    sashimi.set_ffmpeg_paths(ffmpeg=MISSING, ffprobe=shutil.which("ffprobe"))
    config = CONTACT_SHEET.from_params({"animated": animated, "cols": 2, "rows": 1})
    with pytest.raises(BinaryNotFoundError):
        sheets.render(video_blob, tmp_path / "sheet", config, max_workers=1)


@needs_ffmpeg
@pytest.mark.parametrize("target", ["gif", "webp"])
def test_preview_with_missing_ffmpeg_is_not_unreadable_frames(video_blob, tmp_path, target):
    sashimi.set_ffmpeg_paths(ffmpeg=MISSING, ffprobe=shutil.which("ffprobe"))
    with pytest.raises(BinaryNotFoundError):
        video.preview(video_blob, tmp_path / "p", VIDEO_PREVIEW.from_params({"samples": 2}, target=target))
    assert list(tmp_path.iterdir()) == []
