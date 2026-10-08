import copy
import logging
import pathlib
import tomllib
import tomlkit
import tomlkit.items
from functools import lru_cache
from typing import Any, MutableMapping

from .defaults import _DEFAULT_CONFIG_STRING

logger = logging.getLogger("sashimi")


GLOBAL_CONFIG_DIR = pathlib.Path.home() / ".sashimi"
GLOBAL_CONFIG_FILENAME = "sashimi.toml"
PROJECT_CONFIG_FILENAMES = ["sashimi.toml", ".sashimi.toml"]

DEFAULT_CONFIG_DICT: dict[str, Any] = tomllib.loads(_DEFAULT_CONFIG_STRING)


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


def _create_default_global_config_if_not_exists() -> None:
    """Creates the default global config file from the template string if it doesn't exist."""
    global_config_path = get_global_config_path()
    if global_config_path.exists():
        return

    try:
        global_config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(global_config_path, "w", encoding="utf-8") as f:
            f.write(_DEFAULT_CONFIG_STRING)
    except Exception as e:
        logger.error("Failed to write default global config: %s", e)


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
    """
    Loads configuration by merging project-specific settings over global settings,
    which are all merged over the in-memory defaults.
    Precedence: Project > Global > Defaults.
    """
    _create_default_global_config_if_not_exists()
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
