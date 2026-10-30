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
    Limits,
    Mp4TransformConfig,
    Vp9TrimConfig,
)
from sashimi.errors import InvalidOptionsError, MediaTooLongError
from sashimi.core.probe import MediaInfo, StreamInfo
from sashimi.video import (
    build_convert_command,
    build_palette_command,
    build_transform_command,
    build_trim_command,
    check_copy_compatible,
    plan_mp4_route,
    two_pass,
)

COMMON = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-filter_threads", "2"]
FORMATS = "3g2,3gp,aac,ac3,aiff,amr,amrnb,amrwb,asf,au,avi,caf,dts,eac3,flac,flv,gif,m4a,matroska,mj2,mov,mp3,mp4,mpeg,mpegts,ogg,wav,webm,wv,yuv4mpegpipe"
FRAME_PROGRESS = (
    "metadata=mode=add:key=sashimi_progress:value=1,metadata=mode=print:key=sashimi_progress:file=pipe\\\\:2:direct=1"
)
"""Prints each frame's time on stderr: the palette pass's progress."""
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
FILESUSHI = Limits(max_video_seconds=600, max_gif_seconds=30)
"""FileSushi's limits: 10 minutes of video, 30 s GIFs."""


def build(target, duration=103.0, limits=None, palette=None, **params):
    config = VIDEO_CONVERT.from_params(params, target=target)
    return build_convert_command(
        "in.blob", "out.tmp", config, duration=duration, threads=2, limits=limits, palette=palette
    )


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


def test_gif_palette_pass():
    config = VIDEO_CONVERT.from_params({"start_seconds": 12.5, "palette_mode": "full"}, target="gif")
    assert build_palette_command("in.blob", config, duration=103.0, threads=2) == [
        *COMMON, *PROGRESS[3:], "-threads", "2",
        "-ss", "12.5", "-t", "10", "-i", "file:in.blob", "-an",
        "-vf", f"fps=12,scale='min(480,iw)':-2:flags=lanczos,{FRAME_PROGRESS},palettegen=stats_mode=full",
        "-frames:v", "1", "-fflags", "+bitexact", "-f", "image2pipe", "-c:v", "png", "pipe:1",
    ]  # fmt: skip


def test_gif_encode_pass_reads_the_palette_inline():
    assert build("gif", palette=b"\x89PNG") == [
        *COMMON, *PROGRESS, "-threads", "2", "-t", "10", "-i", "file:in.blob",
        "-protocol_whitelist", "data", "-format_whitelist", "png_pipe", "-f", "png_pipe",
        "-i", "data:image/png;base64,iVBORw==",
        "-filter_complex",
        "[0:v]fps=12,scale='min(480,iw)':-2:flags=lanczos[frames];[frames][1:v]"
        "paletteuse=dither=bayer:bayer_scale=4[gif]",
        "-map", "[gif]", "-loop", "0", "-an",
        "-threads", "2", "-fflags", "+bitexact", "-f", "gif", "file:out.tmp",
    ]  # fmt: skip


def test_a_palette_per_frame_takes_one_pass():
    assert two_pass(VIDEO_CONVERT.from_params({}, target="gif"))
    assert two_pass(VIDEO_CONVERT.from_params({"palette_mode": "full"}, target="gif"))
    single = VIDEO_CONVERT.from_params({"palette_mode": "single"}, target="gif")
    assert not two_pass(single)
    assert build("gif", palette_mode="single", palette=b"\x89PNG") == build("gif", palette_mode="single")
    assert build("webp", palette=b"\x89PNG") == build("webp")


def test_the_palette_pass_checks_like_the_encode_pass():
    config = VIDEO_CONVERT.from_params({"start_seconds": 200}, target="gif")
    with pytest.raises(InvalidOptionsError):
        build_palette_command("in.blob", config, duration=103.0, threads=2)
    long = VIDEO_CONVERT.from_params({"duration_seconds": 40}, target="gif")
    with pytest.raises(InvalidOptionsError):
        build_palette_command("in.blob", long, duration=103.0, threads=2, limits=FILESUSHI)


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
    assert e.value.user_message == "The start time (200s) is at or past the end of the video (103s)."


@pytest.mark.parametrize("start, duration, shown", [(4, 3.523, "3.523"), (4, 4.0, "4"), (5, 4.9996, "5")])
def test_a_start_past_the_end_shows_the_length_to_the_millisecond(start, duration, shown):
    with pytest.raises(InvalidOptionsError) as e:
        build("mp4", duration=duration, start_seconds=start)
    assert e.value.user_message == f"The start time ({start}s) is at or past the end of the video ({shown}s)."


@pytest.mark.parametrize(
    "target, message",
    [("gif", "GIFs can be at most 30 seconds long."), ("webp", "Animated WebPs can be at most 30 seconds long.")],
)
def test_animated_length_limit_comes_from_the_limits(target, message):
    config = VIDEO_CONVERT.from_params({"duration_seconds": 31}, target=target)
    with pytest.raises(InvalidOptionsError) as e:
        FILESUSHI.check(config)
    assert (e.value.user_message, e.value.limit) == (message, "max_gif_seconds")
    with pytest.raises(InvalidOptionsError):
        build(target, duration_seconds=31, limits=FILESUSHI)
    build(target, duration_seconds=30, limits=FILESUSHI)
    long = build(target, duration=900.0, duration_seconds=600)
    assert long[long.index("-t") + 1] == "600"


