# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
What a file holds can't overflow a command on any platform: its tag names and values take a bounded
part of one, a removal still removes, and a command too long is never reported as a bad FFmpeg.
"""

import errno
import itertools
import os
import subprocess

import pytest
from typer.testing import CliRunner

import sashimi
from sashimi import audio, metadata, video
from sashimi.config import AUDIO_CONVERT, CopyTrimConfig, Mp4TransformConfig, VIDEO_CONVERT
from sashimi.cli.main import app
from sashimi.core import _ffmpeg, _mediameta
from sashimi.core._mediameta import metadata_args, quicktime_tag_args
from sashimi.core.metadata import build_strip_command
from sashimi.core.probe import MediaInfo, StreamInfo
from sashimi.errors import BinaryNotFoundError, ProcessingError, UnsupportedMediaError
from tests.conftest import ffmetadata_file, needs_ffmpeg

WINDOWS_COMMAND_LIMIT = 32_767
BOUND = 26_000
"""The most a file's names and values may take of a command, attachments' included: under what Windows allows for all of it."""
SHAPES = {
    "20000 location keys": lambda: {f"location_{number}_{'x' * 140}": "v" for number in range(20_000)},
    "400 location keys": lambda: {f"location_{number}_{'x' * 90}": "v" for number in range(400)},
    "one long location key": lambda: {f"location_{'x' * 200_000}": "v"},
    "a location key holding an equals sign": lambda: {"GPS=1": "48.85,2.35"},
}


@pytest.fixture
def windows_budget(monkeypatch):
    monkeypatch.setattr(_mediameta, "MAX_ATTACHMENT_ARGS", _mediameta.WINDOWS_ATTACHMENT_ARGS)


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def tagged(tmp_path, tags: dict[str, str], *sources: str, suffix: str = "mkv", codec: str = "libx264"):
    listing = ffmetadata_file(tmp_path / "tags.txt", {"title": "Holiday", **tags})
    path = tmp_path / f"in.{suffix}"
    ffmpeg(
        *sources, "-f", "ffmetadata", "-i", str(listing), "-map", "0", "-map_metadata", "1", "-c:0", codec, str(path)
    )
    return path


def video_with(tmp_path, tags):
    return tagged(tmp_path, tags, "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=10")


ROUTES = {
    "strip": lambda clip, out: metadata.strip(clip, out / "o.mkv", "remove_location"),
    "convert": lambda clip, out: video.convert(
        clip, out / "o.mp4", VIDEO_CONVERT.from_params({"metadata": "remove_location", "crf": 40}, target="mp4")
    ),
    "trim": lambda clip, out: video.trim(
        clip, out / "o.mkv", CopyTrimConfig(container="mkv", metadata="remove_location")
    ),
    "rotate": lambda clip, out: video.transform(
        clip, out / "o.mp4", Mp4TransformConfig(rotate=90, metadata="remove_location")
    ),
}
OUTPUT = {"strip": "o.mkv", "convert": "o.mp4", "trim": "o.mkv", "rotate": "o.mp4"}


@needs_ffmpeg
@pytest.mark.parametrize(
    ("shape", "route"),
    [
        (shape, route)
        for shape in sorted(SHAPES)
        for route in sorted(ROUTES)
        if shape != "20000 location keys" or route in ("strip", "convert")
    ],
)
def test_location_tags_go_however_many_and_however_long(tmp_path, shape, route):
    clip = video_with(tmp_path, SHAPES[shape]())
    assert metadata.read(clip).has_location
    told = ROUTES[route](clip, tmp_path).fidelity
    after = metadata.read(tmp_path / OUTPUT[route])
    assert not after.has_location
    assert not any("location" in key.lower() or "gps" in key.lower() for key in after.tags)
    assert "location" in told.removed
    if after.title is None:
        assert "tags" in told.lost, "the title went and the result doesn't say so"


@needs_ffmpeg
def test_a_file_whose_tags_fit_keeps_the_rest(tmp_path):
    clip = video_with(tmp_path, {f"location_{number}": "v" for number in range(10)})
    told = metadata.strip(clip, tmp_path / "o.mkv", "remove_location").fidelity
    after = metadata.read(tmp_path / "o.mkv")
    assert after.title == "Holiday" and not after.has_location
    assert "tags" not in told.lost


@needs_ffmpeg
def test_sound_goes_the_same_way(tmp_path):
    clip = tagged(tmp_path, SHAPES["20000 location keys"](), "-f", "lavfi", "-i", "sine=d=1", suffix="m4a", codec="aac")
    out = tmp_path / "o.mp3"
    audio.convert(clip, out, AUDIO_CONVERT.from_params({"metadata": "remove_location"}, target="mp3"))
    assert not metadata.read(out).has_location


@needs_ffmpeg
def test_stream_tags_that_dont_fit_go_and_keep_the_language(tmp_path):
    listing = ffmetadata_file(
        tmp_path / "tags.txt",
        {},
        {"language": "fra", **{f"location_{number}_{'x' * 100}": "v" for number in range(400)}},
    )
    clip = tmp_path / "in.mkv"
    ffmpeg(
        *("-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=10", "-f", "ffmetadata", "-i", str(listing)),
        *("-map", "0", "-map_metadata:s:v:0", "1:s:0", "-c:v", "libx264", str(clip)),
    )
    assert metadata.read(clip).has_location
    metadata.strip(clip, tmp_path / "o.mkv", "remove_location")
    assert not metadata.read(tmp_path / "o.mkv").has_location
    assert sashimi.probe(tmp_path / "o.mkv").streams[0].language == "fra"


def info_with(tags: dict[str, str] | None = None, streams: list[StreamInfo] | None = None) -> MediaInfo:
    return MediaInfo.model_construct(tags=tags or {}, streams=streams or [])


def stream(**fields) -> StreamInfo:
    return StreamInfo.model_construct(
        **{"type": "audio", "codec": "aac", "tags": {}, "language": None, "attached_pic": False, **fields}
    )


CASES = {
    "many long names": info_with({f"location_{number}_{'x' * 140}": "v" for number in range(20_000)}),
    "one long name": info_with({"location_" + "x" * 200_000: "v"}),
    "many streams with long names": info_with(
        streams=[stream(tags={f"location_{'x' * 100}_{number}": "v"}) for number in range(1000)]
    ),
    "a long language": info_with(streams=[stream(language="x" * 200_000, tags={"language": "x" * 200_000})]),
    "many languages": info_with(streams=[stream(language="eng", tags={"language": "eng"})] * 1000),
    "long attachment names": info_with(
        streams=[stream(type="attachment", tags={"filename": "x" * 200_000, "mimetype": "y" * 200_000})]
    ),
    "a long creation time": info_with({"creation_time": "2020-01-01T00:00:00." + "0" * 200_000}),
    "file-type tags in every case": info_with(
        {
            "".join(letters): "v"
            for letters in itertools.product(*zip("major_brand".lower(), "MAJOR_BRAND", strict=True))
        }
    ),
}


@pytest.mark.parametrize("mode", ["remove_location", "remove_all", "keep"])
@pytest.mark.parametrize("muxer", ["matroska", "mp4"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_what_a_file_holds_takes_a_bounded_part_of_a_command(case, muxer, mode, windows_budget):
    info = CASES[case]
    outputs = list(range(len(info.streams)))
    args = [*metadata_args(mode, info, outputs), *quicktime_tag_args(mode, info, muxer)]
    assert sum(len(arg) + 3 for arg in args) <= BOUND


@needs_ffmpeg
def test_a_film_with_many_fonts_keeps_their_names_through_a_removal(tmp_path):
    clip = fonts_film(tmp_path, 100)
    metadata.strip(clip, tmp_path / "o.mkv", "remove_all")
    names = [s.tags.get("filename") for s in sashimi.probe(tmp_path / "o.mkv").streams if s.type == "attachment"]
    assert names == [f"Font-{number:03}-Bold-Italic.ttf" for number in range(100)]


def many_attachments(count: int, *more: StreamInfo) -> MediaInfo:
    named = stream(type="attachment", tags={"filename": "Font-Bold-Italic.ttf", "mimetype": "font/ttf"})
    return info_with(streams=[named] * count + list(more))


def test_the_budget_for_attachments_fits_each_system():
    assert _mediameta.WINDOWS_ATTACHMENT_ARGS < WINDOWS_COMMAND_LIMIT * 0.8
    assert _mediameta.POSIX_ATTACHMENT_ARGS <= 1 << 19
    if os.name != "nt":
        assert _mediameta.MAX_ATTACHMENT_ARGS == _mediameta.POSIX_ATTACHMENT_ARGS


def test_a_posix_system_takes_every_attachment_a_file_can_hold(monkeypatch):
    monkeypatch.setattr(_mediameta, "MAX_ATTACHMENT_ARGS", _mediameta.POSIX_ATTACHMENT_ARGS)
    info = many_attachments(1000)
    args = metadata_args("remove_all", info, list(range(1000)))
    assert sum(arg.startswith("filename=") for arg in args) == 1000
    assert max(map(len, args)) < 128 << 10 and sum(map(len, args)) < 1 << 19


def test_windows_refuses_what_it_cant_hold(windows_budget):
    info = many_attachments(1000)
    with pytest.raises(UnsupportedMediaError) as refused:
        metadata_args("remove_all", info, list(range(1000)))
    assert "too many attachments for this system" in refused.value.user_message
    assert (
        sum(arg.startswith("filename=") for arg in metadata_args("remove_all", many_attachments(150), range(150)))
        == 150
    )


def test_languages_give_way_to_the_attachments_that_need_their_names(monkeypatch):
    monkeypatch.setattr(_mediameta, "MAX_ATTACHMENT_ARGS", 8000)
    spoken = stream(language="fra", tags={"language": "fra"})
    info = info_with(
        streams=[spoken] * 300 + [stream(type="attachment", tags={"filename": "a.ttf", "mimetype": "font/ttf"})]
    )
    args = metadata_args("remove_all", info, list(range(301)))
    assert "filename=a.ttf" in args and "language=fra" not in args


def fonts_film(tmp_path, count: int):
    font = tmp_path / "f.ttf"
    font.write_bytes(b"\0\1\0\0" + bytes(64))
    attached: list[str] = []
    for number in range(count):
        attached += ["-attach", str(font), f"-metadata:s:{number + 1}", f"filename=Font-{number:03}-Bold-Italic.ttf"]
        attached += [f"-metadata:s:{number + 1}", "mimetype=application/x-truetype-font"]
    clip = tmp_path / "in.mkv"
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=10", "-c:v", "libx264", *attached, str(clip))
    return clip


@needs_ffmpeg
@pytest.mark.skipif(os.name == "nt", reason="Windows' budget is below this, and its command can't make the file")
@pytest.mark.parametrize("mode", ["remove_all", "remove_location"])
def test_400_fonts_all_stay_where_a_command_can_hold_them(tmp_path, mode):
    clip = fonts_film(tmp_path, 400)
    told = metadata.strip(clip, tmp_path / "o.mkv", mode).fidelity
    names = [s.tags.get("filename") for s in sashimi.probe(tmp_path / "o.mkv").streams if s.type == "attachment"]
    assert names == [f"Font-{number:03}-Bold-Italic.ttf" for number in range(400)]
    assert told.lost == []


@needs_ffmpeg
def test_past_the_budget_a_film_is_refused_in_plain_words_and_nothing_is_written(tmp_path, monkeypatch):
    clip = fonts_film(tmp_path, 100)
    monkeypatch.setattr(_mediameta, "MAX_ATTACHMENT_ARGS", 1024)
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(UnsupportedMediaError) as refused:
        metadata.strip(clip, out / "o.mkv", "remove_all")
    assert refused.value.user_message == "This file has too many attachments for this system."
    assert list(out.iterdir()) == []
    result = CliRunner().invoke(app, ["strip", str(clip), "--output-dir", str(out)])
    said = " ".join(result.output.split())
    assert result.exit_code == 1 and "too many attachments for this system" in said
    assert "filename tag" not in said and "mimetype" not in said and list(out.iterdir()) == []


def test_a_long_attachment_name_is_replaced_not_lost():
    args = metadata_args("remove_all", CASES["long attachment names"], [0])
    assert "filename=attachment0" in args and "mimetype=application/octet-stream" in args


def test_real_tags_are_still_named_one_by_one():
    info = info_with({"com.apple.quicktime.location.ISO6709": "+48", "title": "Holiday"})
    args = metadata_args("remove_location", info, [])
    assert "-map_metadata:g" not in args and "com.apple.quicktime.location.ISO6709=" in args


@needs_ffmpeg
def test_a_strip_command_fits_on_windows(tmp_path):
    clip = video_with(tmp_path, SHAPES["20000 location keys"]())
    info = sashimi.probe(clip)
    command = build_strip_command(
        clip, tmp_path / "o.mkv", "remove_location", source=info, muxer="matroska", ffmpeg="ffmpeg"
    )
    assert sum(len(arg) + 3 for arg in command) < WINDOWS_COMMAND_LIMIT / 2


@needs_ffmpeg
def test_a_command_too_long_for_the_system_is_not_a_bad_ffmpeg():
    command = [_ffmpeg.ffmpeg_binary(), "-metadata", "x" * (8 << 20)]
    with pytest.raises(ProcessingError) as refused:
        _ffmpeg.run_ffmpeg(command)
    assert not isinstance(refused.value, BinaryNotFoundError)


class _TooLong(OSError):
    winerror = 206


@pytest.mark.parametrize(
    "error",
    [OSError(errno.E2BIG, "Argument list too long"), _TooLong(errno.ENOENT, "too long")],
    ids=["POSIX", "Windows"],
)
@pytest.mark.parametrize(
    "run", [_ffmpeg.run_ffmpeg, lambda cmd: list(_ffmpeg.stream_ffmpeg(cmd, 1024))], ids=["run", "stream"]
)
def test_either_systems_error_for_a_long_command_is_said_as_such(monkeypatch, error, run):
    def refusing(*args, **kwargs):
        raise error

    monkeypatch.setattr(_ffmpeg, "_check", lambda cmd: None)
    monkeypatch.setattr(subprocess, "Popen", refusing)
    with pytest.raises(ProcessingError, match="too long") as refused:
        run(["ffmpeg", "-version"])
    assert not isinstance(refused.value, BinaryNotFoundError)
