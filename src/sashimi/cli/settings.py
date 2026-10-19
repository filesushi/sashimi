# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
CLI settings: TOML config files and their mapping onto the core config models.

This is the only place sashimi reads or writes config files. The library core never does.
Precedence: CLI flags > project ``sashimi.toml``/``.sashimi.toml`` (searched upward from the
working directory) > global ``~/.sashimi/sashimi.toml`` > defaults. Only ``sashimi config set`` writes either file.
"""

import copy
import logging
import pathlib
import tomllib
import tomlkit
import tomlkit.items
from functools import lru_cache
from typing import TYPE_CHECKING, Any, MutableMapping

if TYPE_CHECKING:
    from sashimi.config import SheetConfig


logger = logging.getLogger("sashimi")


GLOBAL_CONFIG_DIR = pathlib.Path.home() / ".sashimi"
GLOBAL_CONFIG_FILENAME = "sashimi.toml"
PROJECT_CONFIG_FILENAMES = ["sashimi.toml", ".sashimi.toml"]

DEFAULT_CONFIG_DICT: dict[str, Any] = {"global": {"verbose": 0, "priority": "normal"}}
"""Settings that apply when no file sets them. Sheet defaults come from the config models."""


def _recursive_merge(base: dict, new: MutableMapping) -> dict:
    """Recursively merges the 'new' dictionary into the 'base' dictionary."""
    for key, value in new.items():
        if isinstance(value, MutableMapping) and key in base and isinstance(base[key], MutableMapping):
            base[key] = _recursive_merge(base[key], value)
        else:
            base[key] = value
    return base


def get_global_config_path() -> pathlib.Path:
    return GLOBAL_CONFIG_DIR / GLOBAL_CONFIG_FILENAME


def find_project_config_path() -> pathlib.Path | None:
    """Finds the highest-precedence project-level config file."""
    global_config_path = get_global_config_path().resolve()
    current_dir = pathlib.Path.cwd()
    root = pathlib.Path(current_dir.root)

    while True:
        for filename in PROJECT_CONFIG_FILENAMES:
            config_file = current_dir / filename
            if config_file.is_file() and config_file.resolve() != global_config_path:
                return config_file

        if current_dir == root or current_dir == current_dir.parent:
            break
        current_dir = current_dir.parent

    return None


def _load_config_file(path: pathlib.Path) -> dict[str, Any]:
    """Safely loads a TOML config file using tomllib."""
    if not path or not path.is_file():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        logger.error("Failed to load config at %s: %s", path, e)
        return {}


@lru_cache(maxsize=1)
def load_raw_config() -> tuple[dict[str, Any], pathlib.Path]:
    """:func:`read_config`, cached for the rest of the command, warning once about keys sashimi doesn't know."""
    from sashimi.cli._output import warn_unknown_settings

    for path in (get_global_config_path(), find_project_config_path()):
        if path is not None:
            warn_unknown_settings(path, unknown_keys(_load_config_file(path)), path != get_global_config_path())
    return read_config()


def read_config() -> tuple[dict[str, Any], pathlib.Path]:
    """
    The project file's settings merged over the global file's, merged over :data:`DEFAULT_CONFIG_DICT`,
    and the most specific file. Never writes: only ``sashimi config set`` creates config files.
    """
    effective_config = copy.deepcopy(DEFAULT_CONFIG_DICT)

    global_config_path = get_global_config_path()
    global_config_data = _load_config_file(global_config_path)
    effective_config = _recursive_merge(effective_config, global_config_data)

    effective_path = global_config_path
    project_config_path = find_project_config_path()

    if project_config_path:
        project_config_data = _load_config_file(project_config_path)
        effective_config = _recursive_merge(effective_config, project_config_data)
        effective_path = project_config_path

    return effective_config, effective_path


def set_config_value(section: str, option: str, value: Any, scope: str = "project") -> None:
    """Sets a configuration value in the specified scope using tomlkit to preserve formatting."""
    if scope == "global":
        config_path = get_global_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        project_config_path = find_project_config_path()
        config_path = project_config_path if project_config_path else pathlib.Path.cwd() / "sashimi.toml"

    if config_path.is_file():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = tomlkit.load(f)
        except Exception:
            config = tomlkit.document()
    else:
        config = tomlkit.document()

    section_table = config.get(section)
    if not isinstance(section_table, tomlkit.items.Table):
        section_table = tomlkit.table()
        config[section] = section_table

    section_table[option] = value

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            tomlkit.dump(config, f)
        load_raw_config.cache_clear()
    except Exception as e:
        raise OSError(f"Failed to write to config file at {config_path}: {e}") from e


