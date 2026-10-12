"""
Exceptions raised by sashimi.

Callers should branch on the exception *type*, never on message text:

- :class:`UserInputError` (and subclasses) means the input file or the options are the problem.
  Its ``user_message`` is one plain sentence that is safe to show to an end user: it never
  contains file paths, commands or tracebacks.
- :class:`ProcessingError` means something went wrong internally (ffmpeg crashed, a binary is
  missing, the disk is full). Its message is meant for logs and may contain paths and ffmpeg output.
"""

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
]


class SashimiError(Exception):
    """Base class for every error sashimi raises on purpose."""


class UserInputError(SashimiError):
    """The input file or the requested options can't be processed; the user can fix this."""

    def __init__(self, user_message: str, detail: str | None = None):
        super().__init__(detail or user_message)
        self.user_message = user_message
        self.detail = detail


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
    """ffmpeg or ffprobe could not be found."""
