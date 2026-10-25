# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Manifest, checksum verification, and rebuild helpers for the local benchmark media in ``data/``.

The ``data/`` directory is ignored by git because the Xiph ``derf`` clips have mixed research and
testing licences and are ~2.4 GB in total. This module records the exact provenance, checksums,
encoding recipes, entropy metrics, and coverage roles of all 20 files, verifies a local ``data/``
checkout, and can rebuild missing synthetic-noise or Xiph ``derf`` files on demand.
"""

from __future__ import annotations

import hashlib
import os
import struct
import subprocess
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import sashimi

DERF_BASE_URL = "https://media.xiph.org/video/derf/y4m"
DERF_LICENCE = "Xiph derf collection (mixed research/testing terms; never commit or redistribute)"
NOISE_LICENCE = "Synthetic uniform YUV420p noise generated locally (public domain / Apache-2.0)"

Category = Literal["derf", "synthetic_noise"]
CodecName = Literal["h264", "hevc"]


@dataclass(frozen=True)
class FixtureSpec:
    """One benchmark media file under ``data/``."""

    rel_path: str
    category: Category
    clip: str
    codec: CodecName
    width: int
    height: int
    fps: float
    frames: int
    duration_s: float
    size_bytes: int
    sha256: str
    bytes_per_pixel: float
    psnr_db: float | None
    source: str
    encode_recipe: str
    licence: str
    covers: str

    @property
    def name(self) -> str:
        """Short identifier without directory or ``.mp4`` suffix."""
        return Path(self.rel_path).stem


FIXTURES: tuple[FixtureSpec, ...] = (
    FixtureSpec(
        rel_path="derf/FourPeople_1280x720_60_h264.mp4",
        category="derf",
        clip="FourPeople",
        codec="h264",
        width=1280,
        height=720,
        fps=60.0,
        frames=601,
        duration_s=10.016667,
        size_bytes=7_928_601,
        sha256="f5a8178fa5bef21e7a5fd9a0187c7eb7777b1ecde63835a16557270daf0d2cf8",
        bytes_per_pixel=1.08,
        psnr_db=None,
        source=f"{DERF_BASE_URL}/FourPeople_1280x720_60.y4m",
        encode_recipe="libx264 -preset medium -crf 16 -pix_fmt yuv420p -movflags +faststart",
        licence=DERF_LICENCE,
        covers="720p60 static video call (easy real footage, 60->30 fps resampling, H.264 stream-copy baseline)",
    ),
    FixtureSpec(
        rel_path="derf/FourPeople_1280x720_60_h265.mp4",
        category="derf",
        clip="FourPeople",
        codec="hevc",
        width=1280,
        height=720,
        fps=60.0,
        frames=601,
        duration_s=10.016667,
        size_bytes=3_883_543,
        sha256="d9fdaac49f055bd46d9860ac075f60548dfbbdaf8f32bfd7cd99e0d9bc049d5b",
        bytes_per_pixel=1.08,
        psnr_db=None,
        source=f"{DERF_BASE_URL}/FourPeople_1280x720_60.y4m",
        encode_recipe="libx265 -preset medium -crf 18 -tag:v hvc1 -pix_fmt yuv420p -movflags +faststart",
        licence=DERF_LICENCE,
        covers="720p60 static video call in HEVC (exercises H.265 decode and MP4 encode route)",
    ),
    FixtureSpec(
        rel_path="derf/crowd_run_1080p50_h264.mp4",
        category="derf",
        clip="crowd_run",
        codec="h264",
        width=1920,
        height=1080,
        fps=50.0,
        frames=500,
        duration_s=10.0,
        size_bytes=96_030_871,
        sha256="4188fcd3f7083b2fb114a77274060859de94a759a7f3bc086bc3116e6f280f77",
        bytes_per_pixel=1.65,
        psnr_db=None,
        source=f"{DERF_BASE_URL}/crowd_run_1080p50.y4m",
        encode_recipe="libx264 -preset medium -crf 16 -pix_fmt yuv420p -movflags +faststart",
        licence=DERF_LICENCE,
        covers="1080p50 dense crowd with fine moving detail across the whole frame",
    ),
    FixtureSpec(
        rel_path="derf/crowd_run_1080p50_h265.mp4",
        category="derf",
        clip="crowd_run",
        codec="hevc",
        width=1920,
        height=1080,
        fps=50.0,
        frames=500,
        duration_s=10.0,
        size_bytes=76_682_654,
        sha256="1cef690f20d301b70159e0f2e258f829f4f4743bafbd8e477d8f3487e303890f",
        bytes_per_pixel=1.65,
        psnr_db=None,
        source=f"{DERF_BASE_URL}/crowd_run_1080p50.y4m",
        encode_recipe="libx265 -preset medium -crf 18 -tag:v hvc1 -pix_fmt yuv420p -movflags +faststart",
        licence=DERF_LICENCE,
        covers="1080p50 dense crowd in HEVC",
    ),
    FixtureSpec(
        rel_path="derf/ducks_take_off_1080p50_h264.mp4",
        category="derf",
        clip="ducks_take_off",
        codec="h264",
        width=1920,
        height=1080,
        fps=50.0,
        frames=500,
        duration_s=10.0,
        size_bytes=154_781_882,
        sha256="49aa19bcde469bc06172cb734052299ed9fe706895b63702acacf50eb5a49aed",
        bytes_per_pixel=1.82,
        psnr_db=None,
        source=f"{DERF_BASE_URL}/ducks_take_off_1080p50.y4m",
        encode_recipe="libx264 -preset medium -crf 16 -pix_fmt yuv420p -movflags +faststart",
        licence=DERF_LICENCE,
        covers="1080p50 splashing water and ripples (hardest bitrate real footage, 124 Mbps H.264)",
    ),
    FixtureSpec(
        rel_path="derf/ducks_take_off_1080p50_h265.mp4",
        category="derf",
        clip="ducks_take_off",
        codec="hevc",
        width=1920,
        height=1080,
        fps=50.0,
        frames=500,
        duration_s=10.0,
        size_bytes=118_126_065,
        sha256="141f226052ced3baed96f413ab80f4d90f86fbcd8c31d39e70787cd4dae38655",
        bytes_per_pixel=1.82,
        psnr_db=None,
        source=f"{DERF_BASE_URL}/ducks_take_off_1080p50.y4m",
        encode_recipe="libx265 -preset medium -crf 18 -tag:v hvc1 -pix_fmt yuv420p -movflags +faststart",
        licence=DERF_LICENCE,
        covers="1080p50 splashing water and ripples in HEVC (94.5 Mbps)",
    ),
    FixtureSpec(
        rel_path="derf/in_to_tree_1080p50_h264.mp4",
        category="derf",
        clip="in_to_tree",
        codec="h264",
        width=1920,
        height=1080,
        fps=50.0,
        frames=500,
        duration_s=10.0,
        size_bytes=93_182_625,
        sha256="d27f646af634c1baf86c07e9b33e59e7ab7c526a6460f262bab4664e5073e980",
        bytes_per_pixel=1.55,
        psnr_db=None,
        source=f"{DERF_BASE_URL}/in_to_tree_1080p50.y4m",
        encode_recipe="libx264 -preset medium -crf 16 -pix_fmt yuv420p -movflags +faststart",
        licence=DERF_LICENCE,
        covers="1080p50 continuous camera zoom into foliage",
    ),
    FixtureSpec(
        rel_path="derf/in_to_tree_1080p50_h265.mp4",
        category="derf",
        clip="in_to_tree",
        codec="hevc",
        width=1920,
        height=1080,
        fps=50.0,
        frames=500,
        duration_s=10.0,
        size_bytes=53_062_673,
        sha256="0436a0c28c15df79515cb6986f82d9d634bae0b26157830fdc1ba73ee4d993eb",
        bytes_per_pixel=1.55,
        psnr_db=None,
        source=f"{DERF_BASE_URL}/in_to_tree_1080p50.y4m",
        encode_recipe="libx265 -preset medium -crf 18 -tag:v hvc1 -pix_fmt yuv420p -movflags +faststart",
        licence=DERF_LICENCE,
        covers="1080p50 continuous camera zoom into foliage in HEVC",
    ),
    FixtureSpec(
        rel_path="derf/park_joy_1080p50_h264.mp4",
        category="derf",
        clip="park_joy",
        codec="h264",
        width=1920,
        height=1080,
        fps=50.0,
        frames=500,
        duration_s=10.0,
        size_bytes=112_618_583,
        sha256="c0017db06d7ffc8318ab5edab621d53afb2d4b4fa9c4c5749fe7726e690c092e",
        bytes_per_pixel=1.89,
        psnr_db=None,
        source=f"{DERF_BASE_URL}/park_joy_1080p50.y4m",
        encode_recipe="libx264 -preset medium -crf 16 -pix_fmt yuv420p -movflags +faststart",
        licence=DERF_LICENCE,
        covers="1080p50 dense foliage with panning camera (highest per-frame spatial entropy in derf)",
    ),
    FixtureSpec(
        rel_path="derf/park_joy_1080p50_h265.mp4",
        category="derf",
        clip="park_joy",
        codec="hevc",
        width=1920,
        height=1080,
        fps=50.0,
        frames=500,
        duration_s=10.0,
        size_bytes=96_434_390,
        sha256="1297c21cef922b01527d40a642a1bb2b3fadfe714bf64a79effaa54a9c148322",
        bytes_per_pixel=1.89,
        psnr_db=None,
        source=f"{DERF_BASE_URL}/park_joy_1080p50.y4m",
        encode_recipe="libx265 -preset medium -crf 18 -tag:v hvc1 -pix_fmt yuv420p -movflags +faststart",
        licence=DERF_LICENCE,
        covers="1080p50 dense foliage with panning camera in HEVC",
    ),
    FixtureSpec(
        rel_path="derf/red_kayak_1080p_h264.mp4",
        category="derf",
        clip="red_kayak",
        codec="h264",
        width=1920,
        height=1080,
        fps=29.97,
        frames=570,
        duration_s=19.019,
        size_bytes=97_710_477,
        sha256="73577aa5119f1cdec327f106798ea1abde49f0adf953da1503fd807a8f2a13e6",
        bytes_per_pixel=1.45,
        psnr_db=None,
        source=f"{DERF_BASE_URL}/red_kayak_1080p.y4m",
        encode_recipe="libx264 -preset medium -crf 16 -pix_fmt yuv420p -movflags +faststart",
        licence=DERF_LICENCE,
        covers="1080p 29.97 fps white-water kayaking (non-integer NTSC framerate, 19s duration)",
    ),
    FixtureSpec(
        rel_path="derf/red_kayak_1080p_h265.mp4",
        category="derf",
        clip="red_kayak",
        codec="hevc",
        width=1920,
        height=1080,
        fps=29.97,
        frames=570,
        duration_s=19.019,
        size_bytes=56_209_893,
        sha256="9d83d48bc4d58c84cbafeaf9052337f75a3ffe087c3ead2cd793fd1ce7d89ac5",
        bytes_per_pixel=1.45,
        psnr_db=None,
        source=f"{DERF_BASE_URL}/red_kayak_1080p.y4m",
        encode_recipe="libx265 -preset medium -crf 18 -tag:v hvc1 -pix_fmt yuv420p -movflags +faststart",
        licence=DERF_LICENCE,
        covers="1080p 29.97 fps white-water kayaking in HEVC",
    ),
    FixtureSpec(
        rel_path="derf/rush_hour_1080p25_h264.mp4",
        category="derf",
        clip="rush_hour",
        codec="h264",
        width=1920,
        height=1080,
        fps=25.0,
        frames=500,
        duration_s=20.0,
        size_bytes=72_749_270,
        sha256="5549ce8b6caa28dc1bba488324d372995f375f43661cb4bd6a8def17757a8f8c",
        bytes_per_pixel=1.35,
        psnr_db=None,
        source=f"{DERF_BASE_URL}/rush_hour_1080p25.y4m",
        encode_recipe="libx264 -preset medium -crf 16 -pix_fmt yuv420p -movflags +faststart",
        licence=DERF_LICENCE,
        covers="1080p25 city traffic with heat haze (moderate complexity, 20s duration)",
    ),
    FixtureSpec(
        rel_path="derf/rush_hour_1080p25_h265.mp4",
        category="derf",
        clip="rush_hour",
        codec="hevc",
        width=1920,
        height=1080,
        fps=25.0,
        frames=500,
        duration_s=20.0,
        size_bytes=25_133_035,
        sha256="233bc47491960c4389e6384c529b9fe8387c120b13c68585f9cf3a5482f459a5",
        bytes_per_pixel=1.35,
        psnr_db=None,
        source=f"{DERF_BASE_URL}/rush_hour_1080p25.y4m",
        encode_recipe="libx265 -preset medium -crf 18 -tag:v hvc1 -pix_fmt yuv420p -movflags +faststart",
        licence=DERF_LICENCE,
        covers="1080p25 city traffic with heat haze in HEVC",
    ),
    FixtureSpec(
        rel_path="synthetic_noise/noise_1280x720_200MB.mp4",
        category="synthetic_noise",
        clip="noise_1280x720_200MB",
        codec="h264",
        width=1280,
        height=720,
        fps=30.0,
        frames=283,
        duration_s=9.433333,
        size_bytes=200_000_000,
        sha256="ffeefa12b341acd1b9a9b574c07b43bb5c6ea0ff61d96cc2be9a500dea0afe41",
        bytes_per_pixel=2.80,
        psnr_db=5.4,
        source="os.urandom YUV420p",
        encode_recipe="libx264 -preset medium -crf 18 -r 30 + free-box pad to 200,000,000B",
        licence=NOISE_LICENCE,
        covers="720p30 CRF 18 pure full-entropy noise (170 Mbps, 283 frames)",
    ),
    FixtureSpec(
        rel_path="synthetic_noise/noise_1280x720_200MB_30s_h264.mp4",
        category="synthetic_noise",
        clip="noise_1280x720_200MB_30s",
        codec="h264",
        width=1280,
        height=720,
        fps=30.0,
        frames=900,
        duration_s=30.0,
        size_bytes=200_000_000,
        sha256="b7e80d958996929905dcfe8a5e4c5092262f9d81b20752e2bf1da8abef173b74",
        bytes_per_pixel=2.46,
        psnr_db=5.9,
        source="os.urandom YUV420p",
        encode_recipe="libx264 -preset medium ABR ~53 Mbps 30s + free-box pad to 200,000,000B",
        licence=NOISE_LICENCE,
        covers="720p30 30s near-full-entropy H.264 noise (best worst case for the 1280x720 x 600-frame corner)",
    ),
    FixtureSpec(
        rel_path="synthetic_noise/noise_1280x720_200MB_30s_h265.mp4",
        category="synthetic_noise",
        clip="noise_1280x720_200MB_30s",
        codec="hevc",
        width=1280,
        height=720,
        fps=30.0,
        frames=900,
        duration_s=30.0,
        size_bytes=200_000_000,
        sha256="eb59452c5b608b6e0d1f1065d8a269c16582b62dbfb99ed15e444aad53e605b5",
        bytes_per_pixel=2.75,
        psnr_db=6.0,
        source="os.urandom YUV420p",
        encode_recipe="libx265 -preset medium -tag:v hvc1 ABR ~53 Mbps 30s + free-box pad to 200,000,000B",
        licence=NOISE_LICENCE,
        covers="720p30 30s near-full-entropy HEVC noise (2.75 B/px, exercises HEVC decode under high entropy)",
    ),
    FixtureSpec(
        rel_path="synthetic_noise/noise_1920x1080_300MB.mp4",
        category="synthetic_noise",
        clip="noise_1920x1080_300MB",
        codec="h264",
        width=1920,
        height=1080,
        fps=30.0,
        frames=188,
        duration_s=6.266667,
        size_bytes=300_000_000,
        sha256="b9f6ef9410da95f68f594952d731fdd18e8861b34809972fc24adb8788bc77aa",
        bytes_per_pixel=2.81,
        psnr_db=5.4,
        source="os.urandom YUV420p",
        encode_recipe="libx264 -preset medium -crf 18 -r 30 + free-box pad to 300,000,000B",
        licence=NOISE_LICENCE,
        covers="1080p30 CRF 18 pure full-entropy noise (383 Mbps, 188 frames, 2.81 B/px ceiling)",
    ),
    FixtureSpec(
        rel_path="synthetic_noise/noise_1920x1080_300MB_30s_h264.mp4",
        category="synthetic_noise",
        clip="noise_1920x1080_300MB_30s",
        codec="h264",
        width=1920,
        height=1080,
        fps=30.0,
        frames=900,
        duration_s=30.0,
        size_bytes=300_000_000,
        sha256="e7a28b5591a61a7a8262c20d31893eda4db231befaeef01903c738ba08a71e0f",
        bytes_per_pixel=1.22,
        psnr_db=11.0,
        source="os.urandom YUV420p",
        encode_recipe="libx264 -preset medium ABR ~80 Mbps 30s + free-box pad to 300,000,000B",
        licence=NOISE_LICENCE,
        covers="1080p30 30s ABR 80 Mbps H.264 noise (rate-smoothed to ~1.22 B/px, 900 frames)",
    ),
    FixtureSpec(
        rel_path="synthetic_noise/noise_1920x1080_300MB_30s_h265.mp4",
        category="synthetic_noise",
        clip="noise_1920x1080_300MB_30s",
        codec="hevc",
        width=1920,
        height=1080,
        fps=30.0,
        frames=900,
        duration_s=30.0,
        size_bytes=300_000_000,
        sha256="5331aadf7e5a8853d3862f10f33890dfbe749ac16c8baf4365608c6599508b41",
        bytes_per_pixel=1.90,
        psnr_db=22.0,
        source="os.urandom YUV420p",
        encode_recipe="libx265 -preset medium -tag:v hvc1 ABR ~80 Mbps 30s + free-box pad to 300,000,000B",
        licence=NOISE_LICENCE,
        covers="1080p30 30s ABR 80 Mbps HEVC noise (900 frames)",
    ),
)

FIXTURES_BY_PATH: dict[str, FixtureSpec] = {f.rel_path: f for f in FIXTURES}
FIXTURES_BY_NAME: dict[str, FixtureSpec] = {f.name: f for f in FIXTURES}


def sha256_file(path: Path) -> str:
    """Computes the hex SHA-256 digest of a file in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def verify_fixture(
    data_dir: Path, spec: FixtureSpec, *, check_hash: bool = True, check_probe: bool = True
) -> dict[str, Any]:
    """Checks one fixture in ``data_dir`` against its manifest specification."""
    path = data_dir / spec.rel_path
    result: dict[str, Any] = {
        "rel_path": spec.rel_path,
        "exists": path.is_file(),
        "ok": False,
        "problems": [],
    }
    if not path.is_file():
        result["problems"].append("missing file")
        return result

    actual_size = path.stat().st_size
    result["size_bytes"] = actual_size
    if actual_size != spec.size_bytes:
        result["problems"].append(f"size {actual_size} != expected {spec.size_bytes}")

    if check_hash:
        actual_sha = sha256_file(path)
        result["sha256"] = actual_sha
        if actual_sha != spec.sha256 and spec.category == "derf":
            result["problems"].append(f"sha256 {actual_sha[:12]}... != expected {spec.sha256[:12]}...")
        elif actual_sha != spec.sha256:
            result["sha256_note"] = "differs from initial random generation (expected for fresh os.urandom runs)"

    if check_probe:
        info = sashimi.probe(path)
        result["probed"] = {
            "width": info.width,
            "height": info.height,
            "duration": info.duration,
            "fps": round(info.fps, 2) if info.fps else None,
            "codecs": list(info.codecs),
        }
        if (info.width, info.height) != (spec.width, spec.height):
            result["problems"].append(f"dimensions {info.width}x{info.height} != {spec.width}x{spec.height}")
        if spec.codec not in info.codecs:
            result["problems"].append(f"codecs {list(info.codecs)} missing {spec.codec}")
        if info.duration is None or abs(info.duration - spec.duration_s) > 0.05:
            result["problems"].append(f"duration {info.duration} != {spec.duration_s}")

    result["ok"] = not result["problems"]
    return result


