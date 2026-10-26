# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""The CLI's limits: ``--max-*`` flags and the ``[limits]`` section of sashimi.toml, parsed into ``sashimi.config.Limits``."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sashimi.config import Limits

FLAGS = {
    "max_pixels": "--max-pixels",
    "max_frame_bytes": "--max-frame-bytes",
    "max_animation_bytes": "--max-animation-bytes",
    "max_sheet_pixels": "--max-sheet-pixels",
}
"""Limits field to CLI flag. The other limits are off by default and set only in sashimi.toml."""
KINDS = {
    "max_pixels": "pixels",
    "max_frame_bytes": "bytes",
    "max_animation_bytes": "bytes",
    "max_sheet_pixels": "pixels",
    "max_video_seconds": "seconds",
    "max_audio_seconds": "seconds",
    "max_gif_seconds": "seconds",
    "max_preview_clips": "count",
    "max_scan_packets": "count",
}
"""How each limit is written: pixels (200M), bytes (2G), seconds (10m, 600) or a count."""

_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]*)")
_PIXEL_UNITS = {"": 1, "k": 10**3, "kp": 10**3, "m": 10**6, "mp": 10**6, "g": 10**9, "gp": 10**9}
_BYTE_UNITS = {"": 1, "b": 1, "k": 2**10, "m": 2**20, "g": 2**30, "t": 2**40}
_HINTS = {
    "pixels": "a number of pixels, e.g. 200M or 200000000",
    "bytes": "a size, e.g. 2G, 512M or 1073741824 (bytes)",
    "seconds": "a length, e.g. 600, 10m or 1h30m",
    "count": "a whole number",
}


def parse(field: str, value: Any) -> int | float | None:
    """
    One limit's value as written on the command line or in sashimi.toml: a number, a number with
    a unit (pixels 200M, sizes 2G or 512MiB, lengths 10m), or "none" for no limit. Raises
    ValueError saying what is expected.
    """
    kind = KINDS[field]
    if value is None or (isinstance(value, str) and value.strip().lower() == "none"):
        return None
    if isinstance(value, bool):
        raise ValueError(f"expected {_HINTS[kind]}, or none")
    if isinstance(value, (int, float)):
        number: float = value
    elif kind == "seconds":
        from sashimi.api.extract import parse_time_to_seconds
        from sashimi.errors import InvalidOptionsError

        try:
            number = parse_time_to_seconds(str(value))
        except InvalidOptionsError:
            raise ValueError(f"expected {_HINTS[kind]}, or none") from None
    else:
        found = _AMOUNT.fullmatch(str(value).strip().lower())
        units = _PIXEL_UNITS if kind == "pixels" else _BYTE_UNITS if kind == "bytes" else {"": 1}
        unit = found.group(2) if found else ""
        if kind == "bytes":
            unit = unit.removesuffix("ib").removesuffix("b") or ("b" if unit else "")
        if not found or unit not in units:
            raise ValueError(f"expected {_HINTS[kind]}, or none")
        number = float(found.group(1)) * units[unit]
    if number <= 0:
        raise ValueError("must be more than 0, or none for no limit")
    if kind == "seconds":
        return float(number)
    if number != int(number):
        raise ValueError(f"expected {_HINTS[kind]}, or none")
    return int(number)


_EXAMPLES = {"pixels": "200M", "bytes": "2G", "seconds": "2h"}
_COUNT_EXAMPLES = {"max_scan_packets": "10000000", "max_preview_clips": "20"}


def hint(field: str) -> str | None:
    """How to change the limit `field` from the CLI, for the end of its refusal; None for an unknown field."""
    kind = KINDS.get(field)
    if kind is None:
        return None
    example = _EXAMPLES.get(kind) or _COUNT_EXAMPLES.get(field, "100")
    return f"To change the limit, run: sashimi config set limits.{field} {example} (or none for no limit)."


def with_hint(message: str, field: str | None) -> str:
    """`message`, followed by how to change the limit `field` if it names one."""
    extra = hint(field) if field else None
    return f"{message} {extra}" if extra else message


def accepted(field: str) -> str:
    """What one limit accepts, for ``sashimi config defaults limits``."""
    return f"{_HINTS[KINDS[field]]}, or none"


def format_limit(field: str, value: int | float | str | None) -> str:
    """A limit as the CLI writes it: 64M (pixels), 1G (bytes), 600 (seconds), or none."""
    if value is None:
        return "none"
    if isinstance(value, str):
        return value
    kind = KINDS[field]
    if kind == "pixels" and value % 10**6 == 0:
        return f"{int(value) // 10**6}M"
    if kind == "bytes":
        for unit, size in (("G", 2**30), ("M", 2**20)):
            if value % size == 0:
                return f"{int(value) // size}{unit}"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def resolve(**flags: str | None) -> Limits:
    """
    The limits for one command: built-in defaults, then sashimi.toml's ``[limits]`` (the project
    file over the global one), then the ``--max-*`` flags given (field name to text, ``None`` for
    unset). Exits with an error naming the flag or setting for an invalid value.
    """
    from sashimi.cli import settings
    from sashimi.cli._output import fail
    from sashimi.config import Limits

    values: dict[str, Any] = {}
    for key, value in settings.limit_settings().items():
        try:
            values[key] = parse(key, value)
        except ValueError as e:
            fail(f"limits.{key} in sashimi.toml: {e}.")
    for field, value in flags.items():
        if value is None:
            continue
        try:
            values[field] = parse(field, value)
        except ValueError as e:
            fail(f"{FLAGS[field]}: {e}.")
    return Limits.model_validate(values)
