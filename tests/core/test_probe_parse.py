from sashimi.core.probe import parse_ffprobe_json


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
            "streams": [video_stream(), {"codec_type": "audio", "codec_name": "aac"}],
        }
    )
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
