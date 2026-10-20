# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""The config module: import isolation, one model per target, and no field that its converter ignores."""

import io
import subprocess
import sys

import pytest
from PIL import Image, ImageCms

from sashimi import audio, config, video
from sashimi.core.image import _render
from sashimi.errors import InvalidOptionsError


def test_importing_config_loads_only_pydantic():
    code = (
        "import sys, sashimi.config\n"
        "loaded = sorted(m for m in sys.modules if m.startswith(('PIL', 'subprocess', 'sashimi.core', 'sashimi.cli')))\n"
        "print(loaded)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"


@pytest.mark.parametrize(
    "family, targets",
    [
        (config.IMAGE_CONVERT, ("jpeg", "png", "webp", "gif", "bmp", "tiff")),
        (config.IMAGE_RESIZE, ("jpeg", "png", "webp", "gif", "bmp", "tiff")),
        (config.VIDEO_CONVERT, ("gif", "webp", "webm", "mp4")),
        (config.VIDEO_TRANSFORM, ("mp4", "webm")),
        (config.VIDEO_PREVIEW, ("gif", "webp")),
        (config.VIDEO_TRIM, ("mkv", "mp4", "webm", "mov")),
        (config.AUDIO_CONVERT, ("mp3", "m4a", "ogg", "wav", "flac")),
        (config.CONTACT_SHEET, ("jpeg", "png", "webp", "gif")),
    ],
)
def test_family_targets(family, targets):
    assert family.targets == targets
    assert all(issubclass(model, config.ParamsModel) for model in family.models)


def test_defaults_are_concrete():
    assert config.Mp4VideoConfig.model_fields["crf"].default == 23
    assert config.WebmVideoConfig.model_fields["crf"].default == 34
    assert config.Mp4VideoConfig.model_fields["preset"].default == "veryfast"
    assert config.WebmTransformConfig.model_fields["audio_bitrate_kbps"].default == 96
    assert config.GifVideoConfig.model_fields["duration_seconds"].default == config.DEFAULT_GIF_SECONDS
    assert config.WebpImageConfig.model_fields["webp_method"].default == 4
    assert config.FlacAudioConfig.model_fields["flac_compression_level"].default == 5
    assert config.Mp3AudioConfig.model_fields["bitrate_kbps"].default == 192
    none_defaults = {
        (model.__name__, name)
        for family in (config.IMAGE_CONVERT, config.VIDEO_CONVERT, config.AUDIO_CONVERT, config.VIDEO_TRIM)
        for model in family.models
        for name, field in model.model_fields.items()
        if field.default is None and not field.is_required()
    }
    assert {name for _, name in none_defaults} == {
        "max_width",
        "max_height",
        "duration_seconds",
        "end_seconds",
        "sample_rate",
    }


ALL_MODELS = {
    model
    for family in (
        config.IMAGE_CONVERT,
        config.IMAGE_RESIZE,
        config.VIDEO_CONVERT,
        config.VIDEO_TRANSFORM,
        config.VIDEO_TRIM,
        config.VIDEO_PREVIEW,
        config.AUDIO_CONVERT,
        config.CONTACT_SHEET,
    )
    for model in family.models
} | {config.IcoConfig, config.FaviconPackConfig}


@pytest.mark.parametrize("model", sorted(ALL_MODELS, key=lambda m: m.__name__), ids=lambda m: m.__name__)
def test_every_field_is_described(model):
    assert [name for name, field in model.model_fields.items() if not field.description] == []
    schema = model.model_json_schema()["properties"]
    assert all(prop.get("description") for prop in schema.values())


def test_limits_are_field_bounds():
    def bound(model, name):
        return next(m.le for m in model.model_fields[name].metadata if hasattr(m, "le"))

    assert bound(config.GifVideoConfig, "duration_seconds") == config.MAX_GIF_SECONDS == 30
    assert bound(config.WebpVideoConfig, "duration_seconds") == 30
    assert bound(config.Mp4VideoConfig, "duration_seconds") == config.MAX_VIDEO_SECONDS == 600
    assert bound(config.Vp9TrimConfig, "duration_seconds") == 600
    assert bound(config.FlacAudioConfig, "duration_seconds") == config.MAX_AUDIO_SECONDS == 3600
    assert bound(config.Mp4TransformConfig, "crf") == 51


def test_gif_length_message_reads_as_a_sentence():
    with pytest.raises(InvalidOptionsError) as e:
        config.VIDEO_CONVERT.from_params({"duration_seconds": 31}, target="gif")
    assert e.value.user_message == "GIFs can be at most 30 seconds long."
    with pytest.raises(InvalidOptionsError) as e:
        config.VIDEO_CONVERT.from_params({"duration_seconds": -1}, target="gif")
    assert e.value.user_message == "Invalid value for duration_seconds: input should be greater than 0."


def test_no_model_names_a_path():
    for family in (config.IMAGE_CONVERT, config.VIDEO_CONVERT, config.CONTACT_SHEET, config.AUDIO_CONVERT):
        for model in family.models:
            assert not [name for name in model.model_fields if "path" in name or name.endswith("_dir")]


VIDEO_ALTERNATIVES = {
    "start_seconds": 1,
    "duration_seconds": 5,
    "end_seconds": 5,
    "max_width": 320,
    "fps": 10,
    "loop": False,
    "dither": "none",
    "palette_mode": "full",
    "quality": 50,
    "lossless": True,
    "crf": 30,
    "preset": "slow",
    "speed": 4,
    "audio_bitrate_kbps": 192,
    "mute": True,
    "rotate": 90,
    "flip": "vertical",
    "bitrate_kbps": 256,
    "mono": True,
    "sample_rate": 48000,
    "wav_bit_depth": 24,
    "flac_compression_level": 8,
}
DISCRIMINATORS = {"target", "container", "accurate"}


def _builder(model):
    if model in config.VIDEO_CONVERT.models:
        return video.build_convert_command, {}
    if model in config.VIDEO_TRANSFORM.models:
        return video.build_transform_command, {"flip": "horizontal"}
    if model in config.VIDEO_TRIM.models:
        return video.build_trim_command, {"container": "mkv"} if model is not config.Vp9TrimConfig else {}
    return audio.build_convert_command, {}


@pytest.mark.parametrize(
    "model",
    [
        *config.VIDEO_CONVERT.models,
        *config.VIDEO_TRANSFORM.models,
        *config.VIDEO_TRIM.models,
        *config.AUDIO_CONVERT.models,
    ],
    ids=lambda m: m.__name__,
)
def test_every_ffmpeg_option_changes_the_command(model):
    build, base = _builder(model)
    reference = build("i", "o", model(**base), duration=100.0, threads=1)
    for name in set(model.model_fields) - DISCRIMINATORS:
        changed = model(**{**base, name: VIDEO_ALTERNATIVES[name]})
        assert build("i", "o", changed, duration=100.0, threads=1) != reference, name


IMAGE_ALTERNATIVES = {
    "max_width": 16,
    "max_height": 8,
    "resample": "nearest",
    "background_color": (0, 0, 0),
    "auto_orient": False,
    "keep_exif": True,
    "keep_icc": False,
    "preserve_transparency": False,
    "quality": 50,
    "lossless": True,
    "progressive": True,
    "jpeg_subsampling": "4:4:4",
    "png_compress_level": 1,
    "webp_method": 6,
    "tiff_compression": "none",
}
IMAGE_CONTEXT = {"resample": {"max_width": 16}, "background_color": {"preserve_transparency": False}}


def _photo():
    img = Image.effect_noise((64, 32), 40).convert("RGBA")
    img.putpixel((0, 0), (0, 0, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x010F] = "TestCam"
    buf = io.BytesIO()
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    img.save(buf, "PNG", exif=exif, icc_profile=srgb)
    return Image.open(io.BytesIO(buf.getvalue()))


def _rendered(model, **fields):
    img, kwargs = _render(_photo(), model(**fields))
    return img.mode, img.size, img.tobytes(), {k: v for k, v in kwargs.items() if k != "exif"}, "exif" in kwargs


@pytest.mark.parametrize("model", config.IMAGE_CONVERT.models, ids=lambda m: m.__name__)
def test_every_image_option_changes_the_output(model):
    for name in set(model.model_fields) - DISCRIMINATORS:
        context = {k: v for k, v in IMAGE_CONTEXT.get(name, {}).items() if k in model.model_fields}
        if model is config.TiffImageConfig and name == "quality":
            context = {"tiff_compression": "jpeg"}
        default = model.model_fields[name].default
        alternative = (not default) if isinstance(default, bool) else IMAGE_ALTERNATIVES[name]
        assert _rendered(model, **context) != _rendered(model, **context, **{name: alternative}), name
