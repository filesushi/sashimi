# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""The ffmpeg arguments for each ``metadata`` mode, and Opus's per-channel bitrate ceiling. Pure."""

import pytest

from sashimi import audio, video
from sashimi.config import AUDIO_CONVERT, OPUS_MAX_KBPS_PER_CHANNEL, VIDEO_CONVERT, VIDEO_TRANSFORM, VIDEO_TRIM
from sashimi.core._mediameta import LOCATION_KEYS, dropped_streams, metadata_args, opus_kbps
from sashimi.core.probe import MediaInfo, StreamInfo

SOURCE = MediaInfo(
    container="mov,mp4,m4a,3gp,3g2,mj2",
    duration=10.0,
    has_video=True,
    has_audio=True,
    audio_channels=1,
    tags={"title": "t", "location": "+1+2/", "GPS_COORDS": "x"},
    streams=[
        StreamInfo(type="video", codec="h264", tags={"handler_name": "h"}),
        StreamInfo(type="audio", codec="aac", language="fra", channels=1, tags={"language": "fra", "location": "y"}),
        StreamInfo(type="data", codec="unknown", codec_tag="text"),
        StreamInfo(type="data", codec="bin_data", codec_tag="gpmd"),
        StreamInfo(type="video", codec="mjpeg", attached_pic=True),
    ],
)


def test_keep_adds_nothing():
    assert metadata_args("keep", SOURCE, [0, 1]) == []


def test_remove_all_drops_tags_and_chapters_but_keeps_languages():
    assert metadata_args("remove_all", SOURCE, [0, 1]) == [
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-metadata:s:1",
        "language=fra",
    ]


def test_remove_location_removes_known_and_found_location_tags():
    args = metadata_args("remove_location", SOURCE, [0, 1])
    pairs = list(zip(args[::2], args[1::2], strict=True))
    assert [("-metadata:g", f"{key}=") for key in LOCATION_KEYS] == pairs[: len(LOCATION_KEYS)]
    assert ("-metadata:g", "GPS_COORDS=") in pairs and ("-metadata:s:1", "location=") in pairs
    assert ("-metadata:g", "title=") not in pairs


def test_dropped_streams():
    assert dropped_streams(SOURCE, "keep") == [2]
    assert dropped_streams(SOURCE, "remove_location") == [2, 3]
    assert dropped_streams(SOURCE, "remove_all") == [2, 3, 4]
    assert dropped_streams(None, "remove_all") == []


def test_builders_without_a_source_keep_their_commands():
    for config, build in (
        (VIDEO_CONVERT.from_params({}, target="mp4"), video.build_convert_command),
        (VIDEO_TRANSFORM.from_params({"rotate": 90}), video.build_transform_command),
        (VIDEO_TRIM.from_params({"container": "mkv"}), video.build_trim_command),
        (AUDIO_CONVERT.from_params({}, target="ogg"), audio.build_convert_command),
    ):
        cmd = build("i", "o", config, duration=10.0, threads=1)
        assert not any(arg.startswith(("-metadata", "-map_metadata", "-map_chapters")) for arg in cmd)


def test_copy_trims_leave_out_what_the_mode_drops():
    for mode, dropped in (("keep", ["-0:2"]), ("remove_all", ["-0:2", "-0:3", "-0:4"])):
        config = VIDEO_TRIM.from_params({"container": "mov", "metadata": mode})
        cmd = video.build_trim_command("i", "o", config, duration=10.0, source=SOURCE)
        assert [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-map"] == ["0", *dropped]


def test_remove_all_keeps_the_remuxed_streams_languages():
    config = VIDEO_TRIM.from_params({"container": "mov", "metadata": "remove_all"})
    cmd = video.build_trim_command("i", "o", config, duration=10.0, source=SOURCE)
    assert cmd[cmd.index("-metadata:s:1") + 1] == "language=fra"


@pytest.mark.parametrize("channels, expected", [(1, 256), (2, 320), (None, 320), (6, 320)])
def test_opus_bitrate_ceiling(channels, expected):
    assert opus_kbps(320, channels) == expected
    assert OPUS_MAX_KBPS_PER_CHANNEL == 256


def test_mono_opus_is_lowered_to_what_opus_allows():
    ogg = AUDIO_CONVERT.from_params({"bitrate_kbps": 320}, target="ogg")
    cmd = audio.build_convert_command("i", "o", ogg, duration=10.0, source=SOURCE)
    assert cmd[cmd.index("-b:a") + 1] == "256k"
    stereo = SOURCE.model_copy(update={"audio_channels": 2})
    cmd = audio.build_convert_command("i", "o", ogg, duration=10.0, source=stereo)
    assert cmd[cmd.index("-b:a") + 1] == "320k"
    mono = AUDIO_CONVERT.from_params({"bitrate_kbps": 512, "mono": True}, target="ogg")
    cmd = audio.build_convert_command("i", "o", mono, duration=10.0, source=stereo)
    assert cmd[cmd.index("-b:a") + 1] == "256k"
    mp3 = AUDIO_CONVERT.from_params({"bitrate_kbps": 320}, target="mp3")
    cmd = audio.build_convert_command("i", "o", mp3, duration=10.0, source=SOURCE)
    assert cmd[cmd.index("-b:a") + 1] == "320k"


def test_mono_opus_in_webm_is_lowered_too():
    for config, build in (
        (VIDEO_CONVERT.from_params({"audio_bitrate_kbps": 384}, target="webm"), video.build_convert_command),
        (
            VIDEO_TRANSFORM.from_params({"rotate": 90, "audio_bitrate_kbps": 384}, target="webm"),
            video.build_transform_command,
        ),
        (
            VIDEO_TRIM.from_params({"container": "webm", "accurate": True, "audio_bitrate_kbps": 384}),
            video.build_trim_command,
        ),
    ):
        cmd = build("i", "o", config, duration=10.0, threads=1, source=SOURCE)
        assert cmd[cmd.index("-b:a") + 1] == "256k"
    mp4 = VIDEO_CONVERT.from_params({"audio_bitrate_kbps": 384}, target="mp4")
    cmd = video.build_convert_command("i", "o", mp4, duration=10.0, threads=1, source=SOURCE)
    assert cmd[cmd.index("-b:a") + 1] == "384k"
