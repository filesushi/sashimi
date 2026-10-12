"""Option defaults for help text and ``sashimi config defaults``: built-in, from the config models, and from sashimi.toml."""

from __future__ import annotations

import re
import types
import typing
from typing import TYPE_CHECKING, Any

import annotated_types

from sashimi.cli import convert_cmd, thumbs_cmd

if TYPE_CHECKING:
    from sashimi.config import ParamsModel

OPERATIONS = ("convert", "resize", "rotate", "extract", "thumbs")
DISCRIMINATORS = frozenset({"target", "container", "accurate", "animated"})
NONE_MEANS = {
    "max_width": "source width",
    "max_height": "source height",
    "duration_seconds": "to the end",
    "end_seconds": "to the end",
    "sample_rate": "source",
}
EXTRACT_FLAGS = {
    "start_seconds": "--start",
    "end_seconds": "--end",
    **{field: convert_cmd.FLAGS[field] for field in ("crf", "preset", "speed", "audio_bitrate_kbps", "mute")},
}
CLI_DEFAULTS = {"rotate": {"rotate": 90}}
"""Commands whose CLI default differs from the model's, by operation and field."""
_ROLE = re.compile(r":\w+:`~?([^`]+)`")


def flags_for(operation: str) -> dict[str, str]:
    """Field name to CLI flag for one command."""
    if operation == "thumbs":
        return thumbs_cmd.FLAGS
    if operation == "extract":
        return EXTRACT_FLAGS
    return convert_cmd.FLAGS


def format_value(value: Any, field: str = "") -> str:
    """A value as the CLI writes it: colours as hex, whole floats without '.0', booleans as true/false."""
    if value is None:
        return NONE_MEANS.get(field, "none")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (tuple, list)):
        if field.endswith("_color"):
            return "#" + "".join(f"{channel:02x}" for channel in value)
        return ",".join(str(item) for item in value)
    return str(value)


def _parts(annotation: Any, metadata: list[Any]) -> tuple[list[Any], list[Any]]:
    """The plain types in an annotation, and every constraint on them."""
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        inner, *extra = typing.get_args(annotation)
        found = [m for item in extra for m in getattr(item, "metadata", [item])]
        return _parts(inner, metadata + found)
    if origin in (typing.Union, types.UnionType):
        kinds: list[Any] = []
        for arg in typing.get_args(annotation):
            more_kinds, metadata = _parts(arg, metadata)
            kinds += more_kinds
        return kinds, metadata
    return [annotation], metadata


def _bounds(metadata: list[Any]) -> str:
    low: Any = None
    high: Any = None
    low_open = high_open = False
    for meta in metadata:
        if isinstance(meta, annotated_types.Ge):
            low = meta.ge
        elif isinstance(meta, annotated_types.Gt):
            low, low_open = meta.gt, True
        elif isinstance(meta, annotated_types.Le):
            high = meta.le
        elif isinstance(meta, annotated_types.Lt):
            high, high_open = meta.lt, True
    if low is not None and high is not None and not low_open and not high_open:
        return f"{format_value(low)}-{format_value(high)}"
    parts = []
    if low is not None:
        parts.append(f"{'>' if low_open else 'at least'} {format_value(low)}")
    if high is not None:
        parts.append(f"{'<' if high_open else 'at most'} {format_value(high)}")
    return ", ".join(parts)


def allowed(name: str, field: Any) -> str:
    """The values a field accepts, e.g. '1-100', 'bayer, none', '> 0, at most 30'."""
    if name.endswith("_color"):
        return "#rrggbbaa" if len(field.default) == 4 else "#rrggbb"
    kinds, metadata = _parts(field.annotation, list(field.metadata))
    choices = [
        str(choice) for kind in kinds if typing.get_origin(kind) is typing.Literal for choice in typing.get_args(kind)
    ]
    if kinds and typing.get_origin(kinds[0]) is typing.Literal:
        return ", ".join(choices)
    return ", ".join(part for part in (_bounds(metadata), *choices) if part)


def describe(field: Any) -> str:
    """A field's description as plain text."""
    text = _ROLE.sub(r"\1", field.description or "")
    return text.replace("``", "")


