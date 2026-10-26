# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Keyframe-aware seeking: the pure parts (where decoding starts, the trimming filters, route choice, resampling)."""

import math
from array import array

import pytest

from sashimi.core._seek import VideoIndex, _index, clip_frames, clip_route, decoding_args
from sashimi.core.probe import MediaInfo
from sashimi.core.sheets.animated import build_clip_command
from sashimi.core.sheets.static import build_frame_command, build_frames_command, sheet_route


def index(frames: int, gop: int, *, fps: int = 25, delay: int = 0, missing_pts: bool = False) -> VideoIndex:
    """A constant-rate stream of `frames` packets with a keyframe every `gop`, decoded `delay` frames before shown."""
    shown = array("d", (n / fps for n in range(frames)))
    decoded = array("d", ((n - delay) / fps for n in range(frames)))
    keys = tuple(range(0, frames, gop))
    return VideoIndex(shown, decoded, keys, missing_pts, 1 / fps, math.inf)


def test_decoding_starts_just_before_the_keyframe_shown_at_or_before_the_time():
    stream = index(300, 50, delay=2)
    assert stream.decode_from(0.5) == 0.0
    assert stream.decode_from(4.5) == round(4.0 - 0.08 - 0.02, 3)
    assert stream.decode_from(3.99) == round(2.0 - 0.08 - 0.02, 3)
    assert stream.decode_from(4.0) == round(4.0 - 0.08 - 0.02, 3)


def test_streams_without_pts_start_a_keyframe_earlier():
    stream = index(300, 50, missing_pts=True)
    assert stream.decode_from(4.5) == round(2.0 - 0.02, 3)
    assert stream.decode_from(2.5) == 0.0


def test_a_single_keyframe_decodes_from_the_start():
    stream = index(3000, 100_000)
    assert stream.decode_from(119.0) == 0.0 and stream.copy_from(119.0) == 0.0


def test_a_copy_starts_at_the_keyframe_at_or_before_the_time():
    stream = index(300, 50, delay=2)
    assert stream.copy_from(4.5) == round(4.0 - 0.08 - 0.02, 3)
    assert stream.copy_from(4.5, at_shown=True) == 4.0
    assert stream.copy_from(1.0) == 0.0


def test_the_index_estimates_display_times_of_packets_without_pts():
    info = MediaInfo(container="mpeg", start_time=0.5, has_video=True, fps=25.0)
    packets = [(0.54, 0.50, True), (None, 0.54, False), (0.66, 0.58, False), (None, 0.62, True), (0.70, 0.66, False)]
    built = _index(packets, info, 10.0, 12.0, None)
    assert built.missing_pts and built.keys == (0, 3)
    assert [round(t, 3) for t in built.shown] == [0.04, 0.12, 0.16, 0.2, 0.2]
    assert [round(t, 3) for t in built.decoded] == [0.0, 0.04, 0.08, 0.12, 0.16]
    assert built.read_to == math.inf


@pytest.mark.parametrize(
    "decode_from, time, length, inputs, video, audio",
    [
        (0.0, 3.0, None, [], "trim=start=3,setpts=PTS-3/TB", "atrim=start=3,asetpts=PTS-3/TB"),
        (2.9, 3.0, 2.0, ["-ss", "2.9", "-t", "2.1"], "trim=start=0.1:end=2.1,setpts=PTS-0.1/TB", None),
        (3.0, 3.0, 2.0, ["-ss", "3", "-t", "2"], None, None),
    ],
)
def test_decoding_args(decode_from, time, length, inputs, video, audio):
    decoding = decoding_args(decode_from, time, length)
    assert decoding.inputs == inputs and decoding.video == video
    if audio is not None:
        assert decoding.audio == audio


def test_the_frame_command_is_unchanged_without_an_index_and_trims_with_one():
    plain = build_frame_command("in", 12.5, 320, 180)
    assert plain[plain.index("-i") - 2 : plain.index("-i")] == ["-ss", "12.5"]
    assert plain[plain.index("-vf") + 1] == "scale=320:180,setsar=1"
    keyed = build_frame_command("in", 12.5, 320, 180, decode_from=9.96)
    assert keyed[keyed.index("-i") - 2 : keyed.index("-i")] == ["-ss", "9.96"]
    assert keyed[keyed.index("-vf") + 1] == "trim=start=2.54,setpts=PTS-2.54/TB,scale=320:180,setsar=1"


