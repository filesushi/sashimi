# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import patch

import pytest

from sashimi import sheets
from sashimi.config import (
    CONTACT_SHEET,
    AnimatedGifSheetConfig,
    AnimatedWebpSheetConfig,
    JpegSheetConfig,
    PngSheetConfig,
)
from sashimi.core.probe import MediaInfo
from sashimi.errors import InvalidOptionsError, MediaTooLargeError, UnsupportedMediaError


def fake_probe(**fields):
    info = MediaInfo(container="matroska,webm", duration=60.0, has_video=True, width=640, height=480, **fields)
    return patch("sashimi.core.sheets.static.probe", return_value=info)


@pytest.mark.parametrize("target, expected", [("jpg", "jpeg"), ("JPEG", "jpeg"), (" PNG ", "png"), ("webp", "webp")])
def test_sheet_target_aliases(target, expected):
    assert CONTACT_SHEET.from_params({"target": target}).target == expected


@pytest.mark.parametrize(
    "params, model",
    [
        ({}, JpegSheetConfig),
        ({"target": "png"}, PngSheetConfig),
        ({"animated": True}, AnimatedWebpSheetConfig),
        ({"animated": "on", "target": "gif", "fps": 8}, AnimatedGifSheetConfig),
        ({"animated": "false", "target": "jpeg"}, JpegSheetConfig),
    ],
)
def test_one_family_picks_still_or_animated(params, model):
    assert type(CONTACT_SHEET.from_params(params)) is model


@pytest.mark.parametrize(
    "params, message",
    [
        ({"target": "gif"}, "Still contact sheets can be JPEG, PNG or WebP. GIF sheets must be animated."),
        ({"animated": True, "target": "png"}, "Animated contact sheets can be WebP or GIF."),
        ({"target": "tiff"}, "Invalid value for target: must be jpeg, png or webp."),
        ({"fps": 12}, "Unknown option: fps."),
        ({"target": "png", "quality": 80}, "Unknown option: quality."),
        ({"animated": True, "target": "gif", "quality": 80}, "Unknown option: quality."),
        ({"animated": "maybe"}, "Invalid value for animated: input should be a valid boolean."),
        (
            {"cols": 20, "width": 400},
            "These settings would make the contact sheet's tiles too small. Use fewer columns or a wider sheet.",
        ),
        ({"header_font_path": "/etc/passwd"}, "Unknown option: header_font_path."),
        ({"emoji_dir": "/tmp"}, "Unknown option: emoji_dir."),
    ],
)
def test_sheet_options_are_validated(params, message):
    with pytest.raises(InvalidOptionsError) as e:
        CONTACT_SHEET.from_params(params)
    assert e.value.user_message == message


def test_target_defaults_follow_animated():
    assert CONTACT_SHEET.model_for(animated=False) is JpegSheetConfig
    assert CONTACT_SHEET.model_for(animated=True) is AnimatedWebpSheetConfig
    assert AnimatedWebpSheetConfig.model_fields["quality"].default == 75
    assert JpegSheetConfig.model_fields["quality"].default == 85


@pytest.mark.parametrize("config", [JpegSheetConfig(), AnimatedWebpSheetConfig()])
def test_video_without_aspect_ratio_is_unsupported(config, tmp_path):
    with fake_probe(display_aspect_ratio=None), pytest.raises(UnsupportedMediaError) as e:
        sheets.render("in.blob", tmp_path / "r", config)
    assert e.value.user_message == "This file doesn't look like a video we can read."
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("config", [JpegSheetConfig(), AnimatedWebpSheetConfig()])
def test_very_wide_videos_are_refused(config, tmp_path):
    with fake_probe(display_aspect_ratio=30.0), pytest.raises(MediaTooLargeError) as e:
        sheets.render("in.blob", tmp_path / "r", config)
    assert e.value.user_message == "This video is too tall or too wide to make a contact sheet."
    assert list(tmp_path.iterdir()) == []


def test_auto_grid_with_a_huge_gap_is_refused_at_run_time(tmp_path):
    config = JpegSheetConfig(width=320, gap=30)
    with fake_probe(display_aspect_ratio=0.5), pytest.raises(InvalidOptionsError):
        sheets.render("in.blob", tmp_path / "r", config)


def test_static_sheet_refuses_very_tall_layouts(tmp_path):
    with fake_probe(display_aspect_ratio=0.01), pytest.raises(MediaTooLargeError):
        sheets.render("in.blob", tmp_path / "r", JpegSheetConfig())
    assert list(tmp_path.iterdir()) == []


def test_static_sheet_refuses_too_many_pixels(tmp_path):
    config = PngSheetConfig(width=8000, cols=1, rows=1)
    with fake_probe(display_aspect_ratio=0.75), pytest.raises(MediaTooLargeError):
        sheets.render("in.blob", tmp_path / "r", config)


def test_animated_sheet_refuses_very_tall_layouts(tmp_path):
    config = AnimatedWebpSheetConfig(cols=1, rows=2, fps=1, sample_duration=1)
    with fake_probe(display_aspect_ratio=0.05), pytest.raises(MediaTooLargeError):
        sheets.render("in.blob", tmp_path / "r", config)
