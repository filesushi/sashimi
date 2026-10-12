import pytest

from sashimi.audio import build_convert_command
from sashimi.config import AUDIO_CONVERT, Mp3AudioConfig
from sashimi.errors import InvalidOptionsError, MediaTooLongError


def build(target, duration=100.0, **params):
    config = AUDIO_CONVERT.from_params(params, target=target)
    return build_convert_command("in", "out", config, duration=duration, threads=1)


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
    cmd = build("mp3", duration=3599.5)
    assert cmd[cmd.index("-i") - 2 : cmd.index("-i")] == ["-t", "3600"]
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


def test_sixty_minute_limit():
    with pytest.raises(MediaTooLongError) as e:
        build("mp3", duration=3601.0)
    assert "60 minutes" in e.value.user_message
    build("mp3", duration=7200.0, duration_seconds=3600)
    with pytest.raises(InvalidOptionsError) as e:
        AUDIO_CONVERT.from_params({"duration_seconds": 3601}, target="m4a")
    assert e.value.user_message == "Audio clips can be at most 60 minutes long."


def test_start_past_end_names_the_source_kind():
    with pytest.raises(InvalidOptionsError) as e:
        build_convert_command("in", "out", Mp3AudioConfig(start_seconds=50), duration=30.0, source_kind="video")
    assert e.value.user_message == "The start time (50s) is past the end of the video (30s)."
