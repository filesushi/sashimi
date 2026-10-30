# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Previews and whole-file remuxes, with real ffmpeg."""

import hashlib
import subprocess

import pytest
from PIL import Image

from sashimi import probe, video
from sashimi.api.remux import remux_files_generator
from sashimi.config import VIDEO_PREVIEW, CopyTrimConfig, GifPreviewConfig, WebpPreviewConfig
from sashimi.errors import InvalidOptionsError, MediaTooShortError, UnsupportedMediaError

from tests.conftest import leftovers, needs_ffmpeg

pytestmark = needs_ffmpeg


def frames(path):
    with Image.open(path) as img:
        durations = []
        for index in range(img.n_frames):
            img.seek(index)
            durations.append(img.info.get("duration", 0))
        return img.size, img.n_frames, sum(durations)


def test_gif_preview_samples_across_the_video(video_blob, tmp_path):
    progress = []
    config = GifPreviewConfig(samples=3, sample_duration=1, fps=10, start_offset_percent=0, end_offset_percent=0)
    video.preview(video_blob, tmp_path / "p", config, max_workers=2, on_progress=lambda f, s: progress.append(f))
    assert frames(tmp_path / "p") == ((320, 180), 30, 3000)
    assert progress[-1] == 1.0 and leftovers(tmp_path) == []


def test_webp_preview_scales_and_crops(video_blob, tmp_path):
    video.preview(video_blob, tmp_path / "p", WebpPreviewConfig(samples=2, fps=5, max_height=90, crop="9:16"))
    size, count, _ = frames(tmp_path / "p")
    assert size == (50, 90) and count == 20


def test_rotated_video_preview_is_upright(rotated_video_blob, tmp_path):
    video.preview(rotated_video_blob, tmp_path / "p", WebpPreviewConfig(samples=1, fps=5))
    assert frames(tmp_path / "p")[0] == (180, 320)


def test_short_video_gets_one_clip_of_its_length(silent_video_blob, tmp_path):
    config = GifPreviewConfig(sample_duration=5, fps=10)
    video.preview(silent_video_blob, tmp_path / "p", config)
    assert frames(tmp_path / "p")[1] == 30


def test_preview_is_deterministic(video_blob, tmp_path):
    config = VIDEO_PREVIEW.from_params({"samples": 2, "fps": 5}, target="webp")
    for name in ("a", "b"):
        video.preview(video_blob, tmp_path / name, config)
    assert (
        hashlib.sha256((tmp_path / "a").read_bytes()).digest() == hashlib.sha256((tmp_path / "b").read_bytes()).digest()
    )


def test_preview_user_errors(junk_blob, tmp_path):
    with pytest.raises(UnsupportedMediaError):
        video.preview(junk_blob, tmp_path / "p", GifPreviewConfig())
    tiny = tmp_path / "tiny.blob"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=64x64:r=10:d=0.3",
            "-f",
            "matroska",
            str(tiny),
        ],
        check=True,
    )
    video.preview(tiny, tmp_path / "p", GifPreviewConfig())
    assert Image.open(tmp_path / "p").n_frames >= 3
    assert leftovers(tmp_path) == []


@pytest.fixture(scope="module")
def mp4_with_subtitles(tmp_path_factory):
    """4 s H.264 MP4 with two AAC tracks and a mov_text subtitle, like phone or iTunes files."""
    folder = tmp_path_factory.mktemp("subs")
    srt = folder / "subs.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:02,000\nHello\n")
    path = folder / "original.blob"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=4",
         "-f", "lavfi", "-i", "sine=f=440:d=4", "-f", "lavfi", "-i", "sine=f=660:d=4", "-i", str(srt),
         "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-c:s", "mov_text", "-f", "mp4", str(path)],
        check=True,
    )  # fmt: skip
    return path


def test_remux_to_mkv_keeps_every_stream_and_converts_subtitles(mp4_with_subtitles, tmp_path):
    video.trim(mp4_with_subtitles, tmp_path / "r", CopyTrimConfig(container="mkv"))
    info = probe(tmp_path / "r")
    assert [f"{s.type}:{s.codec}" for s in info.streams] == [
        "video:h264", "audio:aac", "audio:aac", "subtitle:subrip",
    ]  # fmt: skip
    assert info.duration == pytest.approx(4.0, abs=0.2)
    text = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(tmp_path / "r"), "-map", "0:s:0", "-f", "srt", "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "Hello" in text


def test_text_subtitles_become_mov_text_for_mp4(mp4_with_subtitles, tmp_path):
    video.trim(mp4_with_subtitles, tmp_path / "a.mkv", CopyTrimConfig(container="mkv"))
    video.trim(tmp_path / "a.mkv", tmp_path / "b", CopyTrimConfig(container="mp4"))
    assert [s.codec for s in probe(tmp_path / "b").streams][-1] == "mov_text"


def test_remux_still_refuses_what_a_container_cannot_hold(video_blob, tmp_path):
    with pytest.raises(InvalidOptionsError):
        video.trim(video_blob, tmp_path / "r", CopyTrimConfig(container="webm"))
    assert leftovers(tmp_path) == [] and not (tmp_path / "r").exists()


def test_remux_batch_never_touches_sources(mp4_with_subtitles, video_blob, tmp_path):
    (tmp_path / "phone.mp4").write_bytes(mp4_with_subtitles.read_bytes())
    (tmp_path / "already.mkv").write_bytes(video_blob.read_bytes())
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    items = list(remux_files_generator([tmp_path / "phone.mp4", tmp_path / "already.mkv"], "mkv"))
    finals = [(p.name, status, message) for p, status, message, _ in items[1:] if status != "starting"]
    assert finals == [
        ("already.mkv", "skipped", "Already MKV"),
        (
            "phone.mp4",
            "success",
            "Remuxed to phone.mp4.mkv (picture and sound identical; the subtitles' MP4 text format (converted to SubRip) dropped)",
        ),
    ]
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.name in before} == before
    again = list(remux_files_generator(tmp_path / "phone.mp4", "mkv", force=True))
    assert again[-1][1:3] == (
        "success",
        "Remuxed to phone.mp4.mkv (replaced the existing file; picture and sound identical; "
        "the subtitles' MP4 text format (converted to SubRip) dropped)",
    )
    assert (tmp_path / "phone.mp4").read_bytes() == before["phone.mp4"]
