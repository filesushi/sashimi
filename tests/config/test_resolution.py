import pytest
from unittest.mock import patch
from sashimi.config import resolution
from sashimi.config.schemas.core import SashimiConfig


def test_unflatten_cli_args():

    flat_args = {
        "cols": 5,
        "verbose": 2,
        "sample_duration": 4.5,
        "loop": False,
        "unknown_field": "ignored_in_pydantic",
        "rows": None,
    }

    nested = resolution.unflatten_cli_args(flat_args)

    assert "thumbs" in nested
    assert nested["thumbs"]["cols"] == 5

    assert "animated_thumbs" in nested
    assert nested["animated_thumbs"]["cols"] == 5
    assert nested["animated_thumbs"]["sample_duration"] == 4.5
    assert nested["animated_thumbs"]["loop"] is False
    assert "rows" not in nested["animated_thumbs"]

    assert "global" in nested
    assert nested["global"]["verbose"] == 2


def test_unflatten_cli_args_empty():
    assert resolution.unflatten_cli_args({}) == {}
    assert resolution.unflatten_cli_args({"cols": None}) == {}


@patch("sashimi.config.resolution.load_raw_config")
def test_resolve_config(mock_load_raw):

    mock_load_raw.return_value = (
        {"global": {"verbose": 0, "priority": "low"}, "thumbs": {"cols": 2, "rows": 2}},
        None,
    )

    config = resolution.resolve_config()
    assert isinstance(config, SashimiConfig)
    assert config.global_settings.verbose == 0
    assert config.global_settings.priority == "low"
    assert config.thumbs.cols == 2

    overrides = {"cols": 10, "verbose": 3, "priority": None}
    config = resolution.resolve_config(cli_overrides=overrides)

    assert config.thumbs.cols == 10
    assert config.thumbs.rows == 2
    assert config.global_settings.verbose == 3
    assert config.global_settings.priority == "low"
