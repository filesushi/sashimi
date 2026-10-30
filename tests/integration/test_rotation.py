# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Lossless video rotation: a rotation alone into MP4 copies every stream and sets the display
rotation, composed with the source's, so the coded picture and sound are unchanged and players
show it turned exactly as the re-encode route turns the pixels. Flips, ``reencode``, WebM and
codecs MP4 players don't all play keep the re-encode, and every result says which happened.
"""

import json
import pathlib
import subprocess

import pytest
from PIL import Image, ImageChops
from typer.testing import CliRunner

from sashimi import probe, sheets, video
from sashimi.cli import settings
from sashimi.cli.main import app
from sashimi.config import VIDEO_TRANSFORM, JpegSheetConfig
from tests.conftest import needs_ffmpeg

pytestmark = needs_ffmpeg
runner = CliRunner()


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], check=True)


def _make(folder: pathlib.Path, name: str, video: tuple[str, ...], audio: tuple[str, ...], fmt: str, turn: int = 0):
    """A 2 s 320x180 test video, its display rotation `turn` degrees clockwise (``-display_rotation`` counts the other way)."""
    plain = folder / f"plain-{name}"
    _ffmpeg(
        *("-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=2", "-f", "lavfi", "-i", "sine=d=2"),
        *video,
        *audio,
        *("-f", fmt, str(plain)),
    )
    if not turn:
        return plain
    path = folder / name
    _ffmpeg("-display_rotation", str((360 - turn) % 360), "-i", str(plain), "-c", "copy", "-f", fmt, str(path))
    return path


H264 = ("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "25")
HEVC = ("-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error", "-tag:v", "hvc1")


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    folder = tmp_path_factory.mktemp("rotation")
    return {
        "h264 mp4": _make(folder, "a.mp4", H264, ("-c:a", "aac"), "mp4"),
        "hevc mov portrait": _make(folder, "b.mov", HEVC, ("-c:a", "pcm_s16le"), "mov", turn=90),
        "h264 mkv": _make(folder, "c.mkv", H264, ("-c:a", "libopus"), "matroska"),
        "h264 mp4 upside down": _make(folder, "d.mp4", H264, ("-c:a", "aac"), "mp4", turn=180),
        "mpeg2 ts": _make(folder, "e.ts", ("-c:v", "mpeg2video"), ("-c:a", "mp2"), "mpegts"),
    }


def _frames(path: pathlib.Path, autorotate: bool) -> list[str]:
    args = [] if autorotate else ["-noautorotate"]
    out = subprocess.run(
        ["ffmpeg", "-v", "error", *args, "-i", str(path), "-map", "0:v:0", "-f", "framemd5", "pipe:1"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.split(",")[-1].strip() for line in out.splitlines() if line and not line.startswith("#")]


def _shown(path: pathlib.Path, at: float = 0.5) -> Image.Image:
    """The frame at `at` as players show it (FFmpeg applies the display rotation), as a picture."""
    data = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(at),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    import io

    return Image.open(io.BytesIO(data)).convert("RGB")


def _mean_difference(a: Image.Image, b: Image.Image) -> float:
    assert a.size == b.size, (a.size, b.size)
    diff = ImageChops.difference(a, b).convert("L")
    return sum(diff.getdata()) / (a.size[0] * a.size[1])


LOSSLESS = ["h264 mp4", "hevc mov portrait", "h264 mkv", "h264 mp4 upside down"]


@pytest.mark.parametrize("angle", [90, 180, 270])
@pytest.mark.parametrize("name", LOSSLESS)
def test_a_rotation_alone_is_lossless_and_shows_as_the_reencode_does(sources, tmp_path, name, angle):
    source = sources[name]
    lossless, reencoded = tmp_path / "lossless", tmp_path / "reencoded"
    result = video.transform(source, lossless, VIDEO_TRANSFORM.from_params({"rotate": angle}))
    assert result.route == "display"
    assert _frames(lossless, autorotate=False) == _frames(source, autorotate=False)
    before, after = probe(source), probe(lossless)
    assert after.display_turn == (before.display_turn + angle) % 360
    assert not after.display_mirrored
    assert "mp4" in after.container_names and after.video_codec == before.video_codec
    told = result.fidelity
    assert told.adjusted and "re-encoded as H.264" not in told.text
    if before.audio_codec in ("aac", "opus"):
        assert told.level != "lossy", told.text
    else:
        assert told.changed == ["sound re-encoded as AAC at 128 kb/s"]
    video.transform(source, reencoded, VIDEO_TRANSFORM.from_params({"rotate": angle, "reencode": True}))
    assert probe(reencoded).display_turn == 0
    assert (after.width, after.height) == (probe(reencoded).width, probe(reencoded).height)
    assert _mean_difference(_shown(lossless), _shown(reencoded)) < 12


@pytest.mark.parametrize("name", LOSSLESS)
def test_turning_back_to_upright_removes_the_display_rotation(sources, tmp_path, name):
    source = sources[name]
    back = (360 - probe(source).display_turn) % 360 or 90
    out = tmp_path / "out"
    result = video.transform(source, out, VIDEO_TRANSFORM.from_params({"rotate": back}))
    assert probe(out).display_turn == (probe(source).display_turn + back) % 360
    if probe(source).display_turn:
        assert probe(out).display_turn == 0
        assert f"display rotation of {probe(source).display_turn}° removed" in result.fidelity.text


@pytest.mark.parametrize(
    "name, params",
    [
        ("h264 mp4", {"rotate": 90, "flip": "horizontal"}),
        ("h264 mp4", {"rotate": 90, "reencode": True}),
        ("h264 mp4", {"rotate": 90, "crf": 30}),
        ("h264 mp4", {"rotate": 90, "target": "webm"}),
        ("mpeg2 ts", {"rotate": 90}),
    ],
)
def test_everything_else_reencodes_and_says_so(sources, tmp_path, name, params):
    out = tmp_path / "out"
    result = video.transform(sources[name], out, VIDEO_TRANSFORM.from_params(params))
    assert result.route == "encode" and result.fidelity.level == "lossy"
    assert not result.fidelity.adjusted and "display rotation" not in result.fidelity.text
    assert probe(out).display_turn == 0


def test_sheets_and_gifs_of_a_losslessly_rotated_video_show_it_turned(sources, tmp_path):
    out = tmp_path / "turned.mp4"
    video.transform(sources["h264 mp4"], out, VIDEO_TRANSFORM.from_params({"rotate": 90}))
    sheet = tmp_path / "sheet.jpg"
    sheets.render(out, sheet, JpegSheetConfig(cols=1, rows=1, show_header=False, gap=0, width=360))
    with Image.open(sheet) as img:
        assert img.height > img.width
    gif = tmp_path / "t.gif"
    video.convert(out, gif, __import__("sashimi").config.VIDEO_CONVERT.from_params({}, target="gif"))
    with Image.open(gif) as img:
        assert img.height > img.width


def test_a_rerun_writes_the_same_bytes(sources, tmp_path):
    for name in ("a", "b"):
        video.transform(sources["hevc mov portrait"], tmp_path / name, VIDEO_TRANSFORM.from_params({"rotate": 90}))
    assert (tmp_path / "a").read_bytes() == (tmp_path / "b").read_bytes()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    settings.load_raw_config.cache_clear()
    yield tmp_path
    settings.load_raw_config.cache_clear()


def _said(result) -> str:
    return " ".join(result.output.split())


def test_the_cli_rotates_losslessly_reencodes_when_asked_and_keeps_names(sources, isolated):
    (isolated / "clip.mov").write_bytes(sources["hevc mov portrait"].read_bytes())
    dry = runner.invoke(app, ["rotate", "clip.mov", "90", "--dry-run"])
    assert "clip.mov.rotated.mov" in _said(dry) and not (isolated / "clip.mov.rotated.mov").exists()
    first = runner.invoke(app, ["rotate", "clip.mov", "90"])
    assert first.exit_code == 0 and "display rotation changed from 90° to 180° clockwise" in _said(first)
    assert "picture and sound identical" in _said(first) and "re-encoded" not in _said(first)
    again = runner.invoke(app, ["rotate", "clip.mov", "90"])
    assert "Output already exists" in _said(again)
    forced = runner.invoke(app, ["rotate", "clip.mov", "90", "--reencode", "-f"])
    assert forced.exit_code == 0 and "re-encoded as H.264" in _said(forced)
    assert probe(isolated / "clip.mov.rotated.mov").display_turn == 0
    assert runner.invoke(app, ["config", "set", "rotate.reencode", "true"]).exit_code == 0
    kept = runner.invoke(app, ["rotate", "clip.mov", "90", "-f", "--json"])
    assert "re-encoded as H.264" in kept.output
    lossless = runner.invoke(app, ["rotate", "clip.mov", "90", "-f", "--lossless", "--json"])
    report = json.loads(lossless.output)
    assert report["results"][0]["fidelity"]["adjusted"] == ["display rotation changed from 90° to 180° clockwise"]
    info = runner.invoke(app, ["info", "clip.mov.rotated.mov"])
    assert "shown turned 180° clockwise" in _said(info)


def _streamhashes(path: pathlib.Path) -> dict[str, str]:
    """Each video and audio stream's packets, hashed: equal hashes mean the stream was copied as it is."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v", "-map", "0:a?", "-c", "copy", "-f", "streamhash",
         "-hash", "md5", "pipe:1"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout  # fmt: skip
    return {line.split(",")[1] + line.split(",")[0]: line.split("=")[-1] for line in out.split()}


