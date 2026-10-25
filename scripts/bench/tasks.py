# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Benchmark task and suite definitions for sashimi over the ``data/`` test-media corpus.

Each task is a pure specification that is executed in an isolated worker subprocess so that
CPU seconds (``RUSAGE_SELF`` + ``RUSAGE_CHILDREN``) and peak RSS memory are measured per run
without cross-task contamination. Tasks that need a derived input (such as an unfinalized
Matroska stream or an intermediate GIF/PNG) prepare it in ``setup`` before the timers start.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sashimi
from sashimi import image, metadata, sheets, video
from sashimi.config import (
    CONTACT_SHEET,
    IMAGE_CONVERT,
    IMAGE_EDIT,
    IMAGE_FRAMES,
    VIDEO_CONVERT,
    VIDEO_PREVIEW,
    VIDEO_TRANSFORM,
    VIDEO_TRIM,
    Limits,
)

UNLIMITED = Limits.unlimited()

CODEC_PAIR_CLIPS: tuple[str, ...] = (
    "derf/FourPeople_1280x720_60_h264.mp4",
    "derf/FourPeople_1280x720_60_h265.mp4",
    "derf/park_joy_1080p50_h264.mp4",
    "derf/park_joy_1080p50_h265.mp4",
    "derf/ducks_take_off_1080p50_h264.mp4",
    "derf/ducks_take_off_1080p50_h265.mp4",
    "synthetic_noise/noise_1280x720_200MB_30s_h264.mp4",
    "synthetic_noise/noise_1280x720_200MB_30s_h265.mp4",
    "synthetic_noise/noise_1920x1080_300MB_30s_h264.mp4",
    "synthetic_noise/noise_1920x1080_300MB_30s_h265.mp4",
)

METHOD_CLIPS: tuple[str, ...] = (
    "derf/FourPeople_1280x720_60_h264.mp4",
    "derf/park_joy_1080p50_h264.mp4",
    "derf/ducks_take_off_1080p50_h264.mp4",
    "synthetic_noise/noise_1280x720_200MB.mp4",
)

SetupFn = Callable[[Path, Path, int], Path]
RunnerFn = Callable[[Path, Path, int], Any]


@dataclass(frozen=True)
class TaskSpec:
    """One benchmark operation to run against a media file."""

    name: str
    category: str
    ext: str
    description: str
    run: RunnerFn
    setup: SetupFn | None = None


def _probe_task(src: Path, dst: Path, _workers: int) -> str:
    info = sashimi.probe(src)
    dst.write_text(info.model_dump_json(), encoding="utf-8")
    return info.container


def _setup_unfinalized_mkv(src: Path, scratch: Path, _workers: int) -> Path:
    """Streams ``src`` into a piped Matroska file with no header duration or cues."""
    piped = scratch / "unfinalized.mkv"
    with piped.open("wb") as out:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-i",
                str(src),
                "-map_metadata",
                "-1",
                "-c",
                "copy",
                "-f",
                "matroska",
                "pipe:1",
            ],
            stdout=out,
            check=True,
        )
    return piped


def _metadata_read_task(src: Path, dst: Path, _workers: int) -> str:
    info = metadata.read(src, limits=UNLIMITED)
    dst.write_text(info.model_dump_json(), encoding="utf-8")
    return ",".join(info.carriers) or "none"


def _sheet_task(params: dict[str, Any], target: str) -> RunnerFn:
    cfg = CONTACT_SHEET.from_params(params, target=target)

    def _run(src: Path, dst: Path, workers: int) -> None:
        sheets.render(src, dst, cfg, display_name=src.name, max_workers=workers, limits=UNLIMITED)

    return _run


def _preview_task(params: dict[str, Any], target: str) -> RunnerFn:
    cfg = VIDEO_PREVIEW.from_params(params, target=target)

    def _run(src: Path, dst: Path, workers: int) -> None:
        video.preview(src, dst, cfg, max_workers=workers, limits=UNLIMITED)

    return _run


def _video_convert_task(params: dict[str, Any], target: str) -> RunnerFn:
    cfg = VIDEO_CONVERT.from_params(params, target=target)

    def _run(src: Path, dst: Path, workers: int) -> Any:
        return video.convert(src, dst, cfg, max_workers=workers, limits=UNLIMITED)

    return _run


