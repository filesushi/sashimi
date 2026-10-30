# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
CLI settings: TOML config files and their mapping onto the core config models.

This is the only place sashimi reads or writes config files. The library core never does.
Precedence: CLI flags > project ``sashimi.toml``/``.sashimi.toml`` (searched upward from the
working directory) > your own ``~/.sashimi/sashimi.toml`` > defaults. Only ``sashimi config set``
writes either file: your own by default, the project file with ``--project``.
Sections: ``[thumbs]`` and ``[animated_thumbs]`` (contact sheet defaults), ``[limits]`` (the
limits every command runs with; see :mod:`sashimi.cli._limits`), and one per processing command
(``[convert]``, ``[resize]``, …) for its ``output_name`` pattern and ``output_dir`` folder, which
``[thumbs]`` and ``[animated_thumbs]`` take too, each for its own sheets.
"""

import copy
import logging
import os
import pathlib
import tomllib
import tomlkit
import tomlkit.items
from functools import lru_cache
from typing import TYPE_CHECKING, Any, MutableMapping

if TYPE_CHECKING:
    from sashimi.config import SheetConfig


logger = logging.getLogger("sashimi")


USER_CONFIG_DIR = pathlib.Path.home() / ".sashimi"
USER_CONFIG_FILENAME = "sashimi.toml"
PROJECT_CONFIG_FILENAMES = ["sashimi.toml", ".sashimi.toml"]

DEFAULT_CONFIG_DICT: dict[str, Any] = {}
"""Settings that apply when no file sets them: none. Sheet defaults come from the config models."""

USER_HEADER = (
    "sashimi settings: your own defaults, used wherever you run sashimi.",
    "Change them with `sashimi config set <section>.<option> <value>`, and see every option's",
    "built-in default with `sashimi config defaults <command>`. A project file (sashimi.toml in a",
    "folder, written with `sashimi config set --project`) wins over this one when you run",
    "sashimi in that folder or a folder inside it.",
)
PROJECT_HEADER = (
    "sashimi settings for this folder: used when you run sashimi here or in a folder inside it,",
    "where they win over your own settings (`sashimi config path` shows both files). Change them",
    "with `sashimi config set --project <section>.<option> <value>`.",
)


class SettingsFileError(ValueError):
    """A sashimi.toml file that can't be read or written; the message names the file and the reason."""


def _recursive_merge(base: dict, new: MutableMapping) -> dict:
    """Recursively merges the 'new' dictionary into the 'base' dictionary."""
    for key, value in new.items():
        if isinstance(value, MutableMapping) and key in base and isinstance(base[key], MutableMapping):
            base[key] = _recursive_merge(base[key], value)
        else:
            base[key] = value
    return base


def get_user_config_path() -> pathlib.Path:
    """Your own settings file, ``~/.sashimi/sashimi.toml``, used wherever sashimi runs."""
    return USER_CONFIG_DIR / USER_CONFIG_FILENAME


def find_project_config_path() -> pathlib.Path | None:
    """The project file that applies here: ``sashimi.toml`` or ``.sashimi.toml`` in the current folder or the nearest folder above it."""
    user_config_path = get_user_config_path().resolve()
    current_dir = pathlib.Path.cwd()
    root = pathlib.Path(current_dir.root)

    while True:
        for filename in PROJECT_CONFIG_FILENAMES:
            config_file = current_dir / filename
            if config_file.is_file() and config_file.resolve() != user_config_path:
                return config_file

        if current_dir == root or current_dir == current_dir.parent:
            break
        current_dir = current_dir.parent

    return None


def project_config_target() -> pathlib.Path:
    """The file ``sashimi config set --project`` writes: the project file that applies here, else ``./sashimi.toml``."""
    found = find_project_config_path()
    return found if found is not None else pathlib.Path.cwd() / "sashimi.toml"


def config_path_for(scope: str) -> pathlib.Path:
    """The file a scope names: ``user`` (your settings) or ``project`` (see :func:`project_config_target`)."""
    if scope == "user":
        return get_user_config_path()
    if scope == "project":
        return project_config_target()
    raise ValueError(f"Unknown settings scope {scope!r}; use 'user' or 'project'.")


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


def file_settings(path: pathlib.Path | None) -> dict[str, Any]:
    """The settings one file holds (empty when there's no file, or it can't be read)."""
    return _load_config_file(path) if path is not None else {}


