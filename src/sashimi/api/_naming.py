# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Output name patterns: their syntax and fields, each command's default, how ``{output_ext}`` is
chosen, and the checks every planned name passes. The policy itself is in :mod:`sashimi.api._batch`.
"""

import os
import pathlib
import re
import string
from collections.abc import Callable
from dataclasses import dataclass

from sashimi.errors import InvalidOptionsError

FIELDS = ("input_name", "input_stem", "input_ext", "role", "output_ext")
"""The fields an output name pattern can use, as ``{field}``."""

FIELD_HELP = "{input_name} (clip.mp4), {input_stem} (clip), {input_ext} (mp4), {role} (sheet) and {output_ext} (jpg)"

_RENAMED = {"name": "input_name", "stem": "input_stem", "ext": "input_ext", "format": "output_ext"}

EXTENSIONS: dict[str, tuple[str, ...]] = {
    "jpeg": ("jpg", "jpeg"),
    "png": ("png",),
    "webp": ("webp",),
    "gif": ("gif",),
    "bmp": ("bmp",),
    "tiff": ("tif", "tiff"),
    "avif": ("avif",),
    "apng": ("apng", "png"),
    "ico": ("ico",),
    "zip": ("zip",),
    "mp4": ("mp4", "m4v"),
    "mkv": ("mkv",),
    "webm": ("webm",),
    "mov": ("mov",),
    "mp3": ("mp3",),
    "m4a": ("m4a",),
    "wav": ("wav",),
    "ogg": ("ogg", "opus", "oga"),
    "flac": ("flac",),
}
"""Every extension that names each output format; the first is sashimi's standard one."""
ALSO_NAMED = {"jpeg": ("jfif", "jpe", "jif")}
"""Other extensions of files in a format, which sashimi reads but never writes: a ``.jfif`` is a JPEG."""

ROLES = {
    "resize": "resized",
    "rotate": "rotated",
    "extract": "extracted",
    "edit": "edited",
    "frames": "frames",
    "strip": "stripped",
    "thumbs": "sheet",
    "animated_thumbs": "animated-sheet",
    "preview": "preview",
    "favicon": "favicons",
}
"""The ``{role}`` of each command's outputs. Conversions (``convert``, ``remux``) have none."""

COMMANDS: tuple[str, ...] = (
    "convert", "remux", "resize", "rotate", "extract", "edit", "frames", "strip",
    "thumbs", "animated_thumbs", "preview", "favicon",
)  # fmt: skip
"""Every command whose outputs are named by a pattern (``animated_thumbs``: ``thumbs --animated``)."""

_WINDOWS_FORBIDDEN = frozenset('<>:"/\\|?*') | {chr(code) for code in range(32)}
_LITERAL_FORBIDDEN = _WINDOWS_FORBIDDEN - {"/"}
_DRIVE = re.compile(r"^[A-Za-z]:")
_ELSEWHERE = "a pattern's folders are inside each input's folder; to write somewhere else, set output_dir."
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"} | {f"{p}{n}" for p in ("COM", "LPT") for n in "123456789"})
MAX_NAME_LENGTH = 255
"""The longest file or folder name, in UTF-8 bytes: the limit on Linux and macOS, and stricter than Windows' 255 UTF-16 units."""


def default_output_name(command: str) -> str:
    """
    `command`'s default pattern: ``{input_name}.{role}.{output_ext}`` (``clip.mp4.sheet.jpg``,
    ``photo.jpg.rotated.png``), or ``{input_name}.{output_ext}`` for the conversions ``convert``
    and ``remux``, which have no role (``photo.png.webp``).
    """
    if command not in COMMANDS:
        raise InvalidOptionsError(f"{command} has no output names.")
    if command in ROLES:
        return "{input_name}.{role}.{output_ext}"
    return "{input_name}.{output_ext}"


@dataclass(frozen=True)
class OutputFormat:
    """What one output is written as: its extension, every extension that names its format, and the format's name."""

    extension: str
    accepted: frozenset[str]
    label: str


class NameRefused(Exception):
    """A planned name that can't be used, with the reason for the file's result."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def output_format(target: str, source: pathlib.PurePath | None = None, typed: str | None = None) -> OutputFormat:
    """
    The output format `target` (``jpeg``, ``mkv``, ``zip``, …) and the extension it is written
    with, ``{output_ext}``:

    1. `typed`, the extension the caller chose (``jpeg`` or ``jpg``), as given;
    2. else, for a `source` already in that format, the source's own extension as written
       (``photo.JPEG`` → ``JPEG``);
    3. else sashimi's standard one (``jpg`` for JPEG, ``tif`` for TIFF).

    Raises :class:`NameRefused` when `typed` doesn't name `target`.
    """
    extensions = EXTENSIONS.get(target, (target,))
    accepted = frozenset(extensions)
    label = target.upper()
    if typed is not None:
        if typed.lower() not in accepted:
            raise NameRefused(f".{typed} isn't an extension for {label}, which is written as .{extensions[0]}.")
        return OutputFormat(typed, accepted, label)
    extension = extensions[0]
    if source is not None and source.suffix[1:].lower() in accepted:
        extension = source.suffix[1:]
    return OutputFormat(extension, accepted, label)


def check_output_ext(typed: str | None, targets: tuple[str, ...]) -> None:
    """Raises :class:`~sashimi.errors.InvalidOptionsError` unless `typed` names one of `targets`' formats."""
    if typed is None:
        return
    for target in targets:
        if typed.lower() in EXTENSIONS.get(target, (target,)):
            return
    names = " or ".join(target.upper() for target in targets)
    raise InvalidOptionsError(f".{typed} isn't an extension for {names}.")