def verify_all(data_dir: Path, *, check_hash: bool = True, check_probe: bool = True) -> list[dict[str, Any]]:
    """Verifies all 20 manifest fixtures under ``data_dir``."""
    return [verify_fixture(data_dir, spec, check_hash=check_hash, check_probe=check_probe) for spec in FIXTURES]


def _pad_mp4_free_box(path: Path, target_bytes: int) -> None:
    """Appends a top-level MP4 ``free`` box so ``path`` is exactly ``target_bytes`` long."""
    gap = target_bytes - path.stat().st_size
    if gap < 8:
        raise RuntimeError(f"Cannot pad {path} by {gap} bytes (need at least 8 bytes for a free box)")
    with path.open("ab") as handle:
        handle.write(struct.pack(">I4s", gap, b"free") + b"\0" * (gap - 8))


def generate_noise_crf(
    output: Path,
    width: int,
    height: int,
    target_bytes: int,
    *,
    fps: int = 30,
    crf: int = 18,
) -> None:
    """Generates a variable-duration CRF synthetic noise MP4 padded to ``target_bytes``."""
    output.parent.mkdir(parents=True, exist_ok=True)
    frame_bytes = width * height * 3 // 2

    def _encode(frames: int) -> int:
        proc = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "yuv420p",
                "-s",
                f"{width}x{height}",
                "-r",
                str(fps),
                "-i",
                "-",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                str(crf),
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output),
            ],
            stdin=subprocess.PIPE,
        )
        assert proc.stdin is not None
        for _ in range(frames):
            proc.stdin.write(os.urandom(frame_bytes))
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed while encoding CRF noise video")
        return output.stat().st_size

    probe_size = _encode(30)
    per_frame = probe_size / 30
    frames = int(target_bytes / per_frame) - 1
    size = probe_size
    for _ in range(6):
        size = _encode(frames)
        per_frame = size / frames
        if target_bytes - 8 >= size > target_bytes - 1.5 * per_frame:
            break
        frames = int((target_bytes - 8) / per_frame) - (1 if size <= target_bytes - 8 else 2)
    if size > target_bytes - 8:
        raise RuntimeError(f"Could not land under {target_bytes} bytes (got {size})")
    _pad_mp4_free_box(output, target_bytes)