def test_the_clip_command_keeps_its_length_when_it_decodes_from_earlier():
    keyed = build_clip_command("in", 10.0, 2.0, 12, 200, 112, decode_from=0.0)
    assert "-ss" not in keyed[: keyed.index("-i")]
    assert keyed[keyed.index("-i") - 2 : keyed.index("-i")] == ["-t", "12"]
    assert keyed[keyed.index("-vf") + 1].startswith("trim=start=10:end=12,setpts=PTS-10/TB,fps=12,")


def test_the_frames_command_picks_the_first_frame_at_or_after_each_time():
    cmd = build_frames_command("in", [1.0, 2.5], 160, 90, 2, decode_from=0.0)
    graph = cmd[cmd.index("-vf") + 1]
    assert graph.startswith("select='if(eq(selected_n,0),gte(t,1),if(eq(selected_n,1),gte(t,2.5),0))'")
    assert cmd[cmd.index("-frames:v") + 1] == "2" and cmd[cmd.index("-t") + 1] == "3.5"


def test_sparse_keyframes_take_one_pass_and_dense_ones_a_seek_each():
    times = [i * 1.0 + 0.5 for i in range(24)]
    assert sheet_route(index(1250, 25, fps=50), times) == "seek"
    assert sheet_route(index(1250, 100_000, fps=50), times) == "pass"
    assert sheet_route(None, times) == "seek"
    clips = [(i * 3.0, 2.0) for i in range(9)]
    assert clip_route(index(1500, 25, fps=50), clips) == "seek"
    assert clip_route(index(1500, 100_000, fps=50), clips) == "pass"


def _resampled(times, clips, fps):
    frames = [(t, f"{t:.2f}".encode()) for t in times]
    out: dict[int, list[str]] = {}
    for slot, data in clip_frames(frames, clips, fps):
        out.setdefault(slot, []).append(data.decode())
    return out


def test_resampling_follows_the_fps_filter():
    times = [n / 25 + 0.038 for n in range(100)]
    picked = _resampled(times, [(1.202, 1.0)], 15)
    assert len(picked[0]) == 15
    assert picked[0][:3] == ["1.28", "1.28", "1.36"]


def test_resampling_pads_a_clip_that_ends_early_and_skips_frames_before_it():
    picked = _resampled([0.0, 0.5, 1.0, 1.5], [(0.4, 2.0)], 2)
    assert picked == {0: ["0.50", "1.00", "1.50", "1.50"]}


def test_overlapping_clips_each_get_their_frames():
    times = [n / 10 for n in range(40)]
    picked = _resampled(times, [(0.0, 1.0), (0.5, 1.0)], 5)
    assert picked[0] == ["0.00", "0.20", "0.40", "0.60", "0.80"]
    assert picked[1] == ["0.50", "0.70", "0.90", "1.10", "1.30"]


def test_a_conversion_decoding_from_a_keyframe_trims_video_and_audio():
    from sashimi.config import VIDEO_CONVERT
    from sashimi.core.video import build_convert_command

    config = VIDEO_CONVERT.from_params({"start_seconds": 12.5, "duration_seconds": 3}, target="mp4")
    plain = build_convert_command("in", "out", config, duration=60, threads=2)
    assert plain[plain.index("-i") - 4 : plain.index("-i")] == ["-ss", "12.5", "-t", "3"]
    keyed = build_convert_command("in", "out", config, duration=60, threads=2, decode_from=10.0)
    assert keyed[keyed.index("-i") - 4 : keyed.index("-i")] == ["-ss", "10", "-t", "5.5"]
    assert keyed[keyed.index("-af") + 1] == "atrim=start=2.5:end=5.5,asetpts=PTS-2.5/TB"
    assert keyed[keyed.index("-vf") + 1].startswith("trim=start=2.5:end=5.5,setpts=PTS-2.5/TB,scale=")
    gif = VIDEO_CONVERT.from_params({"start_seconds": 12.5, "duration_seconds": 3}, target="gif")
    keyed_gif = build_convert_command("in", "out", gif, duration=60, threads=2, decode_from=0.0)
    assert "-ss" not in keyed_gif[: keyed_gif.index("-i")] and "-af" not in keyed_gif
    assert keyed_gif[keyed_gif.index("-vf") + 1].startswith("trim=start=12.5:end=15.5,setpts=PTS-12.5/TB,fps=")


