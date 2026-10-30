# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Locating and running ffmpeg/ffprobe, with progress reporting and bounded threading."""

import collections
import errno
import os
import queue
import re
import shlex
import shutil
import subprocess
import threading
from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass
from typing import IO, Literal

from sashimi.errors import BinaryNotFoundError, FFmpegPathError, ProcessingError

ProgressCallback = Callable[[float, str], None]

_configured: dict[str, str | None] = {"ffmpeg": None, "ffprobe": None, "origin": None}

KeptSetting = tuple[str, str] | tuple[str, str, str]
"""A kept ``ffmpeg.path``: (path, where it is set), and optionally how to change it, for messages."""
_settings: list[Callable[[], KeptSetting | None]] = []
"""A lookup of a kept setting (the CLI's ``ffmpeg.path``), consulted after the environment variable."""

ENVIRONMENT = "SASHIMI_FFMPEG"
"""The environment variable that points sashimi at an FFmpeg: its ffmpeg executable (on Windows, ``.exe`` may be left off), or the folder holding ffmpeg and ffprobe."""

VIDEO_CONTAINERS = frozenset(
    {"matroska", "webm", "mov", "mp4", "m4a", "3gp", "3g2", "mj2", "avi", "asf", "mpeg", "mpegts", "flv", "gif"}
    | {"ogg", "yuv4mpegpipe"}
)
AUDIO_CONTAINERS = frozenset(
    {"mp3", "wav", "ogg", "flac", "aac", "matroska", "webm", "mov", "mp4", "m4a"}
    | {"aiff", "ac3", "eac3", "dts", "amr", "amrnb", "amrwb", "caf", "au", "wv", "asf"}
)

INPUT_PROTOCOLS = [
    "-protocol_whitelist",
    "file",
    "-format_whitelist",
    ",".join(sorted(VIDEO_CONTAINERS | AUDIO_CONTAINERS)),
]


def set_ffmpeg_paths(ffmpeg: str | None = None, ffprobe: str | None = None, *, origin: str | None = None) -> None:
    """
    Sets the ffmpeg and ffprobe executables sashimi runs, as a pair: given only one, the other is
    the one beside it (``ffprobe`` next to ``/opt/ffmpeg/bin/ffmpeg``); `ffmpeg` may also be the
    folder holding both. On Windows, ``C:\\ffmpeg\\bin\\ffmpeg`` means ``ffmpeg.exe`` when only that
    exists, as at a command prompt. Nothing is checked until sashimi first needs FFmpeg, which raises
    :class:`~sashimi.errors.FFmpegPathError` when either is missing or isn't FFmpeg's. `origin` says where the
    choice came from, for messages (default ``sashimi.set_ffmpeg_paths()``). With neither, sashimi
    goes back to the :data:`ENVIRONMENT` variable, else PATH. Useful when an app bundles its own
    ffmpeg; an explicit choice always wins, even an FFmpeg sashimi isn't tested with.
    """
    _configured["ffmpeg"] = ffmpeg
    _configured["ffprobe"] = ffprobe
    _configured["origin"] = (origin or "sashimi.set_ffmpeg_paths()") if ffmpeg or ffprobe else None


@dataclass(frozen=True)
class FFmpegPaths:
    """The ffmpeg and ffprobe sashimi runs, chosen together, and where the choice came from."""

    ffmpeg: str | None
    ffprobe: str | None
    origin: str
    """``PATH``, ``the SASHIMI_FFMPEG environment variable``, a sashimi.toml, or ``sashimi.set_ffmpeg_paths()``."""
    fix: str | None = None
    """How to change a kept setting, when its lookup says (the CLI's ``sashimi config set ffmpeg.path …``)."""
    folder: str | None = None
    """The folder that was given instead of an ffmpeg, when one was: both executables are looked for in it."""


def _executable(path: str) -> str:
    """
    `path`, or on Windows ``<path>.exe`` when `path` doesn't exist and that does, as at a command
    prompt (``C:\\ffmpeg\\bin\\ffmpeg`` runs ``ffmpeg.exe``); a path that exists, or ends in
    ``.exe``, is kept as given.
    """
    if os.name != "nt" or path.lower().endswith(".exe") or os.path.lexists(path):
        return path
    return path + ".exe" if os.path.lexists(path + ".exe") else path


def _beside(path: str, name: str) -> str:
    """The executable `name` in `path`'s folder, with the same ``.exe`` ending (on Windows, ``.exe`` when only that exists)."""
    suffix = ".exe" if path.lower().endswith(".exe") else ""
    return _executable(os.path.join(os.path.dirname(path), name + suffix))