def models_for(operation: str, fmt: str | None = None) -> list[tuple[str, type[ParamsModel]]]:
    """The config models behind one command, titled, optionally only those for one output format."""
    from sashimi import config
    from sashimi.config._base import normalize_target

    fmt = normalize_target(fmt)
    found: list[tuple[str, type[ParamsModel]]] = []

    def add(family: Any, kind: str) -> None:
        for target in family.targets:
            if fmt in (None, target):
                found.append((f"{kind} → {target.upper()}", family.model_for(target=target)))

    if operation == "convert":
        add(config.IMAGE_CONVERT, "Images")
        if fmt in (None, "ico"):
            found.append(("Images → ICO", config.IcoConfig))
        add(config.VIDEO_CONVERT, "Videos")
        add(config.AUDIO_CONVERT, "Video or audio")
    elif operation == "resize":
        add(config.IMAGE_RESIZE, "Resized images")
    elif operation == "rotate":
        add(config.VIDEO_TRANSFORM, "Rotated videos")
    elif operation == "extract":
        for container in config.VIDEO_TRIM.targets:
            if fmt in (None, container):
                found.append((f"Lossless cut → {container.upper()}", config.CopyTrimConfig))
                accurate = config.VIDEO_TRIM.model_for(container=container, accurate=True)
                found.append((f"Accurate cut (--accurate) → {container.upper()}", accurate))
    elif operation == "thumbs":
        for animated, kind in ((False, "Still sheets"), (True, "Animated sheets (--animated)")):
            for target in ("jpeg", "png", "webp") if not animated else ("webp", "gif"):
                if fmt in (None, target):
                    found.append(
                        (f"{kind} → {target.upper()}", config.CONTACT_SHEET.model_for(animated=animated, target=target))
                    )
    return found


def toml_overrides(animated: bool, raw: dict[str, Any]) -> dict[str, Any]:
    """The sheet settings in `raw` (merged sashimi.toml) that differ from the built-in defaults, by field."""
    from sashimi.cli import settings
    from sashimi.config import CONTACT_SHEET
    from sashimi.errors import InvalidOptionsError

    data = settings.sheet_settings(animated, raw)
    builtin = CONTACT_SHEET.model_for(animated=animated)
    try:
        model = CONTACT_SHEET.model_for(animated=animated, target=data.get("target"))
    except InvalidOptionsError:
        model = builtin
    found: dict[str, Any] = {}
    for key, value in data.items():
        if key in settings.PATH_KEYS:
            found[key] = value
            continue
        if key not in model.model_fields or (key in DISCRIMINATORS and key != "target"):
            continue
        try:
            value = getattr(model.model_validate({key: value}), key)
        except ValueError:
            pass
        reference = builtin if key in builtin.model_fields else model
        if value != reference.model_fields[key].default:
            found[key] = value
    return found


def toml_notes(raw: dict[str, Any]) -> dict[str, str]:
    """``thumbs`` flag to a note of the sashimi.toml value that replaces its built-in default."""
    per_field: dict[str, dict[str, Any]] = {}
    for animated in (False, True):
        for key, value in toml_overrides(animated, raw).items():
            per_field.setdefault(key, {})["animated" if animated else "still"] = value
    from sashimi.config import CONTACT_SHEET

    still_fields = CONTACT_SHEET.model_for(animated=False).model_fields
    notes = {}
    for key, values in per_field.items():
        flag = thumbs_cmd.FLAGS.get(key)
        if flag is None:
            continue
        shown = {
            label: f"{key} = {format_value(value)}" if isinstance(value, bool) else format_value(value, key)
            for label, value in values.items()
        }
        if len(set(shown.values())) == 1 and len(shown) == 2:
            notes[flag] = next(iter(shown.values()))
        else:
            notes[flag] = ", ".join(
                text if label == "still" or key not in still_fields else f"{label} {text}"
                for label, text in shown.items()
            )
    return notes


def with_toml_note(help: str, note: str) -> str:
    """Adds a sashimi.toml value to an option's (escaped) help text."""
    if "\\[default:" in help:
        head, _, tail = help.rpartition("]")
        return f"{head}; sashimi.toml: {note}]{tail}"
    return f"{help} \\[sashimi.toml: {note}]"


def rows(operation: str, model: type[ParamsModel], overrides: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One row per option of `model`: field, CLI option, default, accepted values, description and any sashimi.toml value."""
    flags = flags_for(operation)
    cli_defaults = CLI_DEFAULTS.get(operation, {})
    found = []
    for name, field in model.model_fields.items():
        if name in DISCRIMINATORS:
            continue
        row = {
            "field": name,
            "option": flags.get(name),
            "default": cli_defaults.get(name, field.default),
            "accepted": allowed(name, field),
            "description": describe(field),
        }
        if name in cli_defaults:
            row["model_default"] = field.default
        if overrides and name in overrides:
            row["sashimi_toml"] = overrides[name]
        found.append(row)
    return found
