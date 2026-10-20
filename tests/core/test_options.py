# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""The extra options exposed on every config, and that their defaults keep the §4 behaviour."""

import pytest
from PIL import Image

from sashimi import audio, image, video
from sashimi.config import (
    AAC_SAMPLE_RATES,
    AUDIO_CONVERT,
    IMAGE_CONVERT,
    MP3_SAMPLE_RATES,
    OPUS_SAMPLE_RATES,
    VIDEO_CONVERT,
    BmpImageConfig,
    GifImageConfig,
    IcoConfig,
    JpegImageConfig,
    Mp4TransformConfig,
    PngImageConfig,
    TiffImageConfig,
    WebpImageConfig,
    parse_color,
)
from sashimi.core.image import _render
from sashimi.errors import InvalidOptionsError


def cmd(target, **params):
    config = VIDEO_CONVERT.from_params(params, target=target)
    return video.build_convert_command("i", "o", config, duration=100.0, threads=2)


def after_input(command):
    return command[command.index("-i") + 2 :]


def test_gif_palette_dither_and_loop():
    command = cmd("gif", palette_mode="single", dither="floyd_steinberg", loop=False)
    vf = command[command.index("-vf") + 1]
    assert vf.endswith("[a]palettegen=stats_mode=single[p];[b][p]paletteuse=new=1:dither=floyd_steinberg")
    assert command[command.index("-loop") + 1] == "-1"
    assert cmd("gif", palette_mode="full", dither="none")[cmd("gif").index("-vf") + 1].endswith(
        "palettegen=stats_mode=full[p];[b][p]paletteuse=dither=none"
    )


def test_mp4_encoder_options():
    tail = after_input(cmd("mp4", crf=18, preset="slow", audio_bitrate_kbps=192))
    assert tail[tail.index("-preset") + 1] == "slow" and tail[tail.index("-crf") + 1] == "18"
    assert tail[tail.index("-b:a") + 1] == "192k"


def test_webm_speed_and_mute():
    tail = after_input(cmd("webm", speed=2, mute=True))
    assert tail[tail.index("-cpu-used") + 1] == "2"
    assert "-an" in tail and "0:a:0?" not in tail and "libopus" not in tail and "-b:a" not in tail


def test_mp4_crf_limit_is_a_field_bound():
    with pytest.raises(InvalidOptionsError):
        VIDEO_CONVERT.from_params({"crf": 60}, target="mp4")
    VIDEO_CONVERT.from_params({"crf": 60}, target="webm")
    assert VIDEO_CONVERT.model_for(target="mp4").model_fields["crf"].metadata[-1].le == 51


def test_transform_uses_encoder_options():
    config = Mp4TransformConfig(rotate=90, crf=30, mute=True)
    command = video.build_transform_command("i", "o", config, duration=10.0, threads=1)
    assert command[command.index("-crf") + 1] == "30" and "-an" in command


def test_audio_options():
    def build(target, **params):
        config = AUDIO_CONVERT.from_params(params, target=target)
        return after_input(audio.build_convert_command("i", "o", config, duration=60.0))

    assert build("wav", wav_bit_depth="24", sample_rate=44100)[5:9] == ["-ar", "44100", "-c:a", "pcm_s24le"]
    flac = build("flac", flac_compression_level=12)
    assert flac[flac.index("-compression_level") + 1] == "12"
    with pytest.raises(InvalidOptionsError) as e:
        AUDIO_CONVERT.from_params({"sample_rate": 44100}, target="ogg")
    assert e.value.user_message == (
        "Invalid value for sample_rate: input should be 8000, 12000, 16000, 24000 or 48000."
    )
    assert AUDIO_CONVERT.from_params({"sample_rate": "48000"}, target="ogg").sample_rate == 48000


@pytest.mark.parametrize(
    "target, rates", [("mp3", MP3_SAMPLE_RATES), ("m4a", AAC_SAMPLE_RATES), ("ogg", OPUS_SAMPLE_RATES)]
)
def test_sample_rates_are_the_encoders_choices(target, rates):
    model = AUDIO_CONVERT.model_for(target=target)
    schema = model.model_json_schema()["properties"]["sample_rate"]
    assert schema["anyOf"] == [{"enum": list(rates), "type": "integer"}, {"type": "null"}]
    assert [AUDIO_CONVERT.from_params({"sample_rate": str(rate)}, target=target).sample_rate for rate in rates] == list(
        rates
    )
    assert AUDIO_CONVERT.from_params({}, target=target).sample_rate is None


def test_mp3_refuses_rates_lame_cannot_encode():
    with pytest.raises(InvalidOptionsError) as e:
        AUDIO_CONVERT.from_params({"sample_rate": 96000}, target="mp3")
    assert e.value.user_message == (
        "Invalid value for sample_rate: input should be 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100 or 48000."
    )
    assert AUDIO_CONVERT.from_params({"sample_rate": 96000}, target="m4a").sample_rate == 96000
    with pytest.raises(InvalidOptionsError):
        AUDIO_CONVERT.from_params({"sample_rate": 192000}, target="m4a")


