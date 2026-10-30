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
from sashimi.config._limits import (
    duration_text,
    megabytes,
    megapixels,
    over_limit_texts,
    resolve_limits,
    too_long_text,
)
from sashimi.core.sheets.static import check_sheet_size
from sashimi.errors import InvalidOptionsError, MediaTooLargeError


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
        "limit is 64 megapixels. Use fewer rows or a smaller width."
    )
    assert e.value.limit == "max_sheet_pixels"
    check_sheet_size(8000, 9000, "png", Limits(max_sheet_pixels=100_000_000))


@pytest.mark.parametrize(
    "kind, length, start, limit, said",
    [
        ("video", 600.4, 0, 600, "This video is 10 minutes 0.4 seconds long, and the limit is 10 minutes."),
        ("video", 3599.6, 0, 3599, "This video is 1 hour long, and the limit is 59 minutes 59 seconds."),
        ("audio", 30.3, 0, 30, "This audio is 30.3 seconds long, and the limit is 30 seconds."),
        ("video", 600.04, 0, 600, "This video is 10 minutes 0.04 seconds long, and the limit is 10 minutes."),
        ("video", 600.0004, 0, 600, "This video is just over 10 minutes long, and the limit is 10 minutes."),
        ("video", 3661, 0, 3660, "This video is 1 hour 1 minute 1 second long, and the limit is 1 hour 1 minute."),
        ("video", 10.5, 0, 10.25, "This video is 10.5 seconds long, and the limit is 10.25 seconds."),
        ("video", 700, 200, 600, "From 200s, this video runs 11 minutes 40 seconds, and the limit is 10 minutes."),
    ],
)
def test_a_refused_length_reads_as_more_than_the_limit(kind, length, start, limit, said):
    assert too_long_text(kind, length, start, limit).startswith(said + " ")


def test_a_refused_length_never_reads_as_the_limit_or_less():
    for limit in (5, 30, 59.9, 60, 600, 3599, 3600, 3660, 7200):
        for extra in (0.0004, 0.004, 0.04, 0.3, 0.4, 0.5, 0.6, 1, 59.5, 61):
            length_words, limit_words = over_limit_texts(limit + extra, limit)
            assert length_words != limit_words, (limit, extra)
