# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Bugs fixed before 0.6.0: mono Opus at high bitrates, ``frames_info`` decoding pixels, and the small ones."""

import io
import subprocess

import pytest
from PIL import Image, ImageFile

import sashimi.api.convert
from sashimi import audio, image, probe, video
from sashimi.config import AUDIO_CONVERT, VIDEO_CONVERT, VIDEO_TRIM
from tests.conftest import needs_ffmpeg


@pytest.fixture(scope="module")
def mono_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("mono") / "original.blob"
    subprocess.run(
        [
            *("ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"),
            *("-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=1", "-f", "lavfi", "-i", "sine=d=1"),
            *("-ac", "1", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"),
            *("-f", "matroska", str(path)),
        ],
        check=True,
    )
    return path


@needs_ffmpeg
@pytest.mark.parametrize("kbps", [257, 320, 512])
def test_mono_ogg_above_256_kbps_is_encoded_at_256(mono_video, tmp_path, kbps):
    out = tmp_path / "out.ogg"
    audio.convert(mono_video, out, AUDIO_CONVERT.from_params({"bitrate_kbps": kbps}, target="ogg"))
    assert probe(out).audio_codec == "opus"
    downmixed = tmp_path / "mono.ogg"
    audio.convert(mono_video, downmixed, AUDIO_CONVERT.from_params({"bitrate_kbps": kbps, "mono": True}, target="ogg"))
    assert probe(downmixed).audio_channels == 1


@needs_ffmpeg
def test_mono_webm_audio_above_256_kbps_is_encoded_at_256(mono_video, tmp_path):
    video.convert(
        mono_video, tmp_path / "a.webm", VIDEO_CONVERT.from_params({"audio_bitrate_kbps": 512}, target="webm")
    )
    config = VIDEO_TRIM.from_params({"container": "webm", "accurate": True, "audio_bitrate_kbps": 320})
    video.trim(mono_video, tmp_path / "b.webm", config)
    assert probe(tmp_path / "a.webm").audio_codec == "opus" and probe(tmp_path / "b.webm").audio_codec == "opus"


@needs_ffmpeg
@pytest.mark.parametrize("target, kbps", [("mp3", 320), ("m4a", 512)])
def test_mono_mp3_and_aac_at_their_highest_bitrate_work(mono_video, tmp_path, target, kbps):
    out = tmp_path / "out"
    audio.convert(mono_video, out, AUDIO_CONVERT.from_params({"bitrate_kbps": kbps}, target=target))
    assert probe(out).audio_channels == 1


def _images(tmp_path):
    frames = [Image.new("RGBA", (40, 30), (i * 60, 0, 0, 255)) for i in range(3)]
    exif = Image.Exif()
    exif[0x0112] = 6
    files = {}
    for name, fmt, kwargs in (
        ("still.png", "PNG", {"exif": exif}),
        ("late-exif.png", "PNG", {}),
        ("anim.png", "PNG", {"save_all": True, "append_images": frames[1:], "duration": [50, 120, 70], "loop": 3}),
        ("anim.gif", "GIF", {"save_all": True, "append_images": frames[1:], "duration": 100}),
        ("anim.webp", "WEBP", {"save_all": True, "append_images": frames[1:], "duration": 100}),
        ("photo.jpg", "JPEG", {"exif": exif}),
        ("pages.tif", "TIFF", {"save_all": True, "append_images": frames[1:]}),
        ("photo.bmp", "BMP", {}),
        ("double.mpo", "MPO", {"save_all": True, "append_images": [frames[1].convert("RGB")]}),
    ):
        first = frames[0] if fmt not in ("JPEG", "BMP", "MPO") else frames[0].convert("RGB")
        path = tmp_path / name
        first.save(path, fmt, **kwargs)
        files[name] = path
    data = files["late-exif.png"].read_bytes()
    exif_chunk = _png_chunk(b"eXIf", exif.tobytes()[6:])
    end = data.rindex(b"IEND") - 4
    files["late-exif.png"].write_bytes(data[:end] + exif_chunk + data[end:])
    return files


def _png_chunk(kind, payload):
    import struct
    import zlib

    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def test_frames_info_decodes_no_pixels(tmp_path, monkeypatch):
    files = _images(tmp_path)

    def refuse(self, *args, **kwargs):
        raise AssertionError(f"{type(self).__name__} decoded pixels")

    monkeypatch.setattr(ImageFile.ImageFile, "load", refuse)
    for plugin in {type(Image.open(io.BytesIO(path.read_bytes()))) for path in files.values()}:
        if "load" in vars(plugin):
            monkeypatch.setattr(plugin, "load", refuse)
    infos = {name: image.frames_info(path) for name, path in files.items()}
    assert (infos["still.png"].width, infos["still.png"].height) == (30, 40)
    assert (infos["late-exif.png"].width, infos["late-exif.png"].height) == (30, 40)
    assert infos["anim.png"].frames == 3 and infos["anim.png"].durations_ms == [50, 120, 70]
    assert infos["anim.png"].loop == 2 and not infos["anim.png"].animated
    assert infos["anim.gif"].frames == 3 and infos["anim.webp"].frames == 3
    assert (infos["photo.jpg"].width, infos["photo.jpg"].height) == (30, 40)
    assert infos["pages.tif"].frames == 3 and infos["double.mpo"].frames == 2
    assert infos["photo.bmp"].frames == 1


def test_api_convert_exports_only_functions():
    assert "Target" not in sashimi.api.convert.__all__ and "TARGET_SUFFIX" not in sashimi.api.convert.__all__
    assert all(callable(getattr(sashimi.api.convert, name)) for name in sashimi.api.convert.__all__)


@needs_ffmpeg
def test_audio_blob_is_as_long_as_it_says(audio_blob):
    info = probe(audio_blob)
    assert info.duration == pytest.approx(4.0, abs=0.1) and info.has_audio
    assert any(stream.attached_pic for stream in info.streams)
