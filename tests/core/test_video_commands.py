# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Parity tests: the video command builders must produce exactly the arguments FileSushi relies on (brief §4)."""

import pytest

from sashimi.config import (
    VIDEO_CONVERT,
    VIDEO_TRANSFORM,
    VIDEO_TRIM,
    CopyTrimConfig,
    H264TrimConfig,
    Mp4TransformConfig,
    Vp9TrimConfig,
)
from sashimi.errors import InvalidOptionsError, MediaTooLongError
from sashimi.core.probe import MediaInfo, StreamInfo
from sashimi.video import (
    build_convert_command,
    build_transform_command,
    build_trim_command,
    check_copy_compatible,
    plan_mp4_route,
)

COMMON = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-filter_threads", "2"]
FORMATS = "3g2,3gp,aac,asf,avi,flac,flv,gif,m4a,matroska,mj2,mov,mp3,mp4,mpeg,mpegts,ogg,wav,webm"
PROGRESS = ["-progress", "pipe:1", "-nostats", "-protocol_whitelist", "file", "-format_whitelist", FORMATS]
GIF_FILTER = (
    "fps={fps},scale='min({w},iw)':-2:flags=lanczos,split[a][b];"
    "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4"
)
VIDEO_COMMON = ["-pix_fmt", "yuv420p", "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]
WEBM = ["-c:v", "libvpx-vp9", "-crf", "34", "-b:v", "0", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1"]
WEBM += ["-c:a", "libopus", "-b:a", "96k"]
MP4 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k"]
MP4 += ["-movflags", "+faststart"]


def build(target, duration=103.0, **params):
    config = VIDEO_CONVERT.from_params(params, target=target)
    return build_convert_command("in.blob", "out.tmp", config, duration=duration, threads=2)


def test_gif_defaults():
    assert build("gif") == [
        *COMMON, *PROGRESS, "-threads", "2", "-t", "10", "-i", "file:in.blob",
        "-vf", GIF_FILTER.format(fps=12, w=480), "-loop", "0", "-an",
        "-threads", "2", "-fflags", "+bitexact", "-f", "gif", "file:out.tmp",
    ]  # fmt: skip


def test_gif_with_options_puts_seek_and_length_before_input():
    cmd = build("gif", start_seconds=12.5, duration_seconds=4, max_width=320, fps=8)
    i = cmd.index("-i")
    assert cmd[i - 4 : i] == ["-ss", "12.5", "-t", "4"]
    assert cmd[cmd.index("-vf") + 1] == GIF_FILTER.format(fps=8, w=320)


def test_animated_webp_defaults():
    assert build("webp") == [
        *COMMON, *PROGRESS, "-threads", "2", "-t", "10", "-i", "file:in.blob",
        "-vf", "fps=12,scale='min(480,iw)':-2:flags=lanczos",
        "-c:v", "libwebp_anim", "-lossless", "0", "-quality", "75", "-loop", "0", "-an",
        "-threads", "2", "-fflags", "+bitexact", "-f", "webp", "file:out.tmp",
    ]  # fmt: skip


def test_animated_webp_options():
    cmd = build("webp", quality=90, lossless=True, loop=False, fps=20, max_width=320, start_seconds=12)
    assert cmd[cmd.index("-i") - 4 : cmd.index("-i")] == ["-ss", "12", "-t", "10"]
    assert cmd[cmd.index("-vf") + 1] == "fps=20,scale='min(320,iw)':-2:flags=lanczos"
    assert cmd[cmd.index("-c:v") : cmd.index("-an")] == [
        "-c:v", "libwebp_anim", "-lossless", "1", "-quality", "90", "-loop", "1",
    ]  # fmt: skip


def test_webm_whole_video_stops_just_past_the_probed_length():
    assert build("webm") == [
        *COMMON, *PROGRESS, "-threads", "2", "-t", "104", "-i", "file:in.blob",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", *VIDEO_COMMON, *WEBM,
        "-threads", "2", "-fflags", "+bitexact", "-f", "webm", "file:out.tmp",
    ]  # fmt: skip


def test_mp4_with_max_width_and_range():
    assert build("mp4", max_width=640, start_seconds=5, duration_seconds=30) == [
        *COMMON, *PROGRESS, "-threads", "2", "-ss", "5", "-t", "30", "-i", "file:in.blob",
        "-vf", "scale='min(640,iw)':-2:flags=lanczos", *VIDEO_COMMON, *MP4,
        "-threads", "2", "-fflags", "+bitexact", "-f", "mp4", "file:out.tmp",
    ]  # fmt: skip


@pytest.mark.parametrize(
    "target, option",
    [("mp4", "fps"), ("mp4", "speed"), ("webm", "preset"), ("gif", "crf"), ("gif", "quality"), ("webp", "dither")],
)
def test_options_for_other_targets_are_rejected(target, option):
    with pytest.raises(InvalidOptionsError) as e:
        VIDEO_CONVERT.from_params({option: 1}, target=target)
    assert e.value.user_message == f"Unknown option: {option}."


def test_probed_length_minus_start_is_the_limit_without_a_duration():
    cmd = build("mp4", duration=103.5, start_seconds=10)
    assert cmd[cmd.index("-i") - 4 : cmd.index("-i")] == ["-ss", "10", "-t", "94.5"]
    cmd = build("mp4", duration=50.0, duration_seconds=80)
    assert cmd[cmd.index("-i") - 2 : cmd.index("-i")] == ["-t", "80"]


def test_start_past_end_is_a_user_error():
    with pytest.raises(InvalidOptionsError) as e:
        build("mp4", duration=103.0, start_seconds=200)
    assert e.value.user_message == "The start time (200s) is past the end of the video (103s)."


@pytest.mark.parametrize(
    "target, message",
    [("gif", "GIFs can be at most 30 seconds long."), ("webp", "Animated WebPs can be at most 30 seconds long.")],
)
def test_animated_length_limit_is_checked_by_the_config(target, message):
    with pytest.raises(InvalidOptionsError) as e:
        VIDEO_CONVERT.from_params({"duration_seconds": 31}, target=target)
    assert e.value.user_message == message
    build(target, duration_seconds=30)


@pytest.mark.parametrize("target", ["webm", "mp4"])
def test_ten_minute_limit(target):
    with pytest.raises(MediaTooLongError) as e:
        build(target, duration=601.0)
    assert e.value.user_message == (
        "Videos longer than 10 minutes aren't supported yet. Set a shorter duration to convert part of it."
    )
    build(target, duration=3600.0, duration_seconds=600)
    cmd = build(target, duration=900.0, start_seconds=300)
    assert cmd[cmd.index("-i") - 4 : cmd.index("-i")] == ["-ss", "300", "-t", "600"]
    with pytest.raises(InvalidOptionsError) as e:
        VIDEO_CONVERT.from_params({"duration_seconds": 601}, target=target)
    assert e.value.user_message == "Video clips can be at most 10 minutes long."


@pytest.mark.parametrize(
    "params",
    [
        {"max_width": 63},
        {"max_width": 3841},
        {"fps": 0},
        {"fps": 31},
        {"start_seconds": -1},
        {"duration_seconds": 0},
        {"quality": 101},
    ],  # fmt: skip
)
def test_out_of_range_parameters(params):
    with pytest.raises(InvalidOptionsError):
        VIDEO_CONVERT.from_params(params, target="webp")


def test_trim_command_is_stream_copy_with_explicit_container():
    config = CopyTrimConfig(container="mkv", start_seconds=20, end_seconds=30)
    assert build_trim_command("in.blob", "out.tmp", config, duration=103.0) == [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-filter_threads", "1", *PROGRESS,
        "-ss", "20", "-t", "10", "-i", "file:in.blob", "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero",
        "-fflags", "+bitexact", "-f", "matroska", "file:out.tmp",
    ]  # fmt: skip
    mp4 = build_trim_command("a", "b", CopyTrimConfig(container="mp4", duration_seconds=5), duration=10.0)
    assert mp4[-7:] == ["-movflags", "+faststart", "-fflags", "+bitexact", "-f", "mp4", "file:b"]


def test_accurate_trim_reencodes_with_container_encoders():
    config = H264TrimConfig(container="mkv", start_seconds=20, end_seconds=30, crf=20)
    assert build_trim_command("in.blob", "out.tmp", config, duration=103.0, threads=2) == [
        *COMMON[:-1], "2", *PROGRESS, "-threads", "2", "-ss", "20", "-t", "10", "-i", "file:in.blob",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", *VIDEO_COMMON,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "128k",
        "-threads", "2", "-fflags", "+bitexact", "-f", "matroska", "file:out.tmp",
    ]  # fmt: skip
    webm = build_trim_command("a", "b", Vp9TrimConfig(mute=True), duration=10.0, threads=1)
    assert webm[webm.index("-t") + 1] == "11"
    assert "libvpx-vp9" in webm and "-an" in webm and "libopus" not in webm
    mp4 = build_trim_command("a", "b", H264TrimConfig(container="mp4"), duration=10.0, threads=1)
    assert mp4[mp4.index("-movflags") + 1] == "+faststart"
    with pytest.raises(MediaTooLongError):
        build_trim_command("a", "b", H264TrimConfig(container="mp4"), duration=700.0, threads=1)
    long = build_trim_command("a", "b", H264TrimConfig(container="mp4", start_seconds=100), duration=700.0, threads=1)
    assert long[long.index("-t") + 1] == "600"


def test_trim_family_picks_the_model():
    assert type(VIDEO_TRIM.from_params({}, container="mov")) is CopyTrimConfig
    assert type(VIDEO_TRIM.from_params({"accurate": "true"}, container="mov")) is H264TrimConfig
    assert type(VIDEO_TRIM.from_params({"accurate": True}, container="webm")) is Vp9TrimConfig
    assert VIDEO_TRIM.from_params({"accurate": True, "crf": 55}, container="webm").crf == 55


@pytest.mark.parametrize(
    "params, container, message",
    [
        ({"start_seconds": 10, "end_seconds": 5}, "mp4", "The end time must be after the start time."),
        ({"end_seconds": 5, "duration_seconds": 5}, "mp4", "Give an end time or a length, not both."),
        ({}, "avi", "Invalid value for container: must be mkv, mp4, webm or mov."),
        ({"crf": 20}, "mp4", "Unknown option: crf."),
        ({"crf": 55, "accurate": True}, "mkv", "Invalid value for crf: input should be less than or equal to 51."),
        ({"speed": 4, "accurate": True}, "mp4", "Unknown option: speed."),
        ({"accurate": True, "end_seconds": 700}, "mkv", "Video clips can be at most 10 minutes long."),
        ({"accurate": True, "duration_seconds": 601}, "webm", "Video clips can be at most 10 minutes long."),
    ],
)
def test_trim_validation(params, container, message):
    with pytest.raises(InvalidOptionsError) as e:
        VIDEO_TRIM.from_params(params, container=container)
    assert e.value.user_message == message


def test_copy_trim_has_no_length_limit_and_checks_the_start():
    VIDEO_TRIM.from_params({"end_seconds": 7200}, container="mkv")
    with pytest.raises(InvalidOptionsError) as e:
        build_trim_command("a", "b", CopyTrimConfig(container="mkv", start_seconds=50), duration=40.0)
    assert e.value.user_message == "The start time (50s) is past the end of the video (40s)."


@pytest.mark.parametrize(
    "rotate, flip, vf",
    [
        (90, "none", "transpose=1"),
        (270, "none", "transpose=2"),
        (180, "none", "hflip,vflip"),
        (0, "horizontal", "hflip"),
        (90, "vertical", "transpose=1,vflip"),
    ],
)
def test_transform_filters(rotate, flip, vf):
    config = Mp4TransformConfig(rotate=rotate, flip=flip)
    cmd = build_transform_command("a", "b", config, duration=10.0, threads=2)
    assert cmd[cmd.index("-vf") + 1] == f"{vf},scale=trunc(iw/2)*2:trunc(ih/2)*2"
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-i") - 2 : cmd.index("-i")] == ["-t", "11"]


def test_transform_is_capped_at_the_limit():
    config = Mp4TransformConfig(rotate=90)
    cmd = build_transform_command("a", "b", config, duration=599.5, threads=2)
    assert cmd[cmd.index("-i") - 2 : cmd.index("-i")] == ["-t", "600"]
    with pytest.raises(MediaTooLongError):
        build_transform_command("a", "b", config, duration=600.5, threads=2)


def test_transform_family():
    with pytest.raises(InvalidOptionsError) as e:
        VIDEO_TRANSFORM.from_params({})
    assert e.value.user_message == "Choose a rotation or a flip."
    assert type(VIDEO_TRANSFORM.from_params({"rotate": "90"})) is Mp4TransformConfig
    webm = VIDEO_TRANSFORM.from_params({"flip": "vertical"}, target="webm")
    cmd = build_transform_command("a", "b", webm, duration=10.0, threads=2)
    assert cmd[cmd.index("-c:v") + 1] == "libvpx-vp9" and cmd[cmd.index("-cpu-used") + 1] == "8"
    with pytest.raises(InvalidOptionsError) as e:
        VIDEO_TRANSFORM.from_params({"rotate": 90, "crf": 60})
    assert e.value.user_message == "Invalid value for crf: input should be less than or equal to 51."


def test_whole_file_copy_is_a_remux():
    cmd = build_trim_command("in.blob", "out.tmp", CopyTrimConfig(container="mkv"), duration=103.0)
    assert cmd == [
        *COMMON[:-1], "1", "-progress", "pipe:1", "-nostats", *PROGRESS[3:], "-i", "file:in.blob",
        "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero",
        "-fflags", "+bitexact", "-f", "matroska", "file:out.tmp",
    ]  # fmt: skip


@pytest.mark.parametrize(
    "container, subtitles, expected",
    [
        ("mkv", ["mov_text"], ["-c:s:0", "srt"]),
        ("mkv", ["subrip", "mov_text", "ass"], ["-c:s:1", "srt"]),
        ("mkv", ["hdmv_pgs_subtitle", "subrip"], []),
        ("mp4", ["subrip", "ass", "mov_text"], ["-c:s:0", "mov_text", "-c:s:1", "mov_text"]),
        ("mov", ["webvtt"], ["-c:s:0", "mov_text"]),
        ("webm", ["mov_text", "webvtt"], ["-c:s:0", "webvtt"]),
        ("mp4", [], []),
    ],
)
def test_text_subtitles_are_converted_for_the_container(container, subtitles, expected):
    cmd = build_trim_command(
        "in.blob", "out.tmp", CopyTrimConfig(container=container), duration=10.0, subtitles=subtitles
    )
    copy = cmd.index("copy")
    assert cmd[copy + 1 : cmd.index("-avoid_negative_ts")] == expected


def test_accurate_trims_drop_subtitles_whatever_they_are():
    config = H264TrimConfig(container="mkv", duration_seconds=2)
    with_subs = build_trim_command("a", "b", config, duration=10.0, threads=2, subtitles=["mov_text"])
    assert with_subs == build_trim_command("a", "b", config, duration=10.0, threads=2)
    assert "-sn" in with_subs


def _info(*streams):
    return MediaInfo(container="mov,mp4", streams=[StreamInfo(type=t, codec=c) for t, c in streams])


def test_copy_check_lets_text_subtitles_through():
    check_copy_compatible(_info(("video", "h264"), ("subtitle", "mov_text")), "mkv")
    check_copy_compatible(_info(("video", "vp9"), ("subtitle", "mov_text")), "webm")
    check_copy_compatible(_info(("video", "h264"), ("subtitle", "subrip")), "mp4")
    with pytest.raises(InvalidOptionsError) as e:
        check_copy_compatible(_info(("video", "h264"), ("subtitle", "hdmv_pgs_subtitle")), "mp4")
    assert e.value.user_message == "This video can't be trimmed to MP4 without re-encoding. Try MKV instead."


def h264(**fields):
    info = {
        "container": "matroska,webm", "duration": 103.0, "has_video": True, "has_audio": True, "width": 1280,
        "height": 720, "video_codec": "h264", "pixel_format": "yuv420p", "audio_codec": "aac",
    }  # fmt: skip
    return MediaInfo(**{**info, **fields})


def mp4(**params):
    return VIDEO_CONVERT.from_params(params, target="mp4")


@pytest.mark.parametrize(
    "info, params, route",
    [
        ({}, {}, "copy"),
        ({"pixel_format": "yuvj420p"}, {}, "copy"),
        ({"has_audio": False, "audio_codec": None}, {}, "copy"),
        ({"audio_codec": "opus"}, {"mute": True}, "copy"),
        ({"audio_codec": "opus"}, {}, "copy_video"),
        ({"audio_codec": "mp3"}, {}, "copy_video"),
        ({}, {"audio_bitrate_kbps": 96}, "copy_video"),
        ({}, {"audio_bitrate_kbps": 128, "crf": 23, "preset": "veryfast"}, "copy"),
        ({}, {"max_width": 1280}, "copy"),
        ({}, {"max_width": 3840}, "copy"),
        ({}, {"duration_seconds": 103}, "copy"),
        ({}, {"max_width": 1279}, "encode"),
        ({}, {"crf": 22}, "encode"),
        ({}, {"preset": "slow"}, "encode"),
        ({}, {"start_seconds": 0.5}, "encode"),
        ({}, {"duration_seconds": 102.5}, "encode"),
        ({"video_codec": "hevc"}, {}, "encode"),
        ({"video_codec": "vp9", "audio_codec": "opus"}, {}, "encode"),
        ({"pixel_format": "yuv420p10le"}, {}, "encode"),
        ({"pixel_format": "yuv444p"}, {}, "encode"),
        ({"pixel_format": None}, {}, "encode"),
        ({"width": 1279}, {}, "encode"),
        ({"height": None}, {}, "encode"),
    ],
)
def test_mp4_route(info, params, route):
    assert plan_mp4_route(h264(**info), mp4(**params)) == route


def test_mp4_route_compares_width_as_shown():
    portrait = h264(width=720, height=1280, rotation=90)
    assert plan_mp4_route(portrait, mp4(max_width=720)) == "copy"
    assert plan_mp4_route(portrait, mp4(max_width=1000)) == "copy"
    assert plan_mp4_route(portrait, mp4(max_width=640)) == "encode"


TAIL = ["-avoid_negative_ts", "make_zero", "-movflags", "+faststart", "-fflags", "+bitexact", "-f", "mp4"]


def test_mp4_copy_command():
    config = mp4()
    cmd = build_convert_command("in.blob", "out.tmp", config, duration=103.0, threads=2, route="copy")
    assert cmd == [
        *COMMON[:-1], "1", *PROGRESS, "-t", "104", "-i", "file:in.blob",
        "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-c:v", "copy", "-c:a", "copy", *TAIL, "file:out.tmp",
    ]  # fmt: skip
    muted = build_convert_command("in.blob", "out.tmp", mp4(mute=True), duration=103.0, threads=2, route="copy")
    assert muted[muted.index("-map") : muted.index("-avoid_negative_ts")] == [
        "-map", "0:v:0", "-an", "-sn", "-dn", "-c:v", "copy",
    ]  # fmt: skip


def test_mp4_copy_video_command_reencodes_only_the_audio():
    cmd = build_convert_command(
        "in.blob", "out.tmp", mp4(audio_bitrate_kbps=96), duration=30.0, threads=2, route="copy_video"
    )
    assert cmd == [
        *COMMON, *PROGRESS, "-t", "31", "-i", "file:in.blob",
        "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-c:v", "copy", "-c:a", "aac", "-b:a", "96k",
        "-threads", "2", *TAIL, "file:out.tmp",
    ]  # fmt: skip


def test_copy_routes_keep_the_limits():
    with pytest.raises(MediaTooLongError):
        build_convert_command("i", "o", mp4(), duration=601.0, threads=1, route="copy")
    cmd = build_convert_command("i", "o", mp4(duration_seconds=200), duration=103.0, threads=1, route="copy")
    assert cmd[cmd.index("-t") + 1] == "200"
    with pytest.raises(ValueError):
        build_convert_command("i", "o", mp4(start_seconds=1), duration=103.0, threads=1, route="copy")
    with pytest.raises(ValueError):
        build_convert_command(
            "i", "o", VIDEO_CONVERT.from_params({}, target="webm"), duration=9, threads=1, route="copy"
        )


def test_the_reencode_route_is_the_default_command():
    config = mp4()
    assert build_convert_command("i", "o", config, duration=103.0, threads=2) == build_convert_command(
        "i", "o", config, duration=103.0, threads=2, route="encode"
    )
