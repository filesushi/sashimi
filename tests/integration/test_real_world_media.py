# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the video and audio bugs the real-world media benchmark found (2026-10-29)."""

import json
import subprocess

import pytest
from PIL import Image
from typer.testing import CliRunner

from sashimi import probe, video
from sashimi.cli import settings
from sashimi.cli.main import app
from sashimi.config import AUDIO_CONVERT, CONTACT_SHEET, VIDEO_CONVERT, VIDEO_PREVIEW, VIDEO_TRIM, CopyTrimConfig
from sashimi.core import video as core_video
from sashimi.core.probe import MediaInfo, StreamInfo, check_decodable
from sashimi.errors import InvalidOptionsError, UnsupportedMediaError
from sashimi.sheets import render

from tests.conftest import needs_trusted_cuts, _ffmpeg as make_media, _has_encoder, needs_ffmpeg

pytestmark = needs_ffmpeg
runner = CliRunner()
VIDEO = ("-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=2")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home")
    monkeypatch.chdir(tmp_path)


def _cli(*args):
    result = runner.invoke(app, [*args, "--json"], env={"COLUMNS": "1000"})
    return json.loads(result.stdout)["results"], result.exit_code


def _streams(path) -> list[tuple[str, str]]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [(line.split(",")[1], line.split(",")[0]) for line in out.split() if line]