def _pair(path: str, origin: str, fix: str | None = None) -> FFmpegPaths:
    """The pair for a chosen path: an ffmpeg and the ffprobe beside it, or a folder holding both."""
    if os.path.isdir(path):
        suffix = ".exe" if os.name == "nt" else ""
        return FFmpegPaths(
            os.path.join(path, "ffmpeg" + suffix), os.path.join(path, "ffprobe" + suffix), origin, fix, path
        )
    ffmpeg = _executable(path)
    return FFmpegPaths(ffmpeg, _beside(ffmpeg, "ffprobe"), origin, fix)


def ffmpeg_paths() -> FFmpegPaths:
    """
    The pair sashimi runs: the one :func:`set_ffmpeg_paths` set; else :data:`ENVIRONMENT`; else a
    kept setting (:func:`use_setting`, the CLI's ``ffmpeg.path``); else the ffmpeg on PATH with
    the ffprobe beside it (the ffprobe on PATH when there is none there). Each of the first three
    may name an ffmpeg executable (ffprobe is the one beside it) or the folder holding both.
    On Windows, a path that doesn't exist means ``<path>.exe`` when that does (``C:\\ffmpeg\\bin\\ffmpeg``
    is ``ffmpeg.exe``, with ``ffprobe.exe`` beside it). Missing executables stay named as given, so
    the error that follows says what was looked for.
    """
    if _configured["ffmpeg"] or _configured["ffprobe"]:
        origin = _configured["origin"] or "sashimi.set_ffmpeg_paths()"
        if _configured["ffmpeg"] and not _configured["ffprobe"]:
            return _pair(str(_configured["ffmpeg"]), origin)
        ffprobe = _executable(str(_configured["ffprobe"]))
        ffmpeg = _executable(_configured["ffmpeg"]) if _configured["ffmpeg"] else _beside(ffprobe, "ffmpeg")
        return FFmpegPaths(ffmpeg, ffprobe, origin)
    chosen = os.environ.get(ENVIRONMENT, "").strip()
    if chosen:
        return _pair(chosen, f"the {ENVIRONMENT} environment variable")
    kept = _settings[0]() if _settings else None
    if kept is not None:
        return _pair(kept[0], kept[1], kept[2] if len(kept) > 2 else None)
    found = shutil.which("ffmpeg")
    beside = _beside(found, "ffprobe") if found else None
    probe = beside if beside and shutil.which(beside) else shutil.which("ffprobe")
    return FFmpegPaths(found, probe, "PATH")


def use_setting(lookup: Callable[[], KeptSetting | None] | None) -> None:
    """
    Consults `lookup` (a kept ``ffmpeg.path``, where it is set, and optionally how to change it)
    after :data:`ENVIRONMENT` and before PATH; None stops.
    """
    _settings.clear()
    if lookup is not None:
        _settings.append(lookup)


INSTALL_ADVICE = (
    "Install FFmpeg (8.1 or 9.0), or point sashimi at one: set SASHIMI_FFMPEG to its ffmpeg, run sashimi config "
    "set ffmpeg.path <its ffmpeg>, or call sashimi.set_ffmpeg_paths()."
)
"""What to do when no FFmpeg is found, or the one found on PATH can't be used."""


def path_fix(paths: FFmpegPaths) -> str:
    """How to fix the choice of FFmpeg in `paths`, for messages: the same advice for the same choice, wherever it is given."""
    if paths.origin == "PATH":
        return INSTALL_ADVICE
    if paths.origin == f"the {ENVIRONMENT} environment variable":
        return (
            f"Set {ENVIRONMENT} to an FFmpeg's ffmpeg (or the folder holding ffmpeg and ffprobe), or unset it so "
            "sashimi uses ffmpeg.path from sashimi.toml, else the FFmpeg on PATH."
        )
    if paths.fix:
        return paths.fix
    if paths.origin.startswith("ffmpeg.path"):
        return "Point ffmpeg.path at an FFmpeg's ffmpeg, or remove it so sashimi uses the FFmpeg on PATH."
    return (
        f"Pass {paths.origin} an FFmpeg's ffmpeg (or the folder holding ffmpeg and ffprobe), or call "
        f"sashimi.set_ffmpeg_paths() with nothing so sashimi uses {ENVIRONMENT}, ffmpeg.path, else the FFmpeg on PATH."
    )


_problems: dict[tuple[str, str, int, int], tuple[str, str] | None] = {}
_problems_lock = threading.Lock()