def remove_config_value(section: str, option: str, scope: str = "project") -> bool:
    """Removes a configuration value from the specified scope."""
    if scope == "global":
        config_path = get_global_config_path()
    else:
        project_config_path = find_project_config_path()
        if not project_config_path:
            return False
        config_path = project_config_path

    if not config_path or not config_path.is_file():
        return False

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = tomlkit.load(f)
    except Exception as e:
        logger.error("Failed to read config %s for removal: %s", config_path, e)
        return False

    section_table = config.get(section)

    if not isinstance(section_table, tomlkit.items.Table):
        return False

    if option in section_table:
        del section_table[option]
        if not section_table:
            del config[section]

        try:
            with open(config_path, "w", encoding="utf-8") as f:
                tomlkit.dump(config, f)
            load_raw_config.cache_clear()
            return True
        except Exception as e:
            raise OSError(f"Failed to write to config file at {config_path}: {e}") from e

    return False


_STATIC_RENAMES = {"static_width": "width", "jpeg_quality": "quality"}
_ANIMATED_RENAMES = {"animated_width": "width", "output_fps": "fps"}
PATH_KEYS = {"header_font_path": "header_font", "timestamp_font_path": "timestamp_font", "emoji_dir": "emoji_dir"}
"""TOML keys naming local files, and the :func:`sashimi.sheets.render` arguments they become."""


SECTIONS = ("global", "thumbs", "animated_thumbs")


def known_keys(section: str) -> frozenset[str]:
    """The keys sashimi reads from one section of a sashimi.toml file (none for an unknown section)."""
    if section == "global":
        return frozenset({"verbose", "priority"})
    if section not in SECTIONS:
        return frozenset()
    from sashimi.config import CONTACT_SHEET

    animated = section == "animated_thumbs"
    models = [m for m in CONTACT_SHEET.models if animated <= m.model_fields["animated"].default]
    keys = {name for model in models for name in model.model_fields if name != "animated"}
    keys |= {*_ANIMATED_RENAMES, "output_format", *PATH_KEYS}
    if not animated:
        keys |= {*_STATIC_RENAMES, "video_extensions"}
    return frozenset(keys)


def unknown_keys(data: dict[str, Any]) -> list[str]:
    """``section.key`` (or ``section``) for each setting in one file's `data` that sashimi ignores."""
    found = []
    for section, values in data.items():
        if section not in SECTIONS or not isinstance(values, MutableMapping):
            found.append(section)
            continue
        known = known_keys(section)
        found += [f"{section}.{key}" for key in values if key not in known]
    return found


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    return dict(value) if isinstance(value, MutableMapping) else {}


def _renamed(data: dict[str, Any], renames: dict[str, str]) -> dict[str, Any]:
    return {renames.get(key, key): value for key, value in data.items()}


def sheet_settings(animated: bool, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    The TOML sheet settings under core field names: [thumbs] for still sheets, and [thumbs] then
    [animated_thumbs] for animated ones. The format comes from ``target`` (still) or
    ``output_format``/``target`` (animated); keys that no sheet model has are kept for the caller.
    Reads the config files unless `raw` settings are given.
    """
    if raw is None:
        raw, _ = load_raw_config()
    thumbs, animated_section = _section(raw, "thumbs"), _section(raw, "animated_thumbs")
    if not animated:
        return _renamed(thumbs, _STATIC_RENAMES)
    target = animated_section.get("target") or animated_section.get("output_format") or thumbs.get("output_format")
    merged = {k: v for k, v in {**thumbs, **animated_section}.items() if k not in ("target", "output_format")}
    data = _renamed(merged, _ANIMATED_RENAMES)
    if target is not None:
        data["target"] = target
    return data


def resolve_sheet(
    animated: bool, overrides: dict[str, Any] | None = None
) -> tuple["SheetConfig", dict[str, str | None], list[str]]:
    """
    Builds a sheet config from the TOML settings plus CLI overrides (core field names, ``None``
    meaning unset). Returns the config, the font/emoji paths for :func:`sashimi.sheets.render`,
    and the override keys the chosen model doesn't use (TOML keys it doesn't use are dropped).
    Raises InvalidOptionsError for an impossible format, and pydantic's ValidationError for
    invalid values.
    """
    from sashimi.config import CONTACT_SHEET

    given = {key: value for key, value in (overrides or {}).items() if value is not None}
    data = {**sheet_settings(animated), **given, "animated": animated}
    paths = {argument: data.pop(key, None) for key, argument in PATH_KEYS.items()}
    model = CONTACT_SHEET.model_for(animated=animated, target=data.get("target"))
    unused = [key for key in given if key not in model.model_fields and key not in PATH_KEYS]
    config = model.model_validate({key: value for key, value in data.items() if key in model.model_fields})
    return config, {key: str(value) if value is not None else None for key, value in paths.items()}, unused


def video_extensions() -> list[str] | None:
    """The optional ``video_extensions`` list from the TOML [thumbs] section."""
    raw, _ = load_raw_config()
    value = _section(raw, "thumbs").get("video_extensions")
    return [str(ext).lower() for ext in value] if isinstance(value, list) else None