@pytest.fixture(scope="module")
def mov_sources(tmp_path_factory):
    folder = tmp_path_factory.mktemp("mov-rotation")
    blob = folder / "blob.bin"
    blob.write_bytes(bytes(range(256)) * 16)
    tracked = folder / "tracked.mov"
    plain = _make(folder, "t.mov", H264, ("-c:a", "pcm_s16le"), "mov")
    _ffmpeg("-i", str(plain), "-f", "data", "-i", str(blob), "-map", "0", "-map", "1", "-c", "copy",
            "-tag:d", "mebx", "-metadata", "location=+40.5674+008.3188/", "-f", "mov", str(tracked))  # fmt: skip
    return {
        "hevc pcm mov": _make(folder, "a.mov", HEVC, ("-c:a", "pcm_s16le"), "mov", turn=90),
        "h264 pcm24 mov": _make(folder, "b.mov", H264, ("-c:a", "pcm_s24le"), "mov"),
        "h264 alac mov": _make(folder, "c.mov", H264, ("-c:a", "alac"), "mov"),
        "h264 aac mov": _make(folder, "d.mov", H264, ("-c:a", "aac"), "mov", turn=270),
        "h264 pcm mp4": _make(folder, "e.mp4", H264, ("-c:a", "pcm_s16le"), "mp4"),
        "h264 opus mkv": _make(folder, "f.mkv", H264, ("-c:a", "libopus"), "matroska"),
        "h264 pcm mov with a data track": tracked,
    }


