# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Every tile, preview clip and start time shows the frame at its stated time, on every container
sashimi reads, whatever the keyframe spacing. The videos draw their own frame number as ten
black-or-white columns, so each output frame can be read back and compared with the frame the
file itself shows at that time (its own timestamps, as a player sees them).
"""

import functools
import pathlib
import re
import subprocess

import pytest
from PIL import Image

from sashimi import probe, sheets, video
from sashimi.config import (
    VIDEO_CONVERT,
    VIDEO_PREVIEW,
    VIDEO_TRIM,
    AnimatedGifSheetConfig,
    PngSheetConfig,
)
from sashimi.core.preview import plan_preview, preview
from sashimi.core.sheets._layout import clip_starts, frame_times, plan_layout
from tests.conftest import HAS_FFMPEG, needs_ffmpeg

pytestmark = needs_ffmpeg

W, H, FPS, SECONDS, BITS = 160, 90, 25, 12, 10


def _encoders() -> set[str]:
    if not HAS_FFMPEG:
        return set()
    listing = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return {line.split()[1] for line in listing.splitlines() if len(line.split()) > 1}


ENCODERS = _encoders()

CONTAINERS = {
    "mp4": ("mp4", "libx264", []),
    "mov": ("mov", "libx264", []),
    "mkv": ("matroska", "libx264", []),
    "webm": ("webm", "libvpx-vp9", ["-deadline", "realtime", "-cpu-used", "8"]),
    "avi": ("avi", "libx264", []),
    "flv": ("flv", "libx264", []),
    "ts": ("mpegts", "libx264", ["-bf", "3"]),
    "m2ts": ("mpegts", "libx264", ["-mpegts_m2ts_mode", "1"]),
    "mpg": ("mpeg", "mpeg2video", ["-bf", "2"]),
    "vob": ("vob", "mpeg2video", ["-bf", "2"]),
    "ogv": ("ogg", "libtheora", []),
    "wmv": ("asf", "wmv2", []),
    "hevc.mp4": ("mp4", "libx265", ["-x265-params", "log-level=error"]),
    "hevc.ts": ("mpegts", "libx265", ["-x265-params", "log-level=error"]),
}
GOPS = {"dense": 12, "sparse": 125, "single": 100_000}


def _code(n_bits: int = BITS) -> str:
    column = W // n_bits
    bit = f"mod(floor(N/pow(2,floor(X/{column}))),2)"
    return f"geq=lum='if(lt(Y,{H // 2}),255*{bit},128)':cb=128:cr=128"


@functools.cache
def _video(folder: pathlib.Path, name: str, gop: str) -> pathlib.Path:
    fmt, codec, extra = CONTAINERS[name]
    if codec not in ENCODERS:
        pytest.skip(f"this ffmpeg has no {codec} encoder")
    path = folder / f"{gop}.{name}"
    keyint = GOPS[gop]
    args = list(extra)
    if codec == "libx265":
        args = ["-x265-params", f"log-level=error:keyint={keyint}:min-keyint={min(keyint, 25)}"]
    else:
        args += ["-g", str(keyint)]
    subprocess.run(
        [
            *("ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"),
            *("-f", "lavfi", "-i", f"nullsrc=s={W}x{H}:r={FPS}:d={SECONDS},format=yuv420p,{_code()}"),
            *("-f", "lavfi", "-i", f"sine=d={SECONDS}"),
            *("-c:v", codec, *args, "-shortest", "-f", fmt, str(path)),
        ],
        check=True,
    )
    return path


def frame_number(image: Image.Image) -> int:
    """The frame number a coded frame shows, read from its top half."""
    gray = image.convert("L")
    width, height = gray.size
    column = width / BITS
    number = 0
    for bit in range(BITS):
        box = (int(bit * column + column / 4), height // 8, int(bit * column + 3 * column / 4), 3 * height // 8)
        pixels = list(gray.crop(box).getdata())
        if sum(pixels) / len(pixels) > 128:
            number |= 1 << bit
    return number


@functools.cache
def timeline(path: pathlib.Path) -> tuple[tuple[float, int], ...]:
    """Every frame the file shows, as ``(seconds from its start, frame number)``, in display order."""
    result = subprocess.run(
        [
            *("ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:v:0", "-vf", "showinfo"),
            *("-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"),
        ],
        capture_output=True,
        check=True,
    )
    seconds = [float(t) for t in re.findall(rb"pts_time:\s*(-?[0-9.]+)", result.stderr)]
    raw, size = result.stdout, W * H
    numbers = [frame_number(Image.frombytes("L", (W, H), raw[i : i + size])) for i in range(0, len(raw), size)]
    assert len(seconds) == len(numbers)
    return tuple(zip(seconds, numbers, strict=True))


def near(path: pathlib.Path, time: float, before: float, after: float) -> set[int]:
    """The frame numbers shown from `before` seconds before `time` to `after` seconds after it, give or take a frame."""
    frames = timeline(path)
    shown = {n for t, n in frames if time - before - 1e-6 <= t < time + after - 1e-6}
    at = next((n for t, n in frames if t >= time - 1e-6), frames[-1][1])
    previous = [n for t, n in frames if t < time - 1e-6]
    shown |= {at, *(previous[-1:])}
    return {n + d for n in shown for d in (-1, 0, 1)}


def shown_at(path: pathlib.Path, time: float) -> set[int]:
    """The frame numbers acceptable at `time`: the frame shown then or the first one after, give or take a frame."""
    frames = timeline(path)
    after = next((i for i, (t, _) in enumerate(frames) if t >= time - 1e-6), len(frames) - 1)
    near = {frames[after][1], frames[max(after - 1, 0)][1]}
    return {n + d for n in near for d in (-1, 0, 1)}


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    folder = tmp_path_factory.mktemp("seeking")
    return lambda name, gop: _video(folder, name, gop)


ALL = [(name, gop) for name in CONTAINERS for gop in GOPS]
TRICKY = ("mp4", "hevc.mp4", "mkv", "avi", "flv", "ts", "mpg", "ogv")
"""Containers where ffmpeg's own seek goes wrong somewhere (and MP4 and MKV as the common case), for the slower routes."""
SOME = [(name, gop) for name in TRICKY for gop in ("dense", "single")]


