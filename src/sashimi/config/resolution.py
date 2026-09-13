import copy
from typing import Any, Dict, Optional
from .manager import load_raw_config, _recursive_merge
from .schemas.core import SashimiConfig


def unflatten_cli_args(flat_args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converts flat CLI arguments into nested dictionary structure.
    If the input is already nested under a known section, it leaves it intact.
    """
    nested: Dict[str, Any] = {}

    # Check if the dictionary is already properly nested
    if any(k in flat_args for k in ["thumbs", "animated_thumbs", "global"]):
        return flat_args

    # Map for flattening raw CLI keyword arguments
    static_fields = {"jpeg_quality"}
    animated_fields = {"output_format", "sample_duration", "output_fps", "loop"}
    shared_fields = {"output_width", "gap", "cols", "rows", "start_offset_percent", "end_offset_percent"}
    global_fields = {"verbose", "priority"}

    for key, value in flat_args.items():
        if value is None:
            continue

        if key in shared_fields or key in static_fields:
            if "thumbs" not in nested:
                nested["thumbs"] = {}
            nested["thumbs"][key] = value

        if key in shared_fields or key in animated_fields:
            if "animated_thumbs" not in nested:
                nested["animated_thumbs"] = {}
            nested["animated_thumbs"][key] = value

        elif key in global_fields:
            if "global" not in nested:
                nested["global"] = {}
            nested["global"][key] = value

    return nested


def resolve_config(cli_overrides: Optional[Dict[str, Any]] = None) -> SashimiConfig:
    if cli_overrides is None:
        cli_overrides = {}

    raw_dict, _ = load_raw_config()
    nested_cli_overrides = unflatten_cli_args(cli_overrides)
    
    # CRITICAL FIX: Deepcopy the raw config so we don't poison the memory cache
    final_dict = _recursive_merge(copy.deepcopy(raw_dict), nested_cli_overrides)

    return SashimiConfig(**final_dict)
