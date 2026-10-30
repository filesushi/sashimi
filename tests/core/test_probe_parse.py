# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import json

import pytest

from sashimi.config import Limits
from sashimi.core import probe as probe_module
from sashimi.core.probe import parse_ffprobe_json, probe
from sashimi.errors import BinaryNotFoundError, MediaTooLargeError, ProcessingError


def video_stream(**extra):
    return {
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "avg_frame_rate": "30000/1001",
        **extra,
    }


def test_basic_fields():
    info = parse_ffprobe_json(
        {
            "format": {"format_name": "matroska,webm", "duration": "103.5", "size": "1234", "bit_rate": "999"},
            "streams": [video_stream(pix_fmt="yuv420p"), {"codec_type": "audio", "codec_name": "aac"}],
        }
    )
    assert info.pixel_format == "yuv420p"
    assert info.container == "matroska,webm"
    assert info.container_names == {"matroska", "webm"}
    assert (info.duration, info.size_bytes, info.bit_rate) == (103.5, 1234, 999)
    assert (info.width, info.height, info.rotation) == (1920, 1080, 0)
    assert round(info.fps, 3) == 29.97
    assert info.has_video and info.has_audio
    assert info.codecs == ["h264", "aac"]


def test_display_matrix_rotation_swaps_dimensions():
    info = parse_ffprobe_json(
        {
            "format": {"format_name": "mov,mp4", "duration": "5"},
            "streams": [video_stream(side_data_list=[{"side_data_type": "Display Matrix", "rotation": -90}])],
        }
    )
    assert (info.width, info.height, info.rotation) == (1080, 1920, 90)
    assert round(info.display_aspect_ratio, 4) == round(1080 / 1920, 4)


def test_a_dolby_vision_configuration_record_is_noticed():
    dovi = {"side_data_type": "DOVI configuration record", "dv_profile": 8, "dv_bl_signal_compatibility_id": 4}
    info = parse_ffprobe_json({"format": {"format_name": "mov,mp4"}, "streams": [video_stream(side_data_list=[dovi])]})
    assert info.dolby_vision
    plain = parse_ffprobe_json({"format": {"format_name": "mov,mp4"}, "streams": [video_stream()]})
    assert not plain.dolby_vision


def test_legacy_rotate_tag():
    info = parse_ffprobe_json(
        {"format": {"format_name": "mov", "duration": "5"}, "streams": [video_stream(tags={"rotate": "270"})]}
    )
    assert (info.width, info.height, info.rotation) == (1080, 1920, 270)


def test_anamorphic_sample_aspect_ratio():
    info = parse_ffprobe_json(
        {
            "format": {"format_name": "mpeg", "duration": "5"},
            "streams": [video_stream(width=720, height=576, sample_aspect_ratio="64:45")],
        }
    )
    assert round(info.display_aspect_ratio, 3) == round(720 * 64 / 45 / 576, 3)


def test_cover_art_is_not_video():
    info = parse_ffprobe_json(
        {
            "format": {"format_name": "mp3", "duration": "4"},
            "streams": [
                {"codec_type": "audio", "codec_name": "mp3"},
                video_stream(codec_name="mjpeg", disposition={"attached_pic": 1}),
            ],
        }
    )
    assert not info.has_video and info.has_audio
    assert info.width is None


def test_missing_duration_falls_back_to_streams_then_none():
    streams = [video_stream(duration="7.5")]
    assert parse_ffprobe_json({"format": {"format_name": "x"}, "streams": streams}).duration == 7.5
    still = parse_ffprobe_json({"format": {"format_name": "png_pipe"}, "streams": [video_stream()]})
    assert still.duration is None


@pytest.mark.parametrize(
    "where, tags, expected",
    [
        ("format", {"DURATION": "00:04:01.500000000"}, 241.5),
        ("stream", {"DURATION": "01:00:00.250000000"}, 3600.25),
        ("stream", {"DURATION-eng": "00:00:07.000000000"}, 7.0),
        ("stream", {"duration-ENG": "00:00:07.000000000"}, 7.0),
        ("format", {"DURATION": "garbage"}, None),
        ("format", {"DURATION": "00:61:00.000"}, None),
        ("format", {"DURATION": "00:00:00.000000000"}, None),
        ("format", {"DURATION": "00:00:nan"}, None),
        ("format", {"DURATION_FROM_SOMETHING": "00:00:07.000"}, None),
    ],
)
def test_duration_tags_when_nothing_else_states_a_length(where, tags, expected):
    fmt = {"format_name": "mov,mp4", **({"tags": tags} if where == "format" else {})}
    stream = video_stream(**({"tags": tags} if where == "stream" else {}))
    assert parse_ffprobe_json({"format": fmt, "streams": [stream]}).duration == expected


