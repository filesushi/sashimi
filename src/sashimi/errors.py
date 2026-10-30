# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Exceptions raised by sashimi.

Callers should branch on the exception *type*, never on message text:

- :class:`UserInputError` (and subclasses) means the input file or the options are the problem.
  Its ``user_message`` is one plain sentence that is safe to show to an end user: it never
  contains file paths, commands or tracebacks.
- :class:`ProcessingError` means something went wrong internally (ffmpeg crashed, a binary is
  missing, the disk is full). Its message is meant for logs and may contain paths and ffmpeg output.
"""

from collections.abc import Sequence
from typing import Any

__all__ = [
    "SashimiError",
    "UserInputError",
    "UnsupportedMediaError",
    "NoAudioTrackError",
    "MediaTooLongError",
    "MediaTooShortError",
    "MediaTooLargeError",
    "InvalidOptionsError",
    "ProcessingError",
    "BinaryNotFoundError",
    "FFmpegPathError",
    "UnsuitableFFmpegError",
    "MissingEncodersError",
    "MissingFiltersError",
    "FFmpegTooOldError",
]


class SashimiError(Exception):
    """Base class for every error sashimi raises on purpose."""


class UserInputError(SashimiError):
    """
    The input file or the requested options can't be processed; the user can fix this.

    ``limit`` names the :class:`~sashimi.config.Limits` field that refused the file or the options
    (``"max_pixels"``, ``"max_video_seconds"``, …), so an app can offer to raise it; it is ``None``
    for every other refusal.
    """

    def __init__(self, user_message: str, detail: str | None = None, *, limit: str | None = None):
        super().__init__(detail or user_message)
        self.user_message = user_message
        self.detail = detail
        self.limit = limit


class UnsupportedMediaError(UserInputError):
    """The input isn't a kind of file this operation can read (wrong type, corrupt, or no usable stream)."""


class NoAudioTrackError(UnsupportedMediaError):
    """Audio was requested from a video that has no audio track."""


class MediaTooLongError(UserInputError):
    """The input (or the requested part of it) is longer than the operation allows."""


class MediaTooShortError(UserInputError):
    """The input is too short for the operation (e.g. a contact sheet of a one-second clip)."""


class MediaTooLargeError(UserInputError):
    """The input's dimensions or the requested output size are too large to process safely."""


class InvalidOptionsError(UserInputError):
    """The options are invalid, or don't fit this input (e.g. a start time past the end)."""


class ProcessingError(SashimiError):
    """An internal failure. The message is for logs, not for end users."""


class BinaryNotFoundError(ProcessingError):
    """ffmpeg or ffprobe could not be found or started. Operations that wrap probing re-raise it unchanged."""


class FFmpegPathError(BinaryNotFoundError):
    """
    sashimi can't run the FFmpeg it was pointed at, or found none: ``problem`` is ``not_found``
    (no ffmpeg or ffprobe on PATH), ``missing`` (the path doesn't exist), ``folder`` (it is a
    folder), ``not_executable``, ``cannot_run`` (the system refused to start it) or
    ``not_ffmpeg`` (it runs, but isn't FFmpeg's ffmpeg or ffprobe). ``name`` is ``ffmpeg`` or
    ``ffprobe``, ``path`` the path (None for ``not_found``), and ``origin`` where it was chosen
    (``PATH``, ``the SASHIMI_FFMPEG environment variable``, ``ffmpeg.path in <its sashimi.toml>``,
    ``sashimi.set_ffmpeg_paths()``). The message says all of this and how to fix it, for people
    running sashimi, not for a website's visitors.
    """

    def __init__(self, message: str, *, name: str, path: str | None, origin: str, problem: str) -> None:
        super().__init__(message)
        self.name = name
        self.path = path
        self.origin = origin
        self.problem = problem


class UnsuitableFFmpegError(BinaryNotFoundError):
    """
    The FFmpeg sashimi runs can't do this task: it lacks an encoder or filter the task needs, or
    its release is known to get the task wrong. Nothing is written. The message names what's
    missing, the FFmpeg used (version, path and where it was chosen) and how to point sashimi at
    another; ``missing`` lists the encoders or filters, ``task`` the task an old release gets
    wrong, and ``info`` describes the FFmpeg (a :class:`sashimi.FFmpegInfo`).
    """

    def __init__(self, message: str, *, missing: Sequence[str] = (), task: str | None = None, info: Any = None) -> None:
        super().__init__(message)
        self.missing = list(missing)
        self.task = task
        self.info = info


class MissingEncodersError(UnsuitableFFmpegError):
    """The FFmpeg sashimi runs lacks an encoder the task needs (``libx264`` for MP4, ``libvpx-vp9`` for WebM, …)."""


class MissingFiltersError(UnsuitableFFmpegError):
    """
    The FFmpeg sashimi runs lacks a filter the task needs: tone-mapping HDR video needs
    ``zscale`` (an FFmpeg built with libzimg), ``tonemap`` and ``sidedata``; GIFs need ``palettegen``.
    """


class FFmpegTooOldError(UnsuitableFFmpegError):
    """The FFmpeg sashimi runs is from a release known to get this task wrong (lossless cuts and start times before 8.1)."""
