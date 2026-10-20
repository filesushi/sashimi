# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Shared fixtures. Media is generated at test time with ffmpeg's lavfi sources (testsrc2, sine),
so no media files are committed. Inputs are named ``original.blob`` like FileSushi's, to prove
nothing depends on file names or extensions.
"""

import logging
import pathlib
import shutil
import subprocess

import pytest

logging.getLogger("PIL").setLevel(logging.INFO)

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], check=True)


def _blob(factory: pytest.TempPathFactory, name: str, *ffmpeg_args: str, fmt: str) -> pathlib.Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not installed")
    path = factory.mktemp(name) / "original.blob"
    _ffmpeg(*ffmpeg_args, "-f", fmt, str(path))
    return path


@pytest.fixture(scope="session")
def video_blob(tmp_path_factory) -> pathlib.Path:
    """6 s, 320x180, 25 fps H.264 + AAC in Matroska, keyframe every second."""
    return _blob(
        tmp_path_factory,
        "video",
        *("-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=6", "-f", "lavfi", "-i", "sine=f=440:d=6"),
        *("-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-pix_fmt", "yuv420p", "-c:a", "aac"),
        fmt="matroska",
    )


@pytest.fixture(scope="session")
def silent_video_blob(tmp_path_factory) -> pathlib.Path:
    """3 s, 320x180 H.264 MP4 without audio."""
    return _blob(
        tmp_path_factory,
        "silent",
        *("-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=3", "-c:v", "libx264", "-preset", "ultrafast"),
        *("-pix_fmt", "yuv420p"),
        fmt="mp4",
    )


@pytest.fixture(scope="session")
def rotated_video_blob(tmp_path_factory, silent_video_blob) -> pathlib.Path:
    """The silent video re-muxed with a 90 degree display rotation, like phone footage."""
    path = tmp_path_factory.mktemp("rotated") / "original.blob"
    _ffmpeg("-display_rotation", "90", "-i", str(silent_video_blob), "-c", "copy", "-f", "mp4", str(path))
    return path


@pytest.fixture(scope="session")
def audio_blob(tmp_path_factory) -> pathlib.Path:
    """4 s stereo MP3 with embedded cover art (a video stream that must be ignored)."""
    return _blob(
        tmp_path_factory,
        "audio",
        *("-f", "lavfi", "-i", "sine=f=440:d=4", "-f", "lavfi", "-i", "testsrc2=s=64x64:r=25:d=0.04"),
        *("-map", "0", "-map", "1", "-ac", "2", "-c:a", "libmp3lame", "-c:v", "mjpeg"),
        *("-disposition:v", "attached_pic"),
        fmt="mp3",
    )


@pytest.fixture(scope="session")
def gif_blob(tmp_path_factory) -> pathlib.Path:
    """A 2 s animated GIF."""
    return _blob(tmp_path_factory, "gif", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=2", fmt="gif")


@pytest.fixture
def junk_blob(tmp_path) -> pathlib.Path:
    """Bytes that aren't media."""
    path = tmp_path / "original.blob"
    path.write_bytes(b"this is not a media file\n" * 100)
    return path


def leftovers(directory: pathlib.Path) -> list[str]:
    """Temporary files an atomic write may have left behind."""
    return sorted(p.name for p in directory.iterdir() if p.name.endswith(".partial"))
