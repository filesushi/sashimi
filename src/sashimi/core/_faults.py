# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Whose fault an exception is. What a caller's progress callback raises is the caller's, and crosses
every handler in sashimi unchanged; what reading a file's bytes raises is the file's, as long as
Pillow reads a sound file of the format; anything else is a fault of the code, sashimi's or a
library's.
"""

import functools
import io
import re
import struct
import sys
import warnings
import zlib
from collections.abc import Callable
from typing import Any

from PIL import Image


class _CallerProgress:
    """A progress callback, recognisable in a traceback by this class's :meth:`__call__`."""

    def __init__(self, report: Callable[..., None]):
        self.report = report

    def __call__(self, *arguments: Any) -> None:
        self.report(*arguments)


def caller_progress(on_progress: Callable[..., None] | None) -> Callable[..., None] | None:
    """`on_progress` in a form :func:`from_caller` can recognise; None stays None."""
    if on_progress is None or isinstance(on_progress, _CallerProgress):
        return on_progress
    return _CallerProgress(on_progress)


def from_caller(error: BaseException) -> bool:
    """Whether `error` was raised by a callback wrapped by :func:`caller_progress`."""
    trace = error.__traceback__
    while trace is not None:
        if trace.tb_frame.f_code is _CallerProgress.__call__.__code__:
            return True
        trace = trace.tb_next
    return False


FILE_FAULTS: tuple[type[Exception], ...] = (
    ArithmeticError,
    BufferError,
    EOFError,
    OSError,
    SyntaxError,
    ValueError,
    struct.error,
    zlib.error,
    Image.DecompressionBombError,
)
"""What sashimi's own parsers raise on bytes they can't read: there, any other exception is a bug."""
_PARSERS = (
    "sashimi.core._heifmeta",
    "sashimi.core._imagemeta",
    "sashimi.core._metaread",
    "sashimi.core._tiffmeta",
    "sashimi.core.metadata",
)
_DECODING_LIBRARIES = ("pillow_heif", "olefile")
"""Libraries sashimi only ever reads files with: whatever they raise, they raise while reading."""
_PILLOW_READS = frozenset(
    {"open", "_open", "_open_core", "load", "load_prepare", "load_read", "load_end", "load_seek", "seek", "_seek"}
)
"""Pillow's functions that read a file's bytes, in :class:`~PIL.Image.Image`, ``ImageFile`` and every format's plugin."""
_NOT_READING = frozenset({"Image.load"})
"""Of those names, the one that touches no file: the pixels of an image in memory."""
_NEVER_THE_FILES: tuple[type[Exception], ...] = (AttributeError, NameError, ImportError, AssertionError)
"""What code raises when it, or a library it calls, has changed: bytes don't rename an attribute."""
_NO_CODEC = re.compile(r"(?:de|en)coder \S+ not available")
"""Pillow's words for a build without a codec."""


def _frames(error: BaseException) -> list[Any]:
    """The code objects `error` passed through, and their modules, from where it was caught, leaving out the standard library's."""
    found = []
    trace = error.__traceback__
    while trace is not None:
        module = trace.tb_frame.f_globals.get("__name__", "")
        if module.partition(".")[0] not in sys.stdlib_module_names:
            found.append((module, trace.tb_frame.f_code))
        trace = trace.tb_next
    return found


def _while_reading(frames: list[Any]) -> bool:
    """Whether Pillow, or a library that only reads, raised while reading a file's bytes, not while working on pixels or writing."""
    if not frames:
        return False
    last = frames[-1][0]
    if last.startswith(_DECODING_LIBRARIES):
        return True
    if not last.startswith("PIL."):
        return False
    return any(
        module.startswith("PIL.") and code.co_name in _PILLOW_READS and code.co_qualname not in _NOT_READING
        for module, code in frames
    )