def test_trims_decode_or_copy_from_the_keyframe():
    from sashimi.config import VIDEO_TRIM
    from sashimi.core.video import build_trim_command

    copy = VIDEO_TRIM.from_params({"start_seconds": 12.5, "duration_seconds": 3}, container="mkv")
    keyed = build_trim_command("in", "out", copy, duration=60, decode_from=10.0)
    assert keyed[keyed.index("-i") - 4 : keyed.index("-i")] == ["-ss", "10", "-t", "5.5"]
    accurate = VIDEO_TRIM.from_params({"start_seconds": 12.5, "duration_seconds": 3, "accurate": True}, container="mkv")
    keyed = build_trim_command("in", "out", accurate, duration=60, threads=2, decode_from=10.0)
    assert keyed[keyed.index("-vf") + 1].startswith("trim=start=2.5:end=5.5,setpts=PTS-2.5/TB,")
    assert (
        build_trim_command("in", "out", accurate, duration=60, threads=2)[-16:]
        == (build_trim_command("in", "out", accurate, duration=60, threads=2, decode_from=None)[-16:])
    )


def test_mpeg_program_stream_copies_generate_timestamps():
    from sashimi.config import CopyTrimConfig
    from sashimi.core.video import build_trim_command

    source = MediaInfo(container="mpeg", has_video=True, duration=10.0)
    cmd = build_trim_command("in", "out", CopyTrimConfig(container="mkv"), duration=10, source=source)
    assert cmd[cmd.index("-i") - 2 : cmd.index("-i")] == ["-fflags", "+genpts"]
    other = MediaInfo(container="matroska,webm", has_video=True, duration=10.0)
    assert "+genpts" not in build_trim_command("in", "out", CopyTrimConfig(container="mkv"), duration=10, source=other)


def test_a_preview_with_an_index_decodes_each_clip_from_its_keyframe():
    from sashimi.config import VIDEO_PREVIEW
    from sashimi.core.preview import build_preview_commands

    config = VIDEO_PREVIEW.from_params({"samples": 2, "sample_duration": 1}, target="gif")
    plain = index(1500, 250)
    stream = VideoIndex(plain.shown, plain.decoded, plain.keys, False, 0.04, math.inf, forward=True)
    clips, _ = build_preview_commands("in", "out", config, duration=60, width=320, height=180, threads=1, index=stream)
    for cmd in clips:
        assert "trim=start=" in cmd[cmd.index("-vf") + 1]


def test_ffmpegs_own_seek_is_kept_where_it_lands_right():
    stream = index(300, 50, delay=2)
    assert stream.start_for(4.5) is None
    assert stream.start_for(0.5) == 0.0
    assert stream.start_for(3.95) == stream.decode_from(3.95)
    forward = VideoIndex(stream.shown, stream.decoded, stream.keys, False, 0.04, math.inf, forward=True)
    assert forward.start_for(4.5) == stream.decode_from(4.5)


def test_half_ticks_round_up_like_the_fps_filter_even_when_read_a_little_low():
    from fractions import Fraction

    start, fps = Fraction(5075, 1000), 12
    exact = [Fraction(n, 30) for n in range(150, 220)]
    ticks = [math.floor((t - start) * fps + Fraction(1, 2)) for t in exact if start <= t < start + 2]
    assert any(((t - start) * fps).denominator == 2 for t in exact)
    shown = [t for t in exact if start <= t < start + 2]
    wanted = []
    for k in range(24):
        candidates = [t for t, tick in zip(shown, ticks, strict=True) if tick <= k]
        wanted.append(f"{float(candidates[-1] if candidates else shown[0]):.4f}")
    frames = [(float(t) - 1e-6, f"{float(t):.4f}".encode()) for t in exact]
    picked = [data.decode() for _, data in clip_frames(frames, [(5.075, 2.0)], fps)]
    assert picked == wanted