@lru_cache(maxsize=1)
def load_raw_config() -> tuple[dict[str, Any], pathlib.Path]:
    """:func:`read_config`, cached for the rest of the command, warning once about keys sashimi doesn't know."""
    from sashimi.cli._output import warn_unknown_settings

    for path in (get_user_config_path(), find_project_config_path()):
        if path is not None:
            warn_unknown_settings(path, unknown_keys(_load_config_file(path)), path != get_user_config_path())
    return read_config()


def read_config() -> tuple[dict[str, Any], pathlib.Path]:
    """
    The project file's settings merged over the user file's, merged over :data:`DEFAULT_CONFIG_DICT`,
    and the most specific file. Never writes: only ``sashimi config set`` creates config files.
    """
    effective_config = copy.deepcopy(DEFAULT_CONFIG_DICT)

    user_config_path = get_user_config_path()
    effective_config = _recursive_merge(effective_config, _load_config_file(user_config_path))

    effective_path = user_config_path
    project_config_path = find_project_config_path()

    if project_config_path:
        effective_config = _recursive_merge(effective_config, _load_config_file(project_config_path))
        effective_path = project_config_path

    return effective_config, effective_path


def _document(path: pathlib.Path, header: tuple[str, ...]) -> tomlkit.TOMLDocument:
    """`path` as an editable document, or a new one starting with `header`; refuses a file that isn't valid TOML."""
    if not path.is_file():
        document = tomlkit.document()
        for line in header:
            document.add(tomlkit.comment(line))
        document.add(tomlkit.nl())
        return document
    try:
        with open(path, "r", encoding="utf-8") as f:
            return tomlkit.load(f)
    except Exception as e:
        raise SettingsFileError(f"{path} isn't valid TOML ({e}), so it was left as it is; fix it by hand first.") from e