def _stat_key(name: str, path: str) -> tuple[str, str, int, int]:
    try:
        found = os.stat(path)
    except OSError:
        return (name, path, -1, -1)
    return (name, path, found.st_size, found.st_mtime_ns)


def _banner(path: str, name: str) -> str:
    """The first line ``<path> -version`` prints (kept for ffmpeg's capability checks too); raises OSError when it can't start."""
    result = subprocess.run([path, "-hide_banner", "-version"], capture_output=True, text=True, timeout=30, check=False)
    from sashimi.core._capabilities import remember_listing

    if name == "ffmpeg":
        remember_listing(path, "-version", result.stdout)
    lines = result.stdout.strip().splitlines()
    return lines[0].strip() if lines else ""


def _check_executable(name: str, path: str) -> tuple[str, str] | None:
    """What's wrong with `path` as FFmpeg's `name` (``problem``, the reason in words), or None; checked once per executable."""
    key = _stat_key(name, path)
    with _problems_lock:
        if key in _problems:
            return _problems[key]
    problem: tuple[str, str] | None = None
    if not os.path.lexists(path):
        problem = ("missing", "doesn't exist")
    elif os.path.isdir(path):
        problem = ("folder", "is a folder, not an executable")
    elif not os.path.exists(path):
        problem = ("missing", "is a link to something that doesn't exist")
    elif not os.access(path, os.X_OK):
        problem = ("not_executable", "isn't executable")
    else:
        try:
            first = _banner(path, name)
        except subprocess.TimeoutExpired:
            first = ""
        except OSError as e:
            problem = ("cannot_run", f"can't be run ({e.strerror or e})")
        else:
            if not first.lower().startswith(f"{name} version"):
                said = f"printed {first[:80]!r}" if first else "printed nothing"
                problem = ("not_ffmpeg", f"isn't FFmpeg's {name}: running it with -version {said}")
    with _problems_lock:
        _problems[key] = problem
    return problem


def _which_path(paths: FFmpegPaths, name: str, path: str) -> str:
    """How messages name `path`, FFmpeg's `name`, and where it came from."""
    if paths.origin == "PATH":
        return f"The {name} found on PATH ({path})"
    if paths.folder is not None:
        return f"{path}, the {name} in the folder {paths.folder} from {paths.origin},"
    if name == "ffprobe" and paths.ffmpeg and path == _beside(paths.ffmpeg, "ffprobe"):
        return f"{path}, the ffprobe beside the ffmpeg from {paths.origin} ({paths.ffmpeg}),"
    return f"{path}, from {paths.origin},"


def path_error(name: str, paths: FFmpegPaths | None = None, problem: tuple[str, str] | None = None) -> FFmpegPathError:
    """The error for FFmpeg's `name` in `paths` (default: the pair sashimi runs), with `problem` (default: as checked now)."""
    paths = paths or ffmpeg_paths()
    path = paths.ffmpeg if name == "ffmpeg" else paths.ffprobe
    if not path:
        return FFmpegPathError(
            f"No {name} was found on PATH. {INSTALL_ADVICE}",
            name=name,
            path=None,
            origin=paths.origin,
            problem="not_found",
        )
    problem = problem or _check_executable(name, path) or ("cannot_run", "can't be run")
    sashimi_needs = " sashimi needs both ffmpeg and ffprobe." if name == "ffprobe" else ""
    return FFmpegPathError(
        f"{_which_path(paths, name, path)} {problem[1]}.{sashimi_needs} {path_fix(paths)}",
        name=name,
        path=path,
        origin=paths.origin,
        problem=problem[0],
    )


def checked_paths() -> FFmpegPaths:
    """The pair sashimi runs (:func:`ffmpeg_paths`), each checked to exist and run as FFmpeg's; raises FFmpegPathError otherwise."""
    paths = ffmpeg_paths()
    for name in ("ffmpeg", "ffprobe"):
        path = paths.ffmpeg if name == "ffmpeg" else paths.ffprobe
        if not path:
            raise path_error(name, paths)
        problem = _check_executable(name, path)
        if problem is not None:
            raise path_error(name, paths, problem)
    return paths


def _binary(name: str) -> str:
    paths = checked_paths()
    found = paths.ffmpeg if name == "ffmpeg" else paths.ffprobe
    assert found is not None
    return found


def _start_error(executable: str, error: OSError) -> BinaryNotFoundError:
    """The error for an executable that wouldn't start: named as the FFmpeg sashimi was pointed at when it is one of them."""
    paths = ffmpeg_paths()
    for name, path in (("ffmpeg", paths.ffmpeg), ("ffprobe", paths.ffprobe)):
        if path == executable:
            with _problems_lock:
                _problems.pop(_stat_key(name, executable), None)
            return path_error(
                name,
                paths,
                _check_executable(name, executable) or ("cannot_run", f"can't be run ({error.strerror or error})"),
            )
    return BinaryNotFoundError(f"Cannot run '{executable}': {error}")