def _frames(path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return int(out.strip().split(",")[0])


def _ipt_c2(path) -> None:
    """An HEVC MP4 tagged with Dolby Vision's IPT-C2 matrix, which swscale can't convert (as profiles 5 and 20 are)."""
    make_media(*VIDEO, "-c:v", "libx265", "-x265-params", "log-level=none", "-pix_fmt", "yuv420p10le",
               "-bsf:v", "hevc_metadata=matrix_coefficients=15:transfer_characteristics=16:colour_primaries=9",
               "-tag:v", "hvc1", "-f", "mp4", str(path))  # fmt: skip


@pytest.mark.skipif(not _has_encoder("libx265"), reason="needs libx265")
@needs_trusted_cuts
def test_dolby_vision_ipt_is_refused_honestly_and_copies_still_work(tmp_path):
    src = tmp_path / "dv.mp4"
    _ipt_c2(src)
    info = probe(src)
    assert info.color_space == "ipt-c2"
    for args in (("thumbs", "dv.mp4"), ("preview", "dv.mp4"), ("convert", "dv.mp4", "gif"), ("convert", "dv.mp4", "webm"),
                 ("rotate", "dv.mp4", "90", "--reencode"), ("extract", "dv.mp4", "0.5s", "1s", "--accurate")):  # fmt: skip
        (row,), code = _cli(*args)
        assert row["message"].startswith("This is Dolby Vision video whose colours need Dolby's own processing"), args
        assert "damaged" not in row["message"] and code == 1
    (row,), _ = _cli("remux", "dv.mp4", "--output-format", "mov")
    assert row["status"] == "success" and _frames(tmp_path / "dv.mp4.mov") == 50
    (row,), _ = _cli("strip", "dv.mp4")
    assert row["status"] == "success"
    (row,), _ = _cli("rotate", "dv.mp4", "90", "-f")
    assert row["status"] == "success" and "display rotation set to 90° clockwise" in row["message"]
    (row,), _ = _cli("extract", "dv.mp4", "0.5s", "1s")
    assert row["status"] == "success"
    (row,), _ = _cli("remux", "dv.mp4")
    assert row["status"] == "error" and "copy it into MP4 or MOV" in row["message"]
    assert not (tmp_path / "dv.mp4.mkv").exists()


def test_prores_raw_is_refused_before_decoding():
    raw = MediaInfo(container="mov,mp4,m4a,3gp,3g2,mj2", duration=2, has_video=True, video_codec="prores_raw")
    with pytest.raises(UnsupportedMediaError) as e:
        check_decodable(raw)
    assert e.value.user_message.startswith("This is ProRes RAW video")
    check_decodable(raw.model_copy(update={"video_codec": "prores"}))


def _anamorphic(path) -> None:
    make_media("-f", "lavfi", "-i", "testsrc2=s=200x100:r=10:d=1,setsar=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-f", "mp4", str(path))  # fmt: skip


@pytest.mark.parametrize("target", ["gif", "webp"])
def test_non_square_pixels_give_gifs_and_webps_their_display_shape(tmp_path, target):
    _anamorphic(tmp_path / "a.mp4")
    (row,), _ = _cli("convert", "a.mp4", target, "--width", "200")
    assert row["status"] == "success"
    assert Image.open(tmp_path / f"a.mp4.{target}").size == (200, 50)
    (row,), _ = _cli("convert", "a.mp4", target, "-o", f"wide.{target}", "--width", "1000")
    assert Image.open(tmp_path / f"wide.{target}").size == (400, 100)


def test_square_pixels_keep_their_commands():
    info = MediaInfo(container="mov", duration=2, has_video=True, width=160, height=90, display_aspect_ratio=160 / 90)
    for target in ("gif", "webp"):
        cmd = video.build_convert_command(
            "i", "o", VIDEO_CONVERT.from_params({}, target=target), duration=2, threads=1, source=info
        )
        assert any("scale='min(480,iw)':-2:flags=lanczos" in arg for arg in cmd)


def test_the_sheet_header_shows_the_display_size(tmp_path):
    _anamorphic(tmp_path / "a.mp4")
    from sashimi.core.sheets.static import header_lines

    assert (
        header_lines(tmp_path / "a.mp4", probe(tmp_path / "a.mp4"), None)[3] == "Dimensions: 400x100 (stored 200x100)"
    )


LAYOUTS = ["mono", "stereo", "2.1", "3.0", "4.0", "quad", "quad(side)", "5.0", "5.0(side)", "5.1", "5.1(side)", "6.1",
           "7.1", "7.1(wide)"]  # fmt: skip


@pytest.mark.parametrize("layout", LAYOUTS)
def test_surround_sound_encodes_to_opus(tmp_path, layout):
    src = tmp_path / "s.mkv"
    make_media(*VIDEO, "-f", "lavfi", "-i", f"sine=d=2,aformat=channel_layouts={layout}", "-c:v", "libx264", "-c:a",
               "ac3" if layout not in ("7.1", "7.1(wide)") else "flac", "-f", "matroska", str(src))  # fmt: skip
    for args, out in ((("convert", "s.mkv", "webm"), "s.mkv.webm"), (("convert", "s.mkv", "ogg"), "s.mkv.ogg"),
                      (("extract", "s.mkv", "0.5s", "1s", "--accurate", "--output-format", "webm"), "s.mkv.extracted.webm"),
                      (("rotate", "s.mkv", "90", "--output-format", "webm"), "s.mkv.rotated.webm")):  # fmt: skip
        (row,), _ = _cli(*args)
        assert row["status"] == "success", (args, row["message"])
        assert ("audio", "opus") in _streams(tmp_path / out)


@pytest.mark.parametrize(
    "codecs",
    [
        ("h263", "-s", "176x144", "amr_nb"),
        ("flv1", "", "", "libmp3lame"),
        ("libtheora", "", "", "libvorbis"),
        ("wmv2", "", "", "wmav2"),
    ],  # fmt: skip
    ids=["h263", "flv1", "theora", "wmv2"],
)
def test_remux_to_mp4_refuses_codecs_mp4_cant_hold_up_front(tmp_path, codecs):
    encoder, *size, audio = codecs
    if not _has_encoder(encoder) or (audio == "amr_nb" and not _has_encoder("libopencore_amrnb")):
        pytest.skip("encoder missing")
    audio = "libopencore_amrnb" if audio == "amr_nb" else audio
    extra = ["-ar", "8000", "-ac", "1"] if audio == "libopencore_amrnb" else []
    src = tmp_path / "v.mkv"
    make_media("-f", "lavfi", "-i", "testsrc2=s=176x144:r=15:d=1", "-f", "lavfi", "-i", "sine=d=1", "-c:v", encoder,
               "-c:a", audio, *extra, "-f", "matroska", str(src))  # fmt: skip
    (row,), code = _cli("remux", "v.mkv", "--output-format", "mp4")
    assert row["status"] == "error" and code == 1
    assert row["message"].startswith("This video can't be copied into MP4 without re-encoding: MP4 can't hold its ")
    assert "Try MKV instead, or convert it (which re-encodes)." in row["message"]
    assert "trimmed" not in row["message"] and "Failed" not in row["message"]
    assert not list(tmp_path.glob("v.mkv.mp4*"))


def _with_data_tracks(*tags: str) -> MediaInfo:
    streams = [StreamInfo(type="video", codec="h264", codec_tag="avc1"), StreamInfo(type="audio", codec="aac")]
    streams += [StreamInfo(type="data", codec="unknown", codec_tag=tag) for tag in tags]
    return MediaInfo(container="mov,mp4,m4a,3gp,3g2,mj2", duration=10, has_video=True, has_audio=True, streams=streams)


@pytest.mark.parametrize("container", ["mp4", "mkv"])
def test_data_tracks_a_container_cant_hold_are_dropped_and_named(container):
    info = _with_data_tracks("gpmd", "fdsc", "mebx", "mebx")
    plan = core_video.plan_copy(info, container)
    assert plan.dropped == [2, 3, 4, 5]
    assert plan.note(container) == (
        "FFmpeg can't copy the GoPro telemetry track (GPS), the GoPro file-description track and 2 Apple metadata "
        f"tracks into {container.upper()}"
    )
    cmd = video.build_trim_command(
        "in", "out", CopyTrimConfig(container=container), duration=10, source=info, drop=plan.dropped
    )
    for index in (2, 3, 4, 5):
        assert f"-0:{index}" in cmd


def test_mov_keeps_the_data_tracks_ffmpeg_can_write_and_leaves_out_the_rest():
    info = _with_data_tracks("gpmd", "fdsc", "mebx", "mebx", "tmcd")
    gopro = info.streams[2].model_copy(update={"codec": "bin_data"})
    info = info.model_copy(update={"streams": [*info.streams[:2], gopro, *info.streams[3:]]})
    plan = core_video.plan_copy(info, "mov")
    assert plan.dropped == [3, 4, 5]
    assert plan.note("mov") == (
        "FFmpeg can't copy the GoPro file-description track and 2 Apple metadata tracks into MOV"
    )


def test_a_timecode_track_is_not_named_when_the_timecode_is_kept():
    info = _with_data_tracks("tmcd")
    tagged = info.model_copy(
        update={
            "streams": [info.streams[0].model_copy(update={"tags": {"timecode": "01:00:00:00"}}), *info.streams[1:]]
        }
    )
    assert core_video.plan_copy(tagged, "mp4").note("mp4") is None
    assert core_video.plan_copy(info, "mp4").note("mp4") == "FFmpeg can't copy the timecode track into MP4"


@needs_trusted_cuts
def test_a_mov_with_a_timecode_remuxes_and_trims_into_mkv_and_mp4(tmp_path):
    make_media(*VIDEO, "-f", "lavfi", "-i", "sine=d=2", "-c:v", "libx264", "-g", "10", "-c:a", "aac",
               "-timecode", "01:00:00:00", "-f", "mov", str(tmp_path / "t.mov"))  # fmt: skip
    assert ("data", "unknown") in _streams(tmp_path / "t.mov")
    for args in (
        ("remux", "t.mov"),
        ("remux", "t.mov", "--output-format", "mp4"),
        ("extract", "t.mov", "0.4s", "1s", "--output-format", "mp4"),
    ):
        (row,), code = _cli(*args)
        assert row["status"] == "success" and code == 0, (args, row["message"])
        assert "trimmed" not in row["message"]


def test_a_copy_that_ffmpeg_leaves_without_video_starts_from_the_keyframe(tmp_path, monkeypatch):
    src = tmp_path / "v.webm"
    make_media(*VIDEO, "-c:v", "libvpx", "-g", "1000", "-b:v", "200k", "-f", "webm", str(src))
    real = core_video.run_ffmpeg

    def dropping(cmd, *args, **kwargs):
        if "copy" in cmd and "-copyinkf" not in cmd and "-ss" in cmd:
            cmd = [*cmd[:-3], "-frames:v", "0", *cmd[-3:]]
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(core_video, "run_ffmpeg", dropping)
    video.trim(
        src, tmp_path / "o.webm", VIDEO_TRIM.from_params({}, container="webm", start_seconds=0.4, duration_seconds=0.5)
    )
    assert _frames(tmp_path / "o.webm") >= 22


def test_short_clips_get_sheets_and_previews(tmp_path):
    make_media(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=s=160x90:r=30:d=0.4",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-f",
        "mp4",
        str(tmp_path / "s.mp4"),
    )
    render(tmp_path / "s.mp4", tmp_path / "s.jpg", CONTACT_SHEET.from_params({"cols": 3, "rows": 2}, target="jpeg"))
    render(tmp_path / "s.mp4", tmp_path / "s.webp", CONTACT_SHEET.from_params({}, animated=True, target="webp"))
    video.preview(tmp_path / "s.mp4", tmp_path / "p.webp", VIDEO_PREVIEW.from_params({}, target="webp"))
    assert Image.open(tmp_path / "p.webp").n_frames >= 5
    from sashimi.core.sheets._render import format_duration

    assert format_duration(0.39, tenths=True) == "00:00.3" and format_duration(65.0) == "01:05"


def test_an_unreadable_file_named_like_the_target_is_refused(tmp_path):
    (tmp_path / "empty.mp4").write_bytes(b"")
    (tmp_path / "junk.mp4").write_bytes(b"not a video at all" * 10)
    for name in ("empty.mp4", "junk.mp4"):
        (row,), code = _cli("convert", name, "mp4")
        assert row["message"] != "Already MP4" and code == 1
    make_media(*VIDEO, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "mp4", str(tmp_path / "ok.mp4"))
    (row,), code = _cli("convert", "ok.mp4", "mp4")
    assert (row["message"], code) == ("Already MP4", 0)
    (tmp_path / "e.mp3").write_bytes(b"")
    (row,), code = _cli("convert", "e.mp3", "mp3")
    assert row["message"] != "Already MP3" and code == 1


def _gif(path, delay_ms: int, frames: int = 10) -> None:
    images = [Image.new("RGB", (32, 32), (i * 20, 0, 0)) for i in range(frames)]
    images[0].save(path, save_all=True, append_images=images[1:], duration=delay_ms, loop=0)


@pytest.mark.parametrize("target", ["mp4", "webm"])
def test_a_gif_s_short_frames_play_as_in_browsers_in_video(tmp_path, target):
    _gif(tmp_path / "fast.gif", 10)
    assert abs(probe(tmp_path / "fast.gif").duration - 1.0) < 0.01
    video.convert(tmp_path / "fast.gif", tmp_path / f"o.{target}", VIDEO_CONVERT.from_params({}, target=target))
    assert abs(probe(tmp_path / f"o.{target}").duration - 1.0) < 0.12
    _gif(tmp_path / "slow.gif", 50)
    cmd = video.build_convert_command("i", "o", VIDEO_CONVERT.from_params({}, target=target), duration=0.5, threads=1)
    assert not any("PREV_INPTS" in arg for arg in cmd)
    assert abs(probe(tmp_path / "slow.gif").duration - 0.5) < 0.01


def test_audio_still_converts_from_dolby_vision_files():
    info = MediaInfo(container="mov", duration=2, has_video=True, has_audio=True, color_space="ipt-c2")
    cmd = __import__("sashimi").audio.build_convert_command(
        "i", "o", AUDIO_CONVERT.from_params({}, target="mp3"), duration=2, source=info
    )
    assert "-map" in cmd


def test_the_opus_layout_filter_leaves_stereo_commands_alone():
    stereo = MediaInfo(container="matroska", duration=2, has_video=True, has_audio=True, audio_channels=2)
    cmd = video.build_convert_command(
        "i", "o", VIDEO_CONVERT.from_params({}, target="webm"), duration=2, threads=1, source=stereo
    )
    assert "-af" not in cmd
    six = stereo.model_copy(update={"audio_channels": 6})
    cmd = video.build_convert_command(
        "i", "o", VIDEO_CONVERT.from_params({}, target="webm"), duration=2, threads=1, source=six
    )
    assert cmd[cmd.index("-af") + 1].startswith("aformat=channel_layouts=mono|stereo")
    with pytest.raises(InvalidOptionsError):
        core_video.plan_copy(six.model_copy(update={"streams": [StreamInfo(type="video", codec="h264")]}), "webm")


STRIP_CONTAINERS = {
    ".ts": ("-c:v", "libx264", "-c:a", "aac", "-f", "mpegts"),
    ".mpg": ("-c:v", "mpeg2video", "-c:a", "mp2", "-f", "vob"),
    ".flv": ("-c:v", "libx264", "-c:a", "aac", "-f", "flv"),
    ".wmv": ("-c:v", "wmv2", "-c:a", "wmav2", "-f", "asf"),
    ".avi": ("-c:v", "mpeg4", "-c:a", "libmp3lame", "-f", "avi"),
    ".3gp": ("-c:v", "libx264", "-c:a", "aac", "-f", "3gp"),
}


@pytest.mark.parametrize("ext", sorted(STRIP_CONTAINERS))
@pytest.mark.parametrize("mode", ["remove_location", "remove_all"])
def test_strip_takes_legacy_containers_keeping_their_streams(tmp_path, ext, mode):
    src = tmp_path / f"v{ext}"
    make_media(*VIDEO, "-f", "lavfi", "-i", "sine=d=2", "-metadata", "title=Holiday", "-metadata",
               "location=+51.5-000.1/", *STRIP_CONTAINERS[ext], str(src))  # fmt: skip
    (row,), code = _cli("strip", src.name, "--remove", mode)
    assert row["status"] == "success" and code == 0, row["message"]
    out = tmp_path / f"v{ext}.stripped{ext}"
    assert _streams(out) == _streams(src)
    assert abs(probe(out).duration - probe(src).duration) < 0.15
    assert not any("location" in key.lower() for key in probe(out).tags)
    (row,), _ = _cli("strip", src.name, "--remove", mode)
    assert row["status"] == "exists"
