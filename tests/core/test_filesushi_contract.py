# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""What FileSushi's web app and workers rely on, beyond the models' fields and defaults (docs/FILESUSHI_HANDBACK.md)."""

import types
import typing

import annotated_types
import pytest
from pydantic import ValidationError

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
    "VIDEO_PREVIEW": (config.VIDEO_PREVIEW, "target", {"target": "gif"}),
    "VIDEO_TRIM": (config.VIDEO_TRIM, "container", {"container": "webm", "accurate": True}),
    "AUDIO_CONVERT": (config.AUDIO_CONVERT, "target", {"target": "mp3"}),
    "CONTACT_SHEET": (config.CONTACT_SHEET, "target", {"target": "gif", "animated": True}),
    "IMAGE_EDIT": (config.IMAGE_EDIT, "target", {"target": "webp"}),
    "IMAGE_FRAMES": (config.IMAGE_FRAMES, "target", {"target": "gif"}),
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
    {model for family, _, _ in FAMILIES.values() for model in family.models}
    | {config.IcoConfig, config.FaviconPackConfig, config.FrameStripConfig, config.Limits},
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

    def lower(model, name):
        return next(m.ge for m in model.model_fields[name].metadata if isinstance(m, annotated_types.Ge))

    assert upper(config.Mp4VideoConfig, "crf") == upper(config.Mp4TransformConfig, "crf") == 51
    assert upper(config.H264TrimConfig, "crf") == 51
    assert upper(config.WebmVideoConfig, "crf") == upper(config.WebmTransformConfig, "crf") == 63
    assert upper(config.Vp9TrimConfig, "crf") == 63
    assert lower(config.GifPreviewConfig, "max_height") == 64
    assert upper(config.GifPreviewConfig, "fps") == config.MAX_ANIMATION_FPS == 50
    for model, name in (
        (config.GifPreviewConfig, "sample_duration"),
        (config.AnimatedGifSheetConfig, "sample_duration"),
    ):
        assert not [m for m in model.model_fields[name].metadata if isinstance(m, annotated_types.Le)], model


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


def test_new_in_0_6_0_for_filesushi():
    assert config.IcoConfig.model_fields["background_color"].default is None
    assert config.FaviconPackConfig.model_fields["background_color"].default == (255, 255, 255)
    assert config.FAVICON_PNG_SIZES == (16, 32, 48, 180, 192, 512)
    assert config.CONTACT_SHEET.model_for(animated=True).model_fields["cols"].default == "auto"


FILESUSHI = {
    "max_pixels": 24_000_000,
    "max_video_seconds": 600,
    "max_audio_seconds": 3600,
    "max_gif_seconds": 30,
    "max_preview_clips": 10,
    "max_frame_bytes": 400 * 1024 * 1024,
    "max_animation_bytes": 256 * 1024 * 1024,
    "max_sheet_pixels": 64_000_000,
}
"""The limits the hand-back tells FileSushi to pass, so that it accepts what it accepted before."""


def test_limits_filesushi_passes():
    limits = config.Limits(**FILESUSHI)
    assert set(FILESUSHI) == set(config.Limits.model_fields)
    assert limits == config.Limits.from_params({key: str(value) for key, value in FILESUSHI.items()})
    assert config.Limits.from_params({"max_video_seconds": "none"}).max_video_seconds is None
    assert config.Limits.unlimited() == config.Limits(**dict.fromkeys(FILESUSHI))
    defaults = config.Limits()
    assert (defaults.max_pixels, defaults.max_sheet_pixels) == (64_000_000, 64_000_000)
    assert (defaults.max_frame_bytes, defaults.max_animation_bytes) == (2**30, 2**30)
    assert [defaults.max_video_seconds, defaults.max_audio_seconds, defaults.max_gif_seconds] == [None] * 3
    assert defaults.max_preview_clips is None
    with pytest.raises(ValidationError):
        limits.max_pixels = 1


