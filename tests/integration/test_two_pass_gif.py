# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
GIFs whose palette is built from every frame are made in two passes: video to GIF, GIF previews
and animated GIF sheets. The output is byte-identical to the one-pass graph, memory doesn't grow
with length, and no palette file is ever written.
"""

import json
import pathlib
import subprocess
import sys

import pytest

from sashimi import sheets, video
from sashimi.config import (
    VIDEO_CONVERT,
    AnimatedGifSheetConfig,
    GifPreviewConfig,
    Limits,
)
from sashimi.core import _ffmpeg, preview as preview_module
from sashimi.core import video as video_module
from sashimi.core.sheets import animated as animated_module
from sashimi.errors import InvalidOptionsError, ProcessingError, UnsupportedMediaError

from tests.conftest import _ffmpeg as make_media, needs_ffmpeg

pytestmark = needs_ffmpeg

MODES = ["diff", "full", "single"]
DITHERS = ["bayer", "sierra2_4a", "floyd_steinberg", "none"]


def gif(**params):
    return VIDEO_CONVERT.from_params(params, target="gif")


def one_pass(monkeypatch, module, name):
    """Makes `module.name`, a command builder, ignore the palette: the one-pass graph."""
    original = getattr(module, name)

    def build(*args, **kwargs):
        if "palette" in kwargs:
            kwargs["palette"] = None
        elif len(args) == 7:
            args = (*args[:6], None)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, build)


def commands(monkeypatch):
    """Records every ffmpeg command run."""
    seen = []
    original = _ffmpeg.run_ffmpeg

    def run(cmd, **kwargs):
        seen.append(cmd)
        return original(cmd, **kwargs)

    for module in (video_module, preview_module, animated_module):
        monkeypatch.setattr(module, "run_ffmpeg", run)
    return seen


def files(*folders):
    return sorted(str(p) for folder in folders for p in pathlib.Path(folder).rglob("*"))


@pytest.mark.parametrize("dither", DITHERS)
@pytest.mark.parametrize("mode", MODES)
def test_video_gifs_are_byte_identical_to_one_pass(video_blob, tmp_path, monkeypatch, mode, dither):
    config = gif(palette_mode=mode, dither=dither, duration_seconds=2, start_seconds=1)
    video.convert(video_blob, tmp_path / "two.gif", config)
    one_pass(monkeypatch, video_module, "build_convert_command")
    video.convert(video_blob, tmp_path / "one.gif", config)
    assert (tmp_path / "two.gif").read_bytes() == (tmp_path / "one.gif").read_bytes()


@pytest.mark.parametrize("crop", ["none", "1:1"])
def test_gif_previews_are_byte_identical_to_one_pass(video_blob, tmp_path, monkeypatch, crop):
    config = GifPreviewConfig(samples=3, sample_duration=1, fps=10, crop=crop, loop=crop == "none")
    video.preview(video_blob, tmp_path / "two.gif", config)
    one_pass(monkeypatch, preview_module, "build_preview_commands")
    video.preview(video_blob, tmp_path / "one.gif", config)
    assert (tmp_path / "two.gif").read_bytes() == (tmp_path / "one.gif").read_bytes()


@pytest.mark.parametrize("header", [True, False])
def test_animated_gif_sheets_are_byte_identical_to_one_pass(video_blob, tmp_path, monkeypatch, header):
    config = AnimatedGifSheetConfig(cols=2, rows=2, sample_duration=1, width=320, show_header=header, loop=header)
    sheets.render(video_blob, tmp_path / "two.gif", config, display_name="clip.mkv")
    one_pass(monkeypatch, animated_module, "build_encode_command")
    sheets.render(video_blob, tmp_path / "one.gif", config, display_name="clip.mkv")
    assert (tmp_path / "two.gif").read_bytes() == (tmp_path / "one.gif").read_bytes()


@pytest.mark.parametrize("mode, passes", [("diff", 2), ("full", 2), ("single", 1)])
def test_the_palette_is_built_in_a_pass_of_its_own(video_blob, tmp_path, monkeypatch, mode, passes):
    seen = commands(monkeypatch)
    video.convert(video_blob, tmp_path / "out.gif", gif(palette_mode=mode, duration_seconds=1))
    assert len(seen) == passes
    graphs = [" ".join(cmd) for cmd in seen]
    if passes == 2:
        assert "palettegen" in graphs[0] and "paletteuse" not in graphs[0] and seen[0][-1] == "pipe:1"
        assert "palettegen" not in graphs[1] and "data:image/png;base64," in graphs[1]
    else:
        assert "palettegen" in graphs[0] and "paletteuse" in graphs[0]


def test_previews_and_sheets_build_the_palette_first(video_blob, tmp_path, monkeypatch):
    seen = commands(monkeypatch)
    video.preview(video_blob, tmp_path / "p.gif", GifPreviewConfig(samples=2, sample_duration=1, fps=5))
    sheets.render(video_blob, tmp_path / "s.gif", AnimatedGifSheetConfig(cols=2, rows=1, sample_duration=1))
    encoders = [" ".join(cmd) for cmd in seen if "pipe:0" in cmd]
    assert len(encoders) == 4
    for first, second in (encoders[0:2], encoders[2:4]):
        assert "palettegen" in first and "paletteuse" not in first
        assert "palettegen" not in second and "data:image/png;base64," in second


def test_progress_moves_across_both_passes(video_blob, tmp_path):
    progress = []
    video.convert(video_blob, tmp_path / "out.gif", gif(), on_progress=lambda f, s: progress.append((f, s)))
    fractions = [f for f, _ in progress]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    stages = [s for _, s in progress]
    assert stages[0] == "analysing" and "encoding" in stages
    assert stages.index("encoding") > len(stages) - 1 - stages[::-1].index("analysing")
    analysing = [f for f, s in progress if s == "analysing"]
    assert max(analysing) <= 1 / 3 + 1e-9 and len({f for f in analysing if 0 < f < 1 / 3}) > 10
    assert min(f for f, s in progress if s == "encoding") >= 1 / 3 - 1e-9


def progress_recorder(progress):
    return lambda fraction, stage: progress.append((fraction, stage))


def test_preview_and_sheet_progress_moves_across_both_passes(video_blob, tmp_path):
    for make in (
        lambda report: video.preview(
            video_blob, tmp_path / "p.gif", GifPreviewConfig(samples=2, sample_duration=1, fps=5), on_progress=report
        ),
        lambda report: sheets.render(
            video_blob,
            tmp_path / "s.gif",
            AnimatedGifSheetConfig(cols=2, rows=1, sample_duration=1),
            on_progress=report,
        ),
    ):
        progress: list = []
        make(progress_recorder(progress))
        fractions = [f for f, _ in progress]
        assert fractions == sorted(fractions) and fractions[-1] == 1.0
        assert "encoding" in [s for _, s in progress]
        assert len({f for f, s in progress if s == "analysing"}) > 3


def test_gifs_and_gif_previews_have_no_frame_budget(video_blob, tmp_path):
    tiny = Limits(max_frame_bytes=1)
    video.convert(video_blob, tmp_path / "v.gif", gif(duration_seconds=6, fps=25, max_width=320), limits=tiny)
    video.preview(video_blob, tmp_path / "p.gif", GifPreviewConfig(samples=3, fps=25), limits=tiny)
    with pytest.raises(InvalidOptionsError) as e:
        sheets.render(video_blob, tmp_path / "s.gif", AnimatedGifSheetConfig(), limits=tiny)
    assert e.value.limit == "max_frame_bytes"
    assert files(tmp_path) == [str(tmp_path / "p.gif"), str(tmp_path / "v.gif")]


@pytest.mark.parametrize("target", ["gif", "webp"])
def test_a_start_after_the_last_frame_is_a_user_error(video_blob, tmp_path, target):
    for mode in ["diff", "single"] if target == "gif" else [None]:
        params = {"start_seconds": 5.99} | ({"palette_mode": mode} if mode else {})
        with pytest.raises(InvalidOptionsError) as e:
            video.convert(video_blob, tmp_path / "out", VIDEO_CONVERT.from_params(params, target=target))
        assert e.value.user_message == (
            "The start time (5.99s) is after the video's last frame, so there's nothing to convert."
        )
    assert files(tmp_path) == []
    video.convert(video_blob, tmp_path / "out", VIDEO_CONVERT.from_params({"start_seconds": 5.9}, target=target))
    assert files(tmp_path) == [str(tmp_path / "out")]


def _broken(monkeypatch, module, name):
    original = getattr(module, name)

    def broken(cmd):
        return [*cmd[:-1], "-no_such_option", "1", cmd[-1]]

    def build(*args, **kwargs):
        built = original(*args, **kwargs)
        return (built[0], broken(built[1])) if isinstance(built, tuple) else broken(built)

    monkeypatch.setattr(module, name, build)


ROUTES = {
    "video": (
        video_module,
        lambda blob, out, scratch, report: video.convert(
            blob, out, gif(duration_seconds=2), scratch_dir=scratch, on_progress=report
        ),
        ("build_palette_command", "build_convert_command"),
    ),
    "preview": (
        preview_module,
        lambda blob, out, scratch, report: video.preview(
            blob, out, GifPreviewConfig(samples=2, sample_duration=1, fps=5), scratch_dir=scratch, on_progress=report
        ),
        ("build_preview_palette_command", "build_preview_commands"),
    ),
    "sheet": (
        animated_module,
        lambda blob, out, scratch, report: sheets.render(
            blob,
            out,
            AnimatedGifSheetConfig(cols=2, rows=1, sample_duration=1),
            scratch_dir=scratch,
            on_progress=report,
        ),
        ("build_palette_command", "build_encode_command"),
    ),
}


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("scratch", [False, True])
def test_success_leaves_only_the_output(video_blob, tmp_path, monkeypatch, route, scratch):
    _, make, _ = ROUTES[route]
    out_dir, scratch_dir, work = tmp_path / "out", tmp_path / "scratch", tmp_path / "cwd"
    for folder in (out_dir, scratch_dir, work):
        folder.mkdir()
    monkeypatch.chdir(work)
    make(video_blob, out_dir / "x.gif", scratch_dir if scratch else None, None)
    assert files(out_dir, scratch_dir, work) == [str(out_dir / "x.gif")]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("which", [0, 1])
def test_an_ffmpeg_failure_in_either_pass_leaves_nothing(video_blob, tmp_path, monkeypatch, route, which):
    module, make, builders = ROUTES[route]
    _broken(monkeypatch, module, builders[which])
    out_dir, scratch_dir = tmp_path / "out", tmp_path / "scratch"
    out_dir.mkdir()
    scratch_dir.mkdir()
    with pytest.raises((ProcessingError, UnsupportedMediaError)):
        make(video_blob, out_dir / "x.gif", scratch_dir, None)
    assert files(out_dir, scratch_dir) == []


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("stage", ["analysing", "encoding"])
def test_cancelling_in_either_pass_leaves_nothing(video_blob, tmp_path, route, stage):
    _, make, _ = ROUTES[route]

    def report(fraction, current):
        if current == stage and fraction > 0:
            raise KeyboardInterrupt

    out_dir, scratch_dir = tmp_path / "out", tmp_path / "scratch"
    out_dir.mkdir()
    scratch_dir.mkdir()
    with pytest.raises(KeyboardInterrupt):
        make(video_blob, out_dir / "x.gif", scratch_dir, report)
    assert files(out_dir, scratch_dir) == []


def test_the_palette_pass_writes_no_file(video_blob, tmp_path, monkeypatch):
    seen = commands(monkeypatch)
    video.convert(video_blob, tmp_path / "x.gif", gif(duration_seconds=1), scratch_dir=tmp_path)
    file_args = [arg for cmd in seen for arg in cmd if arg.startswith("file:")]
    assert file_args == [f"file:{video_blob}", f"file:{video_blob}", file_args[-1]]
    assert pathlib.Path(file_args[-1][5:]).parent == tmp_path


MEMORY_SCRIPT = """
import json, resource, sys
from sashimi import video
from sashimi.config import VIDEO_CONVERT, Limits
config = VIDEO_CONVERT.from_params({"duration_seconds": float(sys.argv[2]), "fps": 30, "max_width": 640}, target="gif")
video.convert(sys.argv[1], sys.argv[3], config, limits=Limits.unlimited(), max_workers=2)
print(json.dumps(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss))
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ru_maxrss is in kilobytes on Linux only")
def test_memory_does_not_grow_with_length(tmp_path_factory):
    """
    A 640x360 GIF at 30 fps holds 0.7 MB a frame: 12 s more of it is 250 MB more in one pass. Two
    passes keep ffmpeg's peak (the largest child's resident memory, measured in a fresh process)
    within a few tens of megabytes, whatever the length.
    """
    folder = tmp_path_factory.mktemp("long")
    source = folder / "original.blob"
    make_media(
        *("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=14", "-c:v", "libx264", "-preset", "ultrafast"),
        *("-pix_fmt", "yuv420p", "-f", "matroska", str(source)),
    )

    def peak(seconds):
        out = subprocess.run(
            [sys.executable, "-c", MEMORY_SCRIPT, str(source), str(seconds), str(folder / f"{seconds}.gif")],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(out.stdout) / 1024

    short, long = peak(1), peak(13)
    assert long < short + 60, f"peak {short:.0f} MB for 1 s, {long:.0f} MB for 13 s"