def test_stated_duration_beats_tags():
    fmt = {"format_name": "mov,mp4", "duration": "5", "tags": {"DURATION": "00:01:00.000"}}
    assert parse_ffprobe_json({"format": fmt, "streams": [video_stream()]}).duration == 5.0


def test_longest_stream_tag_wins():
    streams = [
        video_stream(tags={"DURATION": "00:00:05.000"}),
        {"codec_type": "audio", "tags": {"DURATION": "00:00:05.040"}},
    ]
    assert parse_ffprobe_json({"format": {"format_name": "mov,mp4"}, "streams": streams}).duration == 5.04


@pytest.mark.parametrize(
    "container, start, stated, expected",
    [
        ("matroska,webm", "100.000000", "105.038000", 105.038),
        ("matroska,webm", "1.734000", "00:00:06.749000000", 6.749),
        ("matroska,webm", "0.000000", "105.038000", 105.038),
        ("mov,mp4,m4a,3gp,3g2,mj2", "100.000000", "5.038000", 5.038),
        ("mpegts", "101.376778", "5.023222", 5.023222),
    ],
)
def test_the_parsed_length_is_the_stated_one(container, start, stated, expected):
    fmt = {"format_name": container, "start_time": start, "tags": {"ENCODER": "Lavf62.12.102"}}
    if ":" in stated:
        fmt["tags"]["DURATION"] = stated
    else:
        fmt["duration"] = stated
    info = parse_ffprobe_json({"format": fmt, "streams": [video_stream()]})
    assert info.duration == expected and info.start_time == float(start)


@pytest.mark.parametrize(
    "tags",
    [
        {"ENCODER": "Lavf62.12.102"},
        {"encoder": "Lavf"},
        {},
        {"ENCODER": "libebml v1.4.5 + libmatroska v1.7.1"},
        {"ENCODER": "mkvmerge v88.0 ('All I Know') 64-bit"},
    ],
)
def test_no_muxer_name_shortens_a_stated_length(tags):
    fmt = {"format_name": "matroska,webm", "start_time": "2.000000", "duration": "10.031000", "tags": tags}
    assert parse_ffprobe_json({"format": fmt, "streams": [video_stream()]}).duration == 10.031


@pytest.mark.parametrize(
    "fmt, streams, expected",
    [
        ({"start_time": "100.000000"}, [{"start_time": "90"}], 100.0),
        ({"start_time": "-0.021000"}, [], -0.021),
        ({}, [{"start_time": "1.734"}, {"start_time": "1.600"}], 1.6),
        ({"start_time": "N/A"}, [{"start_time": "N/A"}], 0.0),
        ({}, [], 0.0),
    ],
)
def test_start_time(fmt, streams, expected):
    assert probe_module._start_time({"format": fmt, "streams": streams}) == expected


def unfinalized(container="matroska,webm", start="100.000000", streams=None):
    return {
        "format": {"format_name": container, "start_time": start},
        "streams": streams
        if streams is not None
        else [
            {"index": 0, **video_stream(start_time=start)},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "start_time": start},
        ],
    }


class FakeFfprobe:
    def __init__(self, monkeypatch, data, packets=b"", fail_after=None):
        self.data, self.packets, self.fail_after = data, packets, fail_after
        self.scans = []
        monkeypatch.setattr(probe_module, "run_ffmpeg", self.run)
        monkeypatch.setattr(probe_module, "stream_ffmpeg", self.stream)
        monkeypatch.setattr(probe_module, "ffprobe_binary", lambda: "ffprobe")

    def run(self, cmd, capture_stdout=False, **kwargs):
        assert "-show_format" in cmd
        return json.dumps(self.data).encode()

    def stream(self, cmd, chunk_size):
        self.scans.append(cmd)
        self.stopped_early = True
        for start in range(0, len(self.packets), 7):
            yield self.packets[start : start + 7]
        if self.fail_after is not None:
            raise self.fail_after
        self.stopped_early = False


