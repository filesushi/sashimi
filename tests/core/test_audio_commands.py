# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import pytest

from sashimi.audio import build_convert_command
from sashimi.config import AUDIO_CONVERT, Limits, Mp3AudioConfig
from sashimi.errors import InvalidOptionsError, MediaTooLongError

FILESUSHI = Limits(max_audio_seconds=3600)


def build(target, duration=100.0, limits=None, **params):
    config = AUDIO_CONVERT.from_params(params, target=target)
    return build_convert_command("in", "out", config, duration=duration, threads=1, limits=limits)


def tail_after_input(cmd):
    return cmd[cmd.index("-i") + 2 :]


@pytest.mark.parametrize(
    "target, expected",
    [
        ("mp3", ["-c:a", "libmp3lame", "-b:a", "192k", "-threads", "1", "-fflags", "+bitexact", "-f", "mp3"]),
        (
            "m4a",
            [
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-threads",
                "1",
                "-fflags",
                "+bitexact",
                "-movflags",
                "+faststart",
                "-f",
                "ipod",
            ],
        ),
        ("ogg", ["-c:a", "libopus", "-b:a", "128k", "-threads", "1", "-fflags", "+bitexact", "-f", "ogg"]),
        ("wav", ["-c:a", "pcm_s16le", "-threads", "1", "-fflags", "+bitexact", "-f", "wav"]),
        (
            "flac",
            ["-c:a", "flac", "-compression_level", "5", "-threads", "1", "-fflags", "+bitexact", "-f", "flac"],
        ),
    ],
)
def test_targets_and_default_bitrates(target, expected):
    assert tail_after_input(build(target)) == ["-map", "0:a:0", "-vn", "-sn", "-dn", *expected, "file:out"]


def test_options():
    cmd = build("mp3", bitrate_kbps=320, start_seconds=10, duration_seconds=20, mono=True)
    i = cmd.index("-i")
    assert cmd[i - 4 : i] == ["-ss", "10", "-t", "20"]
    assert ["-ac", "1"] == cmd[cmd.index("-ac") : cmd.index("-ac") + 2]
    assert cmd[cmd.index("-b:a") + 1] == "320k"


def test_whole_file_stops_just_past_the_probed_length():
    cmd = build("mp3")
    assert cmd[cmd.index("-i") - 2 : cmd.index("-i")] == ["-t", "101"]
    cmd = build("mp3", duration=3599.5, limits=FILESUSHI)
    assert cmd[cmd.index("-i") - 2 : cmd.index("-i")] == ["-t", "3600"]
    unlimited = build("mp3", duration=3599.5)
    assert unlimited[unlimited.index("-i") - 2 : unlimited.index("-i")] == ["-t", "3600.5"]
    tail = build("mp3", duration=4000.0, start_seconds=1000)
    assert tail[tail.index("-i") - 4 : tail.index("-i")] == ["-ss", "1000", "-t", "3001"]
    assert cmd[cmd.index("-protocol_whitelist") : cmd.index("-t")] == [
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "3g2,3gp,aac,asf,avi,flac,flv,gif,m4a,matroska,mj2,mov,mp3,mp4,mpeg,mpegts,ogg,wav,webm",
    ]


@pytest.mark.parametrize("target", ["flac", "wav"])
def test_bitrate_is_rejected_for_lossless(target):
    assert "-b:a" not in build(target)
    with pytest.raises(InvalidOptionsError) as e:
        build(target, bitrate_kbps=256)
    assert e.value.user_message == "Unknown option: bitrate_kbps."


@pytest.mark.parametrize("bitrate", [63, 321])
def test_bitrate_range(bitrate):
    with pytest.raises(InvalidOptionsError):
        AUDIO_CONVERT.from_params({"bitrate_kbps": bitrate}, target="mp3")


@pytest.mark.parametrize("target", ["m4a", "ogg"])
def test_aac_and_opus_take_their_encoders_bitrates(target):
    assert "512k" in build(target, bitrate_kbps=512)
    with pytest.raises(InvalidOptionsError):
        AUDIO_CONVERT.from_params({"bitrate_kbps": 513}, target=target)


def test_audio_length_limit():
    with pytest.raises(MediaTooLongError) as e:
        build("mp3", duration=3601.0, limits=FILESUSHI)
    assert e.value.user_message == (
        "Audio longer than 60 minutes isn't supported yet. Set a shorter duration to convert part of it."
    )
    assert e.value.limit == "max_audio_seconds"
    build("mp3", duration=7200.0, duration_seconds=3600, limits=FILESUSHI)
    config = AUDIO_CONVERT.from_params({"duration_seconds": 3601}, target="m4a")
    with pytest.raises(InvalidOptionsError) as e:
        build_convert_command("in", "out", config, duration=7200.0, limits=FILESUSHI)
    assert e.value.user_message == "Audio clips can be at most 60 minutes long."
    with pytest.raises(InvalidOptionsError) as e:
        build("mp3", duration=700.0, duration_seconds=600.5, limits=Limits(max_audio_seconds=600))
    assert e.value.user_message == "Audio clips can be at most 10 minutes long."


def test_audio_has_no_length_limit_by_default():
    cmd = build("flac", duration=14400.0)
    assert cmd[cmd.index("-t") + 1] == "14401"
    cut = build("mp3", duration=20000.0, duration_seconds=19999)
    assert cut[cut.index("-t") + 1] == "19999"


def test_start_past_end_names_the_source_kind():
    with pytest.raises(InvalidOptionsError) as e:
        build_convert_command("in", "out", Mp3AudioConfig(start_seconds=50), duration=30.0, source_kind="video")
    assert e.value.user_message == "The start time (50s) is past the end of the video (30s)."