def file_at_fault(error: BaseException, pil_format: str | None = None) -> bool:
    """
    Whether `error` came from a file's bytes rather than from code. Decided by what was happening,
    not by the exception's class, which is no guide on Pillow's read paths (a damaged AVIF raises
    ``RuntimeError``, a TIFF ``TypeError``, a GIF ``IndexError``):

    - a missing codec, or an exception bytes can't cause (see :data:`_NEVER_THE_FILES`), is never the file's;
    - what Pillow raises while reading is the file's, if Pillow can read a sound file of the
      format, `pil_format` (see :func:`can_read`): a changed Pillow fails on that too;
    - what sashimi's parsers raise is the file's if it's what a parser raises on bad bytes;
    - anything else, such as Pillow's ``convert``, ``resize`` or ``save`` raising, is the code's.
    """
    if isinstance(error, _NEVER_THE_FILES) or _NO_CODEC.search(str(error)):
        return False
    frames = _frames(error)
    if _while_reading(frames):
        return pil_format is None or can_read(pil_format)
    return bool(frames) and isinstance(error, FILE_FAULTS) and frames[-1][0].startswith(_PARSERS)


_SEVERAL_FRAMES = frozenset({"AVIF", "GIF", "MPO", "PNG", "TIFF", "WEBP"})
_NOT_ALWAYS_WRITABLE = frozenset({"HEIF"})
"""A format whose sample may not be possible to make on a sound install: pillow-heif's encoders are optional."""
_PSD_SAMPLE = b"8BPS\0\1" + bytes(6) + struct.pack(">HIIHH", 3, 1, 1, 8, 3) + bytes(12) + b"\0\0" + b"\x40\x80\xc0"
"""One pixel of RGB: Pillow doesn't write PSD."""


def _sample(pil_format: str) -> bytes:
    if pil_format == "PSD":
        return _PSD_SAMPLE
    picture = Image.new("RGB", (16, 16), (40, 90, 160))
    written = io.BytesIO()
    if pil_format in _SEVERAL_FRAMES:
        other = Image.new("RGB", (16, 16), (160, 90, 40))
        picture.save(written, pil_format, save_all=True, append_images=[other])
    else:
        picture.save(written, pil_format)
    return written.getvalue()


@functools.cache
def can_read(pil_format: str) -> bool:
    """
    Whether Pillow reads a small sound file of this format, every frame of it. When it can't, a
    file of the format failing says nothing about the file. Only ever asked once something has
    failed, and remembered for the process.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            sample = _sample(pil_format)
        except Exception:
            return pil_format in _NOT_ALWAYS_WRITABLE
        try:
            with Image.open(io.BytesIO(sample)) as img:
                for frame in range(getattr(img, "n_frames", 1)):
                    img.seek(frame)
                    img.load()
        except Exception:
            return False
    return True


_SIGNATURES = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
    (b"II*\0", "TIFF"),
    (b"MM\0*", "TIFF"),
    (b"8BPS", "PSD"),
    (b"BM", "BMP"),
)


_HEIF_BRANDS = frozenset(
    {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs", b"mif1", b"mif2", b"msf1"}
)
_AVIF_BRANDS = frozenset({b"avif", b"avis"})
_NEUTRAL_BRANDS = frozenset({b"mif1", b"mif2", b"msf1"})


def stated_still_format(head: bytes) -> str | None:
    """
    ``AVIF`` or ``HEIF``, if the ``ftyp`` box a file starts with says so: its major brand does,
    or, under a brand both formats use, its compatible brands say AVIF. Pure.
    """
    if head[4:8] != b"ftyp":
        return None
    major = head[8:12]
    end = min(struct.unpack(">I", head[:4])[0], len(head))
    compatible = {head[at : at + 4] for at in range(16, end - 3, 4)}
    if major in _AVIF_BRANDS or (major in _NEUTRAL_BRANDS and compatible & _AVIF_BRANDS):
        return "AVIF"
    return "HEIF" if major in _HEIF_BRANDS else None


def signature_format(head: bytes) -> str | None:
    """The format the first bytes of a file say it is, for those Pillow always ought to read, and AVIF if this Pillow knows it. Pure."""
    for signature, name in _SIGNATURES:
        if head.startswith(signature):
            return name
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    if head[4:12] in (b"ftypavif", b"ftypavis") and "AVIF" in Image.OPEN:
        return "AVIF"
    if head[4:12] in (b"ftypheic", b"ftypheix", b"ftypheim", b"ftyphevc", b"ftypmif1", b"ftypmsf1"):
        return "HEIF"
    return None