PACKETS_FROM_100 = b"0,100.000000,100.000000,0.040000\n1,100.000000,100.000000,0.021333\n0,104.960000,104.920000,0.040000\n1,104.992000,104.992000,0.021333\n0,104.880000,104.880000,0.040000\n"


def test_unfinalized_video_is_measured_from_its_packets(monkeypatch):
    fake = FakeFfprobe(monkeypatch, unfinalized(), PACKETS_FROM_100)
    assert probe("capture.mkv").duration == 5.013333
    (cmd,) = fake.scans
    assert "-read_intervals" not in cmd
    assert cmd[-1] == "file:capture.mkv"
    assert "packet=stream_index,pts_time,dts_time,duration_time" in cmd
    assert cmd[cmd.index("-format_whitelist") - 2 : cmd.index("-format_whitelist")] == ["-protocol_whitelist", "file"]


def test_unfinalized_video_from_zero(monkeypatch):
    packets = b"0,0.000000,0.000000,0.040000\n0,4.960000,4.960000,0.040000\n1,5.010000,5.010000,0.021333\n"
    FakeFfprobe(monkeypatch, unfinalized(start="0.000000"), packets)
    assert probe("capture.mkv").duration == 5.031333


def test_packets_without_pts_use_dts_and_other_streams_are_ignored(monkeypatch):
    streams = [
        {"index": 0, **video_stream()},
        {"index": 1, **video_stream(codec_name="mjpeg", disposition={"attached_pic": 1})},
        {"index": 2, "codec_type": "subtitle", "codec_name": "ass"},
    ]
    packets = b"0,N/A,3.000000,N/A\n1,90.000000,90.000000,0\n2,95.000000,95.000000,1\nnot,a,packet\n\n"
    FakeFfprobe(monkeypatch, unfinalized(start="0.000000", streams=streams), packets)
    assert probe("capture.mkv").duration == 3.0


def test_unfinalized_audio_is_measured(monkeypatch):
    streams = [{"index": 0, "codec_type": "audio", "codec_name": "opus", "start_time": "100.000000"}]
    fake = FakeFfprobe(monkeypatch, unfinalized(streams=streams), b"0,100.0,100.0,0.02\n0,104.98,104.98,0.02\n")
    assert probe("capture.mka").duration == 5.0
    assert len(fake.scans) == 1


def test_a_scan_that_fails_part_way_keeps_what_it_read(monkeypatch):
    FakeFfprobe(monkeypatch, unfinalized(), PACKETS_FROM_100, fail_after=ProcessingError("truncated"))
    assert probe("capture.mkv").duration == 5.013333


def test_a_scan_without_packets_leaves_the_length_unknown(monkeypatch):
    FakeFfprobe(monkeypatch, unfinalized(), b"", fail_after=ProcessingError("unreadable"))
    assert probe("capture.mkv").duration is None


def test_a_missing_ffprobe_during_the_scan_is_not_hidden(monkeypatch):
    FakeFfprobe(monkeypatch, unfinalized(), b"", fail_after=BinaryNotFoundError("gone"))
    with pytest.raises(BinaryNotFoundError):
        probe("capture.mkv")


@pytest.mark.parametrize(
    "data",
    [
        {"format": {"format_name": "gif"}, "streams": [{"index": 0, **video_stream(codec_name="gif")}]},
        {"format": {"format_name": "png_pipe"}, "streams": [{"index": 0, **video_stream(codec_name="png")}]},
        {"format": {"format_name": "image2"}, "streams": [{"index": 0, **video_stream(codec_name="mjpeg")}]},
        {"format": {"format_name": "webp_pipe"}, "streams": [{"index": 0, **video_stream(codec_name="webp")}]},
        {"format": {"format_name": "matroska,webm", "duration": "5.0"}, "streams": [{"index": 0, **video_stream()}]},
        {"format": {"format_name": "matroska,webm"}, "streams": [{"index": 0, "codec_type": "subtitle"}]},
        {
            "format": {"format_name": "mp3"},
            "streams": [{"index": 0, **video_stream(codec_name="mjpeg", disposition={"attached_pic": 1})}],
        },
    ],
)
def test_no_scan_for_stills_or_stated_lengths(monkeypatch, data):
    fake = FakeFfprobe(monkeypatch, data, b"0,99.0,99.0,1\n")
    probe("still")
    assert fake.scans == []