def generate_noise_fixed(
    output: Path,
    codec: CodecName,
    width: int,
    height: int,
    target_bytes: int,
    seconds: int,
    *,
    fps: int = 30,
) -> None:
    """Generates a fixed-duration ABR synthetic noise MP4 padded to ``target_bytes``."""
    output.parent.mkdir(parents=True, exist_ok=True)
    frame_bytes = width * height * 3 // 2
    frames = seconds * fps
    codec_args = (
        ["-c:v", "libx264", "-preset", "medium"]
        if codec == "h264"
        else ["-c:v", "libx265", "-preset", "medium", "-tag:v", "hvc1", "-x265-params", "log-level=error"]
    )
    goal = target_bytes * 0.997
    kbps = int(goal * 8 / seconds / 1000)

    for _ in range(6):
        proc = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "yuv420p",
                "-s",
                f"{width}x{height}",
                "-r",
                str(fps),
                "-i",
                "-",
                *codec_args,
                "-b:v",
                f"{kbps}k",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output),
            ],
            stdin=subprocess.PIPE,
        )
        assert proc.stdin is not None
        for _ in range(frames):
            proc.stdin.write(os.urandom(frame_bytes))
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed while encoding fixed-length noise video")
        size = output.stat().st_size
        if target_bytes - 8 >= size >= target_bytes * 0.993:
            break
        kbps = int(kbps * goal / size)
    else:
        raise RuntimeError(f"Could not land near {target_bytes} bytes")
    _pad_mp4_free_box(output, target_bytes)


