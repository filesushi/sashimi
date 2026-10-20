# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Parity tests for the preview command builder and its pure sample planner."""

import pytest

from sashimi.config import VIDEO_PREVIEW, GifPreviewConfig, WebpPreviewConfig
from sashimi.core.preview import preview_size
from sashimi.errors import InvalidOptionsError, MediaTooShortError
from sashimi.video import build_preview_commands, plan_preview

COMMON = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-filter_threads"]
FORMATS = "3g2,3gp,aac,asf,avi,flac,flv,gif,m4a,matroska,mj2,mov,mp3,mp4,mpeg,mpegts,ogg,wav,webm"
INPUT = ["-protocol_whitelist", "file", "-format_whitelist", FORMATS, "-threads", "1"]
RAW = ["-f", "rawvideo", "-pix_fmt", "rgb24"]
VF = "fps=15,scale=640:360:flags=lanczos,setsar=1"


def build(target, duration=103.0, width=1280, height=720, **params):
    config = VIDEO_PREVIEW.from_params(params, target=target)
    return build_preview_commands(
        "in.blob", "out.tmp", config, duration=duration, width=width, height=height, threads=2
    )


def clip(start, length="2", frames="30", vf=VF):
    seek = ["-ss", start] if start else []
    return [
        *COMMON, "1", *INPUT, *seek, "-t", length, "-i", "file:in.blob", "-map", "0:V:0", "-vf", vf,
        "-frames:v", frames, *RAW, "pipe:1",
    ]  # fmt: skip


def encoder(size="640x360", fps="15"):
    return [*COMMON, "2", "-progress", "pipe:1", "-nostats", *RAW, "-s", size, "-framerate", fps, "-i", "pipe:0"]


def test_webp_defaults():
    clips, encode = build("webp")
    assert clips == [clip(start) for start in ("10.3", "30.4", "50.5", "70.6", "90.7")]
    assert encode == [
        *encoder(), "-c:v", "libwebp_anim", "-lossless", "0", "-quality", "75", "-loop", "0", "-threads", "2",
        "-fflags", "+bitexact", "-f", "webp", "file:out.tmp",
    ]  # fmt: skip


def test_gif_defaults():
    clips, encode = build("gif")
    assert len(clips) == 5 and clips[0] == clip("10.3")
    assert encode == [
        *encoder(), "-vf", "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
        "-loop", "0", "-fflags", "+bitexact", "-f", "gif", "file:out.tmp",
    ]  # fmt: skip


def test_options():
    clips, encode = build(
        "webp", samples=2, sample_duration=1.5, fps=10, quality=90, loop=False, start_offset_percent=0
    )
    vf = "fps=10,scale=640:360:flags=lanczos,setsar=1"
    assert clips == [clip(None, "1.5", "15", vf), clip("91.2", "1.5", "15", vf)]
    assert encode[encode.index("-framerate") + 1] == "10"
    assert encode[encode.index("-quality") : encode.index("-threads", encode.index("-quality"))] == [
        "-quality", "90", "-loop", "1",
    ]  # fmt: skip
    assert build("gif", loop=False)[1][-7:-5] == ["-loop", "-1"]


def test_gifs_that_would_not_fit_in_memory_are_refused():
    assert len(build("gif", samples=10, sample_duration=3, fps=15)[0]) == 10
    with pytest.raises(InvalidOptionsError) as e:
        build("gif", samples=10, sample_duration=3, fps=30, max_height=720)
    assert e.value.user_message.startswith("These settings would make the preview too large to render as a GIF.")
    assert len(build("webp", samples=10, sample_duration=3, fps=30, max_height=720)[0]) == 10


@pytest.mark.parametrize(
    "size, params, expected",
    [
        ((1280, 720), {}, ((640, 360), (640, 360))),
        ((320, 180), {}, ((320, 180), (320, 180))),
        ((1280, 720), {"max_width": 320}, ((320, 180), (320, 180))),
        ((360, 640), {}, ((202, 360), (202, 360))),
        ((1280, 720), {"crop": "9:16"}, ((640, 360), (202, 360))),
        ((1280, 720), {"crop": "1:1", "max_height": 200}, ((356, 200), (200, 200))),
        ((360, 640), {"crop": "1:1"}, ((360, 640), (360, 360))),
        ((360, 640), {"crop": "9:16"}, ((202, 360), (202, 360))),
    ],
)
def test_preview_size_never_upscales_and_crops_the_centre(size, params, expected):
    assert preview_size(*size, WebpPreviewConfig(**params)) == expected