@pytest.mark.parametrize("name, gop", ALL)
def test_every_still_tile_shows_the_frame_at_its_time(media, tmp_path, name, gop):
    path = media(name, gop)
    config = PngSheetConfig(width=640, cols=4, rows=4, gap=0, show_header=False, show_timestamps=False)
    out = tmp_path / "sheet.png"
    sheets.render(path, out, config)
    info = probe(path)
    layout = plan_layout(640, 4, 4, 0, info.display_aspect_ratio)
    times = frame_times(info.duration, layout.cells, config.start_offset_percent, config.end_offset_percent)
    wrong = []
    with Image.open(out) as sheet:
        for slot, time in enumerate(times):
            x, y = layout.cell_origin(slot)
            tile = sheet.crop((x, y, x + layout.thumb_w, y + layout.thumb_h))
            if (got := frame_number(tile)) not in shown_at(path, time):
                wrong.append((round(time, 3), got, sorted(shown_at(path, time))))
    assert not wrong


def _gif_frames(path: pathlib.Path) -> list[Image.Image]:
    frames = []
    with Image.open(path) as image:
        for index in range(getattr(image, "n_frames", 1)):
            image.seek(index)
            frames.append(image.convert("RGB"))
    return frames


@pytest.mark.parametrize("name, gop", SOME)
def test_every_animated_tile_starts_at_its_time(media, tmp_path, name, gop):
    path = media(name, gop)
    config = AnimatedGifSheetConfig(
        width=640, cols=3, rows=2, gap=0, fps=5, sample_duration=1.0, show_header=False, show_timestamps=False
    )
    out = tmp_path / "sheet.gif"
    sheets.render(path, out, config)
    info = probe(path)
    layout = plan_layout(640, 3, 2, 0, info.display_aspect_ratio, even=True, animated=True)
    starts = clip_starts(info.duration, layout.cells, 1.0, config.start_offset_percent, config.end_offset_percent)
    frames = _gif_frames(out)
    wrong = []
    for slot, start in enumerate(starts):
        x, y = layout.cell_origin(slot)
        for tick in (0, 3):
            if tick >= len(frames):
                continue
            tile = frames[tick].crop((x, y, x + layout.thumb_w, y + layout.thumb_h))
            time = start + tick / config.fps
            allowed = near(path, time, 0.0, 0.5 / config.fps)
            if (got := frame_number(tile)) not in allowed:
                wrong.append((round(time, 3), got, sorted(allowed)))
    assert not wrong


@pytest.mark.parametrize("name, gop", SOME)
def test_every_preview_clip_starts_at_its_time(media, tmp_path, name, gop):
    path = media(name, gop)
    config = VIDEO_PREVIEW.from_params({"samples": 4, "sample_duration": 1, "fps": 5, "max_height": 90}, target="gif")
    out = tmp_path / "preview.gif"
    preview(path, out, config)
    info = probe(path)
    clips = plan_preview(
        info.duration, config.samples, config.sample_duration, config.start_offset_percent, config.end_offset_percent
    )
    frames = _gif_frames(out)
    wrong = []
    for number, (start, _) in enumerate(clips):
        first = frames[number * 5]
        allowed = near(path, start, 0.0, 0.5 / config.fps)
        if (got := frame_number(first)) not in allowed:
            wrong.append((round(start, 3), got, sorted(allowed)))
    assert not wrong