def _save(document: tomlkit.TOMLDocument, path: pathlib.Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            tomlkit.dump(document, f)
    except Exception as e:
        raise OSError(f"Failed to write to config file at {path}: {e}") from e
    finally:
        load_raw_config.cache_clear()


def set_config_value(section: str, option: str, value: Any, scope: str = "user") -> pathlib.Path:
    """
    Sets `option` in `section` of your settings file (``scope="user"``, the default) or of the
    project file (``scope="project"``), keeping the file's comments and layout, and returns the file
    written. A new file starts with a comment saying what it is. Raises :class:`SettingsFileError`
    for a file that isn't valid TOML, which is left as it is.
    """
    config_path = config_path_for(scope)
    config = _document(config_path, USER_HEADER if scope == "user" else PROJECT_HEADER)

    section_table = config.get(section)
    if not isinstance(section_table, tomlkit.items.Table):
        section_table = tomlkit.table()
        config[section] = section_table

    section_table[option] = value
    _save(config, config_path)
    return config_path


def remove_config_value(section: str, option: str, scope: str = "user") -> bool:
    """
    Removes `option` from `section` of your settings file or the project file that applies here.
    Returns False when that file doesn't set it. Raises :class:`SettingsFileError` for a file that
    isn't valid TOML.
    """
    if scope not in ("user", "project"):
        raise ValueError(f"Unknown settings scope {scope!r}; use 'user' or 'project'.")
    config_path = get_user_config_path() if scope == "user" else find_project_config_path()
    if config_path is None or not config_path.is_file():
        return False

    config = _document(config_path, ())
    section_table = config.get(section)
    if not isinstance(section_table, tomlkit.items.Table) or option not in section_table:
        return False

    del section_table[option]
    if not section_table:
        del config[section]
    _save(config, config_path)
    return True


PATH_KEYS = {"header_font_path": "header_font", "timestamp_font_path": "timestamp_font", "emoji_dir": "emoji_dir"}
"""TOML keys naming local files, and the :func:`sashimi.sheets.render` arguments they become."""


NAMED_SECTIONS = ("convert", "remux", "resize", "rotate", "extract", "edit", "frames", "strip", "preview", "favicon")
"""Sections that hold only where outputs go (``output_name``, ``output_dir``), one per command."""
OUTPUT_KEYS = frozenset({"output_name", "output_dir"})
"""The keys every processing command's section takes: its name pattern and its output folder."""
COMMAND_KEYS: dict[str, frozenset[str]] = {
    "convert": frozenset({"animated", "keep_hdr"}),
    "resize": frozenset({"animated"}),
    "edit": frozenset({"animated"}),
    "rotate": frozenset({"keep_hdr", "reencode"}),
    "extract": frozenset({"keep_hdr"}),
}
"""Settings a command's section takes besides where its outputs go: true/false switches."""
SECTIONS = ("thumbs", "animated_thumbs", "limits", *NAMED_SECTIONS, "ffmpeg")


def known_keys(section: str) -> frozenset[str]:
    """The keys sashimi reads from one section of a sashimi.toml file (none for an unknown section)."""
    if section not in SECTIONS:
        return frozenset()
    if section == "limits":
        from sashimi.cli._limits import KINDS

        return frozenset(KINDS)
    if section == "ffmpeg":
        return frozenset({"path"})
    if section in NAMED_SECTIONS:
        return OUTPUT_KEYS | COMMAND_KEYS.get(section, frozenset())
    from sashimi.config import CONTACT_SHEET

    animated = section == "animated_thumbs"
    models = [m for m in CONTACT_SHEET.models if animated <= m.model_fields["animated"].default]
    keys = {name for model in models for name in model.model_fields if name != "animated"}
    keys |= {*PATH_KEYS, *OUTPUT_KEYS}
    if not animated:
        keys.add("video_extensions")
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


def sheet_settings(animated: bool, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    The TOML sheet settings: [thumbs] for still sheets, and [thumbs] then [animated_thumbs] for
    animated ones, except that ``target``, ``output_name`` and ``output_dir`` apply only to their
    own section's sheets. Keys that no sheet model has are kept for the caller. Reads the config files unless
    `raw` settings are given.
    """
    if raw is None:
        raw, _ = load_raw_config()
    thumbs, animated_section = _section(raw, "thumbs"), _section(raw, "animated_thumbs")
    if not animated:
        return thumbs
    own = ("target", *OUTPUT_KEYS)
    data = {k: v for k, v in thumbs.items() if k not in own}
    return {**data, **animated_section}


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


def limit_settings(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    The ``[limits]`` section: ``sashimi.config.Limits`` field names to values as written (numbers,
    or strings such as "200M", "2G", "10m" or "none"), for :func:`sashimi.cli._limits.resolve`.
    Unknown keys are left out (and warned about once). Reads the config files unless `raw` is given.
    """
    if raw is None:
        raw, _ = load_raw_config()
    known = known_keys("limits")
    return {key: value for key, value in _section(raw, "limits").items() if key in known}


def output_name_setting(section: str, raw: dict[str, Any] | None = None) -> Any:
    """The ``output_name`` pattern set in `section` (``thumbs``, ``convert``, …), or None. Reads the config files unless `raw` is given."""
    if raw is None:
        raw, _ = load_raw_config()
    return _section(raw, section).get("output_name")


def output_dir_setting(section: str, raw: dict[str, Any] | None = None) -> Any:
    """The ``output_dir`` folder set in `section` (``thumbs``, ``convert``, …), as written, or None."""
    if raw is None:
        raw, _ = load_raw_config()
    return _section(raw, section).get("output_dir")


def command_setting(section: str, key: str, raw: dict[str, Any] | None = None) -> Any:
    """A switch in `section` (see :data:`COMMAND_KEYS`), such as ``animated`` in ``[convert]``, or None."""
    if raw is None:
        raw, _ = load_raw_config()
    return _section(raw, section).get(key)


def output_name_origin(section: str, key: str = "output_name") -> str:
    """Where `key` in `section` is set, for messages: ``output_name in [thumbs] of /home/user/sashimi.toml``."""
    for path in (find_project_config_path(), get_user_config_path()):
        if path is not None and key in _section(_load_config_file(path), section):
            return f"{key} in [{section}] of {path}"
    return f"{key} in [{section}] of sashimi.toml"


def ffmpeg_setting() -> tuple[str, str, str] | None:
    """
    The FFmpeg kept with ``sashimi config set ffmpeg.path`` (``~`` expanded), where it is set,
    and the commands that change it there, or None.
    """
    raw, _ = load_raw_config()
    value = _section(raw, "ffmpeg").get("path")
    if not isinstance(value, str) or not value.strip():
        return None
    project = find_project_config_path()
    scope = ""
    origin = "ffmpeg.path in sashimi.toml"
    for path in (project, get_user_config_path()):
        if path is not None and "path" in _section(_load_config_file(path), "ffmpeg"):
            origin = f"ffmpeg.path in {path}"
            scope = " --project" if path == project else ""
            break
    fix = (
        f"Run sashimi config set{scope} ffmpeg.path <an FFmpeg's ffmpeg>, or sashimi config unset{scope} "
        "ffmpeg.path so sashimi uses the FFmpeg on PATH."
    )
    return os.path.expanduser(value.strip()), origin, fix


def video_extensions() -> list[str] | None:
    """The optional ``video_extensions`` list from the TOML [thumbs] section."""
    raw, _ = load_raw_config()
    value = _section(raw, "thumbs").get("video_extensions")
    return [str(ext).lower() for ext in value] if isinstance(value, list) else None
