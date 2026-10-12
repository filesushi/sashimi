import os
import pathlib
import pytest
from unittest.mock import patch
import tomlkit

from sashimi.cli import settings as manager


@pytest.fixture(autouse=True)
def reset_cache():
    """Ensure the lru_cache is cleared before every test."""
    manager.load_raw_config.cache_clear()
    yield


def test_recursive_merge():
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    new = {"b": {"c": 99, "e": 4}, "f": 5}

    result = manager._recursive_merge(base, new)

    assert result["a"] == 1
    assert result["b"]["c"] == 99
    assert result["b"]["d"] == 3
    assert result["b"]["e"] == 4
    assert result["f"] == 5


def test_get_global_config_path(fs):
    home = pathlib.Path.home()
    expected = home / ".sashimi" / "sashimi.toml"
    assert str(manager.get_global_config_path()) == str(expected)


def test_find_project_config_path(fs):

    root_dir = "/fake_proj"
    sub_dir = "/fake_proj/nested"
    fs.create_dir(sub_dir)
    os.chdir(sub_dir)

    assert manager.find_project_config_path() is None

    fs.create_file(f"{root_dir}/sashimi.toml")
    assert manager.find_project_config_path() == pathlib.Path(f"{root_dir}/sashimi.toml")

    fs.create_file(f"{sub_dir}/.sashimi.toml")
    assert manager.find_project_config_path() == pathlib.Path(f"{sub_dir}/.sashimi.toml")

    with patch("sashimi.cli.settings.get_global_config_path") as mock_global:
        mock_global.return_value = pathlib.Path(f"{sub_dir}/.sashimi.toml")

        assert manager.find_project_config_path() == pathlib.Path(f"{root_dir}/sashimi.toml")


def test_reading_settings_creates_no_files(fs):
    os.chdir("/")
    config, path = manager.load_raw_config()
    assert config == manager.DEFAULT_CONFIG_DICT
    assert path == manager.get_global_config_path()
    assert not path.exists() and not manager.GLOBAL_CONFIG_DIR.exists()


def test_load_config_file(fs):

    assert manager._load_config_file(pathlib.Path("/missing.toml")) == {}

    valid_path = pathlib.Path("/valid.toml")
    fs.create_file(str(valid_path), contents='[test]\nkey = "value"')
    assert manager._load_config_file(valid_path) == {"test": {"key": "value"}}

    invalid_path = pathlib.Path("/invalid.toml")
    fs.create_file(str(invalid_path), contents="[test]\nbad syntax")
    assert manager._load_config_file(invalid_path) == {}


def test_load_raw_config(fs):
    global_path = manager.get_global_config_path()
    fs.create_file(str(global_path), contents="[global]\nverbose = 1\n[thumbs]\ncols = 5")

    os.chdir("/")
    fs.create_file("/sashimi.toml", contents="[thumbs]\ncols = 10\nrows = 8")

    config, path = manager.load_raw_config()

    assert config["global"]["verbose"] == 1
    assert config["thumbs"]["cols"] == 10
    assert config["thumbs"]["rows"] == 8
    assert path == pathlib.Path("/sashimi.toml")


def test_set_config_value(fs):
    os.chdir("/")

    manager.set_config_value("global", "verbose", 5, scope="global")
    global_path = manager.get_global_config_path()
    assert "verbose = 5" in global_path.read_text()

    manager.set_config_value("thumbs", "cols", 4, scope="project")
    proj_path = pathlib.Path("/sashimi.toml")
    assert "cols = 4" in proj_path.read_text()

    manager.set_config_value("new_section", "key", "val", scope="project")
    assert "[new_section]" in proj_path.read_text()
    assert 'key = "val"' in proj_path.read_text()

    proj_path.write_text("invalid [ toml")
    manager.set_config_value("thumbs", "cols", 2, scope="project")
    assert "cols = 2" in proj_path.read_text()
    assert "invalid" not in proj_path.read_text()


@patch("sashimi.cli.settings.open", side_effect=IOError("Write failed"))
def test_set_config_value_write_exception(mock_open, fs):
    with pytest.raises(OSError, match="Failed to write to config file"):
        manager.set_config_value("thumbs", "cols", 5)


def test_remove_config_value(fs):
    os.chdir("/")
    proj_path = pathlib.Path("/sashimi.toml")
    fs.create_file(str(proj_path), contents="[thumbs]\ncols = 5\nrows = 5\n[global]\nverbose = 1")

    assert manager.remove_config_value("thumbs", "cols", scope="project") is True
    content = proj_path.read_text()
    assert "rows = 5" in content
    assert "cols = 5" not in content

    assert manager.remove_config_value("thumbs", "rows", scope="project") is True
    content = proj_path.read_text()
    assert "[thumbs]" not in content
    assert "[global]" in content

    assert manager.remove_config_value("thumbs", "cols", scope="project") is False
    assert manager.remove_config_value("fake", "key", scope="project") is False

    global_path = manager.get_global_config_path()
    fs.create_file(str(global_path), contents="[global]\nverbose = 1")
    assert manager.remove_config_value("global", "verbose", scope="global") is True

    os.remove(str(proj_path))
    assert manager.remove_config_value("global", "verbose", scope="project") is False


@patch("sashimi.cli.settings.tomlkit.load", side_effect=Exception("Read error"))
def test_remove_config_value_read_exception(mock_load, fs):
    os.chdir("/")
    fs.create_file("/sashimi.toml", contents="dummy")
    assert manager.remove_config_value("thumbs", "cols") is False


@patch("sashimi.cli.settings.tomlkit.dump", side_effect=Exception("Write error"))
def test_remove_config_value_write_exception(mock_dump, fs):
    os.chdir("/")
    fs.create_file("/sashimi.toml", contents="[thumbs]\ncols = 5")
    with pytest.raises(OSError, match="Failed to write to config file"):
        manager.remove_config_value("thumbs", "cols")


@patch("sashimi.cli.settings.tomlkit.load", side_effect=Exception)
def test_set_config_value_invalid_toml(mock_load, fs):
    """Hits the except Exception fallback that creates a new document."""
    fs.create_file("/sashimi.toml", contents="invalid [")
    manager.set_config_value("global", "verbose", 1, scope="project")
    assert "verbose = 1" in pathlib.Path("/sashimi.toml").read_text()


def test_remove_config_value_not_a_table(fs):
    """Hits the branch where a config section exists but isn't a TOML table."""
    fs.create_file("/sashimi.toml", contents='global = "just_a_string"')
    assert manager.remove_config_value("global", "verbose", scope="project") is False


def test_unknown_keys():
    data = {
        "global": {"verbose": 1, "colour": "red"},
        "thumbs": {"static_width": 1920, "jpeg_quality": 80, "fps": 10, "output_width": 2560, "start_offset": "5%"},
        "animated_thumbs": {"animated_width": 960, "target": "gif", "static_width": 1, "video_extensions": []},
        "extras": {"x": 1},
    }
    assert manager.unknown_keys(data) == [
        "global.colour",
        "thumbs.output_width",
        "thumbs.start_offset",
        "animated_thumbs.static_width",
        "animated_thumbs.video_extensions",
        "extras",
    ]