def lines_of_packets(count, start=100.0):
    return b"".join(b"0,%.6f,%.6f,0.040000\n" % (start + i * 0.04, start + i * 0.04) for i in range(count))


@pytest.fixture
def capture(tmp_path):
    path = tmp_path / "capture.mkv"
    path.write_bytes(b"recording")
    probe_module._scans.clear()
    yield path
    probe_module._scans.clear()


def test_a_scan_past_the_limit_is_refused_and_stops_reading(monkeypatch, capture):
    fake = FakeFfprobe(monkeypatch, unfinalized(), lines_of_packets(50))
    with pytest.raises(MediaTooLargeError) as e:
        probe(capture, limits=Limits(max_scan_packets=10))
    assert e.value.limit == "max_scan_packets"
    assert e.value.user_message == (
        "This recording doesn't state its length, so it would have to be read through to measure it, and it has "
        "more than 10 audio and video packets (about 1 second of 30 fps video with sound); the limit is 10 packets."
    )
    assert fake.stopped_early


@pytest.mark.parametrize("cap", [50, 51, None])
def test_a_scan_within_the_limit_or_without_one_measures_the_file(monkeypatch, tmp_path, cap):
    probe_module._scans.clear()
    FakeFfprobe(monkeypatch, unfinalized(), lines_of_packets(50))
    assert probe(tmp_path / "x.mkv", limits=Limits(max_scan_packets=cap)).duration == 2.0


def test_the_default_limit_applies_when_none_is_given(monkeypatch, capture):
    monkeypatch.setattr(probe_module, "resolve_limits", lambda limits: Limits(max_scan_packets=3))
    FakeFfprobe(monkeypatch, unfinalized(), lines_of_packets(5))
    with pytest.raises(MediaTooLargeError):
        probe(capture)
    assert Limits().max_scan_packets == 2_000_000


def test_a_measured_file_is_not_read_again(monkeypatch, capture):
    fake = FakeFfprobe(monkeypatch, unfinalized(), PACKETS_FROM_100)
    assert [probe(capture).duration for _ in range(3)] == [5.013333] * 3
    assert len(fake.scans) == 1


def test_a_changed_file_is_measured_again(monkeypatch, capture):
    fake = FakeFfprobe(monkeypatch, unfinalized(), PACKETS_FROM_100)
    probe(capture)
    capture.write_bytes(b"a longer recording")
    fake.packets = lines_of_packets(10)
    assert probe(capture).duration == 0.4
    assert len(fake.scans) == 2


def test_remembered_measurements_follow_the_limit_given(monkeypatch, capture):
    fake = FakeFfprobe(monkeypatch, unfinalized(), lines_of_packets(20))
    assert probe(capture, limits=Limits(max_scan_packets=None)).duration == 0.8
    with pytest.raises(MediaTooLargeError):
        probe(capture, limits=Limits(max_scan_packets=19))
    assert len(fake.scans) == 1
    probe_module._scans.clear()
    with pytest.raises(MediaTooLargeError):
        probe(capture, limits=Limits(max_scan_packets=5))
    with pytest.raises(MediaTooLargeError):
        probe(capture, limits=Limits(max_scan_packets=4))
    assert len(fake.scans) == 2
    assert probe(capture, limits=Limits(max_scan_packets=20)).duration == 0.8
    assert len(fake.scans) == 3


def test_remembered_measurements_are_bounded(monkeypatch, tmp_path):
    probe_module._scans.clear()
    FakeFfprobe(monkeypatch, unfinalized(), PACKETS_FROM_100)
    for i in range(probe_module._SCAN_CACHE_SIZE + 10):
        path = tmp_path / f"{i}.mkv"
        path.write_bytes(b"x")
        probe(path)
    assert len(probe_module._scans) == probe_module._SCAN_CACHE_SIZE
    probe_module._scans.clear()
