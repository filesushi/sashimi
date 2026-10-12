"""The core must not touch HOME, config files or the network (brief §3)."""

import sys

import pytest

from sashimi import audio, image, sheets, video
from sashimi.config import AnimatedWebpSheetConfig, GifVideoConfig, JpegSheetConfig, Mp3AudioConfig, PngImageConfig

from tests.conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


@pytest.fixture
def no_network(monkeypatch):
    attempts = []

    def hook(event, args):
        if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "urllib.Request"):
            attempts.append(event)

    sys.addaudithook(hook)
    yield attempts


def test_core_runs_offline_without_touching_home(video_blob, tmp_path, monkeypatch, no_network):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sashimi.toml").write_text("[thumbs]\nstatic_width = 9999\n")

    out = tmp_path / "out"
    out.mkdir()
    sheets.render(video_blob, out / "sheet", JpegSheetConfig(width=320, cols=2, rows=1))
    sheets.render(
        video_blob, out / "anim", AnimatedWebpSheetConfig(width=320, cols=2, rows=1, fps=4, sample_duration=1)
    )
    video.convert(video_blob, out / "gif", GifVideoConfig(duration_seconds=1, max_width=96))
    audio.convert(video_blob, out / "mp3", Mp3AudioConfig(duration_seconds=1))
    image.convert(out / "sheet", out / "png", PngImageConfig())

    from PIL import Image

    assert Image.open(out / "sheet").width == 320
    assert list(home.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["home", "out", "sashimi.toml"]
    assert no_network == []
