# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pytest
from pydantic import ValidationError

from sashimi.config import IMAGE_CONVERT, VIDEO_CONVERT, GifVideoConfig, JpegImageConfig, PngImageConfig
from sashimi.errors import InvalidOptionsError, MediaTooLongError, NoAudioTrackError, UserInputError


def test_none_and_omitted_mean_default():
    a = VIDEO_CONVERT.from_params({"max_width": None, "fps": None}, target="gif")
    b = VIDEO_CONVERT.from_params({}, target="gif")
    assert a == b == GifVideoConfig() and a.max_width == 480 and a.fps == 12


def test_unknown_keys_are_rejected_with_a_user_message():
    with pytest.raises(InvalidOptionsError) as e:
        VIDEO_CONVERT.from_params({"colour": "red"}, target="gif")
    assert e.value.user_message == "Unknown option: colour."


def test_invalid_value_message_names_the_option():
    with pytest.raises(InvalidOptionsError) as e:
        IMAGE_CONVERT.from_params({"quality": 101}, target="jpeg")
    assert e.value.user_message == "Invalid value for quality: input should be less than or equal to 100."


@pytest.mark.parametrize(
    "fields, message",
    [
        ({}, "Missing option: target."),
        ({"target": "tga"}, "Invalid value for target: must be jpeg, png, webp, gif, bmp, tiff, avif or apng."),
    ],
)
def test_missing_or_unknown_target(fields, message):
    with pytest.raises(InvalidOptionsError) as e:
        IMAGE_CONVERT.from_params({}, **fields)
    assert e.value.user_message == message


def test_direct_construction_still_raises_pydantic_errors():
    with pytest.raises(ValidationError):
        JpegImageConfig(target="png")  # type: ignore[arg-type]


def test_jpg_alias_and_case():
    assert IMAGE_CONVERT.from_params({}, target="JPG").target == "jpeg"
    assert JpegImageConfig.from_params({"target": "jpg"}).target == "jpeg"


def test_explicit_fields_win_over_params():
    assert type(IMAGE_CONVERT.from_params({"target": "png"}, target="webp")).__name__ == "WebpImageConfig"


def test_configs_are_immutable():
    config = PngImageConfig()
    with pytest.raises(ValidationError):
        config.quality = 10  # type: ignore[misc]


def test_error_hierarchy():
    assert issubclass(NoAudioTrackError, UserInputError)
    assert issubclass(MediaTooLongError, UserInputError)
    err = MediaTooLongError("Plain sentence.", detail="internal detail")
    assert err.user_message == "Plain sentence." and str(err) == "internal detail"
