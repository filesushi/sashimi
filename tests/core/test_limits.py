# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""The caller's limits: the model, the checks that need no file, and the pure checks that need the file's size."""

import pytest

from sashimi.config import (
    VIDEO_CONVERT,
    VIDEO_PREVIEW,
    VIDEO_TRANSFORM,
    VIDEO_TRIM,
    CopyTrimConfig,
    JpegImageConfig,
    Limits,
    Mp3AudioConfig,
)
from sashimi.config._limits import duration_text, megabytes, megapixels, resolve_limits
from sashimi.core.probe import MediaInfo
from sashimi.core.sheets.static import check_sheet_size
from sashimi.errors import InvalidOptionsError, MediaTooLargeError
from sashimi.video import gif_frame_bytes


@pytest.mark.parametrize(
    "seconds, text",
    [
        (1, "1 second"),
        (0.5, "0.5 seconds"),
        (30, "30 seconds"),
        (90, "90 seconds"),
        (60, "1 minute"),
        (600, "10 minutes"),
        (3600, "60 minutes"),
        (5400, "90 minutes"),
        (7200, "2 hours"),
        (600.0, "10 minutes"),
    ],
)
def test_lengths_read_as_words(seconds, text):
    assert duration_text(seconds) == text


def test_sizes_read_as_megabytes_and_megapixels():
    assert megabytes(256 * 1024 * 1024) == "256 MB"
    assert megabytes(3 * 1024**3) == "3,072 MB"
    assert (megapixels(64_000_000), megapixels(12_345_678)) == ("64", "12.3")


@pytest.mark.parametrize("value", [0, -5, "lots", 1.5, float("inf"), True])
def test_invalid_limits_are_refused(value):
    with pytest.raises(InvalidOptionsError):
        Limits.from_params({"max_pixels": value})


def test_none_turns_a_limit_off_and_unknown_limits_are_refused():
    for off in (None, "none", "None", ""):
        assert Limits.from_params({"max_frame_bytes": off}).max_frame_bytes is None
    assert Limits.from_params({}) == Limits() == resolve_limits(None)
    custom = Limits(max_pixels=1)
    assert resolve_limits(custom) is custom
    with pytest.raises(InvalidOptionsError) as e:
        Limits.from_params({"max_seconds": 5})
    assert e.value.user_message == "Unknown option: max_seconds."


def test_configs_without_limits_pass_any_check():
    strict = Limits(**{name: 1 for name in Limits.model_fields})
    for config in (
        JpegImageConfig(),
        VIDEO_TRANSFORM.from_params({"rotate": 90}),
        CopyTrimConfig(container="mkv", end_seconds=100_000),
        VIDEO_TRIM.from_params({"accurate": True}, container="mp4"),
        Mp3AudioConfig(),
        VIDEO_CONVERT.from_params({}, target="mp4"),
    ):
        strict.check(config)


def test_each_length_limit_checks_only_its_own_outputs():
    Limits(max_video_seconds=5).check(VIDEO_CONVERT.from_params({"duration_seconds": 10}, target="gif"))
    Limits(max_gif_seconds=5).check(VIDEO_CONVERT.from_params({"duration_seconds": 10}, target="mp4"))
    Limits(max_gif_seconds=5).check(Mp3AudioConfig(duration_seconds=10))
    Limits(max_audio_seconds=5).check(VIDEO_PREVIEW.from_params({"samples": 10}, target="gif"))
    with pytest.raises(InvalidOptionsError):
        Limits(max_gif_seconds=5).check(VIDEO_PREVIEW.from_params({"samples": 3}, target="gif"))


def _video(width, height, duration=60.0):
    return MediaInfo(container="matroska", duration=duration, width=width, height=height, has_video=True)


def test_gif_frame_memory_is_counted_like_the_other_frame_budgets():
    config = VIDEO_CONVERT.from_params({}, target="gif")
    assert gif_frame_bytes(_video(1280, 720), config) == 120 * 480 * 270 * 3
    small = VIDEO_CONVERT.from_params({"duration_seconds": 30, "fps": 30, "max_width": 640}, target="gif")
    assert gif_frame_bytes(_video(320, 240, duration=10.0), small) == 300 * 320 * 240 * 3
    late = VIDEO_CONVERT.from_params({"start_seconds": 55}, target="gif")
    assert gif_frame_bytes(_video(1280, 720), late) == 60 * 480 * 270 * 3


def test_sheet_size_is_checked_against_the_format_and_the_limit():
    check_sheet_size(2560, 20000, "jpeg", None)
    check_sheet_size(2560, 70000, "png", Limits(max_sheet_pixels=None))
    with pytest.raises(MediaTooLargeError) as e:
        check_sheet_size(2560, 20000, "webp", Limits(max_sheet_pixels=None))
    assert e.value.user_message == (
        "These settings would make the contact sheet too large for WebP, which can be at most 16383 pixels on a "
        "side (2560x20000). Use fewer rows or a smaller width."
    )
    assert e.value.limit is None
    with pytest.raises(MediaTooLargeError) as e:
        check_sheet_size(8000, 9000, "png", None)
    assert e.value.user_message == (
        "These settings would make the contact sheet too large: it would have 72 megapixels (8000x9000), and the "
        "limit is 64. Use fewer rows or a smaller width."
    )
    assert e.value.limit == "max_sheet_pixels"
    check_sheet_size(8000, 9000, "png", Limits(max_sheet_pixels=100_000_000))
