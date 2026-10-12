"""
Sandbox conformance driver. Runs one conversion job the way a FileSushi worker would, inside the
locked-down container started by scripts/sandbox_check.sh, then verifies the result.

Usage (inside the container):  python /opt/sandbox/driver.py <job-name>

Input:  /vault/input/original.blob (read-only)    Output: /vault/output/result.<ext>
Prints one JSON line with timings, peak memory, peak pids/threads and the checks performed.
Exits 0 when the job behaved as expected (including expected user errors), 1 otherwise.
"""

import json
import pathlib
import sys
import time

NETWORK_EVENTS = {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.bind", "urllib.Request"}
network_attempts: list[str] = []


def _audit(event: str, args: tuple) -> None:
    if event in NETWORK_EVENTS:
        network_attempts.append(event)


sys.addaudithook(_audit)

import sashimi  # noqa: E402  (imported after the audit hook so import-time activity is covered)
from sashimi import audio, image, sheets, video  # noqa: E402
from sashimi.config import (  # noqa: E402
    AUDIO_CONVERT,
    CONTACT_SHEET,
    IMAGE_CONVERT,
    IMAGE_RESIZE,
    VIDEO_CONVERT,
    VIDEO_TRANSFORM,
    VIDEO_TRIM,
)
from sashimi.errors import UserInputError  # noqa: E402

INPUT = pathlib.Path("/vault/input/original.blob")
OUTPUT_DIR = pathlib.Path("/vault/output")
WORKERS = 2

JOBS = {
    "image-png-to-jpeg": (
        "jpg",
        lambda s, d: image.convert(s, d, IMAGE_CONVERT.from_params({}, target="jpeg")),
        "JPEG",
    ),
    "image-jpeg-to-png": (
        "png",
        lambda s, d: image.convert(s, d, IMAGE_CONVERT.from_params({"max_width": 1600}, target="png")),
        "PNG",
    ),
    "image-to-webp-lossless": (
        "webp",
        lambda s, d: image.convert(s, d, IMAGE_CONVERT.from_params({"lossless": True}, target="webp")),
        "WEBP",
    ),
    "image-to-gif": (
        "gif",
        lambda s, d: image.convert(s, d, IMAGE_CONVERT.from_params({}, target="gif")),
        "GIF",
    ),
    "image-resize": (
        "jpg",
        lambda s, d: image.convert(
            s, d, IMAGE_RESIZE.from_params({"max_width": 800, "max_height": 800, "quality": 80}, target="jpeg")
        ),
        "JPEG",
    ),
    "image-to-ico": ("ico", lambda s, d: image.to_ico(s, d), "ICO"),
    "heic-to-jpeg": (
        "jpg",
        lambda s, d: image.convert(s, d, IMAGE_CONVERT.from_params({}, target="jpeg")),
        "JPEG",
    ),
    "video-to-gif": (
        "gif",
        lambda s, d: video.convert(s, d, VIDEO_CONVERT.from_params({}, target="gif"), max_workers=WORKERS),
        "gif",
    ),
    "gif-to-webm": (
        "webm",
        lambda s, d: video.convert(s, d, VIDEO_CONVERT.from_params({}, target="webm"), max_workers=WORKERS),
        "webm",
    ),
    "video-to-webm": (
        "webm",
        lambda s, d: video.convert(s, d, VIDEO_CONVERT.from_params({}, target="webm"), max_workers=WORKERS),
        "webm",
    ),
    "video-to-mp4-640": (
        "mp4",
        lambda s, d: video.convert(
            s, d, VIDEO_CONVERT.from_params({"max_width": 640}, target="mp4"), max_workers=WORKERS
        ),
        "mp4",
    ),
    "video-to-webp": (
        "webp",
        lambda s, d: video.convert(s, d, VIDEO_CONVERT.from_params({}, target="webp"), max_workers=WORKERS),
        "WEBP",
    ),
    "video-trim": (
        "mkv",
        lambda s, d: video.trim(
            s, d, VIDEO_TRIM.from_params({"start_seconds": 20, "duration_seconds": 10}, container="mkv")
        ),
        "matroska",
    ),
    "video-trim-accurate": (
        "mp4",
        lambda s, d: video.trim(
            s,
            d,
            VIDEO_TRIM.from_params({"start_seconds": 20.5, "duration_seconds": 10, "accurate": True}, container="mp4"),
            max_workers=WORKERS,
        ),
        "mp4",
    ),
    "video-rotate": (
        "mp4",
        lambda s, d: video.transform(
            s, d, VIDEO_TRANSFORM.from_params({"rotate": 90}, target="mp4"), max_workers=WORKERS
        ),
        "mp4",
    ),
    "sheet-static": (
        "jpg",
        lambda s, d: sheets.render(
            s, d, CONTACT_SHEET.from_params({}, target="jpg"), display_name="holiday-clip.mkv", max_workers=WORKERS
        ),
        "JPEG",
    ),
    "sheet-animated": (
        "webp",
        lambda s, d: sheets.render(
            s,
            d,
            CONTACT_SHEET.from_params({"animated": "true", "fps": 12}, target="webp"),
            display_name="holiday-clip.mkv",
            max_workers=WORKERS,
        ),
        "WEBP",
    ),
    "sheet-animated-long": (
        "webp",
        lambda s, d: sheets.render(
            s, d, CONTACT_SHEET.from_params({"animated": True}), display_name="long.mkv", max_workers=WORKERS
        ),
        "WEBP",
    ),
    "video-trim-wrong-container": (
        "webm",
        lambda s, d: video.trim(s, d, VIDEO_TRIM.from_params({"duration_seconds": 10}, container="webm")),
        "user-error",
    ),
    "video-to-mp3": (
        "mp3",
        lambda s, d: audio.convert(s, d, AUDIO_CONVERT.from_params({}, target="mp3"), max_workers=WORKERS),
        "mp3",
    ),
    "audio-to-m4a": (
        "m4a",
        lambda s, d: audio.convert(s, d, AUDIO_CONVERT.from_params({"mono": True}, target="m4a"), max_workers=WORKERS),
        "mp4",
    ),
    "audio-to-wav": (
        "wav",
        lambda s, d: audio.convert(s, d, AUDIO_CONVERT.from_params({}, target="wav"), max_workers=WORKERS),
        "wav",
    ),
    "audio-to-ogg": (
        "ogg",
        lambda s, d: audio.convert(
            s, d, AUDIO_CONVERT.from_params({"bitrate_kbps": 96}, target="ogg"), max_workers=WORKERS
        ),
        "ogg",
    ),
    "audio-to-flac": (
        "flac",
        lambda s, d: audio.convert(
            s, d, AUDIO_CONVERT.from_params({"duration_seconds": 30}, target="flac"), max_workers=WORKERS
        ),
        "flac",
    ),
    "junk-to-mp4": (
        "mp4",
        lambda s, d: video.convert(s, d, VIDEO_CONVERT.from_params({}, target="mp4"), max_workers=WORKERS),
        "user-error",
    ),
    "probe": ("json", lambda s, d: d.write_text(sashimi.probe(s).model_dump_json()), "json"),
}


def cgroup_value(name: str) -> int | None:
    try:
        return int(pathlib.Path(f"/sys/fs/cgroup/{name}").read_text().split()[0])
    except (OSError, ValueError):
        return None


def validate(output: pathlib.Path, expected: str) -> str:
    """Opens the output with Pillow (images) or ffprobe (media) and returns a short description."""
    if expected == "json":
        return "probe: " + json.loads(output.read_text())["container"]
    if expected.isupper():
        from PIL import Image

        with Image.open(output) as img:
            assert img.format == expected, f"expected {expected}, got {img.format}"
            return f"{img.format} {img.width}x{img.height}"
    info = sashimi.probe(output)
    assert expected in info.container_names, f"expected {expected}, got {info.container}"
    dims = f" {info.width}x{info.height}" if info.width else ""
    return f"{info.container.split(',')[0]}{dims} {info.duration or 0:.1f}s {','.join(info.codecs)}"


def main() -> int:
    job = sys.argv[1]
    ext, run, expected = JOBS[job]
    output = OUTPUT_DIR / f"result.{ext}"
    report: dict = {"job": job, "ok": False}

    start = time.perf_counter()
    try:
        run(INPUT, output)
        report["seconds"] = round(time.perf_counter() - start, 2)
    except UserInputError as e:
        report["seconds"] = round(time.perf_counter() - start, 2)
        (OUTPUT_DIR / "error.txt").write_text(e.user_message)
        report["user_error"] = e.user_message
    report["peak_memory_mb"] = round((cgroup_value("memory.peak") or 0) / 2**20)
    report["peak_pids"] = cgroup_value("pids.peak")

    problems = []
    if expected == "user-error":
        if "user_error" not in report:
            problems.append("expected a user error")
        if output.exists():
            problems.append("output written despite error")
    elif "user_error" in report:
        problems.append("unexpected user error")
    else:
        try:
            report["output"] = validate(output, expected)
            report["output_kb"] = output.stat().st_size // 1024
        except Exception as e:  # noqa: BLE001
            problems.append(f"invalid output: {e}")

    extra_outputs = sorted(p.name for p in OUTPUT_DIR.iterdir() if p.name not in (output.name, "error.txt"))
    tmp_leftovers = sorted(str(p) for p in pathlib.Path("/tmp").rglob("*"))
    if extra_outputs:
        problems.append(f"unexpected files in /vault/output: {extra_outputs}")
    if tmp_leftovers:
        problems.append(f"files left in /tmp: {tmp_leftovers[:5]}")
    if network_attempts:
        problems.append(f"network access attempted: {sorted(set(network_attempts))}")

    outcome = "error.txt written, no output" if expected == "user-error" else "output valid"
    report["checks"] = problems or f"{outcome}; nothing outside /vault/output; /tmp empty; no network"
    report["ok"] = not problems
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