def _rotate_cli(folder: pathlib.Path, source: pathlib.Path, *args: str) -> dict:
    name = source.name if source.suffix != ".mov" else source.stem + ".MOV"
    (folder / name).write_bytes(source.read_bytes())
    result = runner.invoke(app, ["rotate", name, "90", "--json", "-f", *args])
    report = json.loads(result.output)
    (row,) = report["results"]
    assert row["status"] == "success", row
    return row


@pytest.mark.parametrize("name", ["hevc pcm mov", "h264 pcm24 mov", "h264 alac mov", "h264 aac mov",
                                  "h264 pcm mp4", "h264 opus mkv", "h264 pcm mov with a data track"])  # fmt: skip
def test_a_default_rotation_copies_every_stream_some_container_it_may_write_holds(mov_sources, isolated, name):
    source = mov_sources[name]
    row = _rotate_cli(isolated, source)
    out = isolated / row["output"]
    expected = ".MOV" if source.suffix == ".mov" else ".mp4"
    assert out.name.endswith(f".rotated{expected}"), out.name
    assert _streamhashes(out) == _streamhashes(source)
    told = row["fidelity"]
    assert told["level"] != "lossy" and not told["changed"], told
    assert "re-encoded" not in told["text"]
    assert probe(out).display_turn == (probe(source).display_turn + 90) % 360
    if "data track" in name:
        assert told["lost"] and "into MOV" in " ".join(told["notes"])


def test_a_mov_rotated_into_mp4_says_the_sound_was_reencoded(mov_sources, isolated):
    row = _rotate_cli(isolated, mov_sources["hevc pcm mov"], "--output-format", "mp4")
    assert row["output"].endswith(".rotated.mp4")
    assert row["fidelity"]["changed"] == ["sound re-encoded as AAC at 128 kb/s"]
    assert probe(isolated / row["output"]).audio_codec == "aac"


def test_plan_rotation_reports_the_mov_route(mov_sources):
    from sashimi.config import MovTransformConfig, Mp4TransformConfig

    info = probe(mov_sources["hevc pcm mov"])
    assert video.plan_rotation(info, MovTransformConfig(rotate=90)) == "display"
    assert video.plan_rotation(info, Mp4TransformConfig(rotate=90)) == "display"
    assert video.plan_rotation(info, MovTransformConfig(rotate=90, flip="horizontal")) == "encode"
    assert video.plan_rotation(info, MovTransformConfig(rotate=90, reencode=True)) == "encode"
    vp9 = probe(mov_sources["h264 opus mkv"]).model_copy(update={"video_codec": "vp9"})
    assert video.plan_rotation(vp9, MovTransformConfig(rotate=90)) == "encode"
    assert video.plan_rotation(vp9, Mp4TransformConfig(rotate=90)) == "display"