def source_format(source: pathlib.PurePath) -> OutputFormat:
    """The input's own format, judged by its extension, keeping the extension as written."""
    suffix = source.suffix[1:]
    for target, extensions in EXTENSIONS.items():
        if suffix.lower() in extensions:
            return OutputFormat(suffix, frozenset(extensions), target.upper())
    return OutputFormat(suffix, frozenset({suffix.lower()}), suffix.upper() or "this")


class NameTemplate:
    """
    An output name pattern such as ``{input_name}.{role}.{output_ext}``: text and the fields in
    :data:`FIELDS`, with ``{{`` and ``}}`` for literal braces. It may start with folders, which
    are inside each input's own folder: ``sheets/{input_name}.jpeg``; ``\\`` separates folders
    as ``/`` does, on every system. `origin` says where it was set (``--output-name``, or
    ``output_name in [thumbs] of /home/user/sashimi.toml``), for messages. Raises
    :class:`~sashimi.errors.InvalidOptionsError` for an unknown field, a format spec or
    conversion, unbalanced braces, an empty pattern, text that can't be in a file name on
    Windows or POSIX (``<>:"|?*`` and control characters), and folders that leave the input's
    folder or are empty: an absolute path, a drive letter, ``~``, ``.`` or ``..``, ``//``, or a
    pattern ending in ``/``.
    """

    def __init__(self, text: str, origin: str | None = None):
        if not isinstance(text, str) or not text.strip():
            raise InvalidOptionsError("The output name pattern is empty.")
        written = text
        text = text.replace("\\", "/")
        try:
            parsed = list(string.Formatter().parse(text))
        except ValueError:
            raise InvalidOptionsError(
                f"The output name pattern '{written}' has an unmatched brace; write {{{{ or }}}} for a literal one."
            ) from None
        _check_folders(written, text)
        pieces: list[tuple[str, str | None]] = []
        for literal, field, spec, conversion in parsed:
            bad = sorted(set(literal) & _LITERAL_FORBIDDEN)
            if bad:
                shown = ", ".join(repr(char) for char in bad)
                raise InvalidOptionsError(
                    f"The output name pattern '{written}' has characters file names can't have on every system: {shown}."
                )
            if field is not None and (field not in FIELDS or spec or conversion):
                shown_field = f"{{{field}{'!' + conversion if conversion else ''}{':' + spec if spec else ''}}}"
                hint = f" (did you mean {{{_RENAMED[field]}}}?)" if field in _RENAMED and not spec else ""
                raise InvalidOptionsError(
                    f"The output name pattern '{written}' has an unknown field {shown_field}{hint}; "
                    f"the fields are {FIELD_HELP}."
                )
            pieces.append((literal, field))
        self.text = written
        self.origin = origin
        self.fields = frozenset(field for _, field in pieces if field is not None)
        self._pieces = tuple(pieces)

    def __repr__(self) -> str:
        return f"NameTemplate({self.text!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, NameTemplate) and other.text == self.text

    def __hash__(self) -> int:
        return hash(self.text)

    @property
    def literal_extension(self) -> str | None:
        """The extension the pattern spells out after its last field (``jpeg`` for ``{input_name}.jpeg``), if any."""
        literal, field = self._pieces[-1]
        name = literal.rpartition("/")[2]
        if field is not None or "." not in name:
            return None
        return name.rpartition(".")[2] or None

    def described(self) -> str:
        """The pattern and where it was set, for messages: ``{input_stem}.jpg (--output-name)``."""
        return f"{self.text} ({self.origin})" if self.origin else self.text

    @property
    def folders(self) -> bool:
        """True when the pattern puts outputs in folders inside each input's folder."""
        return any(literal and "/" in literal for literal, _ in self._pieces)

    def render(self, source: pathlib.PurePath, role: str | None, extension: str) -> str:
        """The name for `source`, with any folders before it (``sheets/clip.mp4.jpeg``), without any checks."""
        values = {
            "input_name": source.name,
            "input_stem": source.stem,
            "input_ext": source.suffix[1:],
            "role": role or "",
            "output_ext": extension,
        }
        return "".join(literal + (values[field] if field is not None else "") for literal, field in self._pieces)