@pytest.mark.parametrize("target", ["webm", "mp4"])
def test_video_length_limit(target):
    with pytest.raises(MediaTooLongError) as e:
        build(target, duration=601.0, limits=FILESUSHI)
    assert e.value.user_message == (
        "This video is 10 minutes 1 second long, and the limit is 10 minutes. Set a shorter duration to convert "
        "part of it."
    )
    assert e.value.limit == "max_video_seconds"
    build(target, duration=3600.0, duration_seconds=600, limits=FILESUSHI)
    cmd = build(target, duration=900.0, start_seconds=300, limits=FILESUSHI)
    assert cmd[cmd.index("-i") - 4 : cmd.index("-i")] == ["-ss", "300", "-t", "600"]
    with pytest.raises(InvalidOptionsError) as e:
        build(target, duration=3600.0, duration_seconds=601, limits=FILESUSHI)
    assert e.value.user_message == "Video clips can be at most 10 minutes long."
    with pytest.raises(MediaTooLongError) as e:
        build(target, duration=7201.0, limits=Limits(max_video_seconds=7200))
    assert e.value.user_message.startswith("This video is 2 hours 1 second long, and the limit is 2 hours.")
    with pytest.raises(MediaTooLongError) as e:
        build(target, duration=900.0, start_seconds=200, limits=FILESUSHI)
    assert e.value.user_message.startswith("From 200s, this video runs 11 minutes 40 seconds, and the limit is")


@pytest.mark.parametrize("target", ["webm", "mp4"])
def test_videos_have_no_length_limit_by_default(target):
    cmd = build(target, duration=7200.0)
    assert cmd[cmd.index("-t") + 1] == "7201"
    cmd = build(target, duration=900.0, start_seconds=300)
    assert cmd[cmd.index("-i") - 4 : cmd.index("-i")] == ["-ss", "300", "-t", "601"]
    assert build(target, duration=10.0) == build(target, duration=10.0, limits=FILESUSHI)


@pytest.mark.parametrize(
    "params",
    [
        {"max_width": 63},
        {"fps": 0},
        {"fps": 51},
        {"start_seconds": -1},
        {"duration_seconds": 0},
        {"quality": 101},
    ],  # fmt: skip
)
def test_out_of_range_parameters(params):
    with pytest.raises(InvalidOptionsError):
        VIDEO_CONVERT.from_params(params, target="webp")


def test_only_format_limits_bound_animations():
    config = VIDEO_CONVERT.from_params({"max_width": 7680, "fps": 50, "duration_seconds": 600}, target="webp")
    assert (config.max_width, config.fps, config.duration_seconds) == (7680, 50, 600)
    with pytest.raises(InvalidOptionsError) as e:
        VIDEO_CONVERT.from_params({"duration_seconds": float("inf")}, target="gif")
    assert e.value.user_message == "Invalid value for duration_seconds: input should be a finite number."


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
        build_trim_command("a", "b", H264TrimConfig(container="mp4"), duration=700.0, threads=1, limits=FILESUSHI)
    long = build_trim_command(
        "a", "b", H264TrimConfig(container="mp4", start_seconds=100), duration=700.0, threads=1, limits=FILESUSHI
    )
    assert long[long.index("-t") + 1] == "600"
    whole = build_trim_command("a", "b", H264TrimConfig(container="mp4"), duration=700.0, threads=1)
    assert whole[whole.index("-t") + 1] == "701"


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
    ],
)
def test_trim_validation(params, container, message):
    with pytest.raises(InvalidOptionsError) as e:
        VIDEO_TRIM.from_params(params, container=container)
    assert e.value.user_message == message


@pytest.mark.parametrize(
    "params, container",
    [({"accurate": True, "end_seconds": 700}, "mkv"), ({"accurate": True, "duration_seconds": 601}, "webm")],
)
def test_accurate_trims_are_limited_by_the_limits(params, container):
    config = VIDEO_TRIM.from_params(params, container=container)
    with pytest.raises(InvalidOptionsError) as e:
        FILESUSHI.check(config)
    assert (e.value.user_message, e.value.limit) == ("Video clips can be at most 10 minutes long.", "max_video_seconds")
    with pytest.raises(InvalidOptionsError):
        build_trim_command("a", "b", config, duration=3600.0, limits=FILESUSHI)
    build_trim_command("a", "b", config, duration=3600.0)
    FILESUSHI.check(VIDEO_TRIM.from_params({"end_seconds": 7200}, container=container))


def test_copy_trim_has_no_length_limit_and_checks_the_start():
    VIDEO_TRIM.from_params({"end_seconds": 7200}, container="mkv")
    with pytest.raises(InvalidOptionsError) as e:
        build_trim_command("a", "b", CopyTrimConfig(container="mkv", start_seconds=50), duration=40.0)
    assert e.value.user_message == "The start time (50s) is at or past the end of the video (40s)."


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
    cmd = build_transform_command("a", "b", config, duration=599.5, threads=2, limits=FILESUSHI)
    assert cmd[cmd.index("-i") - 2 : cmd.index("-i")] == ["-t", "600"]
    with pytest.raises(MediaTooLongError):
        build_transform_command("a", "b", config, duration=600.5, threads=2, limits=FILESUSHI)
    unlimited = build_transform_command("a", "b", config, duration=3600.5, threads=2)
    assert unlimited[unlimited.index("-i") - 2 : unlimited.index("-i")] == ["-t", "3601.5"]


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
    assert e.value.user_message == (
        "This video can't be copied into MP4 without re-encoding: MP4 can't hold its hdmv_pgs_subtitle subtitle. "
        "Try MKV instead, or convert it (which re-encodes)."
    )


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
        build_convert_command("i", "o", mp4(), duration=601.0, threads=1, route="copy", limits=FILESUSHI)
    long = build_convert_command("i", "o", mp4(), duration=7200.0, threads=1, route="copy")
    assert long[long.index("-t") + 1] == "7201"
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