def test_a_mov_rotation_reencodes_into_mov_when_asked_and_reruns_are_identical(mov_sources, tmp_path):
    from sashimi.config import MovTransformConfig

    source = mov_sources["hevc pcm mov"]
    turned = video.transform(source, tmp_path / "a.mov", MovTransformConfig(rotate=90, reencode=True))
    assert turned.route == "encode" and "mov" in probe(tmp_path / "a.mov").container_names
    for name in ("b", "c"):
        video.transform(source, tmp_path / name, MovTransformConfig(rotate=90))
    assert (tmp_path / "b").read_bytes() == (tmp_path / "c").read_bytes()


@pytest.mark.parametrize("mode", ["keep", "remove_location", "remove_all"])
@pytest.mark.parametrize("target", ["mp4", "mov"])
def test_tracks_the_output_cant_hold_are_never_said_to_be_removed_as_asked(mov_sources, tmp_path, mode, target):
    source = mov_sources["h264 pcm mov with a data track"]
    result = video.transform(source, tmp_path / "out", VIDEO_TRANSFORM.from_params({"rotate": 90, "metadata": mode},
                                                                                    target=target))  # fmt: skip
    told = result.fidelity
    tracks = [item for item in [*told.lost, *told.removed] if "track" in item]
    assert len(tracks) == 1
    if mode == "remove_all":
        assert tracks[0] in told.removed
    else:
        assert tracks[0] in told.lost and tracks[0] not in told.removed
        assert told.notes == [f"FFmpeg can't copy {tracks[0]} into {target.upper()}"]
    assert ("location" in told.removed) == (mode != "keep")


ROUTES = [
    ("remux", "--output-format", "mkv"),
    ("remux", "--output-format", "mp4"),
    ("extract", "0", "1", "--output-format", "mov"),
    ("extract", "0", "1", "--output-format", "mkv"),
    ("convert", "mp4"),
    ("convert", "webm", "--crf", "50"),
    ("rotate", "90"),
    ("rotate", "90", "--output-format", "mp4"),
    ("rotate", "90", "--output-format", "webm"),
]


@pytest.mark.parametrize("route", ROUTES, ids=lambda r: "-".join(r))
def test_only_the_location_is_removed_as_asked_on_every_route(mov_sources, isolated, route):
    (isolated / "clip.mov").write_bytes(mov_sources["h264 pcm mov with a data track"].read_bytes())
    command, *rest = route
    result = runner.invoke(app, [command, "clip.mov", *rest, "--metadata", "remove_location", "--json", "-f"])
    (row,) = json.loads(result.output)["results"]
    assert row["status"] == "success", row
    told = row["fidelity"]
    assert told["removed"] == ["location"], told
    assert any("data track" in item for item in told["lost"]), told
    assert "and location removed as asked" not in told["text"]


def _info(audio: str, *, dolby_vision: bool = False, video: str = "hevc"):
    from sashimi.core.probe import MediaInfo, StreamInfo

    streams = [StreamInfo(type="video", codec=video, codec_tag="hvc1"), StreamInfo(type="audio", codec=audio)]
    streams += [StreamInfo(type="data", codec="unknown", codec_tag="mebx")]
    return MediaInfo(
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=5,
        has_video=True,
        has_audio=True,
        width=1080,
        height=1920,
        video_codec=video,
        audio_codec=audio,
        streams=streams,
        dolby_vision=dolby_vision,
    )


@pytest.mark.parametrize(
    "audio, dolby_vision, codec, expected",
    [
        ("aac", False, "hevc", "mov"),
        ("aac", True, "hevc", "mp4"),
        ("pcm_s16le", False, "hevc", "mov"),
        ("pcm_s16le", True, "hevc", "mov"),
        ("aac", False, "prores", "mov"),
        ("aac", False, "av1", "mp4"),
        ("aac", False, "mpeg2video", "mov"),
    ],
)
def test_a_mov_is_rotated_into_the_container_that_keeps_more(audio, dolby_vision, codec, expected):
    from sashimi.config import MovTransformConfig, Mp4TransformConfig

    info = _info(audio, dolby_vision=dolby_vision, video=codec)
    assert video.rotation_container(info, Mp4TransformConfig(rotate=90), MovTransformConfig(rotate=90)) == expected