def test_crop_follows_the_scale():
    clips, encode = build("webp", crop="9:16")
    assert clips[0][clips[0].index("-vf") + 1] == "fps=15,scale=640:360:flags=lanczos,setsar=1,crop=202:360"
    assert encode[encode.index("-s") + 1] == "202x360"


def test_plan_spreads_clips_inside_the_offsets():
    assert plan_preview(100.0, 5, 2.0, 10, 10) == pytest.approx(
        [(10.0, 2.0), (29.5, 2.0), (49.0, 2.0), (68.5, 2.0), (88.0, 2.0)]
    )


def test_plan_takes_fewer_clips_when_they_would_overlap():
    clips = plan_preview(12.0, 5, 2.0, 10, 10)
    assert len(clips) == 4
    assert all(b[0] >= a[0] + a[1] for a, b in zip(clips, clips[1:], strict=False))
    assert clips[0][0] == pytest.approx(1.2) and clips[-1][0] + 2.0 == pytest.approx(10.8)


def test_plan_falls_back_to_the_whole_video_and_centres_one_clip():
    assert plan_preview(3.0, 5, 2.0, 40, 40) == [(0.5, 2.0)]
    assert plan_preview(10.0, 1, 2.0, 0, 0) == [(4.0, 2.0)]


def test_plan_for_a_clip_shorter_than_one_sample():
    assert plan_preview(1.5, 5, 2.0, 10, 10) == [(0.0, 1.5)]
    with pytest.raises(MediaTooShortError) as e:
        plan_preview(0.4, 5, 2.0, 10, 10)
    assert e.value.user_message == "This video is too short for a preview."


def test_one_clip_needs_no_seek():
    clips, _ = build("gif", duration=1.5, width=640, height=360)
    assert clips == [clip(None, "1.5", "22")]


def test_total_length_is_limited_like_gifs():
    assert VIDEO_PREVIEW.from_params({"samples": 10, "sample_duration": 3}, target="gif").samples == 10
    with pytest.raises(InvalidOptionsError) as e:
        VIDEO_PREVIEW.from_params({"samples": 10, "sample_duration": 3.5}, target="webp")
    assert e.value.user_message == "Previews can be at most 30 seconds long. Use fewer clips or shorter ones."


def test_family():
    assert VIDEO_PREVIEW.targets == ("gif", "webp")
    assert VIDEO_PREVIEW.model_for(target="GIF") is GifPreviewConfig
    with pytest.raises(InvalidOptionsError) as e:
        VIDEO_PREVIEW.from_params({"quality": 80}, target="gif")
    assert e.value.user_message == "Unknown option: quality."
    defaults = WebpPreviewConfig()
    assert (defaults.samples, defaults.sample_duration, defaults.max_height, defaults.fps) == (5, 2.0, 360, 15)
    assert (defaults.start_offset_percent, defaults.end_offset_percent, defaults.crop) == (10.0, 10.0, "none")


ALTERNATIVES = {
    "samples": 3,
    "sample_duration": 1.0,
    "start_offset_percent": 0.0,
    "end_offset_percent": 30.0,
    "max_height": 200,
    "max_width": 300,
    "crop": "9:16",
    "fps": 10,
    "loop": False,
    "quality": 50,
}


@pytest.mark.parametrize("model", VIDEO_PREVIEW.models, ids=lambda m: m.__name__)
def test_every_preview_option_changes_the_command(model):
    def command(config):
        return build_preview_commands("i", "o", config, duration=100.0, width=1280, height=720, threads=1)

    reference = command(model())
    for name in set(model.model_fields) - {"target"}:
        assert command(model(**{name: ALTERNATIVES[name]})) != reference, name
