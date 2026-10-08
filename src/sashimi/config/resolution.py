import copy
from typing import Any, Dict, Optional
from .manager import load_raw_config, _recursive_merge
from .schemas.core import SashimiConfig, GlobalConfig
from .schemas.thumbs import StaticThumbsConfig, AnimatedThumbsConfig

# Each flat key is routed to every section whose schema declares it, so new schema fields
# are picked up automatically (shared thumbs fields land in both thumbs sections).
_SECTION_FIELDS: Dict[str, frozenset] = {
    "thumbs": frozenset(StaticThumbsConfig.model_fields),
    "animated_thumbs": frozenset(AnimatedThumbsConfig.model_fields),
    "global": frozenset(GlobalConfig.model_fields),
}


def unflatten_cli_args(flat_args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converts flat CLI arguments into nested dictionary structure.
    If the input is already nested under a known section, it leaves it intact.
    Keys that no section declares are dropped.
    """
    if any(k in flat_args for k in _SECTION_FIELDS):
        return flat_args

    nested: Dict[str, Any] = {}
    for key, value in flat_args.items():
        if value is None:
            continue
        for section, fields in _SECTION_FIELDS.items():
            if key in fields:
                nested.setdefault(section, {})[key] = value

    return nested


def resolve_config(cli_overrides: Optional[Dict[str, Any]] = None) -> SashimiConfig:
    if cli_overrides is None:
        cli_overrides = {}

    raw_dict, _ = load_raw_config()
    raw_dict_copy = copy.deepcopy(raw_dict)

    # --- ADDED: Unified TOML Mapping ---
    # The user's TOML only has a [thumbs] section for UX simplicity.
    # We clone it into 'animated_thumbs' so Pydantic can map both schemas from the single TOML block.
    if "thumbs" in raw_dict_copy:
        # Merge thumbs into animated_thumbs in case animated_thumbs already exists partially
        existing_animated = raw_dict_copy.get("animated_thumbs", {})
        raw_dict_copy["animated_thumbs"] = _recursive_merge(copy.deepcopy(raw_dict_copy["thumbs"]), existing_animated)

    nested_cli_overrides = unflatten_cli_args(cli_overrides)

    # Merge CLI overrides over the TOML config
    final_dict = _recursive_merge(raw_dict_copy, nested_cli_overrides)

    # Pydantic safely parses final_dict.
    # StaticThumbsConfig will ignore 'animated_width' and AnimatedThumbsConfig will ignore 'static_width'.
    return SashimiConfig(**final_dict)