def _first_frame(path: pathlib.Path) -> int:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-frames:v", "1"]
        + ["-vf", f"scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
        capture_output=True,
        check=True,
    ).stdout
    return frame_number(Image.frombytes("L", (W, H), raw[: W * H]))


STARTS = [0.3, 4.52, 9.96]


@pytest.mark.parametrize("target", ["mp4", "gif"])
@pytest.mark.parametrize("name, gop", SOME)
def test_a_conversion_starts_at_its_start_time(media, tmp_path, name, gop, target):
    path = media(name, gop)
    wrong = []
    for start in STARTS:
        out = tmp_path / f"{start}.{target}"
        params = {"start_seconds": start, "duration_seconds": 1}
        if target == "webm":
            params["speed"] = 8
        config = VIDEO_CONVERT.from_params(params, target=target)
        video.convert(path, out, config)
        fps = getattr(config, "fps", None)
        allowed = near(path, start, 0.0, 0.5 / fps) if fps else shown_at(path, start)
        if (got := _first_frame(out)) not in allowed:
            wrong.append((start, got, sorted(allowed)))
    assert not wrong


@pytest.mark.parametrize("name, gop", SOME)
def test_an_accurate_cut_starts_at_its_start_time(media, tmp_path, name, gop):
    path = media(name, gop)
    wrong = []
    for start in STARTS:
        out = tmp_path / f"{start}.mkv"
        config = VIDEO_TRIM.from_params(
            {"start_seconds": start, "duration_seconds": 1, "accurate": True}, container="mkv"
        )
        video.trim(path, out, config)
        if (got := _first_frame(out)) not in shown_at(path, start):
            wrong.append((start, got, sorted(shown_at(path, start))))
    assert not wrong


def _keyframes_shown(path: pathlib.Path) -> list[int]:
    """The frame numbers of the frames the decoder marks as keyframes, in order."""
    flags = subprocess.run(
        [
            *("ffprobe", "-v", "error", "-select_streams", "v:0"),
            *("-show_entries", "frame=key_frame", "-of", "csv=p=0", str(path)),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    frames = timeline(path)
    return sorted(n for (_, n), flag in zip(frames, flags, strict=False) if flag.strip(",") == "1")


COPYABLE = [(name, gop) for name in ("mp4", "hevc.mp4", "mkv", "webm", "ts", "mpg") for gop in ("dense", "sparse")]


@pytest.mark.parametrize("name, gop", COPYABLE)
def test_a_lossless_cut_starts_at_the_keyframe_at_or_before_its_start(media, tmp_path, name, gop):
    """It never starts after its start time; MPEG program streams' imprecise seek may start one keyframe early."""
    path = media(name, gop)
    container = "webm" if name == "webm" else "mkv"
    keys = _keyframes_shown(path)
    wrong = []
    for start in STARTS:
        out = tmp_path / f"{start}.{container}"
        config = VIDEO_TRIM.from_params({"start_seconds": start, "duration_seconds": 1}, container=container)
        video.trim(path, out, config)
        at = next(n for t, n in timeline(path) if t >= start - 1e-6)
        earlier = [k for k in keys if k <= at - 1]
        allowed = {k for k in keys if at - 1 <= k <= at + 1} | set(earlier[-2:])
        got = _first_frame(out)
        if not any(abs(got - k) <= 1 for k in allowed):
            wrong.append((start, got, sorted(allowed)))
    assert not wrong


@pytest.mark.parametrize("ext", ["ts", "m2ts"])
def test_a_short_ts_with_one_keyframe_makes_a_sheet_and_a_preview(tmp_path, ext):
    path = tmp_path / f"plain.{ext}"
    subprocess.run(
        [
            *("ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"),
            *("-f", "lavfi", "-i", "testsrc2=size=64x36:rate=25:duration=5", "-c:v", "libx264"),
            *(["-mpegts_m2ts_mode", "1"] if ext == "m2ts" else []),
            str(path),
        ],
        check=True,
    )
    sheets.render(path, tmp_path / "sheet.jpg", PngSheetConfig(width=640, cols=4, rows=4))
    preview(path, tmp_path / "preview.gif", VIDEO_PREVIEW.from_params({}, target="gif"))
    with Image.open(tmp_path / "sheet.jpg") as sheet:
        assert sheet.width == 640
    assert len(_gif_frames(tmp_path / "preview.gif")) > 1


@pytest.mark.parametrize("name", ["avi", "flv", "mp4"])
def test_a_start_before_the_first_keyframe_is_shown_is_not_skipped(media, tmp_path, name):
    path = media(name, "dense")
    wrong = []
    for start in (0.02, 0.06):
        out = tmp_path / f"{start}.mp4"
        video.convert(
            path, out, VIDEO_CONVERT.from_params({"start_seconds": start, "duration_seconds": 1}, target="mp4")
        )
        if (got := _first_frame(out)) not in shown_at(path, start):
            wrong.append((start, got, sorted(shown_at(path, start))))
    assert not wrong
