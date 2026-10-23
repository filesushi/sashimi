# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import os
import sys

import pytest

from sashimi.core import _resources
from sashimi.core._atomic import atomic_output
from sashimi.core._ffmpeg import file_arg, fmt_seconds, run_ffmpeg
from sashimi.errors import ProcessingError


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
