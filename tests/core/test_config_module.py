# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""The config module: import isolation, one model per target, and no field that its converter ignores."""

import io
import subprocess
import sys

import pytest
from PIL import Image, ImageCms
from PIL.PngImagePlugin import PngInfo

from sashimi import audio, config, image, video
from sashimi.core._imagemeta import Carriers
from sashimi.core.image import _render
from sashimi.core.probe import MediaInfo
from sashimi.errors import InvalidOptionsError
from tests.conftest import noise


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
        (config.IMAGE_CONVERT, ("jpeg", "png", "webp", "gif", "bmp", "tiff", "avif", "apng")),
        (config.IMAGE_RESIZE, ("jpeg", "png", "webp", "gif", "bmp", "tiff", "avif", "apng")),
        (config.VIDEO_CONVERT, ("gif", "webp", "webm", "mp4")),
        (config.VIDEO_TRANSFORM, ("mp4", "webm", "mov")),
        (config.VIDEO_PREVIEW, ("gif", "webp")),
        (config.VIDEO_TRIM, ("mkv", "mp4", "webm", "mov")),
        (config.AUDIO_CONVERT, ("mp3", "m4a", "ogg", "wav", "flac")),
        (config.CONTACT_SHEET, ("jpeg", "png", "webp", "gif")),
        (config.IMAGE_EDIT, ("jpeg", "png", "webp", "gif", "bmp", "tiff", "avif", "apng")),
        (config.IMAGE_FRAMES, ("gif", "webp", "avif", "apng", "png")),
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
        config.IMAGE_EDIT,
        config.IMAGE_FRAMES,
    )
    for model in family.models
} | {config.IcoConfig, config.FaviconPackConfig, config.FrameStripConfig}


@pytest.mark.parametrize("model", sorted(ALL_MODELS, key=lambda m: m.__name__), ids=lambda m: m.__name__)
def test_every_field_is_described(model):
    assert [name for name, field in model.model_fields.items() if not field.description] == []
    schema = model.model_json_schema()["properties"]
    assert all(prop.get("description") for prop in schema.values())


FORMAT_UPPER_BOUNDS = {
    "crf": {51, 63},
    "speed": {8},
    "quality": {100},
    "audio_bitrate_kbps": {512},
    "bitrate_kbps": {320, 512},
    "flac_compression_level": {12},
    "png_compress_level": {9},
    "webp_method": {6},
    "avif_speed": {10},
    "fps": {config.MAX_ANIMATION_FPS},
    "start_offset_percent": {100},
    "end_offset_percent": {100},
    "header_font_size": {200},
    "timestamp_font_size": {200},
    "sample_rate": {config.FLAC_MAX_SAMPLE_RATE},
}
"""Every upper bound a config field may have: format and codec limits and correctness rules, never a policy."""


@pytest.mark.parametrize("model", sorted(ALL_MODELS, key=lambda m: m.__name__), ids=lambda m: m.__name__)
def test_field_bounds_are_only_format_limits(model):
    for name, field in model.model_fields.items():
        for meta in field.metadata:
            upper = getattr(meta, "le", None) or getattr(meta, "lt", None)
            if upper is not None:
                assert upper in FORMAT_UPPER_BOUNDS.get(name, set()), (model.__name__, name, upper)


def test_lengths_are_limits_not_field_bounds():
    for model in (config.GifVideoConfig, config.Mp4VideoConfig, config.Vp9TrimConfig, config.FlacAudioConfig):
        assert not [m for m in model.model_fields["duration_seconds"].metadata if hasattr(m, "le")]
    config.VIDEO_CONVERT.from_params({"duration_seconds": 3600}, target="gif")
    config.GifPreviewConfig(samples=100, sample_duration=60)