def _video_trim_task(params: dict[str, Any], container: str) -> RunnerFn:
    cfg = VIDEO_TRIM.from_params(params, container=container)

    def _run(src: Path, dst: Path, workers: int) -> None:
        video.trim(src, dst, cfg, max_workers=workers, limits=UNLIMITED)

    return _run


def _video_transform_task(params: dict[str, Any], target: str) -> RunnerFn:
    cfg = VIDEO_TRANSFORM.from_params(params, target=target)

    def _run(src: Path, dst: Path, workers: int) -> None:
        video.transform(src, dst, cfg, max_workers=workers, limits=UNLIMITED)

    return _run


def _setup_intermediate_gif(src: Path, scratch: Path, workers: int) -> Path:
    mid = scratch / "source.gif"
    gif_cfg = VIDEO_CONVERT.from_params({"duration_seconds": 4, "max_width": 480, "fps": 15}, target="gif")
    video.convert(src, mid, gif_cfg, max_workers=workers, limits=UNLIMITED)
    return mid


def _setup_intermediate_png_sheet(src: Path, scratch: Path, workers: int) -> Path:
    mid = scratch / "source_sheet.png"
    sheet_cfg = CONTACT_SHEET.from_params({"width": 1920, "cols": 4, "rows": 4}, target="png")
    sheets.render(src, mid, sheet_cfg, display_name=src.name, max_workers=workers, limits=UNLIMITED)
    return mid


def _gif_edit_task(src: Path, dst: Path, _workers: int) -> None:
    edit_cfg = IMAGE_EDIT.from_params(
        {"rotate": 90, "brightness": 1.1, "contrast": 1.05, "saturation": 1.15},
        target="gif",
    )
    image.edit(src, dst, edit_cfg, limits=UNLIMITED)


def _gif_frames_task(src: Path, dst: Path, _workers: int) -> None:
    frames_cfg = IMAGE_FRAMES.from_params({"keep": "0-29", "speed": 1.5}, target="gif")
    image.select_frames(src, dst, frames_cfg, limits=UNLIMITED)


def _gif_strip_task(src: Path, dst: Path, _workers: int) -> None:
    image.frame_strip(src, dst, limits=UNLIMITED)


def _png_to_lossless_webp_task(src: Path, dst: Path, _workers: int) -> None:
    img_cfg = IMAGE_CONVERT.from_params({"lossless": True, "webp_method": 4}, target="webp")
    image.convert(src, dst, img_cfg, limits=UNLIMITED)