def parse_ffmpeg_version(banner: str) -> tuple[int, int] | None:
    """
    The release series (major, minor) in the first line of ``ffmpeg -version``: ``ffmpeg version
    n8.1.3-24-g92611900bb`` and ``8.0.1-3ubuntu1`` give (8, 1) and (8, 0). None for a build from
    the development branch (``N-121234-g…``), which is newer than any release. Pure.
    """
    match = re.match(r"\S+ version n?(\d+)\.(\d+)", banner.strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def ffmpeg_version(binary: str | None = None) -> tuple[int, int] | None:
    """
    The release series of the ffmpeg sashimi runs (or `binary`), read once per executable; None
    when it can't be told (a development build, or ``-version`` failing).
    """
    from sashimi.core._capabilities import version_of

    return version_of(binary or ffmpeg_binary())[1]


def ffmpeg_binary() -> str:
    """The ffmpeg executable sashimi runs (see :func:`ffmpeg_paths`)."""
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


_CLOSED_PIPE_ERRNOS = (errno.EPIPE, errno.EINVAL)


def _feed(stream: IO[bytes], chunks: Iterable[bytes], errors: list[BaseException]) -> None:
    try:
        for chunk in chunks:
            stream.write(chunk)
    except OSError as e:
        if e.errno not in _CLOSED_PIPE_ERRNOS:
            errors.append(e)
    except BaseException as e:
        errors.append(e)
    finally:
        try:
            stream.close()
        except OSError as e:
            if e.errno not in _CLOSED_PIPE_ERRNOS:
                errors.append(e)


def stream_ffmpeg(cmd: list[str], chunk_size: int) -> Generator[bytes, None, None]:
    """
    Runs an ffmpeg command and yields its stdout in chunks of `chunk_size` bytes (the last may be
    shorter), so a large output never sits in memory. Raises ProcessingError after the last chunk
    if ffmpeg failed, and BinaryNotFoundError if it can't be started. Closing the generator early
    kills the process.
    """
    _check(cmd)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        raise _start_error(cmd[0], e) from e
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


def _check(cmd: list[str]) -> None:
    """Refuses a command whose ffmpeg lacks an encoder or filter it uses (see :func:`~sashimi.core._capabilities.check_command`)."""
    from sashimi.core._capabilities import check_command

    check_command(cmd)


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
    _check(cmd)
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
        raise _start_error(cmd[0], e) from e

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


def _frame_times(stream: IO[bytes], times: "queue.Queue[float | None]", sink: collections.deque) -> None:
    try:
        for line in iter(stream.readline, b""):
            if frame := _FRAME_LINE.fullmatch(line.strip()):
                times.put(float(frame.group(1)))
            elif line.strip() != _FRAME_KEY_LINE:
                sink.append(line)
    finally:
        times.put(None)


def stream_timed_frames(cmd: list[str], frame_bytes: int) -> Generator[tuple[float, bytes], None, None]:
    """
    Runs an ffmpeg command whose graph ends in :data:`FRAME_PROGRESS` and writes raw frames of
    `frame_bytes` to stdout, and yields each frame with its time (seconds, as the filter graph
    saw it). Raises ProcessingError after the last frame if ffmpeg failed, and BinaryNotFoundError
    if it can't be started. Closing the generator early kills the process.
    """
    _check(cmd)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        raise _start_error(cmd[0], e) from e
    assert proc.stdout is not None and proc.stderr is not None
    stderr_tail: collections.deque = collections.deque(maxlen=40)
    times: queue.Queue[float | None] = queue.Queue()
    reader = threading.Thread(target=_frame_times, args=(proc.stderr, times, stderr_tail), daemon=True)
    reader.start()
    try:
        while len(frame := proc.stdout.read(frame_bytes)) == frame_bytes:
            time = times.get()
            if time is None:
                break
            yield time, frame
        proc.stdout.read()
        proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        reader.join(timeout=5)
        proc.stdout.close()
        proc.stderr.close()
    if proc.returncode != 0:
        tail = b"".join(stderr_tail).decode("utf-8", "replace").strip()
        raise ProcessingError(
            f"{os.path.basename(cmd[0])} exited with code {proc.returncode}: {tail}\ncommand: {shlex.join(cmd)}"
        )
