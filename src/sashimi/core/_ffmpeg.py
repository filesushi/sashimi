# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Locating and running ffmpeg/ffprobe, with progress reporting and bounded threading."""

import collections
import os
import re
import shlex
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterable, Iterator
from typing import IO, Literal

from sashimi.errors import BinaryNotFoundError, ProcessingError

ProgressCallback = Callable[[float, str], None]

_configured: dict[str, str | None] = {"ffmpeg": None, "ffprobe": None}

VIDEO_CONTAINERS = frozenset(
    {"matroska", "webm", "mov", "mp4", "m4a", "3gp", "3g2", "mj2", "avi", "asf", "mpeg", "mpegts", "flv", "gif"}
)
AUDIO_CONTAINERS = frozenset({"mp3", "wav", "ogg", "flac", "aac", "matroska", "webm", "mov", "mp4", "m4a"})

INPUT_PROTOCOLS = [
    "-protocol_whitelist",
    "file",
    "-format_whitelist",
    ",".join(sorted(VIDEO_CONTAINERS | AUDIO_CONTAINERS)),
]


def set_ffmpeg_paths(ffmpeg: str | None = None, ffprobe: str | None = None) -> None:
    """
    Sets the ffmpeg and ffprobe executables sashimi runs. ``None`` means "look up on PATH",
    which is the default. Useful when an app bundles its own ffmpeg.
    """
    _configured["ffmpeg"] = ffmpeg
    _configured["ffprobe"] = ffprobe


def _binary(name: str) -> str:
    configured = _configured[name]
    if configured:
        return configured
    found = shutil.which(name)
    if not found:
        raise BinaryNotFoundError(
            f"'{name}' was not found on PATH. Install FFmpeg, or call sashimi.set_ffmpeg_paths()."
        )
    return found


def ffmpeg_binary() -> str:
    return _binary("ffmpeg")


def ffprobe_binary() -> str:
    return _binary("ffprobe")


def file_arg(path: str | os.PathLike[str]) -> str:
    """Forces the file protocol, so names such as 'http:x' or '-y' can't be misread by ffmpeg."""
    return "file:" + os.fspath(path)


def fmt_seconds(seconds: float) -> str:
    """Formats seconds for ffmpeg without exponent notation: 12.5 -> '12.5', 10.0 -> '10'."""
    text = f"{seconds:.3f}".rstrip("0").rstrip(".")
    return text or "0"


OUTPUT_FLAGS = ["-fflags", "+bitexact"]


def base_args(threads: int) -> list[str]:
    """
    Global options for every ffmpeg run. `-filter_threads` bounds filtergraph threads, which
    otherwise default to one per host CPU (12+ in a container that can only use 2).
    """
    return [
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-filter_threads",
        str(threads),
    ]


FRAME_PROGRESS_KEY = "sashimi_progress"
FRAME_PROGRESS = (
    f"metadata=mode=add:key={FRAME_PROGRESS_KEY}:value=1,"
    f"metadata=mode=print:key={FRAME_PROGRESS_KEY}:file=pipe\\\\:2:direct=1"
)
"""
Filters that print each frame's timestamp to stderr as it passes, for progress where
``-progress`` has nothing to report (an output that writes only at the end, such as
``palettegen``'s). Frames are otherwise unchanged.
"""
_FRAME_LINE = re.compile(rb"frame:\s*\d+\s+pts:\s*\S+\s+pts_time:\s*(-?[0-9.]+)\s*")
_FRAME_KEY_LINE = f"{FRAME_PROGRESS_KEY}=1".encode()


def _drain(stream: IO[bytes], sink: collections.deque) -> None:
    for line in iter(stream.readline, b""):
        sink.append(line)


def _read_all(stream: IO[bytes], parts: list[bytes]) -> None:
    parts.append(stream.read())


def _report_progress(raw: bytes, on_progress: ProgressCallback, total: float, stage: str) -> None:
    key, _, value = raw.decode("ascii", "replace").strip().partition("=")
    if key == "out_time_us" and value.lstrip("-").isdigit():
        done = max(0, int(value)) / 1_000_000
        on_progress(min(1.0, done / total), stage)


def _feed(stream: IO[bytes], chunks: Iterable[bytes], errors: list[BaseException]) -> None:
    try:
        for chunk in chunks:
            stream.write(chunk)
    except BrokenPipeError:
        pass
    except BaseException as e:
        errors.append(e)
    finally:
        try:
            stream.close()
        except BrokenPipeError:
            pass


