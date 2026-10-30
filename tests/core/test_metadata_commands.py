# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""The ffmpeg arguments for each ``metadata`` mode, and Opus's per-channel bitrate ceiling. Pure."""

import pytest

from sashimi import audio, video
from sashimi.config import AUDIO_CONVERT, OPUS_MAX_KBPS_PER_CHANNEL, VIDEO_CONVERT, VIDEO_TRANSFORM, VIDEO_TRIM
from sashimi.core._mediameta import (
    LOCATION_KEYS,
    dropped_streams,
    merged_movflags,
    metadata_args,
    opus_kbps,
    quicktime_tag_args,
)
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


def _tagged(**tags: str) -> MediaInfo:
    return MediaInfo(container="mov,mp4,m4a,3gp,3g2,mj2", duration=1.0, has_video=True, tags=tags)


IPHONE = _tagged(
    major_brand="qt  ",
    minor_version="0",
    compatible_brands="qt  ",
    creation_time="2024-09-06T16:08:07.000000Z",
    **{"com.apple.quicktime.make": "Apple", "com.apple.quicktime.location.ISO6709": "+40+008/"},
)


@pytest.mark.parametrize("muxer", ["mp4", "mov", "ipod", "3gp"])
def test_quicktime_keys_are_written_as_metadata_keys_with_the_source_creation_time(muxer):
    assert quicktime_tag_args("keep", IPHONE, muxer) == [
        "-movflags",
        "+use_metadata_tags",
        *("-metadata:g", "major_brand=", "-metadata:g", "minor_version=", "-metadata:g", "compatible_brands="),
        *("-metadata:g", "creation_time=2024-09-06T16:08:07.000000Z"),
    ]


def test_remove_location_keeps_the_other_keys_and_remove_all_adds_nothing():
    assert "+use_metadata_tags" in quicktime_tag_args("remove_location", IPHONE, "mp4")
    only_location = _tagged(**{"com.apple.quicktime.location.ISO6709": "+40+008/", "title": "t"})
    assert quicktime_tag_args("remove_location", only_location, "mp4") == []
    assert quicktime_tag_args("keep", only_location, "mp4")[:2] == ["-movflags", "+use_metadata_tags"]
    assert quicktime_tag_args("remove_all", IPHONE, "mp4") == []


def test_tags_the_plain_writer_keeps_need_no_metadata_keys():
    assert quicktime_tag_args("keep", _tagged(title="t", artist="a", encoder="Lavf"), "mp4") == []
    assert quicktime_tag_args("keep", _tagged(title="t", location="+1+2/"), "mov") == []
    assert quicktime_tag_args("keep", _tagged(title="t", location="+1+2/"), "mp4")[1] == "+use_metadata_tags"
    assert quicktime_tag_args("keep", _tagged(composer="c"), "mov")[1] == "+use_metadata_tags"
    assert quicktime_tag_args("keep", _tagged(firmware="x"), "matroska") == []
    assert quicktime_tag_args("keep", None, "mp4") == []


def test_only_a_well_formed_creation_time_is_carried():
    assert quicktime_tag_args("keep", _tagged(creation_time="2019-11-18T15:41:25.000000Z"), "mp4") == [
        "-metadata:g",
        "creation_time=2019-11-18T15:41:25.000000Z",
    ]
    assert quicktime_tag_args("keep", _tagged(creation_time="yesterday"), "mp4") == []


def test_movflags_are_merged_into_one():
    cmd = ["ffmpeg", "-movflags", "+faststart", "-c", "copy", "-movflags", "+use_metadata_tags", "out"]
    assert merged_movflags(cmd) == ["ffmpeg", "-movflags", "+faststart+use_metadata_tags", "-c", "copy", "out"]
    assert merged_movflags(["ffmpeg", "-movflags", "+faststart", "out"]) == ["ffmpeg", "-movflags", "+faststart", "out"]


def test_builders_merge_the_flags_for_a_tagged_source():
    tagged = IPHONE.model_copy(update={"streams": [StreamInfo(type="video", codec="h264")]})
    cmd = video.build_trim_command(
        "in", "out", VIDEO_TRIM.from_params({"container": "mp4"}), duration=1.0, source=tagged
    )
    assert cmd.count("-movflags") == 1 and "+faststart+use_metadata_tags" in cmd


def test_other_muxers_leave_out_the_sources_file_type_tags():
    brands = ("-metadata:g", "major_brand=", "-metadata:g", "minor_version=", "-metadata:g", "compatible_brands=")
    assert quicktime_tag_args("keep", IPHONE, "mp3") == [*brands]
    assert quicktime_tag_args("remove_all", IPHONE, "matroska") == []


@pytest.mark.parametrize("muxer", ["matroska", "webm"])
@pytest.mark.parametrize("mode", ["keep", "remove_location"])
def test_matroska_and_webm_get_the_sources_creation_time(muxer, mode):
    brands = ("-metadata:g", "major_brand=", "-metadata:g", "minor_version=", "-metadata:g", "compatible_brands=")
    assert quicktime_tag_args(mode, IPHONE, muxer) == [
        *brands,
        *("-metadata:g", "creation_time=2024-09-06T16:08:07.000000Z"),
    ]
    untimed = IPHONE.model_copy(update={"tags": {k: v for k, v in IPHONE.tags.items() if k != "creation_time"}})
    assert quicktime_tag_args(mode, untimed, muxer) == [*brands]
    assert quicktime_tag_args("remove_all", IPHONE, muxer) == []


def test_a_creation_time_ffmpeg_9_states_twice_is_carried_once():
    joined = _tagged(creation_time="2021-06-05T12:01:47.000000Z;2021-06-05T12:01:47.000000Z")
    assert quicktime_tag_args("keep", joined, "mp4") == ["-metadata:g", "creation_time=2021-06-05T12:01:47.000000Z"]
    differing = _tagged(creation_time="soon;2021-06-05T12:01:47.000000Z")
    assert quicktime_tag_args("keep", differing, "mp4") == ["-metadata:g", "creation_time=2021-06-05T12:01:47.000000Z"]


def test_ffprobe_tags_repeated_with_a_semicolon_are_read_once():
    from sashimi.core.probe import parse_ffprobe_json

    data = {
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "1.0",
            "tags": {"creation_time": "2021-06-05T12:01:47Z;2021-06-05T12:01:47Z", "major_brand": "isom;mp42"},
        },
        "streams": [],
    }
    tags = parse_ffprobe_json(data).tags
    assert tags["creation_time"] == "2021-06-05T12:01:47Z" and tags["major_brand"] == "isom;mp42"