TASKS: dict[str, TaskSpec] = {
    t.name: t
    for t in (
        TaskSpec(
            name="probe",
            category="probe",
            ext="json",
            description="ffprobe container & stream inspection (sashimi.probe)",
            run=_probe_task,
        ),
        TaskSpec(
            name="metadata-read",
            category="probe",
            ext="json",
            description="Container & tag metadata inspection (sashimi.metadata.read)",
            run=_metadata_read_task,
        ),
        TaskSpec(
            name="probe-unfinalized-mkv",
            category="probe",
            ext="json",
            description="Streaming packet-scan duration fallback on unfinalized MKV (sashimi.probe 0.6.1)",
            run=_probe_task,
            setup=_setup_unfinalized_mkv,
        ),
        TaskSpec(
            name="sheet-static-jpeg",
            category="sheets",
            ext="jpg",
            description="Static JPEG contact sheet (2560px, auto 4x6 grid, quality 85)",
            run=_sheet_task({}, "jpeg"),
        ),
        TaskSpec(
            name="sheet-static-webp",
            category="sheets",
            ext="webp",
            description="Static WebP contact sheet (2560px, auto 4x6 grid, quality 85)",
            run=_sheet_task({}, "webp"),
        ),
        TaskSpec(
            name="sheet-static-png",
            category="sheets",
            ext="png",
            description="Static PNG contact sheet (2560px, auto 4x6 grid)",
            run=_sheet_task({}, "png"),
        ),
        TaskSpec(
            name="sheet-animated-webp",
            category="sheets",
            ext="webp",
            description="Animated WebP contact sheet (1280px, 3x3 grid, 2s @ 12 fps, quality 75)",
            run=_sheet_task({"animated": True}, "webp"),
        ),
        TaskSpec(
            name="sheet-animated-gif",
            category="sheets",
            ext="gif",
            description="Animated GIF contact sheet (1280px, 3x3 grid, 2s @ 12 fps, two-pass palette)",
            run=_sheet_task({"animated": True}, "gif"),
        ),
        TaskSpec(
            name="preview-gif",
            category="preview",
            ext="gif",
            description="GIF video preview (5 x 2s clips @ 15 fps, 360p, two-pass palette)",
            run=_preview_task({}, "gif"),
        ),
        TaskSpec(
            name="preview-webp",
            category="preview",
            ext="webp",
            description="Animated WebP video preview (5 x 2s clips @ 15 fps, 360p, quality 75)",
            run=_preview_task({}, "webp"),
        ),
        TaskSpec(
            name="preview-webp-9x16",
            category="preview",
            ext="webp",
            description="Portrait 9:16 cropped animated WebP preview (5 x 2s @ 15 fps, 360p)",
            run=_preview_task({"crop": "9:16"}, "webp"),
        ),
        TaskSpec(
            name="gif-diff-bayer",
            category="gif-methods",
            ext="gif",
            description="GIF 5s 480w 12fps: palette_mode=diff, dither=bayer (sashimi default)",
            run=_video_convert_task(
                {"duration_seconds": 5, "max_width": 480, "fps": 12, "palette_mode": "diff", "dither": "bayer"},
                "gif",
            ),
        ),
        TaskSpec(
            name="gif-full-bayer",
            category="gif-methods",
            ext="gif",
            description="GIF 5s 480w 12fps: palette_mode=full, dither=bayer (two-pass full palette)",
            run=_video_convert_task(
                {"duration_seconds": 5, "max_width": 480, "fps": 12, "palette_mode": "full", "dither": "bayer"},
                "gif",
            ),
        ),
        TaskSpec(
            name="gif-single-bayer",
            category="gif-methods",
            ext="gif",
            description="GIF 5s 480w 12fps: palette_mode=single, dither=bayer (one-pass per-frame palette)",
            run=_video_convert_task(
                {"duration_seconds": 5, "max_width": 480, "fps": 12, "palette_mode": "single", "dither": "bayer"},
                "gif",
            ),
        ),
        TaskSpec(
            name="gif-diff-sierra2_4a",
            category="gif-methods",
            ext="gif",
            description="GIF 5s 480w 12fps: palette_mode=diff, dither=sierra2_4a",
            run=_video_convert_task(
                {"duration_seconds": 5, "max_width": 480, "fps": 12, "palette_mode": "diff", "dither": "sierra2_4a"},
                "gif",
            ),
        ),
        TaskSpec(
            name="gif-diff-floyd",
            category="gif-methods",
            ext="gif",
            description="GIF 5s 480w 12fps: palette_mode=diff, dither=floyd_steinberg",
            run=_video_convert_task(
                {
                    "duration_seconds": 5,
                    "max_width": 480,
                    "fps": 12,
                    "palette_mode": "diff",
                    "dither": "floyd_steinberg",
                },
                "gif",
            ),
        ),
        TaskSpec(
            name="gif-diff-none",
            category="gif-methods",
            ext="gif",
            description="GIF 5s 480w 12fps: palette_mode=diff, dither=none",
            run=_video_convert_task(
                {"duration_seconds": 5, "max_width": 480, "fps": 12, "palette_mode": "diff", "dither": "none"},
                "gif",
            ),
        ),
        TaskSpec(
            name="webp-480p-5s-12fps",
            category="animations",
            ext="webp",
            description="Animated WebP 5s 480w 12fps quality 75 (sashimi default)",
            run=_video_convert_task({"duration_seconds": 5, "max_width": 480, "fps": 12, "quality": 75}, "webp"),
        ),
        TaskSpec(
            name="gif-720p-6s-30fps",
            category="animations",
            ext="gif",
            description="GIF 6s 1280w 30fps (180 frames at 720p, two-pass diff/bayer)",
            run=_video_convert_task({"duration_seconds": 6, "max_width": 1280, "fps": 30}, "gif"),
        ),
        TaskSpec(
            name="webp-720p-6s-30fps",
            category="animations",
            ext="webp",
            description="Animated WebP 6s 1280w 30fps quality 75 (180 frames at 720p)",
            run=_video_convert_task({"duration_seconds": 6, "max_width": 1280, "fps": 30, "quality": 75}, "webp"),
        ),
        TaskSpec(
            name="gif-720p-20s-30fps",
            category="animations",
            ext="gif",
            description="GIF 20s 1280w 30fps (600 frames: 552.96M pixel budget corner)",
            run=_video_convert_task({"duration_seconds": 20, "max_width": 1280, "fps": 30}, "gif"),
        ),
        TaskSpec(
            name="gif-1080p-5s-30fps",
            category="animations",
            ext="gif",
            description="GIF 5s 1920w 30fps (150 frames at 1080p)",
            run=_video_convert_task({"duration_seconds": 5, "max_width": 1920, "fps": 30}, "gif"),
        ),
        TaskSpec(
            name="webp-1080p-5s-30fps",
            category="animations",
            ext="webp",
            description="Animated WebP 5s 1920w 30fps quality 75 (150 frames at 1080p)",
            run=_video_convert_task({"duration_seconds": 5, "max_width": 1920, "fps": 30, "quality": 75}, "webp"),
        ),
        TaskSpec(
            name="gif-1080p-8.8s-30fps",
            category="animations",
            ext="gif",
            description="GIF 8.8s 1920w 30fps (264 frames: 1080p 552.96M pixel budget corner)",
            run=_video_convert_task({"duration_seconds": 8.8, "max_width": 1920, "fps": 30}, "gif"),
        ),
        TaskSpec(
            name="mp4-auto",
            category="transcode",
            ext="mp4",
            description="Video to MP4 default (stream-copy for H.264, H.264 re-encode for HEVC)",
            run=_video_convert_task({}, "mp4"),
        ),
        TaskSpec(
            name="mp4-720p-ultrafast",
            category="transcode",
            ext="mp4",
            description="Video to MP4 5s 1280w CRF 23 preset=ultrafast",
            run=_video_convert_task(
                {"duration_seconds": 5, "max_width": 1280, "crf": 23, "preset": "ultrafast"},
                "mp4",
            ),
        ),
        TaskSpec(
            name="mp4-720p-veryfast",
            category="transcode",
            ext="mp4",
            description="Video to MP4 5s 1280w CRF 23 preset=veryfast (sashimi default preset)",
            run=_video_convert_task(
                {"duration_seconds": 5, "max_width": 1280, "crf": 23, "preset": "veryfast"},
                "mp4",
            ),
        ),
        TaskSpec(
            name="mp4-720p-medium",
            category="transcode",
            ext="mp4",
            description="Video to MP4 5s 1280w CRF 23 preset=medium",
            run=_video_convert_task(
                {"duration_seconds": 5, "max_width": 1280, "crf": 23, "preset": "medium"},
                "mp4",
            ),
        ),
        TaskSpec(
            name="webm-720p-speed8",
            category="transcode",
            ext="webm",
            description="Video to WebM (VP9) 5s 1280w CRF 34 speed=8 (sashimi default)",
            run=_video_convert_task(
                {"duration_seconds": 5, "max_width": 1280, "crf": 34, "speed": 8},
                "webm",
            ),
        ),
        TaskSpec(
            name="webm-720p-speed4",
            category="transcode",
            ext="webm",
            description="Video to WebM (VP9) 5s 1280w CRF 34 speed=4",
            run=_video_convert_task(
                {"duration_seconds": 5, "max_width": 1280, "crf": 34, "speed": 4},
                "webm",
            ),
        ),
        TaskSpec(
            name="trim-copy-5s",
            category="transcode",
            ext="mp4",
            description="Lossless keyframe stream-copy trim (start=2s, duration=4s)",
            run=_video_trim_task({"start_seconds": 2.0, "duration_seconds": 4.0, "accurate": False}, "mp4"),
        ),
        TaskSpec(
            name="trim-accurate-5s",
            category="transcode",
            ext="mp4",
            description="Frame-accurate re-encode trim (start=2s, duration=4s, H.264 veryfast)",
            run=_video_trim_task({"start_seconds": 2.0, "duration_seconds": 4.0, "accurate": True}, "mp4"),
        ),
        TaskSpec(
            name="rotate-90-mp4",
            category="transcode",
            ext="mp4",
            description="Rotate video 90 degrees clockwise to MP4 (H.264 veryfast)",
            run=_video_transform_task({"rotate": 90}, "mp4"),
        ),
        TaskSpec(
            name="image-gif-edit",
            category="image",
            ext="gif",
            description="sashimi.image.edit on 60-frame 480p GIF (rotate 90 + brightness/contrast/saturation)",
            run=_gif_edit_task,
            setup=_setup_intermediate_gif,
        ),
        TaskSpec(
            name="image-gif-frames",
            category="image",
            ext="gif",
            description="sashimi.image.select_frames on 60-frame 480p GIF (keep 30 frames, speed 1.5x)",
            run=_gif_frames_task,
            setup=_setup_intermediate_gif,
        ),
        TaskSpec(
            name="image-gif-strip",
            category="image",
            ext="webp",
            description="sashimi.image.frame_strip on 60-frame 480p GIF (filmstrip WebP)",
            run=_gif_strip_task,
            setup=_setup_intermediate_gif,
        ),
        TaskSpec(
            name="image-sheet-lossless-webp",
            category="image",
            ext="webp",
            description="sashimi.image.convert on 1920px PNG contact sheet -> lossless WebP (method 4)",
            run=_png_to_lossless_webp_task,
            setup=_setup_intermediate_png_sheet,
        ),
    )
}


