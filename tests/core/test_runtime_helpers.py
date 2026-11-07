# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import collections
import io
import logging
import os
import queue
import sys

import pytest

from sashimi.core import _resources
from sashimi.core._atomic import atomic_output
from sashimi.core import _ffmpeg
from sashimi.core._ffmpeg import _frame_times, file_arg, fmt_seconds, run_ffmpeg, stream_timed_frames
from sashimi.errors import ProcessingError

from tests.conftest import open_ended_intervals


@pytest.mark.parametrize("value, text", [(0, "0"), (10.0, "10"), (12.5, "12.5"), (1e-5, "0"), (3.14159, "3.142")])
def test_fmt_seconds(value, text):
    assert fmt_seconds(value) == text


def test_file_arg_forces_file_protocol():
    assert file_arg("http:evil") == "file:http:evil"
    assert file_arg("-y") == "file:-y"


def test_default_workers_is_small(monkeypatch):
    monkeypatch.setattr(_resources, "available_cpus", lambda: 12)
    assert _resources.resolve_max_workers(None) == 4
    monkeypatch.setattr(_resources, "available_cpus", lambda: 2)
    assert _resources.resolve_max_workers(None) == 2
    assert _resources.resolve_max_workers(7) == 7
    with pytest.raises(ValueError):
        _resources.resolve_max_workers(0)


@pytest.mark.parametrize("content, expected", [("200000 100000\n", 2), ("150000 100000", 2), ("max 100000", None)])
def test_cgroup_v2_quota(fs, content, expected):
    fs.create_file("/sys/fs/cgroup/cpu.max", contents=content)
    assert _resources._cgroup_cpu_limit() == expected


def test_atomic_output_moves_into_place(tmp_path):
    out = tmp_path / "result.gif"
    with atomic_output(out) as temp:
        assert temp.parent == tmp_path and temp.name.endswith(".partial")
        temp.write_bytes(b"data")
    assert out.read_bytes() == b"data"
    assert [p.name for p in tmp_path.iterdir()] == ["result.gif"]


@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyboardInterrupt()])
def test_atomic_output_leaves_nothing_on_failure(tmp_path, exc):
    with pytest.raises(type(exc)):
        with atomic_output(tmp_path / "result.gif") as temp:
            temp.write_bytes(b"partial")
            raise exc
    assert list(tmp_path.iterdir()) == []