def test_gif_length_message_reads_as_a_sentence():
    gif = config.VIDEO_CONVERT.from_params({"duration_seconds": 31}, target="gif")
    with pytest.raises(InvalidOptionsError) as e:
        config.Limits(max_gif_seconds=30).check(gif)
    assert e.value.user_message == "GIFs can be at most 30 seconds long."
    with pytest.raises(InvalidOptionsError) as e:
        config.Limits(max_gif_seconds=1).check(gif)
    assert e.value.user_message == "GIFs can be at most 1 second long."
    with pytest.raises(InvalidOptionsError) as e:
        config.Limits(max_gif_seconds=12.5).check(gif)
    assert e.value.user_message == "GIFs can be at most 12.5 seconds long."
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
    "metadata": "remove_all",
    "keep_hdr": True,
    "reencode": True,
}
HDR_SOURCE = MediaInfo(
    container="mov,mp4,m4a,3gp,3g2,mj2",
    duration=100.0,
    has_video=True,
    width=64,
    height=36,
    video_codec="hevc",
    pixel_format="yuv420p10le",
    color_transfer="smpte2084",
    color_primaries="bt2020",
    color_space="bt2020nc",
)
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
        if name == "reencode":
            from sashimi.video import plan_rotation

            source = MediaInfo(container="mov", duration=100.0, has_video=True, width=64, height=36, video_codec="h264")
            turn = model(rotate=90)
            assert (
                plan_rotation(source, turn) == "display"
                and plan_rotation(source, model(rotate=90, reencode=True)) == "encode"
            )
            continue
        if name == "keep_hdr":
            hdr = build("i", "o", model(**base), duration=100.0, threads=1, source=HDR_SOURCE)
            assert build("i", "o", changed, duration=100.0, threads=1, source=HDR_SOURCE) != hdr, name
            assert build("i", "o", changed, duration=100.0, threads=1) == reference, name
            continue
        assert build("i", "o", changed, duration=100.0, threads=1) != reference, name


IMAGE_ALTERNATIVES = {
    "max_width": 16,
    "max_height": 8,
    "resample": "nearest",
    "background_color": (0, 0, 0),
    "auto_orient": False,
    "metadata": "remove_all",
    "keep_icc": False,
    "preserve_transparency": False,
    "quality": 50,
    "lossless": True,
    "progressive": True,
    "jpeg_subsampling": "4:4:4",
    "png_compress_level": 1,
    "webp_method": 6,
    "tiff_compression": "none",
    "avif_speed": 0,
}
IMAGE_CONTEXT = {"resample": {"max_width": 16}, "background_color": {"preserve_transparency": False}}


def _two_colours():
    img = Image.new("RGB", (64, 32), (200, 30, 30))
    img.paste((20, 40, 220), (0, 0, 32, 32))
    return img


def _photo(pixels=None):
    """A photo with an orientation, EXIF, an sRGB profile and a comment: noise, or `pixels` when given."""
    img = (noise((64, 32), 3) if pixels is None else pixels).convert("RGBA")
    img.putpixel((0, 0), (0, 0, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x010F] = "TestCam"
    buf = io.BytesIO()
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    img.save(buf, "PNG", exif=exif, icc_profile=srgb, pnginfo=_comment())
    return Image.open(io.BytesIO(buf.getvalue()))


def _comment():
    info = PngInfo()
    info.add_text("Comment", "a comment")
    return info


def _carriers():
    return Carriers(exif=_photo().info["exif"].removeprefix(b"Exif\x00\x00"), comments=[b"a comment"])


def _rendered(model, pixels=None, **fields):
    img, kwargs, _ = _render(_photo(pixels), model(**fields), _carriers())
    metadata = {k: v for k, v in kwargs.items() if k in ("exif", "comment", "pnginfo", "xmp", "extra")}
    others = {k: v for k, v in kwargs.items() if k not in metadata}
    return img.mode, img.size, img.tobytes(), others, sorted(metadata)


def _written(tmp_path, model, pixels=None, **fields):
    source = tmp_path / ("photo.png" if pixels is None else "few.png")
    if not source.exists():
        photo = _photo(pixels)
        photo.save(source, "PNG", exif=photo.getexif(), icc_profile=photo.info["icc_profile"], pnginfo=_comment())
    out = tmp_path / "out"
    image.convert(source, out, model(**fields))
    return out.read_bytes()


@pytest.mark.parametrize("model", config.IMAGE_CONVERT.models, ids=lambda m: m.__name__)
def test_every_image_option_changes_the_output(model, tmp_path):
    for name in set(model.model_fields) - DISCRIMINATORS - {"animated"}:
        context = {k: v for k, v in IMAGE_CONTEXT.get(name, {}).items() if k in model.model_fields}
        if model is config.TiffImageConfig and name == "quality":
            context = {"tiff_compression": "jpeg"}
        default = model.model_fields[name].default
        alternative = (not default) if isinstance(default, bool) else IMAGE_ALTERNATIVES[name]
        pixels = _two_colours() if model is config.GifImageConfig and name == "optimize" else None
        assert _rendered(model, pixels, **context) != _rendered(model, pixels, **context, **{name: alternative}), name
        written = _written(tmp_path, model, pixels, **context)
        changed = _written(tmp_path, model, pixels, **context, **{name: alternative})
        assert written != changed, name
        if pixels is not None:
            assert len(changed) < len(written), "optimize keeps only the GIF palette's used colours"