def test_refusals_name_the_limit():
    error = InvalidOptionsError("A sentence.", limit="max_gif_seconds")
    assert (error.limit, error.detail) == ("max_gif_seconds", None)
    assert UserInputError("A sentence.").limit is None


def test_old_limit_names_are_gone():
    for name in ("MAX_GIF_SECONDS", "MAX_VIDEO_SECONDS", "MAX_AUDIO_SECONDS", "MAX_PREVIEW_SAMPLES"):
        assert not hasattr(config, name), name
    for name in ("MAX_FRAME_BYTES", "MAX_ANIMATION_BYTES", "MAX_SHEET_PIXELS", "MAX_SHEET_HEIGHT"):
        assert not hasattr(config, name), name


def test_new_in_0_6_0_for_filesushis_editors():
    assert config.EDIT_QUALITY == 92
    assert config.JpegEditConfig.model_fields["quality"].default == 92
    assert config.MIN_FRAME_MS == 20
    assert config.CROP_ASPECTS == ("free", "1:1", "4:3", "16:9", "9:16")
    for model in config.IMAGE_EDIT.models:
        assert {name: model.model_fields[name].default for name in ("brightness", "contrast", "saturation")} == {
            "brightness": 1.0,
            "contrast": 1.0,
            "saturation": 1.0,
        }
        assert model.model_fields["crop_width"].default is None and model.model_fields["rotate"].default == 0
    assert config.GifFramesConfig.model_fields["keep"].default is None
    assert config.GifFramesConfig.from_params({"keep": "0,2-3", "speed": "1.5"}).keep == (0, 2, 3)
    assert "route" not in config.Mp4VideoConfig.model_fields


def test_mp4_routes_are_named():
    import typing

    from sashimi import video

    assert typing.get_args(video.VideoRoute) == ("copy", "copy_video", "encode")


WITH_METADATA = (
    *config.IMAGE_CONVERT.models,
    *config.IMAGE_RESIZE.models,
    *config.IMAGE_EDIT.models,
    *config.IMAGE_FRAMES.models,
    config.WebmVideoConfig,
    config.Mp4VideoConfig,
    *config.VIDEO_TRANSFORM.models,
    *config.VIDEO_TRIM.models,
    *config.AUDIO_CONVERT.models,
)
WITHOUT_METADATA = (
    config.BmpImageConfig,
    config.BmpResizeConfig,
    config.BmpEditConfig,
    config.GifVideoConfig,
    config.WebpVideoConfig,
    *config.VIDEO_PREVIEW.models,
    *config.CONTACT_SHEET.models,
    config.IcoConfig,
    config.FaviconPackConfig,
    config.FrameStripConfig,
)


def test_metadata_is_one_field_that_keeps_by_default():
    assert config.METADATA_MODES == ("keep", "remove_location", "remove_all")
    for model in WITH_METADATA:
        if model in WITHOUT_METADATA:
            continue
        field = model.model_fields["metadata"]
        assert field.default == "keep", model.__name__
        assert model.model_json_schema()["properties"]["metadata"]["enum"] == list(config.METADATA_MODES)
        assert "keep_exif" not in model.model_fields
    for model in WITHOUT_METADATA:
        assert "metadata" not in model.model_fields, model.__name__
    with pytest.raises(InvalidOptionsError) as e:
        config.IMAGE_CONVERT.from_params({"keep_exif": True}, target="jpeg")
    assert e.value.user_message == "Unknown option: keep_exif."


def test_metadata_tools_for_filesushi():
    import inspect

    from sashimi import metadata

    assert list(inspect.signature(metadata.strip).parameters) == [
        "input_path",
        "output_path",
        "mode",
        "keep_icc",
        "scratch_dir",
        "limits",
        "on_progress",
    ]
    assert list(inspect.signature(metadata.read).parameters) == ["input_path", "limits"]
    assert {"carriers", "has_location", "latitude", "longitude", "place", "has_color_profile"} <= set(
        metadata.MetadataInfo.model_fields
    )
    assert config.OPUS_MAX_KBPS_PER_CHANNEL == 256