def rebuild_derf_fixture(data_dir: Path, spec: FixtureSpec, scratch_dir: Path) -> None:
    """Downloads a Xiph ``.y4m`` master if needed and encodes ``spec`` into ``data_dir``."""
    if spec.category != "derf":
        raise ValueError(f"{spec.rel_path} is not a derf fixture")
    y4m_name = spec.source.rsplit("/", 1)[-1]
    y4m_path = scratch_dir / y4m_name
    if not y4m_path.is_file():
        scratch_dir.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(spec.source, headers={"User-Agent": "sashimi-bench/1.0"})
        with urllib.request.urlopen(req) as resp, y4m_path.open("wb") as out_file:
            while chunk := resp.read(1 << 20):
                out_file.write(chunk)
    dest = data_dir / spec.rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    codec_args = (
        ["-c:v", "libx264", "-preset", "medium", "-crf", "16"]
        if spec.codec == "h264"
        else ["-c:v", "libx265", "-preset", "medium", "-crf", "18", "-tag:v", "hvc1"]
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(y4m_path),
            *codec_args,
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        check=True,
    )


def rebuild_missing(data_dir: Path, scratch_dir: Path) -> list[str]:
    """Rebuilds any missing files in ``data_dir`` and returns their relative paths."""
    rebuilt: list[str] = []
    for spec in FIXTURES:
        dest = data_dir / spec.rel_path
        if dest.is_file():
            continue
        if spec.category == "derf":
            rebuild_derf_fixture(data_dir, spec, scratch_dir)
        elif "30s" in spec.clip:
            generate_noise_fixed(dest, spec.codec, spec.width, spec.height, spec.size_bytes, 30)
        else:
            generate_noise_crf(dest, spec.width, spec.height, spec.size_bytes)
        rebuilt.append(spec.rel_path)
    return rebuilt


def manifest_as_dicts() -> list[dict[str, Any]]:
    """Returns all fixture specifications as plain dictionaries for JSON serialization."""
    return [asdict(spec) for spec in FIXTURES]