def stream_ffmpeg(cmd: list[str], chunk_size: int) -> Iterator[bytes]:
    """
    Runs an ffmpeg command and yields its stdout in chunks of `chunk_size` bytes (the last may be
    shorter), so a large output never sits in memory. Raises ProcessingError after the last chunk
    if ffmpeg failed, and BinaryNotFoundError if it can't be started. Closing the generator early
    kills the process.
    """
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        raise BinaryNotFoundError(f"Cannot run {cmd[0]!r}: {e}") from e
    assert proc.stdout is not None and proc.stderr is not None
    stderr_tail: collections.deque = collections.deque(maxlen=40)
    drain = threading.Thread(target=_drain, args=(proc.stderr, stderr_tail), daemon=True)
    drain.start()
    try:
        while chunk := proc.stdout.read(chunk_size):
            yield chunk
        proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        drain.join(timeout=5)
        proc.stdout.close()
        proc.stderr.close()
    if proc.returncode != 0:
        tail = b"".join(stderr_tail).decode("utf-8", "replace").strip()
        raise ProcessingError(
            f"{os.path.basename(cmd[0])} exited with code {proc.returncode}: {tail}\ncommand: {shlex.join(cmd)}"
        )


def run_ffmpeg(
    cmd: list[str],
    *,
    stdin_chunks: Iterable[bytes] | None = None,
    capture_stdout: bool = False,
    progress_total: float | None = None,
    on_progress: ProgressCallback | None = None,
    stage: str = "encoding",
) -> bytes:
    """
    Runs an ffmpeg (or ffprobe) command and returns its stdout if `capture_stdout`.

    With `on_progress` and `progress_total` (seconds of media the command will produce), the command
    must include ``-progress pipe:1 -nostats``; with `capture_stdout` (stdout is then the
    output), its graph must end in :data:`FRAME_PROGRESS` instead, which reports each frame's
    time on stderr. Progress is reported as fractions, on the calling thread.
    Raises ProcessingError on a non-zero exit, with the tail of stderr for the logs, and
    BinaryNotFoundError if the executable is missing or can't be started.
    """
    report_progress = on_progress is not None and bool(progress_total)
    progress_on_stderr = report_progress and capture_stdout
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if stdin_chunks is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE if (capture_stdout or report_progress) else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except OSError as e:
        raise BinaryNotFoundError(f"Cannot run {cmd[0]!r}: {e}") from e

    stderr_tail: collections.deque = collections.deque(maxlen=40)
    stdout_parts: list[bytes] = []
    feed_errors: list[BaseException] = []
    threads = []
    assert proc.stderr is not None
    if progress_on_stderr:
        assert proc.stdout is not None
        threads.append(threading.Thread(target=_read_all, args=(proc.stdout, stdout_parts), daemon=True))
    else:
        threads.append(threading.Thread(target=_drain, args=(proc.stderr, stderr_tail), daemon=True))
    if stdin_chunks is not None:
        assert proc.stdin is not None
        threads.append(threading.Thread(target=_feed, args=(proc.stdin, stdin_chunks, feed_errors), daemon=True))
    for t in threads:
        t.start()

    stdout = b""
    try:
        if progress_on_stderr:
            assert on_progress is not None and progress_total
            for raw in proc.stderr:
                if frame := _FRAME_LINE.fullmatch(raw.strip()):
                    on_progress(min(1.0, max(0.0, float(frame.group(1))) / progress_total), stage)
                elif raw.strip() != _FRAME_KEY_LINE:
                    stderr_tail.append(raw)
        elif capture_stdout:
            assert proc.stdout is not None
            stdout = proc.stdout.read()
        elif report_progress:
            assert proc.stdout is not None and on_progress is not None and progress_total
            for raw in proc.stdout:
                _report_progress(raw, on_progress, progress_total, stage)
        proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        for t in threads:
            t.join(timeout=5)

    if progress_on_stderr:
        stdout = b"".join(stdout_parts)
    if feed_errors:
        raise feed_errors[0]
    if proc.returncode != 0:
        tail = b"".join(stderr_tail).decode("utf-8", "replace").strip()
        raise ProcessingError(
            f"{os.path.basename(cmd[0])} exited with code {proc.returncode}: {tail}\ncommand: {shlex.join(cmd)}"
        )
    return stdout


def holds_packets(path: str | os.PathLike[str], kind: Literal["video", "audio"]) -> bool:
    """
    Whether the file's first `kind` stream (``"video"`` or ``"audio"``) holds at least one
    packet. ffprobe reads only up to that packet. False when the file can't be read.
    """
    cmd = [ffprobe_binary(), "-hide_banner", "-loglevel", "error", *INPUT_PROTOCOLS]
    cmd += ["-select_streams", f"{kind[0]}:0", "-read_intervals", "%+#1", "-show_entries", "packet=stream_index"]
    cmd += ["-of", "csv=p=0", file_arg(path)]
    try:
        return bool(run_ffmpeg(cmd, capture_stdout=True).strip())
    except BinaryNotFoundError:
        raise
    except ProcessingError:
        return False