def test_atomic_output_via_scratch_dir(tmp_path):
    scratch, out_dir = tmp_path / "scratch", tmp_path / "out"
    scratch.mkdir()
    out_dir.mkdir()
    with atomic_output(out_dir / "r.bin", scratch) as temp:
        assert temp.parent == scratch
        temp.write_bytes(b"x")
    assert (out_dir / "r.bin").read_bytes() == b"x" and list(scratch.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
@pytest.mark.parametrize("umask, mode", [(0o022, 0o644), (0o077, 0o600), (0o002, 0o664)])
def test_atomic_output_mode_follows_umask(tmp_path, umask, mode):
    previous = os.umask(umask)
    try:
        with atomic_output(tmp_path / "r.bin") as temp:
            temp.write_bytes(b"x")
    finally:
        os.umask(previous)
    assert (tmp_path / "r.bin").stat().st_mode & 0o777 == mode


FAKE = [sys.executable, "-c"]


def test_runner_parses_progress_and_feeds_stdin():
    script = (
        "import sys; data = sys.stdin.buffer.read(); "
        "print('out_time_us=500000'); print('out_time_us=1000000'); print('progress=end'); "
        "sys.stderr.write(str(len(data)))"
    )
    seen = []
    run_ffmpeg(
        [*FAKE, script],
        stdin_chunks=iter([b"a" * 10, b"b" * 5]),
        progress_total=2.0,
        on_progress=lambda f, s: seen.append((f, s)),
    )
    assert seen == [(0.25, "encoding"), (0.5, "encoding")]


def test_runner_captures_stdout():
    assert run_ffmpeg([*FAKE, "import sys; sys.stdout.buffer.write(b'xyz')"], capture_stdout=True) == b"xyz"


def test_runner_raises_processing_error_with_stderr_tail():
    with pytest.raises(ProcessingError) as e:
        run_ffmpeg([*FAKE, "import sys; sys.stderr.write('decoder exploded\\n'); sys.exit(3)"])
    assert "exited with code 3" in str(e.value) and "decoder exploded" in str(e.value)


def test_runner_missing_binary():
    from sashimi.errors import BinaryNotFoundError

    with pytest.raises(BinaryNotFoundError):
        run_ffmpeg([os.path.join(os.sep, "definitely", "not", "ffmpeg")])


def test_ffmpeg_versions_are_read_from_the_banner():
    from sashimi.core._ffmpeg import parse_ffmpeg_version

    assert parse_ffmpeg_version("ffmpeg version n8.1.3-24-g92611900bb-20261026 Copyright (c)") == (8, 1)
    assert parse_ffmpeg_version("ffmpeg version 8.0.1-3ubuntu1 Copyright (c) 2000-2025") == (8, 0)
    assert parse_ffmpeg_version("ffmpeg version 6.1.2 Copyright") == (6, 1)
    assert parse_ffmpeg_version("ffmpeg version N-121234-gabcdef Copyright") is None
    assert parse_ffmpeg_version("") is None


def _read_frame_times(stderr: bytes) -> tuple[list[tuple[int, float] | None], list[bytes]]:
    times: queue.Queue[tuple[int, float] | None] = queue.Queue()
    said: collections.deque[bytes] = collections.deque()
    _frame_times(io.BytesIO(stderr), times, said)
    return [times.get_nowait() for _ in range(times.qsize())], list(said)


def _frame_line(number: int, seconds: float) -> bytes:
    return f"frame:{number:<4} pts:{round(seconds * 1000):<7} pts_time:{seconds:g}\n".encode()


def test_a_frame_line_is_read_as_the_filter_prints_it():
    assert _frame_line(10, 2.8) == b"frame:10   pts:2800    pts_time:2.8\n"
    assert _read_frame_times(_frame_line(12345, 0.5) + b"sashimi_progress=1\r\n") == ([(12345, 0.5), None], [])


@pytest.mark.parametrize(
    "prefix",
    [
        b"",
        b"[in#0/matroska,webm @ 0x63d7b72bee80] ",
        b"[in#0/matroska,webm @ 0x1] [vost#0:0/rawvideo @ 0x2] ",
        b"[in#0/matroska,webm @ 000002bb3991e6c0] File ended prematurely",
    ],
    ids=["alone", "after a log prefix", "after two log prefixes", "after a whole message"],
)
def test_a_frame_keeps_its_time_when_a_message_runs_into_its_line(prefix):
    stderr = _frame_line(8, 2.4) + b"sashimi_progress=1\n" + prefix + _frame_line(9, 2.44) + b"sashimi_progress=1\n"
    times, said = _read_frame_times(stderr + b"Error while decoding\n")
    assert times == [(8, 2.4), (9, 2.44), None]
    assert said == ([prefix.rstrip() + b"\n"] if prefix else []) + [b"Error while decoding\n"]


def test_a_message_that_runs_into_a_progress_key_is_kept_without_it():
    times, said = _read_frame_times(_frame_line(0, 0) + b"[in#0 @ 0x1] File ended prematurelysashimi_progress=1\n")
    assert times == [(0, 0.0), None]
    assert said == [b"[in#0 @ 0x1] File ended prematurely\n"]


@pytest.mark.parametrize(
    "line",
    [
        b"[in#0 @ 0x1] Error opening file:frame:1 pts:2 pts_time:3\n",
        b"file:frame:1 pts:2 pts_time:3: Invalid data found\n",
        b"[in#0 @ 0x1] frame:1 pts:2 pts_time:3: Invalid data found\n",
        b"[in#0 @ 0x1] " + _frame_line(1, 3).rstrip() + b": Invalid data found\n",
        b"frame:1    pts:NOPTS   pts_time:NOPTS\n",
    ],
    ids=["after other words", "file name first", "words after it", "a whole frame line, words after it", "no time"],
)
def test_a_log_line_quoting_a_frame_line_gives_no_time(line):
    times, said = _read_frame_times(line)
    assert times == [None]
    assert said == [line]


def _timed_frames(stderr: bytes, frames: int, code: int = 0) -> list[tuple[float, bytes]]:
    script = (
        "import sys\n"
        f"sys.stderr.buffer.write({stderr!r}); sys.stderr.flush()\n"
        f"for i in range({frames}): sys.stdout.buffer.write(bytes([i]) * 3)\n"
        f"sys.exit({code})\n"
    )
    return list(stream_timed_frames([*FAKE, script], 3))


def _sheet_stderr(frames: int, glued: dict[int, bytes] | None = None, lost: frozenset[int] = frozenset()) -> bytes:
    glued = glued or {}
    lines = [
        glued.get(n, b"") + (b"frame:%d pts:? pts_time:?\n" % n if n in lost else _frame_line(n, 0.4 * n))
        for n in range(frames)
    ]
    return b"".join(line + b"sashimi_progress=1\n" for line in lines)


GLUED = b"[in#0/matroska,webm @ 000002bb3991e6c0] File ended prematurely"


def test_a_message_run_into_a_frame_line_leaves_every_frame_with_its_own_time():
    got = _timed_frames(_sheet_stderr(12, {10: GLUED}), 12)
    assert got == [(round(0.4 * n, 6), bytes([n]) * 3) for n in range(12)]
    assert GLUED + _frame_line(10, 2.8) == (
        b"[in#0/matroska,webm @ 000002bb3991e6c0] File ended prematurelyframe:10   pts:2800    pts_time:2.8\n"
    )


def test_a_message_run_into_a_frame_line_is_still_reported():
    seen = []
    with pytest.raises(ProcessingError) as e:
        for item in stream_timed_frames(
            [*FAKE, f"import sys; sys.stderr.buffer.write({_sheet_stderr(3, {1: GLUED})!r}); "
             "sys.stdout.buffer.write(bytes(9)); sys.exit(1)"],
            3,
        ):  # fmt: skip
            seen.append(item[0])
    assert seen == [0.0, 0.4, 0.8]
    said = str(e.value).partition("\ncommand:")[0]
    assert said.endswith(" exited with code 1: [in#0/matroska,webm @ 000002bb3991e6c0] File ended prematurely")


@pytest.mark.parametrize("lost", [frozenset({5}), frozenset({0}), frozenset({10, 11}), frozenset({3, 4, 9})], ids=str)
def test_a_frame_whose_time_is_lost_is_left_out_and_the_rest_keep_theirs(lost, caplog):
    with caplog.at_level(logging.WARNING, logger="sashimi"):
        got = _timed_frames(_sheet_stderr(12, lost=lost), 12)
    assert got == [(round(0.4 * n, 6), bytes([n]) * 3) for n in range(12) if n not in lost]
    assert [r.getMessage() for r in caplog.records] == [
        f"ffmpeg didn't report the time of {len(lost)} of 12 frames, so they were left out"
    ]


def test_frames_past_the_last_time_are_left_out(caplog):
    stderr = b"".join(_frame_line(n, 0.4 * n) for n in range(3))
    with caplog.at_level(logging.WARNING, logger="sashimi"):
        assert _timed_frames(stderr, 5) == [(0.0, bytes(3)), (0.4, bytes([1]) * 3), (0.8, bytes([2]) * 3)]
    assert [r.getMessage() for r in caplog.records] == [
        "ffmpeg didn't report the time of 2 of 5 frames, so they were left out"
    ]


def _streamed(script: str, frame_bytes: int = 3) -> list[tuple[float, bytes]]:
    return list(stream_timed_frames([*FAKE, script], frame_bytes))


def _lines(*numbered: tuple[int, float]) -> bytes:
    return b"".join(_frame_line(n, t) + b"sashimi_progress=1\n" for n, t in numbered)


def _frames_of(count: int) -> list[bytes]:
    return [bytes([n]) * 3 for n in range(count)]


RESTARTED = [(n % 5, 0.1 * n) for n in range(15)]
"""Three graphs of five frames each, as FFmpeg numbers them when it rebuilds the graph mid-stream."""


@pytest.mark.parametrize(
    "lines, kept",
    [
        (RESTARTED, list(range(15))),
        ([line for i, line in enumerate(RESTARTED) if i != 5], [n for n in range(15) if n != 5]),
        ([line for i, line in enumerate(RESTARTED) if i != 6], [n for n in range(15) if n != 6]),
        ([line for i, line in enumerate(RESTARTED) if i != 7], [n for n in range(15) if n != 7]),
        ([(0, 0.0), (0, 0.1), (0, 0.2), (0, 0.3)], [0, 1, 2, 3]),
        ([(0, 0.0), (1, 0.1), (2, 0.2), (0, 0.15), (1, 0.25)], [0, 1, 2, 3, 4]),
    ],
    ids=[
        "restarts",
        "a restart's first line lost",
        "a restart's second line lost",
        "a lost line",
        "a graph per frame",
        "times stepping back at a rebuild",
    ],
)
def test_frames_keep_their_own_times_across_graph_rebuilds(finishes_within, lines, kept):
    count = max(kept) + 1
    got = finishes_within(20, lambda: _timed_frames(_lines(*lines), count))
    times = [round(time, 6) for _, time in lines]
    assert got == [(times[at], bytes([n]) * 3) for at, n in enumerate(kept)]


@pytest.mark.parametrize("frame_bytes", [3, 1 << 20], ids=["small frames", "frames that fill the pipe"])
def test_no_times_at_all_never_blocks_stdout(finishes_within, monkeypatch, caplog, frame_bytes):
    monkeypatch.setattr(_ffmpeg, "TIME_WAIT_SECONDS", 0.5, raising=False)
    script = f"import sys\nfor i in range(8): sys.stdout.buffer.write(bytes([i]) * {frame_bytes})\n"
    with caplog.at_level(logging.WARNING, logger="sashimi"):
        assert finishes_within(30, lambda: _streamed(script, frame_bytes)) == []
    assert "ffmpeg didn't report the time of 8 of 8 frames" in caplog.text


def test_times_that_come_only_after_the_frames_never_block_stdout(finishes_within, monkeypatch):
    monkeypatch.setattr(_ffmpeg, "TIME_WAIT_SECONDS", 0.5, raising=False)
    size = 1 << 20
    script = (
        "import sys\n"
        f"for i in range(6): sys.stdout.buffer.write(bytes([i]) * {size})\n"
        "sys.stdout.flush()\n"
        f"sys.stderr.buffer.write({_lines(*((n, 0.1 * n) for n in range(6)))!r})\n"
    )
    got = finishes_within(30, lambda: _streamed(script, size))
    assert all(frame == bytes([round(time * 10)]) * size for time, frame in got)


@pytest.mark.parametrize(
    "lines, pairs",
    [
        ([(0, 0.0), (1, 0.1), (3, 0.3), (2, 0.2), (4, 0.4)], [(0.0, 0), (0.1, 1), (0.3, 3)]),
        ([(0, 0.0), (1, 0.1), (1, 0.1), (2, 0.2)], [(0.0, 0), (0.1, 1), (0.1, 3), (0.2, 4)]),
        ([(5, 0.5), (4, 0.4), (3, 0.3), (2, 0.2), (1, 0.1)], [(0.5, 5)]),
        ([(n, 0.1 * n) for n in range(0, 40, 7)], [(0.0, 0)]),
    ],
    ids=["out of order", "a repeat", "counting down", "most lines lost"],
)
def test_frame_lines_ffmpeg_never_prints_still_let_the_run_finish(finishes_within, lines, pairs):
    got = finishes_within(20, lambda: _timed_frames(_lines(*lines), 6))
    assert [(time, frame[0]) for time, frame in got] == pairs
    assert all(time <= 0.1 * frame[0] + 1e-9 for time, frame in got), "a frame took a later frame's time"


@pytest.mark.parametrize(
    "value, open_ended",
    [
        ("999999999%", ["999999999%"]),
        ("97.000%", ["97.000%"]),
        ("5", ["5"]),
        ("%", ["%"]),
        ("1%2,30%", ["30%"]),
        ("%+#1", []),
        ("%12.5", []),
        ("3%+2", []),
        ("999999999%99999999999", []),
    ],
)
def test_an_interval_with_no_end_is_recognised(value, open_ended):
    assert open_ended_intervals(["ffprobe", "-read_intervals", value, "file:x"]) == open_ended


def test_running_an_interval_with_no_end_fails_the_test(every_read_interval_has_an_end):
    from sashimi.core import _ffmpeg

    _ffmpeg._check(["ffprobe", "-read_intervals", "5%", "file:x"])
    assert every_read_interval_has_an_end == ["5%"]
    every_read_interval_has_an_end.clear()


def test_every_interval_sashimi_reads_to_the_end_has_one():
    from sashimi.core._ffmpeg import NO_END, read_from

    assert read_from("999999999") == ["-read_intervals", "999999999%99999999999"]
    assert not open_ended_intervals(read_from("0.000"))
    assert int(NO_END) * 1_000_000 < 2**63 - 1