@dataclass(frozen=True)
class BenchCase:
    """One (suite, task, clip, workers) benchmark execution item."""

    suite: str
    task_name: str
    rel_path: str
    workers: int


def build_suite_cases(suite: str, default_workers: int = 2) -> list[BenchCase]:
    """Expands a named benchmark suite into ordered :class:`BenchCase` items."""
    from scripts.bench.manifest import FIXTURES

    all_paths = [f.rel_path for f in FIXTURES]

    if suite == "probe":
        cases = [
            BenchCase(suite="probe", task_name=task, rel_path=path, workers=default_workers)
            for path in all_paths
            for task in ("probe", "metadata-read")
        ]
        for path in (
            "derf/FourPeople_1280x720_60_h264.mp4",
            "derf/ducks_take_off_1080p50_h264.mp4",
            "synthetic_noise/noise_1280x720_200MB_30s_h264.mp4",
            "synthetic_noise/noise_1920x1080_300MB.mp4",
        ):
            cases.append(
                BenchCase(suite="probe", task_name="probe-unfinalized-mkv", rel_path=path, workers=default_workers)
            )
        return cases

    if suite == "sheets":
        cases = []
        base_clips = (
            "derf/FourPeople_1280x720_60_h264.mp4",
            "derf/red_kayak_1080p_h264.mp4",
            "derf/park_joy_1080p50_h264.mp4",
            "derf/ducks_take_off_1080p50_h264.mp4",
            "synthetic_noise/noise_1280x720_200MB_30s_h264.mp4",
            "synthetic_noise/noise_1920x1080_300MB.mp4",
        )
        for clip in base_clips:
            cases.append(
                BenchCase(suite="sheets", task_name="sheet-static-jpeg", rel_path=clip, workers=default_workers)
            )
            cases.append(
                BenchCase(suite="sheets", task_name="sheet-animated-webp", rel_path=clip, workers=default_workers)
            )
        for clip in (
            "derf/FourPeople_1280x720_60_h264.mp4",
            "derf/ducks_take_off_1080p50_h264.mp4",
            "synthetic_noise/noise_1280x720_200MB_30s_h264.mp4",
        ):
            for task in ("sheet-static-webp", "sheet-static-png", "sheet-animated-gif"):
                cases.append(BenchCase(suite="sheets", task_name=task, rel_path=clip, workers=default_workers))
        return cases

    if suite == "preview":
        clips = (
            "derf/FourPeople_1280x720_60_h264.mp4",
            "derf/rush_hour_1080p25_h264.mp4",
            "derf/ducks_take_off_1080p50_h264.mp4",
            "synthetic_noise/noise_1280x720_200MB_30s_h264.mp4",
        )
        tasks = ("preview-gif", "preview-webp", "preview-webp-9x16")
        return [
            BenchCase(suite="preview", task_name=t, rel_path=c, workers=default_workers) for c in clips for t in tasks
        ]

    if suite == "gif-methods":
        tasks = (
            "gif-diff-bayer",
            "gif-full-bayer",
            "gif-single-bayer",
            "gif-diff-sierra2_4a",
            "gif-diff-floyd",
            "gif-diff-none",
        )
        return [
            BenchCase(suite="gif-methods", task_name=t, rel_path=c, workers=default_workers)
            for c in METHOD_CLIPS
            for t in tasks
        ]

    if suite == "animations":
        cases = []
        base_clips = (
            "derf/FourPeople_1280x720_60_h264.mp4",
            "derf/park_joy_1080p50_h264.mp4",
            "derf/ducks_take_off_1080p50_h264.mp4",
            "synthetic_noise/noise_1280x720_200MB_30s_h264.mp4",
        )
        for clip in base_clips:
            for task in (
                "gif-diff-bayer",
                "webp-480p-5s-12fps",
                "gif-720p-6s-30fps",
                "webp-720p-6s-30fps",
                "gif-1080p-5s-30fps",
                "webp-1080p-5s-30fps",
            ):
                cases.append(BenchCase(suite="animations", task_name=task, rel_path=clip, workers=default_workers))
        for clip in (
            "derf/rush_hour_1080p25_h264.mp4",
            "synthetic_noise/noise_1280x720_200MB_30s_h264.mp4",
        ):
            cases.append(
                BenchCase(suite="animations", task_name="gif-720p-20s-30fps", rel_path=clip, workers=default_workers)
            )
        for clip in (
            "derf/park_joy_1080p50_h264.mp4",
            "derf/ducks_take_off_1080p50_h264.mp4",
            "synthetic_noise/noise_1920x1080_300MB_30s_h264.mp4",
        ):
            cases.append(
                BenchCase(suite="animations", task_name="gif-1080p-8.8s-30fps", rel_path=clip, workers=default_workers)
            )
        return cases

    if suite == "transcode":
        clips = (
            "derf/FourPeople_1280x720_60_h264.mp4",
            "derf/park_joy_1080p50_h264.mp4",
            "derf/ducks_take_off_1080p50_h264.mp4",
            "synthetic_noise/noise_1280x720_200MB.mp4",
        )
        tasks = (
            "mp4-auto",
            "mp4-720p-ultrafast",
            "mp4-720p-veryfast",
            "mp4-720p-medium",
            "webm-720p-speed8",
            "webm-720p-speed4",
            "trim-copy-5s",
            "trim-accurate-5s",
        )
        return [
            BenchCase(suite="transcode", task_name=t, rel_path=c, workers=default_workers) for c in clips for t in tasks
        ]

    if suite == "codecs":
        tasks = ("sheet-static-jpeg", "sheet-animated-webp", "gif-diff-bayer", "webp-480p-5s-12fps", "mp4-auto")
        return [
            BenchCase(suite="codecs", task_name=t, rel_path=c, workers=default_workers)
            for c in CODEC_PAIR_CLIPS
            for t in tasks
        ]

    if suite == "workers":
        clips = (
            "derf/ducks_take_off_1080p50_h264.mp4",
            "synthetic_noise/noise_1280x720_200MB_30s_h264.mp4",
        )
        tasks = ("sheet-static-jpeg", "sheet-animated-webp", "gif-720p-6s-30fps", "mp4-720p-veryfast")
        return [
            BenchCase(suite="workers", task_name=t, rel_path=c, workers=w)
            for c in clips
            for t in tasks
            for w in (1, 2, 4, 6)
        ]

    if suite == "image":
        clips = (
            "derf/FourPeople_1280x720_60_h264.mp4",
            "derf/ducks_take_off_1080p50_h264.mp4",
            "synthetic_noise/noise_1280x720_200MB.mp4",
        )
        tasks = ("image-gif-edit", "image-gif-frames", "image-gif-strip", "image-sheet-lossless-webp")
        return [
            BenchCase(suite="image", task_name=t, rel_path=c, workers=default_workers) for c in clips for t in tasks
        ]

    if suite == "smoke":
        return [
            BenchCase(
                suite="smoke",
                task_name=t,
                rel_path="derf/FourPeople_1280x720_60_h264.mp4",
                workers=default_workers,
            )
            for t in ("probe", "sheet-static-jpeg", "gif-diff-bayer", "mp4-auto", "trim-copy-5s")
        ]

    raise ValueError(f"Unknown suite: {suite!r}")


SUITES: tuple[str, ...] = (
    "smoke",
    "probe",
    "sheets",
    "preview",
    "gif-methods",
    "animations",
    "transcode",
    "codecs",
    "workers",
    "image",
)

DEFAULT_SUITES: tuple[str, ...] = (
    "probe",
    "sheets",
    "preview",
    "gif-methods",
    "animations",
    "transcode",
    "codecs",
    "workers",
    "image",
)
