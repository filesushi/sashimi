# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""What FileSushi's web app and workers rely on, beyond the models' fields and defaults (docs/FILESUSHI_HANDBACK.md)."""

import types
import typing

import annotated_types
import pytest

from sashimi import config
from sashimi.errors import (
    InvalidOptionsError,
    MediaTooLargeError,
    MediaTooLongError,
    MediaTooShortError,
    NoAudioTrackError,
    UnsupportedMediaError,
    UserInputError,
)

FAMILIES = {
    "IMAGE_CONVERT": (config.IMAGE_CONVERT, "target", {"target": "png"}),
    "IMAGE_RESIZE": (config.IMAGE_RESIZE, "target", {"target": "webp"}),
    "VIDEO_CONVERT": (config.VIDEO_CONVERT, "target", {"target": "mp4"}),
    "VIDEO_TRANSFORM": (config.VIDEO_TRANSFORM, "target", {"target": "webm"}),
    "VIDEO_TRIM": (config.VIDEO_TRIM, "container", {"container": "webm", "accurate": True}),
    "AUDIO_CONVERT": (config.AUDIO_CONVERT, "target", {"target": "mp3"}),
    "CONTACT_SHEET": (config.CONTACT_SHEET, "target", {"target": "gif", "animated": True}),
}
FORM_FIELDS = {
    "quality": 80,
    "crf": 20,
    "fps": 10,
    "sample_rate": 44100,
    "start_seconds": 1.5,
    "max_width": 640,
    "mute": True,
    "cols": 4,
    "not_an_option": "ignored",
}
ALL_MODELS = sorted(
    {model for family, _, _ in FAMILIES.values() for model in family.models} | {config.IcoConfig},
    key=lambda model: model.__name__,
)


@pytest.mark.parametrize("name", FAMILIES)
def test_key_names_the_discriminating_option(name):
    family, key, _ = FAMILIES[name]
    assert family.key == key


@pytest.mark.parametrize("name", FAMILIES)
def test_model_for_ignores_options_that_do_not_pick_the_model(name):
    family, _, discriminators = FAMILIES[name]
    expected = family.model_for(**discriminators)
    assert family.model_for(**{**FORM_FIELDS, **discriminators}) is expected
    assert family.model_for(**{**FORM_FIELDS, "target": "jpeg", **discriminators}) is expected


def _numeric(annotation):
    args = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
    if typing.get_origin(annotation) in (typing.Union, types.UnionType) and len(args) == 1:
        annotation = args[0]
    return annotation in (int, float)


def _maximum(schema):
    if "maximum" in schema:
        return schema["maximum"]
    return next((branch["maximum"] for branch in schema.get("anyOf", []) if "maximum" in branch), None)


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda model: model.__name__)
def test_every_bounded_number_has_an_inclusive_upper_bound_in_its_metadata(model):
    schema = model.model_json_schema()["properties"]
    for name, field in model.model_fields.items():
        if not _numeric(field.annotation):
            continue
        assert "exclusiveMaximum" not in str(schema[name]), name
        upper = [m.le for m in field.metadata if isinstance(m, annotated_types.Le)]
        maximum = _maximum(schema[name])
        assert upper == ([] if maximum is None else [maximum]), name


def test_bounds_filesushi_reads():
    def upper(model, name):
        return next(m.le for m in model.model_fields[name].metadata if isinstance(m, annotated_types.Le))

    assert upper(config.Mp4VideoConfig, "crf") == upper(config.Mp4TransformConfig, "crf") == 51
    assert upper(config.H264TrimConfig, "crf") == 51
    assert upper(config.WebmVideoConfig, "crf") == upper(config.WebmTransformConfig, "crf") == 63
    assert upper(config.Vp9TrimConfig, "crf") == 63


def test_keyframe_note_text():
    assert config.KEYFRAME_NOTE == (
        "Trimming without re-encoding is instant and keeps the original quality, but cuts can only "
        "land on keyframes, so your clip may start a moment before the time you chose."
    )


@pytest.mark.parametrize(
    "error_type",
    [
        UserInputError,
        UnsupportedMediaError,
        NoAudioTrackError,
        MediaTooLongError,
        MediaTooShortError,
        MediaTooLargeError,
        InvalidOptionsError,
    ],
)
def test_user_errors_take_a_message_and_a_detail(error_type):
    error = error_type("This image is too large to convert safely.", detail="70000000 px")
    assert error.user_message == "This image is too large to convert safely."
    assert error.detail == "70000000 px"
    assert str(error) == "70000000 px"
    plain = error_type("A sentence.")
    assert plain.detail is None and str(plain) == "A sentence."