@pytest.mark.parametrize("target", ["wav", "flac"])
def test_pcm_and_flac_take_any_rate_their_format_holds(target):
    assert AUDIO_CONVERT.from_params({"sample_rate": 384000}, target=target).sample_rate == 384000
    assert AUDIO_CONVERT.from_params({"sample_rate": 9000}, target=target).sample_rate == 9000
    assert AUDIO_CONVERT.from_params({"sample_rate": 1_048_575}, target=target).sample_rate == 1_048_575
    with pytest.raises(InvalidOptionsError):
        AUDIO_CONVERT.from_params({"sample_rate": 7999}, target=target)


def test_flac_stops_at_its_highest_rate():
    assert AUDIO_CONVERT.from_params({"sample_rate": 2_000_000}, target="wav").sample_rate == 2_000_000
    with pytest.raises(InvalidOptionsError) as e:
        AUDIO_CONVERT.from_params({"sample_rate": 1_048_576}, target="flac")
    assert e.value.user_message == "Invalid value for sample_rate: input should be less than or equal to 1048575."


@pytest.mark.parametrize(
    "value, rgb", [("#fff", (255, 255, 255)), ("#102030", (16, 32, 48)), ("1, 2, 3", (1, 2, 3)), ([4, 5, 6], (4, 5, 6))]
)
def test_parse_color(value, rgb):
    assert parse_color(value) == rgb


@pytest.mark.parametrize("bad", ["#12", "red", "#gggggg", "1,2,300"])
def test_bad_colours_are_rejected(bad):
    with pytest.raises(InvalidOptionsError):
        IMAGE_CONVERT.from_params({"background_color": bad}, target="jpeg")


@pytest.mark.parametrize(
    "model, expected",
    [(JpegImageConfig, True), (PngImageConfig, False), (GifImageConfig, False)],
)
def test_optimize_defaults_per_format(model, expected):
    assert model.model_fields["optimize"].default is expected
    _, kwargs, _ = _render(Image.new("RGB", (8, 8)), model())
    assert kwargs["optimize"] is expected
    _, forced, _ = _render(Image.new("RGB", (8, 8)), model(optimize=not expected))
    assert forced["optimize"] is (not expected)


def test_jpeg_and_webp_specific_options():
    _, jpeg, _ = _render(Image.new("RGB", (8, 8)), JpegImageConfig(jpeg_subsampling="4:4:4", progressive=True))
    assert jpeg["subsampling"] == "4:4:4" and jpeg["progressive"] is True
    _, webp, _ = _render(Image.new("RGB", (8, 8)), WebpImageConfig(webp_method=6))
    assert webp["method"] == 6
    _, defaults, _ = _render(Image.new("RGB", (8, 8)), JpegImageConfig())
    assert defaults["subsampling"] == "4:2:0"


@pytest.mark.parametrize(
    "target, option",
    [
        ("jpeg", "lossless"),
        ("jpeg", "preserve_transparency"),
        ("png", "quality"),
        ("png", "webp_method"),
        ("gif", "keep_icc"),
        ("bmp", "metadata"),
        ("jpeg", "keep_exif"),
        ("bmp", "optimize"),
        ("webp", "optimize"),
        ("tiff", "progressive"),
        ("webp", "png_compress_level"),
    ],
)
def test_options_for_other_formats_are_rejected(target, option):
    with pytest.raises(InvalidOptionsError) as e:
        IMAGE_CONVERT.from_params({option: 1}, target=target)
    assert e.value.user_message == f"Unknown option: {option}."


def test_background_colour_and_bmp_flattening(tmp_path):
    src = tmp_path / "original.blob"
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(src, "PNG")
    image.convert(src, tmp_path / "a", BmpImageConfig(background_color="#00ff00"))
    with Image.open(tmp_path / "a") as out:
        assert out.format == "BMP" and out.convert("RGB").getpixel((1, 1)) == (0, 255, 0)


def test_exif_is_kept_unless_removed(tmp_path):
    src = tmp_path / "original.blob"
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    Image.new("RGB", (10, 10)).save(src, "JPEG", exif=exif)
    image.convert(src, tmp_path / "kept", JpegImageConfig())
    image.convert(src, tmp_path / "dropped", JpegImageConfig(metadata="remove_all"))
    assert 0x010F not in Image.open(tmp_path / "dropped").getexif()
    assert Image.open(tmp_path / "kept").getexif()[0x010F] == "TestCam"


@pytest.mark.parametrize("compression", ["none", "lzw", "deflate", "jpeg"])
def test_tiff_compression(tmp_path, compression):
    src = tmp_path / "original.blob"
    Image.new("RGB", (16, 16), "red").save(src, "PNG")
    image.convert(src, tmp_path / "r", IMAGE_CONVERT.from_params({"tiff_compression": compression}, target="tif"))
    assert Image.open(tmp_path / "r").format == "TIFF"


def test_ico_crop_fills_the_square(tmp_path):
    src = tmp_path / "original.blob"
    Image.new("RGBA", (300, 100), "red").save(src, "PNG")
    image.to_ico(src, tmp_path / "r", IcoConfig.from_params({"fit": "crop", "sizes": "32"}))
    with Image.open(tmp_path / "r") as ico:
        assert ico.convert("RGBA").getpixel((0, 0))[3] == 255
