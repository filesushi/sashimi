# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Every tile and clip shows the frame on screen at its stated time, whichever route sashimi takes
(a seek per tile, or one decoding pass), for short clips with fewer frames than tiles, every grid
size, and variable-frame-rate video that holds a frame for seconds (screen and browser
recordings). The rule, as a player shows it: the first frame at or after the time when it starts
within 1.5 frame intervals (what ffmpeg's seek shows, kept so outputs match 0.6.0), else the
frame still on screen. The videos draw their own frame number (see ``test_seeking``).
"""

import functools
import re
import pathlib
import subprocess

import pytest
from PIL import Image

import sashimi.core.preview as preview_module
import sashimi.core.sheets.animated as animated_module
import sashimi.core.sheets.static as static_module
from sashimi import probe, sheets
from sashimi.config import VIDEO_PREVIEW, AnimatedGifSheetConfig, PngSheetConfig
from sashimi.core.preview import plan_preview, preview
from sashimi.core.sheets._layout import clip_starts, fit_grid, frame_times, plan_clips, plan_layout
from tests.conftest import needs_ffmpeg
from tests.integration.test_seeking import ENCODERS, H, W, _code, _gif_frames, frame_number, timeline

pytestmark = needs_ffmpeg

FORMATS = {
    "mp4": ("mp4", "libx264", []),
    "mkv": ("matroska", "libx264", []),
    "ts": ("mpegts", "libx264", ["-bf", "3"]),
    "webm": ("webm", "libvpx-vp9", ["-deadline", "realtime", "-cpu-used", "8"]),
}
HELD_FRAMES = 1.5
"""Frame intervals within which the next frame is shown for a time (sashimi's ``_seek.HELD_FRAMES``)."""
HOLD_FROM, HOLD_TO = 4.0, 9.0
"""The variable-frame-rate videos show one frame from HOLD_FROM to HOLD_TO seconds."""


@functools.cache
def _coded(folder: pathlib.Path, name: str, fps: int, seconds: float, gop: int, vfr: bool) -> pathlib.Path:
    fmt, codec, extra = FORMATS[name]
    if codec not in ENCODERS:
        pytest.skip(f"this ffmpeg has no {codec} encoder")
    path = folder / f"{fps}fps-{seconds}s-g{gop}{'-vfr' if vfr else ''}.{name}"
    graph = f"nullsrc=s={W}x{H}:r={fps}:d={seconds},format=yuv420p,{_code()}"
    if vfr:
        graph += f",select='lt(t,{HOLD_FROM})+eq(n,{round(HOLD_FROM * fps)})+gte(t,{HOLD_TO})'"
    subprocess.run(
        [
            *("ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i", graph),
            *("-fps_mode", "vfr" if vfr else "cfr", "-c:v", codec, *extra, "-g", str(gop), "-f", fmt, str(path)),
        ],
        check=True,
    )
    return path


@pytest.fixture(scope="module")
def coded(tmp_path_factory):
    folder = tmp_path_factory.mktemp("sheet-frames")
    return lambda name, fps, seconds, gop=12, vfr=False: _coded(folder, name, fps, seconds, gop, vfr)


@pytest.fixture(params=["auto", "seek", "pass"])
def route(request, monkeypatch):
    """Lets sashimi choose the route, or forces one."""
    if request.param != "auto":
        forced = request.param
        monkeypatch.setattr(static_module, "sheet_route", lambda index, times: forced)
        monkeypatch.setattr(animated_module, "clip_route", lambda index, clips: forced)
        monkeypatch.setattr(preview_module, "clip_route", lambda index, clips: forced)
    return request.param


def shown_for(path: pathlib.Path, time: float, fps: float) -> set[int]:
    """The frame shown for a tile at `time` by the rule above, give or take a frame number."""
    frames = timeline(path)
    at = round(time, 3)
    following = next(((t, n) for t, n in frames if t >= at - 1e-6), None)
    previous = [(t, n) for t, n in frames if t < at - 1e-6][-1:]
    limit = HELD_FRAMES / fps
    if following is not None and (not previous or following[0] - at <= limit + 1e-6):
        pick = {following[1]}
    else:
        pick = {previous[0][1]}
    if following is not None and abs(following[0] - at - limit) < 2e-3 and previous:
        pick.add(previous[0][1])
    return {n + d for n in pick for d in (-1, 0, 1)}


def on_screen_between(path: pathlib.Path, start: float, end: float) -> set[int]:
    """The frames on screen at any moment from `start` to `end`, give or take a frame number."""
    frames = timeline(path)
    numbers = {n for t, n in frames if start - 1e-6 <= t <= end + 1e-6}
    before = [n for t, n in frames if t < start - 1e-6]
    numbers |= set(before[-1:])
    return {n + d for n in numbers for d in (-1, 0, 1)}


def _is_blank(tile: Image.Image) -> bool:
    gray = tile.convert("L")
    width, height = gray.size
    lower = list(gray.crop((0, height * 5 // 8, width, height * 7 // 8)).getdata())
    return abs(sum(lower) / len(lower) - 128) > 20


def _still_errors(path: pathlib.Path, cols: int, rows: int, fps: float, tmp_path: pathlib.Path) -> list:
    config = PngSheetConfig(width=1280, cols=cols, rows=rows, gap=0, show_header=False, show_timestamps=False)
    out = tmp_path / f"sheet-{cols}x{rows}.png"
    sheets.render(path, out, config)
    info = probe(path)
    layout = plan_layout(1280, cols, rows, 0, info.display_aspect_ratio)
    times = frame_times(info.duration, layout.cells, config.start_offset_percent, config.end_offset_percent)
    shown = [t for t, _ in timeline(path)]
    if len(shown) < layout.cells:
        layout = plan_layout(1280, *fit_grid(len(shown), cols, rows), 0, info.display_aspect_ratio)
        times = shown
    wrong = []
    with Image.open(out) as sheet:
        if len(shown) <= cols * rows:
            numbers = [
                frame_number(sheet.crop((x, y, x + layout.thumb_w, y + layout.thumb_h)))
                for x, y in (layout.cell_origin(slot) for slot in range(len(times)))
            ]
            if len(set(numbers)) != len(numbers):
                wrong.append(("repeated tiles", numbers))
        for slot, time in enumerate(times):
            x, y = layout.cell_origin(slot)
            tile = sheet.crop((x, y, x + layout.thumb_w, y + layout.thumb_h))
            if _is_blank(tile):
                wrong.append((round(time, 3), "blank"))
            elif (got := frame_number(tile)) not in shown_for(path, time, fps):
                wrong.append((round(time, 3), got, sorted(shown_for(path, time, fps))))
    return wrong


SHORT = [(10, 2.0), (25, 1.0), (5, 3.0), (30, 1.2)]
GRIDS = [(1, 1), (3, 2), (4, 4), (6, 5), (8, 8)]


@pytest.mark.parametrize("fps, seconds", SHORT)
@pytest.mark.parametrize("cols, rows", GRIDS)
def test_a_short_clip_fills_every_tile_with_the_frame_at_its_time(coded, tmp_path, route, fps, seconds, cols, rows):
    path = coded("mp4", fps, seconds)
    assert not _still_errors(path, cols, rows, fps, tmp_path)


@pytest.mark.parametrize("name", list(FORMATS))
@pytest.mark.parametrize("gop", [12, 100_000])
def test_a_held_frame_is_shown_in_the_tiles_it_covers(coded, tmp_path, route, name, gop):
    path = coded(name, 25, 12.0, gop, vfr=True)
    assert not _still_errors(path, 6, 5, 25, tmp_path)


def _clip_errors(path, frames, starts, layout, fps, ticks, per_clip=0):
    """Wrong ticks of each clip: a tile of each frame (`layout`), or clips one after another of `per_clip` frames."""
    wrong = []
    for slot, start in enumerate(starts):
        x, y = layout.cell_origin(slot) if layout is not None else (0, 0)
        for tick in ticks:
            index = tick if layout is not None else slot * per_clip + tick
            if index >= len(frames):
                wrong.append((slot, tick, "missing"))
                continue
            frame = frames[index]
            tile = frame.crop((x, y, x + layout.thumb_w, y + layout.thumb_h)) if layout is not None else frame
            time = start + tick / fps
            allowed = on_screen_between(path, time - 0.5 / fps, time + 0.5 / fps)
            if (got := frame_number(tile)) not in allowed:
                wrong.append((round(time, 3), got, sorted(allowed)))
    return wrong


@pytest.mark.parametrize("name", ["mp4", "mkv", "ts"])
@pytest.mark.parametrize("gop", [12, 100_000])
def test_a_held_frame_is_shown_in_the_animated_clips_it_covers(coded, tmp_path, route, name, gop):
    path = coded(name, 25, 12.0, gop, vfr=True)
    config = AnimatedGifSheetConfig(
        width=640,
        cols=4,
        rows=3,
        gap=0,
        fps=5,
        sample_duration=1.0,
        show_header=False,
        show_timestamps=False,
        start_offset_percent=0,
        end_offset_percent=0,
    )
    out = tmp_path / "sheet.gif"
    sheets.render(path, out, config)
    info = probe(path)
    layout = plan_layout(640, 4, 3, 0, info.display_aspect_ratio, even=True, animated=True)
    starts = clip_starts(info.duration, layout.cells, 1.0, config.start_offset_percent, config.end_offset_percent)
    assert any(HOLD_FROM < start < HOLD_TO for start in starts)
    assert not _clip_errors(path, _gif_frames(out), starts, layout, 5, [0, 2, 4])


@pytest.mark.parametrize("name", ["mp4", "mkv", "ts"])
@pytest.mark.parametrize("gop", [12, 100_000])
def test_a_preview_keeps_every_clip_and_shows_a_held_frame(coded, tmp_path, route, name, gop):
    path = coded(name, 25, 12.0, gop, vfr=True)
    config = VIDEO_PREVIEW.from_params(
        {"samples": 5, "sample_duration": 1, "fps": 5, "max_height": 90, "start_offset_percent": 0}, target="gif"
    )
    out = tmp_path / "preview.gif"
    preview(path, out, config)
    info = probe(path)
    clips = plan_preview(
        info.duration, config.samples, config.sample_duration, config.start_offset_percent, config.end_offset_percent
    )
    assert any(HOLD_FROM < start < HOLD_TO for start, _ in clips)
    frames = _gif_frames(out)
    assert len(frames) == 5 * len(clips)
    assert not _clip_errors(path, frames, [start for start, _ in clips], None, 5, [0, 2, 4], per_clip=5)


@pytest.mark.parametrize("fps, seconds", [(10, 2.0), (25, 1.0)])
def test_both_routes_give_the_same_sheet(coded, tmp_path, monkeypatch, fps, seconds):
    path = coded("mp4", fps, seconds)
    outputs = []
    for forced in ("seek", "pass"):
        monkeypatch.setattr(static_module, "sheet_route", lambda index, times, r=forced: r)
        out = tmp_path / f"{forced}.png"
        sheets.render(path, out, PngSheetConfig(width=640, cols=6, rows=5))
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1]


def test_both_routes_round_like_ffmpeg_in_a_coarse_time_base(tmp_path, monkeypatch):
    """Web clips often have a 1/100 s time base: ffmpeg's seek then counts a frame up to half a tick early as at the time."""
    path = tmp_path / "coarse.mp4"
    subprocess.run(
        [
            *("ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi"),
            *("-i", f"nullsrc=s={W}x{H}:r=10:d=5.3,format=yuv420p,{_code()}"),
            *("-c:v", "libx264", "-g", "12", "-video_track_timescale", "100", str(path)),
        ],
        check=True,
    )
    config = PngSheetConfig(width=1280, cols=6, rows=6, show_timestamps=False)
    outputs = []
    for forced in ("seek", "pass"):
        monkeypatch.setattr(static_module, "sheet_route", lambda index, times, r=forced: r)
        out = tmp_path / f"{forced}.png"
        sheets.render(path, out, config)
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("name", ["mp4", "mkv", "ts"])
def test_tiles_after_the_video_ends_show_its_last_frame(tmp_path, route, name):
    """A file whose sound runs on after its picture states the longer length; a player shows the last frame."""
    fmt, codec, extra = FORMATS[name]
    path = tmp_path / f"short-picture.{name}"
    subprocess.run(
        [
            *("ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi"),
            *("-i", f"nullsrc=s={W}x{H}:r=25:d=3,format=yuv420p,{_code()}", "-f", "lavfi", "-i", "sine=d=6"),
            *("-c:v", codec, *extra, "-g", "12", "-c:a", "aac", "-f", fmt, str(path)),
        ],
        check=True,
    )
    assert probe(path).duration > 5
    assert not _still_errors(path, 6, 5, 25, tmp_path)


@pytest.mark.parametrize("source_fps", [5, 24, 30])
def test_clip_resampling_matches_ffmpeg_for_every_start(tmp_path, source_fps):
    """ffmpeg starts a clip at its first frame's tick without filling the ticks before it; the one-pass route must too."""
    from sashimi.core._seek import clip_frames
    from sashimi.core.sheets.animated import build_clip_command

    path = tmp_path / f"{source_fps}.mp4"
    subprocess.run(
        [
            *("ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi"),
            *("-i", f"nullsrc=s={W}x{H}:r={source_fps}:d=4,format=yuv420p,{_code()}", "-c:v", "libx264", str(path)),
        ],
        check=True,
    )
    frames = timeline(path)
    wrong = []
    for step in range(12):
        start = 1.0 + step * 0.0173
        for fps in (5, 15):
            raw = subprocess.run(
                build_clip_command(path, start, 1.0, fps, W, H), capture_output=True, check=True
            ).stdout
            size = W * H * 3
            real = [frame_number(Image.frombytes("RGB", (W, H), raw[i : i + size])) for i in range(0, len(raw), size)]
            resampled = [
                int(data) for _, data in clip_frames(((t, str(n).encode()) for t, n in frames), [(start, 1.0)], fps)
            ]
            if real != resampled:
                wrong.append((round(start, 4), fps, real, resampled))
    assert not wrong


@pytest.mark.parametrize("source_fps", [24, 30])
def test_both_routes_give_the_same_preview_and_animated_sheet(tmp_path, monkeypatch, source_fps):
    path = tmp_path / f"{source_fps}.mp4"
    subprocess.run(
        [
            *("ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi"),
            *("-i", f"testsrc2=s=320x180:r={source_fps}:d=20", "-c:v", "libx264", str(path)),
        ],
        check=True,
    )
    outputs = []
    for forced in ("seek", "pass"):
        monkeypatch.setattr(animated_module, "clip_route", lambda index, clips, r=forced: r)
        monkeypatch.setattr(preview_module, "clip_route", lambda index, clips, r=forced: r)
        sheets.render(path, tmp_path / f"{forced}.gif", AnimatedGifSheetConfig(width=640))
        preview(path, tmp_path / f"{forced}.p.gif", VIDEO_PREVIEW.from_params({}, target="gif"))
        outputs.append(((tmp_path / f"{forced}.gif").read_bytes(), (tmp_path / f"{forced}.p.gif").read_bytes()))
    assert outputs[0] == outputs[1]


LATE = {"ts": ("mpegts", "libx264", "aac"), "mpg": ("mpeg", "mpeg2video", "mp2"), "mkv": ("matroska", "libx264", "aac")}


@functools.cache
def _late_video(folder: pathlib.Path, name: str) -> pathlib.Path:
    fmt, codec, sound = LATE[name]
    path = folder / f"late-video.{name}"
    subprocess.run(
        [
            *("ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-itsoffset", "0.5", "-f", "lavfi"),
            *("-i", f"nullsrc=s={W}x{H}:r=25:d=10,format=yuv420p,{_code()}", "-f", "lavfi", "-i", "sine=d=11"),
            *("-map", "0:v", "-map", "1:a", "-c:v", codec, "-g", "100", "-c:a", sound, "-fps_mode", "passthrough"),
            *("-f", fmt, str(path)),
        ],
        check=True,
    )
    return path


@functools.cache
def file_timeline(path: pathlib.Path) -> tuple[tuple[float, int], ...]:
    """Every frame as ``(seconds from the file's start, frame number)``: the clock start times count on."""
    result = subprocess.run(
        [
            *("ffmpeg", "-hide_banner", "-nostdin", "-copyts", "-i", str(path), "-map", "0:v:0", "-vf", "showinfo"),
            *("-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"),
        ],
        capture_output=True,
        check=True,
    )
    origin = probe(path).start_time
    seconds = [float(t) - origin for t in re.findall(rb"pts_time:\s*(-?[0-9.]+)", result.stderr)]
    raw, size = result.stdout, W * H
    numbers = [frame_number(Image.frombytes("L", (W, H), raw[i : i + size])) for i in range(0, len(raw), size)]
    return tuple(zip(seconds, numbers, strict=True))


def _expected_at(path: pathlib.Path, time: float, after: float = 0.0) -> set[int]:
    """The first frame at or after `time` and any shown up to `after` seconds later, give or take a frame number."""
    frames = file_timeline(path)
    following = next((n for t, n in frames if t >= round(time, 3) - 1e-3), frames[-1][1])
    shown = {following} | {n for t, n in frames if time - 1e-3 <= t < time + after}
    return {n + d for n in shown for d in (-1, 0, 1)}


@pytest.fixture(scope="module")
def late(tmp_path_factory):
    folder = tmp_path_factory.mktemp("late-video")
    return lambda name: _late_video(folder, name)


@pytest.mark.parametrize("name", list(LATE))
def test_a_video_that_starts_after_its_sound_shows_the_frame_at_each_tile_time(late, tmp_path, route, name):
    """MPEG-TS and program streams count a video-only command's timestamps from the video's start."""
    path = late(name)
    assert file_timeline(path)[0][0] == pytest.approx(0.5, abs=0.06)
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
            got = frame_number(sheet.crop((x, y, x + layout.thumb_w, y + layout.thumb_h)))
            if time > 0.6 and got not in _expected_at(path, time):
                wrong.append((round(time, 3), got, sorted(_expected_at(path, time))))
    assert not wrong


@pytest.mark.parametrize("name", list(LATE))
def test_a_video_that_starts_after_its_sound_starts_clips_at_their_time(late, tmp_path, route, name):
    path = late(name)
    config = VIDEO_PREVIEW.from_params(
        {"samples": 4, "sample_duration": 1, "fps": 5, "max_height": 90, "start_offset_percent": 8}, target="gif"
    )
    out = tmp_path / "preview.gif"
    preview(path, out, config)
    info = probe(path)
    clips = plan_preview(
        info.duration, config.samples, config.sample_duration, config.start_offset_percent, config.end_offset_percent
    )
    frames = _gif_frames(out)
    wrong = [
        (round(start, 3), got)
        for number, (start, _) in enumerate(clips)
        if (got := frame_number(frames[number * 5])) not in _expected_at(path, start, 0.5 / config.fps)
    ]
    assert not wrong


@pytest.mark.parametrize("name", list(LATE))
@pytest.mark.parametrize(
    "target, params",
    [
        ("gif", {}),
        ("mp4", {}),
        ("mp4", {"mute": True}),
        ("mkv", {"accurate": True}),
        ("mkv", {"accurate": True, "mute": True}),
    ],
)
def test_a_video_that_starts_after_its_sound_converts_from_its_start_time(late, tmp_path, name, target, params):
    from sashimi import video
    from sashimi.config import VIDEO_CONVERT, VIDEO_TRIM

    path = late(name)
    wrong = []
    for start in (0.7, 1.3, 2.62):
        out = tmp_path / f"{start}.{target}"
        if target == "mkv":
            config = VIDEO_TRIM.from_params({"start_seconds": start, "duration_seconds": 1, **params}, container="mkv")
            video.trim(path, out, config)
        else:
            config = VIDEO_CONVERT.from_params({"start_seconds": start, "duration_seconds": 1, **params}, target=target)
            video.convert(path, out, config)
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(out), "-map", "0:v:0", "-frames:v", "1"]
            + ["-vf", f"scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
            capture_output=True,
            check=True,
        ).stdout
        got = frame_number(Image.frombytes("L", (W, H), raw[: W * H]))
        if got not in _expected_at(path, start):
            wrong.append((start, got, sorted(_expected_at(path, start))))
    assert not wrong


def test_a_gif_that_holds_a_frame_shows_it_in_the_tiles_it_covers(tmp_path, route):
    """GIF frames last as long as their delay says; ffmpeg seeks in GIFs by itself, but the frame shown must still be the one on screen."""
    path = tmp_path / "held.gif"
    frames = [Image.new("RGB", (160, 90), (i * 12, 80, 40)) for i in range(20)]
    delays = [5000 if i == 5 else 100 for i in range(20)]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=delays)
    config = PngSheetConfig(width=640, cols=4, rows=3, gap=0, show_header=False, show_timestamps=False)
    out = tmp_path / "sheet.png"
    sheets.render(path, out, config)
    info = probe(path)
    layout = plan_layout(640, 4, 3, 0, info.display_aspect_ratio)
    times = frame_times(info.duration, layout.cells, config.start_offset_percent, config.end_offset_percent)
    starts = [sum(delays[:i]) / 1000 for i in range(20)]
    wrong = []
    with Image.open(out) as sheet:
        for slot, time in enumerate(times):
            x, y = layout.cell_origin(slot)
            red = sheet.crop((x + 4, y + 4, x + 20, y + 20)).convert("RGB").getpixel((8, 8))[0]
            got = round(red / 12)
            on_screen = max(i for i, start in enumerate(starts) if start <= time + 1e-6)
            following = min((i for i, start in enumerate(starts) if start >= time - 1e-6), default=on_screen)
            allowed = {on_screen} | ({following} if starts[following] - time <= 0.15 else set())
            if got not in allowed:
                wrong.append((round(time, 2), got, sorted(allowed)))
    assert not wrong


@pytest.mark.parametrize("seconds", [1.28, 1.52, 3.32, 7.6, 12.0])
def test_an_animated_sheet_draws_the_clips_it_states_without_overlap(coded, tmp_path, route, seconds):
    path = coded("mp4", 25, seconds)
    config = AnimatedGifSheetConfig(
        width=640, gap=4, fps=5, sample_duration=2.0, show_header=False, show_timestamps=False
    )
    out = tmp_path / "sheet.gif"
    result = sheets.render(path, out, config)
    info = probe(path)
    plan = plan_clips(info.duration, 3, 3, 2.0, config.start_offset_percent, config.end_offset_percent)
    stated = int(re.match(r"(\d+) clips? at", result.fidelity.changed[0]).group(1))
    frames = _gif_frames(out)
    layout = plan_layout(640, plan.cols, plan.rows, 4, info.display_aspect_ratio, even=True, animated=True)
    drawn = []
    for slot in range(layout.cells):
        x, y = layout.cell_origin(slot)
        tile = frames[0].crop((x, y, x + layout.thumb_w, y + layout.thumb_h))
        if any(high - low > 24 for low, high in tile.getextrema()):
            drawn.append(slot)
    assert stated == len(plan.starts) == len(drawn), (result.fidelity.text, drawn)
    assert drawn == list(range(len(drawn)))
    if result.fidelity.notes:
        assert f"shows {stated} clip" in result.fidelity.notes[0] or "shows one clip of" in (result.fidelity.notes[0])
    shown: list[set[int]] = []
    for slot in drawn:
        x, y = layout.cell_origin(slot)
        shown.append({frame_number(f.crop((x, y, x + layout.thumb_w, y + layout.thumb_h))) for f in frames})
    for first, second in zip(shown, shown[1:], strict=False):
        assert not first & second, (first & second, result.fidelity.text)
    assert not _clip_errors(path, frames, plan.starts, layout, 5, [0, 4])
