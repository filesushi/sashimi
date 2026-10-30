# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
HDR video (PQ and HLG): every route that makes standard-range output tone-maps it, never writes
8-bit video tagged PQ or HLG, and drops the HDR metadata the picture no longer matches; with
``keep_hdr`` re-encodes stay 10-bit HDR with their colour and mastering metadata.
"""

import json
import pathlib
import subprocess

import pytest
from PIL import Image
from typer.testing import CliRunner

from sashimi import audio, config, probe, sheets, video
from sashimi.cli import settings
from sashimi.cli.main import app
from sashimi.config import (
    AnimatedWebpSheetConfig,
    CopyTrimConfig,
    JpegSheetConfig,
    Mp4VideoConfig,
    VIDEO_CONVERT,
    VIDEO_PREVIEW,
    VIDEO_TRANSFORM,
    VIDEO_TRIM,
)
from sashimi.core import _hdr
from sashimi.core.fidelity import same_picture
from sashimi.core.probe import MediaInfo
from sashimi.errors import MissingFiltersError
from tests.conftest import HAS_FFMPEG, _has_encoder, needs_ffmpeg

pytestmark = [
    needs_ffmpeg,
    pytest.mark.skipif(
        HAS_FFMPEG and not (_has_encoder("libx265") and _has_encoder("libvpx-vp9")),
        reason="this ffmpeg can't encode 10-bit HEVC or VP9",
    ),
]
runner = CliRunner()
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
HDR_SIDE_DATA = {"Mastering display metadata", "Content light level metadata"}
MASTERING = "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)"


def _make(path: pathlib.Path, transfer: str, codec: str = "hevc", seconds: float = 2.0) -> pathlib.Path:
    """A 10-bit HDR test video: a bright test pattern tagged PQ or HLG, with HDR10 mastering metadata for PQ."""
    source = f"testsrc2=s=192x108:r=25:d={seconds}"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i", source]
    cmd += ["-f", "lavfi", "-i", f"sine=d={seconds}", "-pix_fmt", "yuv420p10le"]
    cmd += ["-color_primaries", "bt2020", "-color_trc", transfer, "-colorspace", "bt2020nc", "-color_range", "tv"]
    if codec == "hevc":
        params = f"log-level=error:colorprim=bt2020:transfer={transfer}:colormatrix=bt2020nc"
        if transfer == "smpte2084":
            params += f":hdr10=1:master-display={MASTERING}:max-cll=1000,400"
        cmd += ["-c:v", "libx265", "-preset", "ultrafast", "-x265-params", params, "-tag:v", "hvc1"]
    else:
        cmd += ["-c:v", "libvpx-vp9", "-profile:v", "2", "-deadline", "realtime", "-cpu-used", "8"]
    cmd += ["-g", "25", "-c:a", "aac" if codec == "hevc" else "libopus"]
    if codec == "hevc":
        subprocess.run([*cmd, str(path)], check=True)
        return path
    raw = path.with_name("raw-" + path.name)
    subprocess.run([*cmd, str(raw)], check=True)
    tags = ["-color_trc", transfer, "-color_primaries", "bt2020", "-colorspace", "bt2020nc"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-c", "copy", *tags, str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def hdr(tmp_path_factory) -> dict[str, pathlib.Path]:
    folder = tmp_path_factory.mktemp("hdr")
    return {
        "pq": _make(folder / "pq.mp4", "smpte2084"),
        "hlg": _make(folder / "hlg.mov", "arib-std-b67"),
        "vp9": _make(folder / "vp9.webm", "smpte2084", codec="vp9"),
    }


def _video(path: pathlib.Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_frames", "-read_intervals",
         "%+#1", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    data = json.loads(out)
    stream = data["streams"][0]
    side = {item.get("side_data_type") for item in stream.get("side_data_list", [])}
    for frame in data.get("frames", []):
        side |= {item.get("side_data_type") for item in frame.get("side_data_list", [])}
    stream["side"] = side
    return stream


def _ten_bit(stream: dict) -> bool:
    return "10" in stream.get("pix_fmt", "") or "12" in stream.get("pix_fmt", "")


def _assert_sdr(path: pathlib.Path) -> None:
    stream = _video(path)
    assert stream.get("color_transfer") not in HDR_TRANSFERS, stream.get("color_transfer")
    assert not _ten_bit(stream), stream["pix_fmt"]
    assert not stream["side"] & HDR_SIDE_DATA, stream["side"]


def _never_8_bit_hdr(path: pathlib.Path) -> None:
    stream = _video(path)
    if stream.get("color_transfer") in HDR_TRANSFERS:
        assert _ten_bit(stream), (path, stream["pix_fmt"], stream.get("color_transfer"))


REENCODES = [
    ("convert", lambda **f: VIDEO_CONVERT.from_params(f, target="mp4")),
    ("convert", lambda **f: VIDEO_CONVERT.from_params(f, target="webm")),
    ("transform", lambda **f: VIDEO_TRANSFORM.from_params({"rotate": 90, "reencode": True, **f}, target="mp4")),
    ("transform", lambda **f: VIDEO_TRANSFORM.from_params({"rotate": 90, **f}, target="webm")),
    ("trim", lambda **f: VIDEO_TRIM.from_params({"start_seconds": 0.5, "accurate": True, **f}, container="mkv")),
    ("trim", lambda **f: VIDEO_TRIM.from_params({"start_seconds": 0.5, "accurate": True, **f}, container="mp4")),
    ("trim", lambda **f: VIDEO_TRIM.from_params({"start_seconds": 0.5, "accurate": True, **f}, container="webm")),
]


def _run(kind: str, src: pathlib.Path, out: pathlib.Path, cfg) -> object:
    return {"convert": video.convert, "transform": video.transform, "trim": video.trim}[kind](src, out, cfg)


@pytest.mark.parametrize("source", ["pq", "hlg", "vp9"])
@pytest.mark.parametrize("kind, make", REENCODES)
@pytest.mark.parametrize("keep", [False, True])
def test_every_reencode_tone_maps_or_keeps_10_bit_hdr(hdr, tmp_path, source, kind, make, keep):
    out = tmp_path / "out"
    cfg = make(keep_hdr=True) if keep else make()
    result = _run(kind, hdr[source], out, cfg)
    _never_8_bit_hdr(out)
    stream = _video(out)
    if keep:
        assert stream["color_transfer"] == _video(hdr[source])["color_transfer"]
        assert stream["color_primaries"] == "bt2020" and _ten_bit(stream)
        copied = getattr(result, "route", "encode") == "copy"
        assert (result.fidelity.level != "lossy") == copied
        assert not any("tone-mapped" in c for c in result.fidelity.changed)
    else:
        _assert_sdr(out)
        assert any(c.startswith("tone-mapped from HDR") for c in result.fidelity.changed)


@pytest.mark.parametrize("target", ["mp4", "webm"])
def test_kept_hdr_carries_the_mastering_metadata(hdr, tmp_path, target):
    out = tmp_path / f"out.{target}"
    video.convert(hdr["pq"], out, VIDEO_CONVERT.from_params({"keep_hdr": True, "crf": 30}, target=target))
    stream = _video(out)
    assert stream["side"] >= HDR_SIDE_DATA and stream["color_transfer"] == "smpte2084"
    assert (stream["codec_name"], stream.get("profile")) in (("hevc", "Main 10"), ("vp9", "Profile 2"))


def test_an_hdr_hevc_mp4_with_keep_hdr_is_copied(hdr, tmp_path):
    result = video.convert(hdr["hlg"], tmp_path / "o.mp4", Mp4VideoConfig(keep_hdr=True))
    assert result.route == "copy" and result.fidelity.level != "lossy"
    stream = _video(tmp_path / "o.mp4")
    assert stream["codec_tag_string"] == "hvc1" and stream["color_transfer"] == "arib-std-b67"
    assert video.convert(hdr["hlg"], tmp_path / "t.mp4", Mp4VideoConfig()).route == "encode"


@pytest.mark.parametrize("source", ["pq", "hlg", "vp9"])
@pytest.mark.parametrize(
    "make",
    [
        lambda: VIDEO_CONVERT.from_params({"duration_seconds": 1}, target="gif"),
        lambda: VIDEO_CONVERT.from_params({"duration_seconds": 1}, target="webp"),
        lambda: VIDEO_PREVIEW.from_params({"samples": 2, "sample_duration": 0.5}, target="gif"),
        lambda: VIDEO_PREVIEW.from_params({"samples": 2, "sample_duration": 0.5}, target="webp"),
        lambda: JpegSheetConfig(cols=2, rows=2, width=400),
        lambda: AnimatedWebpSheetConfig(cols=2, rows=1, width=400, sample_duration=0.5, fps=4),
    ],
)
def test_every_picture_from_hdr_video_is_tone_mapped(hdr, tmp_path, monkeypatch, source, make):
    cfg = make()
    calls = []
    real = _hdr.tone_map_filter

    def spy(info):
        result = real(info)
        calls.append(result)
        return result

    monkeypatch.setattr(_hdr, "tone_map_filter", spy)
    out = tmp_path / "out"
    if isinstance(cfg, (JpegSheetConfig, AnimatedWebpSheetConfig)):
        result = sheets.render(hdr[source], out, cfg)
    elif hasattr(cfg, "samples"):
        result = video.preview(hdr[source], out, cfg)
    else:
        result = video.convert(hdr[source], out, cfg)
    assert calls and all(calls), "the route didn't ask for tone mapping"
    assert any(c.startswith("tone-mapped from HDR") for c in result.fidelity.changed)
    with Image.open(out) as img:
        img.load()


def test_tone_mapping_changes_the_picture_and_matches_its_reference(hdr, tmp_path):
    sheet = JpegSheetConfig(cols=1, rows=1, width=384, show_header=False, show_timestamps=False, quality=100, gap=0)
    sheets.render(hdr["pq"], tmp_path / "mapped.jpg", sheet)
    info = probe(hdr["pq"])
    chain = _hdr.tone_map_filter(info)
    assert chain is not None and "tonemap=mobius" in chain and "npl=100" in chain
    assert "npl=203" in (_hdr.tone_map_filter(probe(hdr["hlg"])) or "")
    flat = tmp_path / "flat.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", str(hdr["pq"]), "-frames:v", "1", "-vf", "scale=384:216",
         str(flat)],
        check=True,
    )  # fmt: skip
    with Image.open(tmp_path / "mapped.jpg") as mapped, Image.open(flat) as untouched:
        assert not same_picture(mapped.convert("RGB").resize(untouched.size), untouched.convert("RGB"))


def test_sdr_commands_are_unchanged():
    sdr = MediaInfo(container="mov", duration=10.0, has_video=True, width=64, height=36)
    for cfg in (VIDEO_CONVERT.from_params({}, target=t) for t in ("gif", "webp", "mp4", "webm")):
        cmd = video.build_convert_command("i", "o", cfg, duration=10.0, threads=1, source=sdr)
        assert not any("tonemap" in part or "zscale" in part for part in cmd)
    assert _hdr.tone_map_filter(sdr) is None


def test_an_ffmpeg_without_zscale_refuses_rather_than_washing_out(hdr, tmp_path, monkeypatch):
    monkeypatch.setattr(_hdr, "_available_filters", lambda ffmpeg: frozenset({"tonemap", "sidedata"}))
    for call in (
        lambda out: video.convert(hdr["pq"], out, VIDEO_CONVERT.from_params({}, target="gif")),
        lambda out: sheets.render(hdr["pq"], out, JpegSheetConfig(cols=2, rows=2)),
        lambda out: video.preview(hdr["pq"], out, VIDEO_PREVIEW.from_params({}, target="webp")),
        lambda out: video.transform(
            hdr["pq"], out, VIDEO_TRANSFORM.from_params({"rotate": 90, "reencode": True}, target="mp4")
        ),
    ):
        out = tmp_path / "out"
        with pytest.raises(MissingFiltersError) as e:
            call(out)
        assert "zscale" in str(e.value) and "washed out" in str(e.value)
        assert not out.exists()
    video.convert(hdr["pq"], tmp_path / "kept.mp4", Mp4VideoConfig(keep_hdr=True, crf=40))
    video.transform(hdr["pq"], tmp_path / "turned.mp4", VIDEO_TRANSFORM.from_params({"rotate": 90}, target="mp4"))
    video.trim(hdr["pq"], tmp_path / "copy.mkv", CopyTrimConfig(container="mkv"))
    audio.convert(hdr["pq"], tmp_path / "a.mp3", config.AUDIO_CONVERT.from_params({}, target="mp3"))


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    settings.load_raw_config.cache_clear()
    yield tmp_path
    settings.load_raw_config.cache_clear()


def test_the_cli_refuses_keep_hdr_for_formats_that_cant_hold_it(hdr, isolated):
    (isolated / "v.mp4").write_bytes(hdr["pq"].read_bytes())
    for args in (
        ["convert", "v.mp4", "gif", "--keep-hdr"],
        ["rotate", "v.mp4", "90", "--keep-hdr", "--output-format", "png"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 2, result.output
        assert "can't hold HDR video, so --keep-hdr can't be used" in " ".join(result.output.split())
    assert not list(isolated.glob("v.mp4.*"))


def test_keep_hdr_can_be_kept_in_sashimi_toml(hdr, isolated):
    (isolated / "v.mkv").write_bytes(hdr["hlg"].read_bytes())
    assert runner.invoke(app, ["config", "set", "convert.keep_hdr", "true"]).exit_code == 0
    assert runner.invoke(app, ["convert", "v.mkv", "webm", "--crf", "50"]).exit_code == 0
    assert _video(isolated / "v.mkv.webm")["color_transfer"] == "arib-std-b67"
    assert runner.invoke(app, ["convert", "v.mkv", "webm", "--crf", "50", "--tone-map", "-f"]).exit_code == 0
    _assert_sdr(isolated / "v.mkv.webm")
    assert runner.invoke(app, ["convert", "v.mkv", "gif"]).exit_code == 0


@pytest.mark.parametrize("name, target", [("pq", "mp4"), ("vp9", "webm")])
def test_an_hdr_video_converted_to_its_own_format_is_tone_mapped_and_says_so(hdr, isolated, name, target):
    source = isolated / f"v.{target}"
    source.write_bytes(hdr[name].read_bytes())
    result = runner.invoke(app, ["convert", source.name, target, "--crf", "45"])
    assert result.exit_code == 0, result.output
    assert "tone-mapped from HDR (PQ)" in " ".join(result.output.split())
    _assert_sdr(isolated / f"v.{target}.{target}")
    again = runner.invoke(app, ["convert", source.name, target, "--crf", "45"])
    assert "Output already exists" in " ".join(again.output.split())
    kept = runner.invoke(app, ["convert", source.name, target, "--keep-hdr"])
    assert kept.exit_code == 0 and f"Already {target.upper()}" in kept.output
    sdr = runner.invoke(app, ["convert", f"v.{target}.{target}", target])
    assert sdr.exit_code == 0 and f"Already {target.upper()}" in sdr.output
    searched = runner.invoke(app, ["convert", ".", target, "--dry-run"])
    assert f"v.{target}: " not in searched.output