def _check_folders(written: str, text: str) -> None:
    """Refuses a pattern whose folders would leave the input's folder, or are empty."""
    if "/" not in text:
        return
    segments = text.split("/")
    if text.startswith("/") or _DRIVE.match(text) or segments[0] == "~":
        raise InvalidOptionsError(f"The output name pattern '{written}' is a full path; {_ELSEWHERE}")
    if any(segment in (".", "..") for segment in segments):
        raise InvalidOptionsError(f"The output name pattern '{written}' has a . or .. folder; {_ELSEWHERE}")
    if "" in segments:
        where = "ends in a folder separator" if segments[-1] == "" else "has an empty folder name"
        raise InvalidOptionsError(f"The output name pattern '{written}' {where}; it must end in a file name.")


def _check_part(part: str, source: pathlib.PurePath, what: str) -> None:
    if part in ("", ".", "..") or "\0" in part:
        raise NameRefused(f"{part!r} isn't a {what} name.")
    own = set(source.name)
    bad = sorted((set(part) & _WINDOWS_FORBIDDEN) - own)
    if bad:
        raise NameRefused(f"{part} has characters {what} names can't have on Windows: {', '.join(map(repr, bad))}.")
    base = part.split(".")[0].rstrip(" ").upper()
    if base in _RESERVED and base != source.name.split(".")[0].rstrip(" ").upper():
        raise NameRefused(f"{part} can't be used as a {what} name on Windows.")
    if part.endswith((" ", ".")):
        raise NameRefused(f"{part!r} ends in a space or a dot, which Windows removes.")
    too_long = length_problem(part, source, what)
    if too_long:
        raise NameRefused(too_long)


def length_problem(part: str, source: pathlib.PurePath, what: str = "file") -> str | None:
    """Why `part` is too long to be a file or folder name (see :data:`MAX_NAME_LENGTH`), or None."""
    size = len(os.fsencode(part))
    if size <= MAX_NAME_LENGTH:
        return None
    return (
        f"The output {what} name for {source.name} would be {size} bytes long; "
        f"{what} names can be at most {MAX_NAME_LENGTH} bytes (UTF-8)."
    )


def check_name(name: str, source: pathlib.PurePath, fmt: OutputFormat) -> None:
    """
    Raises :class:`NameRefused` unless `name` is a usable output name for `source` in format
    `fmt`: a file name, after any folders (``sheets/clip.mp4.jpeg``), each valid on Windows and
    POSIX (except for characters the input's own name already has) and at most 255 bytes (UTF-8),
    the file name ending in one of the format's extensions (``.jpg`` or ``.jpeg`` for JPEG).
    """
    *folders, file_name = name.split("/")
    for folder in folders:
        _check_part(folder, source, "folder")
    _check_part(file_name, source, "file")
    suffix = pathlib.PurePosixPath(file_name).suffix[1:].lower()
    if suffix not in fmt.accepted:
        raise NameRefused(f"{file_name} must end in {endings(fmt)}, since the output is {fmt.label}.")


def endings(fmt: OutputFormat) -> str:
    """The extensions that name `fmt`, standard one first: ``.jpg or .jpeg``."""
    return " or ".join(f".{ext}" for ext in sorted(fmt.accepted, key=lambda e: (e != fmt.extension.lower(), e)))


@dataclass(frozen=True)
class Naming:
    """
    How one batch names its outputs: the command, its pattern and role, each input's output
    format, and whether the pattern is the command's default.
    """

    command: str
    template: NameTemplate
    role: str | None
    format_for: Callable[[pathlib.Path], OutputFormat]
    default: bool = True

    def name_for(self, source: pathlib.Path) -> str:
        """The checked output name for `source`; raises :class:`NameRefused`."""
        fmt = self.format_for(source)
        name = self.template.render(source, self.role, fmt.extension)
        check_name(name, source, fmt)
        return name


def naming(
    command: str,
    format_for: Callable[[pathlib.Path], OutputFormat],
    output_name: "str | NameTemplate | None" = None,
    *,
    fixed: OutputFormat | None = None,
) -> Naming:
    """
    The naming for one batch of `command`: `output_name` if given, else the command's default
    (see :func:`default_output_name`). `fixed` is the output format when every input gets the
    same one, so a pattern spelling out another format's extension is refused up front. Raises
    :class:`~sashimi.errors.InvalidOptionsError` for an invalid pattern, one using ``{role}`` for
    a conversion, which has none, or one ending in an extension `fixed` doesn't have.
    """
    role = ROLES.get(command)
    if output_name is None:
        return Naming(command, NameTemplate(default_output_name(command)), role, format_for)
    template = output_name if isinstance(output_name, NameTemplate) else NameTemplate(output_name)
    if role is None and "role" in template.fields:
        raise InvalidOptionsError(
            f"{command} outputs have no role, so the pattern {template.described()} can't use {{role}}."
        )
    literal = template.literal_extension
    if fixed is not None and literal is not None and literal.lower() not in fixed.accepted:
        raise InvalidOptionsError(
            f"The pattern {template.described()} ends in .{literal}, but the output is {fixed.label}; "
            f"end it in {endings(fixed)}, or in {{output_ext}}."
        )
    return Naming(command, template, role, format_for, default=template.text == default_output_name(command))
