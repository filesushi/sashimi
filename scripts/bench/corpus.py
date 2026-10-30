# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
A manifest-driven benchmark over a corpus of real-world media: every task that applies to each file,
run through the CLI with its defaults, measured and checked.

The corpus is a JSON list of entries (``path`` relative to the manifest's folder, ``kind``
``image`` or ``video``, ``categories`` and ``facts``), optionally joined by other manifests that add
a ``tests`` description to entries by path. Nothing here names a file: tasks are chosen from each
entry's kind, facts and categories, so the corpus can hold media that must never enter the repository.

Each case runs in its own child process (``run.py --corpus-child``), which calls the CLI in-process
(so a refusal's exception type and limit are recorded as well as its message and exit code), takes
CPU seconds and peak memory around the call, and then inspects the source and every output. The
parent adds a wall-clock timeout, the peak memory of the whole process tree sampled while the case
runs, and checks of each output against what the task should give (:func:`assess`). A refusal by a
limit is run again with every limit off (``[limits]`` set to ``none`` in a ``sashimi.toml`` beside
the case), to learn whether sashimi can do the job at all and what it costs.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LONG_SECONDS = 120.0
"""Inputs longer than this have their re-encoding tasks measured on a stretch, not the whole file."""
STRETCH_SECONDS = 60.0
"""The stretch measured on long inputs."""
EXTRACT_SECONDS = 10.0
"""The longest section the extract tasks cut."""
RESIZE_WIDTH = 800
DEFAULT_TIMEOUT = 600.0
DECODE_CHECK_FULL_BYTES = 300_000_000
"""Outputs up to this size are decoded whole to check them; larger ones only their first and last 20 seconds."""
LIMIT_FIELDS = (
    "max_pixels",
    "max_frame_bytes",
    "max_animation_bytes",
    "max_sheet_pixels",
    "max_video_seconds",
    "max_audio_seconds",
    "max_gif_seconds",
    "max_preview_clips",
    "max_scan_packets",
)
BENIGN_DECODE = ("non monotonically increasing dts", "Last message repeated")
"""ffmpeg messages a decode check prints for outputs that play correctly (equal timestamps after rounding)."""
ANIMATED_CATEGORIES = frozenset({"animated-gif", "animated-webp", "apng", "animated-avif", "long-animation"})


@dataclass(frozen=True)
class CorpusEntry:
    """One media file of the corpus, as its manifest describes it."""

    path: str
    kind: str
    categories: tuple[str, ...] = ()
    facts: dict[str, Any] = field(default_factory=dict)
    size: int = 0
    summary: str = ""
    tests: str = ""

    @property
    def role(self) -> str:
        """The folder the file sits in, which names its main role."""
        return Path(self.path).parent.name

    @property
    def duration(self) -> float | None:
        value = self.facts.get("duration")
        return float(value) if isinstance(value, (int, float)) and value > 0 else None

    @property
    def frames(self) -> int:
        value = self.facts.get("frames")
        return int(value) if isinstance(value, int) and value > 0 else 1

    @property
    def animated(self) -> bool:
        return self.kind == "image" and (self.frames > 1 or bool(ANIMATED_CATEGORIES & set(self.categories)))

    @property
    def has_audio(self) -> bool:
        return bool(self.facts.get("audio"))

    @property
    def long(self) -> bool:
        return self.duration is not None and self.duration > LONG_SECONDS

    @property
    def readable(self) -> bool:
        """False for files the manifest's probe couldn't read (empty, truncated, unknown codec)."""
        return bool(self.facts) and "codec:?" not in self.categories

    @property
    def image_format(self) -> str:
        return str(self.facts.get("format", "")).upper()


def load_corpus(manifest: Path, *extra: Path) -> list[CorpusEntry]:
    """
    The image and video entries of `manifest`, with each path relative to the data folder that holds
    `manifest`, and the ``tests`` description of any `extra` manifest's entry for the same file
    (whose paths are relative to its own folder). Entries of other kinds are left out.
    """
    root = manifest.resolve().parent
    notes: dict[Path, str] = {}
    for other in extra:
        base = other.resolve().parent
        for item in json.loads(other.read_text(encoding="utf-8")):
            if item.get("tests"):
                notes[(base / item["path"]).resolve()] = str(item["tests"])
    entries = []
    for item in json.loads(manifest.read_text(encoding="utf-8")):
        if item.get("kind") not in ("image", "video"):
            continue
        entries.append(
            CorpusEntry(
                path=item["path"],
                kind=item["kind"],
                categories=tuple(item.get("categories") or ()),
                facts=dict(item.get("facts") or {}),
                size=int(item.get("size") or 0),
                summary=str(item.get("summary") or ""),
                tests=notes.get((root / item["path"]).resolve(), ""),
            )
        )
    return entries


ArgvFn = Callable[[CorpusEntry], list[str]]


@dataclass(frozen=True)
class CorpusTask:
    """
    One task: ``runner`` is ``cli`` (``argv`` after ``sashimi``, with ``{input}`` for the input),
    ``probe`` (``sashimi.probe``), ``frames`` (``sashimi.image.frames_info``) or ``metadata``
    (``sashimi.metadata.read``). ``expect`` names the
    checks :func:`assess` makes of the output.
    """

    name: str
    kind: str
    runner: str
    description: str
    argv: ArgvFn | None = None
    applies: Callable[[CorpusEntry], bool] = lambda entry: True
    setup: Callable[[CorpusEntry], str | None] = lambda entry: None
    expect: dict[str, Any] = field(default_factory=dict)
    outputs: bool = True

    def applies_to(self, entry: CorpusEntry) -> bool:
        return (self.kind in ("any", entry.kind)) and self.applies(entry)


def _fixed(*args: str) -> ArgvFn:
    return lambda entry: [*args]


def section(entry: CorpusEntry) -> tuple[float, float]:
    """Start and length of the section the extract tasks cut: from 30% in, at most 10 seconds."""
    duration = entry.duration
    if duration is None:
        return 1.0, 3.0
    start = round(duration * 0.3, 3)
    return start, round(min(EXTRACT_SECONDS, duration * 0.4), 3)


def stretch(entry: CorpusEntry) -> list[str]:
    """``--start``/``--duration`` arguments for a re-encode of a long input; none for others."""
    if not entry.long or entry.duration is None:
        return []
    start = round(entry.duration * 0.25, 3)
    return ["--start", f"{start}", "--duration", f"{STRETCH_SECONDS:g}"]


def _extract(accurate: bool) -> ArgvFn:
    def argv(entry: CorpusEntry) -> list[str]:
        start, length = section(entry)
        return ["extract", "{input}", "--start", f"{start}", "--duration", f"{length}"] + (
            ["--accurate"] if accurate else []
        )

    return argv


def _drop_every_other(entry: CorpusEntry) -> list[str]:
    frames = max(entry.frames, 2)
    return ["frames", "{input}", "--drop", ",".join(str(n) for n in range(2, frames + 1, 2))]


def _excerpt_if_long(entry: CorpusEntry) -> str | None:
    return "excerpt" if entry.long else None


def _location(entry: CorpusEntry) -> bool:
    return bool({"gps", "location", "gopro-gps"} & set(entry.categories))


def _anim_target(entry: CorpusEntry) -> list[str]:
    target = "webp" if entry.image_format == "GIF" else "gif"
    return ["convert", "{input}", target]


TASKS: tuple[CorpusTask, ...] = (
    CorpusTask("probe", "video", "probe", "sashimi.probe() with the default limits", outputs=False),
    CorpusTask("frames-info", "image", "frames", "sashimi.image.frames_info() with the default limits", outputs=False),
    CorpusTask("metadata-read", "any", "metadata", "sashimi.metadata.read() with the default limits", outputs=False),
    CorpusTask("cli-info", "any", "cli", "sashimi info --json", _fixed("info", "{input}"), outputs=False),
    CorpusTask("cli-metadata", "any", "cli", "sashimi metadata --json", _fixed("metadata", "{input}"), outputs=False),
    CorpusTask(
        "img-to-jpeg",
        "image",
        "cli",
        "convert to JPEG",
        _fixed("convert", "{input}", "jpeg"),
        expect={"format": "JPEG", "dims": "display"},
    ),
    CorpusTask(
        "img-to-png",
        "image",
        "cli",
        "convert to PNG",
        _fixed("convert", "{input}", "png"),
        expect={"format": "PNG", "dims": "display"},
    ),
    CorpusTask(
        "img-to-webp",
        "image",
        "cli",
        "convert to WebP",
        _fixed("convert", "{input}", "webp"),
        expect={"format": "WEBP", "dims": "display", "frames": "same"},
    ),
    CorpusTask(
        "img-resize",
        "image",
        "cli",
        f"resize -w {RESIZE_WIDTH}",
        _fixed("resize", "{input}", "-w", str(RESIZE_WIDTH)),
        expect={"dims": ("max_width", RESIZE_WIDTH), "frames": "same"},
    ),
    CorpusTask(
        "img-rotate",
        "image",
        "cli",
        "rotate 90",
        _fixed("rotate", "{input}", "90"),
        expect={"dims": "swapped", "frames": "same"},
    ),
    CorpusTask(
        "img-edit",
        "image",
        "cli",
        "edit: square crop, horizontal flip, brightness/contrast/saturation",
        _fixed(
            "edit",
            "{input}",
            "--aspect",
            "1:1",
            "--flip",
            "horizontal",
            "--brightness",
            "1.1",
            "--contrast",
            "1.1",
            "--saturation",
            "1.2",
        ),
        expect={"dims": "square", "frames": "same"},
    ),
    CorpusTask(
        "img-strip-location",
        "image",
        "cli",
        "strip --remove remove_location",
        _fixed("strip", "{input}", "--remove", "remove_location"),
        expect={"dims": "stored", "frames": "same", "no_location": True},
    ),
    CorpusTask(
        "img-strip-all",
        "image",
        "cli",
        "strip (remove_all)",
        _fixed("strip", "{input}"),
        expect={"dims": "stored", "frames": "same", "no_location": True},
    ),
    CorpusTask("img-favicon", "image", "cli", "favicon pack", _fixed("favicon", "{input}"), expect={"zip": True}),
    CorpusTask(
        "anim-convert",
        "image",
        "cli",
        "animation between GIF and WebP (GIF to WebP, others to GIF)",
        _anim_target,
        applies=lambda e: e.animated,
        expect={"frames": "same", "dims": "display", "timing": "same"},
    ),
    CorpusTask(
        "anim-to-mp4",
        "image",
        "cli",
        "animated GIF to MP4",
        _fixed("convert", "{input}", "mp4"),
        applies=lambda e: e.animated and e.image_format == "GIF",
        expect={"container": "mov", "timing": "same"},
    ),
    CorpusTask(
        "anim-frames-drop",
        "image",
        "cli",
        "frames: drop every other frame",
        _drop_every_other,
        applies=lambda e: e.animated,
        expect={"frames": "half", "dims": "display"},
    ),
    CorpusTask(
        "anim-frames-speed",
        "image",
        "cli",
        "frames --speed 2",
        _fixed("frames", "{input}", "--speed", "2"),
        applies=lambda e: e.animated,
        expect={"frames": "same", "timing": "half"},
    ),
    CorpusTask(
        "vid-sheet",
        "video",
        "cli",
        "thumbs (still contact sheet)",
        _fixed("thumbs", "{input}"),
        expect={"format": "JPEG", "width": 2560},
    ),
    CorpusTask(
        "vid-sheet-animated",
        "video",
        "cli",
        "thumbs --animated",
        _fixed("thumbs", "{input}", "--animated"),
        expect={"format": "WEBP", "width": 1280, "animated": True},
    ),
    CorpusTask(
        "vid-preview-webp",
        "video",
        "cli",
        "preview (animated WebP)",
        _fixed("preview", "{input}"),
        expect={"format": "WEBP", "height": 360, "animated": True, "preview": True},
    ),
    CorpusTask(
        "vid-preview-gif",
        "video",
        "cli",
        "preview --output-format gif",
        _fixed("preview", "{input}", "--output-format", "gif"),
        expect={"format": "GIF", "height": 360, "animated": True, "preview": True},
    ),
    CorpusTask(
        "vid-to-mp4",
        "video",
        "cli",
        "convert to MP4 (copy when it fits, else encode)",
        _fixed("convert", "{input}", "mp4"),
        expect={"container": "mov", "duration": "same", "dims": "display"},
    ),
    CorpusTask(
        "vid-to-webm",
        "video",
        "cli",
        "convert to WebM (a stretch of long inputs)",
        lambda e: ["convert", "{input}", "webm", *stretch(e)],
        expect={"container": "matroska", "duration": "same", "dims": "display"},
    ),
    CorpusTask(
        "vid-to-gif",
        "video",
        "cli",
        "convert to GIF (defaults: 10 s at 480 px)",
        _fixed("convert", "{input}", "gif"),
        expect={"format": "GIF", "duration": ("clip", 10.0), "dims": ("max_width", 480)},
    ),
    CorpusTask(
        "vid-to-webp",
        "video",
        "cli",
        "convert to animated WebP (defaults: 10 s at 480 px)",
        _fixed("convert", "{input}", "webp"),
        expect={"format": "WEBP", "duration": ("clip", 10.0), "dims": ("max_width", 480)},
    ),
    CorpusTask(
        "vid-to-mp3",
        "video",
        "cli",
        "convert to MP3 (a stretch of long inputs)",
        lambda e: ["convert", "{input}", "mp3", *stretch(e)],
        applies=lambda e: e.has_audio,
        expect={"audio_only": True, "duration": "same"},
    ),
    CorpusTask(
        "vid-extract",
        "video",
        "cli",
        f"extract (lossless) up to {EXTRACT_SECONDS:g} s from 30% in",
        _extract(False),
        expect={"duration": "section-copy", "dims": "stored"},
    ),
    CorpusTask(
        "vid-extract-accurate",
        "video",
        "cli",
        f"extract --accurate up to {EXTRACT_SECONDS:g} s from 30% in",
        _extract(True),
        expect={"duration": "section", "dims": "display"},
    ),
    CorpusTask(
        "vid-remux-mkv",
        "video",
        "cli",
        "remux to MKV",
        _fixed("remux", "{input}", "--output-format", "mkv"),
        expect={"container": "matroska", "duration": "same", "dims": "stored", "streams": "same"},
    ),
    CorpusTask(
        "vid-remux-mp4",
        "video",
        "cli",
        "remux to MP4",
        _fixed("remux", "{input}", "--output-format", "mp4"),
        expect={"container": "mov", "duration": "same", "dims": "stored"},
    ),
    CorpusTask(
        "vid-rotate",
        "video",
        "cli",
        "rotate 90 (a lossless excerpt of long inputs)",
        _fixed("rotate", "{input}", "90"),
        setup=_excerpt_if_long,
        expect={"duration": "same", "dims": "swapped"},
    ),
    CorpusTask(
        "vid-strip-all",
        "video",
        "cli",
        "strip (remove_all)",
        _fixed("strip", "{input}"),
        expect={"duration": "same", "dims": "stored", "no_location": True},
    ),
    CorpusTask(
        "vid-strip-location",
        "video",
        "cli",
        "strip --remove remove_location",
        _fixed("strip", "{input}", "--remove", "remove_location"),
        applies=_location,
        expect={"duration": "same", "dims": "stored", "no_location": True},
    ),
)
TASKS_BY_NAME = {task.name: task for task in TASKS}


@dataclass(frozen=True)
class CorpusCase:
    """One task on one file, by default limits (``capability`` False) or with every limit off."""

    task: str
    path: str
    capability: bool = False

    @property
    def key(self) -> str:
        return f"{self.task}|{self.path}|{'capability' if self.capability else 'default'}"


def select_cases(
    entries: list[CorpusEntry],
    tasks: tuple[CorpusTask, ...] = TASKS,
    *,
    task_glob: str | None = None,
    path_glob: str | None = None,
) -> list[CorpusCase]:
    """
    Every task that applies to every entry, in the order they run: images before videos, smaller
    files first, so a long run gives results across the corpus early.
    """
    import fnmatch

    chosen = [t for t in tasks if task_glob is None or fnmatch.fnmatch(t.name, task_glob)]
    ordered = sorted(entries, key=lambda e: (e.kind != "image", e.size, e.path))
    return [
        CorpusCase(task.name, entry.path)
        for entry in ordered
        if path_glob is None or fnmatch.fnmatch(entry.path, path_glob)
        for task in chosen
        if task.applies_to(entry)
    ]


def cli_argv(task: CorpusTask, entry: CorpusEntry, input_path: Path, out_dir: Path) -> list[str]:
    """The arguments after ``sashimi`` for a CLI task: its own, then the output folder and ``--json``."""
    assert task.argv is not None
    args = [str(input_path) if arg == "{input}" else arg for arg in task.argv(entry)]
    if task.outputs:
        args += ["--output-dir", str(out_dir)]
    return args + ["--json"]


def power_state() -> dict[str, Any]:
    """Mains or battery, the power profile and the CPU's energy preference, where Linux exposes them."""
    state: dict[str, Any] = {}
    online = {}
    for supply in sorted(Path("/sys/class/power_supply").glob("*/online")):
        with contextlib.suppress(OSError):
            online[supply.parent.name] = supply.read_text(encoding="utf-8").strip()
    state["supplies_online"] = online
    try:
        state["power_profile"] = subprocess.run(
            ["powerprofilesctl", "get"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        state["power_profile"] = None
    epp = Path("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")
    state["energy_performance_preference"] = epp.read_text(encoding="utf-8").strip() if epp.is_file() else None
    return state


def ffprobe_json(path: Path) -> dict[str, Any]:
    """ffprobe's streams and format for `path`, or ``{"error": ...}``."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr.strip()[-500:] or f"ffprobe exited {proc.returncode}"}
    return json.loads(proc.stdout or "{}")


def _rotation(stream: dict[str, Any]) -> int:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            return int(round(float(side["rotation"])))
    rotate = (stream.get("tags") or {}).get("rotate")
    return -int(rotate) if rotate else 0


def _ratio(text: str | None) -> float | None:
    if not text or ":" not in text:
        return None
    num, den = text.split(":", 1)
    with contextlib.suppress(ValueError, ZeroDivisionError):
        value = float(num) / float(den)
        return value if value > 0 else None
    return None


def _fraction(text: str | None) -> float | None:
    if not text:
        return None
    with contextlib.suppress(ValueError, ZeroDivisionError):
        if "/" in text:
            num, den = text.split("/", 1)
            return float(num) / float(den) if float(den) else None
        return float(text)
    return None


def describe_media(path: Path) -> dict[str, Any]:
    """A video or audio file's container, length, streams and first video stream, as ffprobe reads them."""
    data = ffprobe_json(path)
    if "error" in data:
        return {"readable": False, "error": data["error"]}
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    videos = [
        s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
    ]
    info: dict[str, Any] = {
        "readable": True,
        "container": fmt.get("format_name"),
        "duration": _fraction(fmt.get("duration")),
        "start_time": _fraction(fmt.get("start_time")),
        "streams": [s.get("codec_type") for s in streams],
        "codecs": [s.get("codec_name") for s in streams],
        "tags": sorted((fmt.get("tags") or {}).keys()),
        "location_tags": sorted(
            k for k in (fmt.get("tags") or {}) if "location" in k.lower() or "gps" in k.lower() or k.lower() == "xyz"
        ),
    }
    if videos:
        v = videos[0]
        width, height = int(v.get("width") or 0), int(v.get("height") or 0)
        rotation = _rotation(v)
        sar = _ratio(v.get("sample_aspect_ratio")) or 1.0
        shown_w, shown_h = (height, width) if rotation % 180 else (width, height)
        if rotation % 180:
            shown_h = round(shown_h * sar) if sar != 1.0 else shown_h
        else:
            shown_w = round(shown_w * sar) if sar != 1.0 else shown_w
        info.update(
            width=width,
            height=height,
            rotation=rotation,
            sar=v.get("sample_aspect_ratio"),
            display=[shown_w, shown_h],
            video_codec=v.get("codec_name"),
            pix_fmt=v.get("pix_fmt"),
            color_transfer=v.get("color_transfer"),
            color_primaries=v.get("color_primaries"),
            color_space=v.get("color_space"),
            field_order=v.get("field_order"),
            fps=_fraction(v.get("avg_frame_rate")),
            nb_frames=int(v["nb_frames"]) if str(v.get("nb_frames", "")).isdigit() else None,
            side_data=[s.get("side_data_type") for s in v.get("side_data_list") or []],
        )
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if audio:
        info.update(audio_codec=audio[0].get("codec_name"), channels=audio[0].get("channels"))
    return info


def decode_check(path: Path, size: int) -> dict[str, Any]:
    """Decodes `path` with ffmpeg and counts its errors: whole up to 300 MB, else its first and last 20 s."""
    spans = [[]] if size <= DECODE_CHECK_FULL_BYTES else [["-t", "20"], ["-sseof", "-20"]]
    errors: list[str] = []
    for span in spans:
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error"]
        cmd += span[:2] if span and span[0] == "-sseof" else []
        cmd += ["-i", str(path)]
        cmd += span if span and span[0] == "-t" else []
        cmd += ["-f", "null", "-"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        errors += [line for line in proc.stderr.splitlines() if line.strip()]
        if proc.returncode != 0:
            errors.append(f"ffmpeg exited {proc.returncode}")
    return {"decode_errors": len(errors), "decode_first_errors": errors[:5], "decode_whole": len(spans) == 1}


def _open_image(path: Path) -> Any:
    from PIL import Image

    with contextlib.suppress(ImportError):
        from pillow_heif import register_heif_opener

        register_heif_opener()
    return Image.open(path)


@contextlib.contextmanager
def _no_pixel_limit() -> Any:
    """Pillow's decompression-bomb limit off while a check reads an image, then back as it was."""
    from PIL import Image

    saved = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        yield
    finally:
        Image.MAX_IMAGE_PIXELS = saved


def describe_image(path: Path, *, decode: bool = True) -> dict[str, Any]:
    """An image's format, size as stored and as shown, frames, timing, alpha, profile and location carriers."""
    with _no_pixel_limit():
        return _describe_image(path, decode=decode)


def _describe_image(path: Path, *, decode: bool) -> dict[str, Any]:
    try:
        img = _open_image(path)
    except Exception as exc:  # noqa: BLE001
        return {"readable": False, "error": f"{type(exc).__name__}: {exc}"}
    with img:
        exif = img.getexif()
        orientation = int(exif.get(0x0112, 1) or 1)
        width, height = img.size
        frames = int(getattr(img, "n_frames", 1))
        info: dict[str, Any] = {
            "readable": True,
            "format": img.format,
            "mode": img.mode,
            "width": width,
            "height": height,
            "orientation": orientation,
            "display": [height, width] if orientation in (5, 6, 7, 8) else [width, height],
            "frames": frames,
            "icc": bool(img.info.get("icc_profile")),
            "gps": bool(exif.get_ifd(0x8825)) if exif else False,
            "xmp_gps": b"GPS" in (img.info.get("xmp") or b"")
            if isinstance(img.info.get("xmp"), bytes)
            else "GPS" in str(img.info.get("xmp") or ""),
            "loop": img.info.get("loop"),
        }
        durations = []
        transparent = "transparency" in img.info or img.mode in ("RGBA", "LA", "PA")
        if decode:
            try:
                for index in range(frames):
                    if frames > 1:
                        img.seek(index)
                        durations.append(int(img.info.get("duration") or 0))
                    img.load()
                    if index == 0 and transparent and width * height <= 40_000_000:
                        alpha = img.convert("RGBA").getchannel("A").getextrema()
                        info["alpha_range"] = list(alpha)
                info["decoded"] = True
            except Exception as exc:  # noqa: BLE001
                info["decoded"] = False
                info["decode_error"] = f"{type(exc).__name__}: {exc}"
        info["transparency"] = transparent
        if img.format == "WEBP" and frames > 1:
            durations = webp_durations(path) or durations
        if durations:
            info["duration_ms"] = sum(durations)
            info["min_frame_ms"] = min(durations)
        return info


def webp_durations(path: Path) -> list[int]:
    """Each frame's duration as an animated WebP stores it (its ANMF chunks), which Pillow's reader doesn't report for the last frame."""
    data = path.read_bytes()
    durations, offset = [], 12
    while offset + 8 <= len(data):
        tag, size = data[offset : offset + 4], int.from_bytes(data[offset + 4 : offset + 8], "little")
        if tag == b"ANMF" and size >= 16:
            durations.append(int.from_bytes(data[offset + 20 : offset + 23], "little"))
        offset += 8 + size + (size & 1)
    return durations


def describe_zip(path: Path) -> dict[str, Any]:
    """The names in a favicon pack and whether every image in it opens."""
    try:
        with zipfile.ZipFile(path) as pack:
            names = pack.namelist()
            sizes = {}
            for name in names:
                if name.endswith((".png", ".ico")):
                    with pack.open(name) as member:
                        data = io.BytesIO(member.read())
                    from PIL import Image

                    with Image.open(data) as img:
                        img.load()
                        sizes[name] = list(img.size)
            return {"readable": True, "names": names, "sizes": sizes}
    except Exception as exc:  # noqa: BLE001
        return {"readable": False, "error": f"{type(exc).__name__}: {exc}"}


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".ico", ".heic", ".avif"}


def describe_output(path: Path) -> dict[str, Any]:
    """What an output is: an image, a zip, or a video or audio file, decoded to check it."""
    size = path.stat().st_size
    info: dict[str, Any] = {"name_suffix": "".join(path.suffixes[-1:]).lower(), "bytes": size}
    suffix = path.suffix.lower()
    if suffix == ".zip":
        info.update(describe_zip(path))
    elif suffix in IMAGE_SUFFIXES and suffix not in (".mp4", ".webm"):
        info.update(describe_image(path))
    else:
        info.update(describe_media(path))
        if info.get("readable"):
            info.update(decode_check(path, size))
    return info


def describe_source(entry: CorpusEntry, path: Path) -> dict[str, Any]:
    """The source as the checks compare outputs with it."""
    if entry.kind == "image":
        return describe_image(path, decode=entry.animated)
    info = describe_media(path)
    if info.get("readable") and not info.get("duration"):
        with contextlib.suppress(Exception):
            import sashimi
            from sashimi.config import Limits

            info["duration"] = sashimi.probe(path, limits=Limits.unlimited()).duration
            info["duration_from"] = "sashimi.probe, unlimited"
    return info


def make_excerpt(src: Path, scratch: Path, entry: CorpusEntry) -> Path:
    """A lossless excerpt of a long video (60 s from a quarter in), made before the timer starts."""
    assert entry.duration is not None
    out = scratch / f"excerpt{src.suffix.lower() or '.mkv'}"
    start = entry.duration * 0.25
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(src),
            "-t",
            f"{STRETCH_SECONDS:g}",
            "-map",
            "0",
            "-c",
            "copy",
            str(out),
        ],
        check=True,
    )
    return out


def _error_record(exc: BaseException) -> dict[str, Any]:
    from sashimi.errors import UserInputError

    record: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": str(exc).splitlines()[0][:500] if str(exc) else "",
        "user_error": isinstance(exc, UserInputError),
    }
    if isinstance(exc, UserInputError):
        record["user_message"] = exc.user_message
        record["limit"] = getattr(exc, "limit", None)
    else:
        record["traceback"] = "".join(traceback.format_exception(exc))[-4000:]
    return record


def run_child(spec_path: Path) -> int:
    """
    Runs one case (the JSON spec at `spec_path`) in this process and writes its result next to the
    spec: the outcome, CPU seconds, peak memory, and a description of the source and of every output.
    """
    from scripts.bench.run import rusage

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    work = Path(spec["work"])
    out_dir = work / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    task = TASKS_BY_NAME[spec["task"]]
    entry = CorpusEntry(**{**spec["entry"], "categories": tuple(spec["entry"]["categories"])})
    source = Path(spec["input"])
    result: dict[str, Any] = {"input_used": "source"}
    if spec["capability"]:
        (work / "sashimi.toml").write_text(
            "[limits]\n" + "".join(f'{name} = "none"\n' for name in LIMIT_FIELDS), encoding="utf-8"
        )
    os.chdir(work)
    actual = source
    if task.setup(entry) == "excerpt":
        actual = make_excerpt(source, work, entry)
        result["input_used"] = f"lossless excerpt, {STRETCH_SECONDS:g} s from {entry.duration * 0.25:.1f} s"  # type: ignore[operator]

    import sashimi
    import sashimi.api._batch as batch
    from sashimi.cli.main import app
    from sashimi.config import Limits

    errors: list[dict[str, Any]] = []
    classify = batch.classify

    def recording_classify(error: Exception) -> tuple[str, str]:
        errors.append(_error_record(error))
        return classify(error)

    batch.classify = recording_classify
    limits = Limits.unlimited() if spec["capability"] else None
    stdout, stderr = io.StringIO(), io.StringIO()
    before = rusage()
    started = time.perf_counter()
    exit_code: int | None = None
    returned: dict[str, Any] | None = None
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            if task.runner == "probe":
                returned = sashimi.probe(actual, limits=limits).model_dump(mode="json")
            elif task.runner == "frames":
                returned = sashimi.image.frames_info(actual, limits=limits).model_dump(mode="json")
            elif task.runner == "metadata":
                returned = sashimi.metadata.read(actual, limits=limits).model_dump(mode="json")
            else:
                try:
                    app(args=cli_argv(task, entry, actual, out_dir), prog_name="sashimi")
                except SystemExit as stop:
                    exit_code = stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
    except Exception as exc:  # noqa: BLE001
        errors.append(_error_record(exc))
    wall = time.perf_counter() - started
    after = rusage()
    (work / "measured").touch()
    batch.classify = classify

    result.update(wall_s=round(wall, 3), exit_code=exit_code, errors=errors)
    if before is not None and after is not None:
        result.update(
            cpu_user_s=round(after[0] - before[0], 3),
            cpu_sys_s=round(after[1] - before[1], 3),
            cpu_s=round(after[0] - before[0] + after[1] - before[1], 3),
            python_rss_mb=round(after[2], 1),
            child_rss_mb=round(after[3], 1),
        )
    text = stdout.getvalue()
    result["stderr_tail"] = stderr.getvalue()[-2000:]
    if task.runner == "cli":
        report = _last_json(text)
        result["cli_report"] = _trim_report(report) if report is not None else None
        if report is None:
            result["stdout_tail"] = text[-2000:]
    elif returned is not None:
        result["returned"] = _trim_returned(returned)
    result["outputs"] = {
        str(path.relative_to(out_dir)): describe_output(path) for path in sorted(out_dir.rglob("*")) if path.is_file()
    }
    (work / "result.json").write_text(json.dumps(result, default=str), encoding="utf-8")
    return 0


def _last_json(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    if start < 0:
        return None
    with contextlib.suppress(json.JSONDecodeError):
        value = json.loads(text[start:])
        return value if isinstance(value, dict) else None
    return None


def _trim_report(report: dict[str, Any]) -> dict[str, Any]:
    """A CLI report without its long lists; ``info`` and ``metadata`` keep their own top-level fields."""
    if "results" not in report:
        keep = {k: v for k, v in report.items() if k not in ("streams", "tags", "frames", "frame_durations")}
        if "frames" in report:
            keep["frames_count"] = len(report["frames"]) if isinstance(report["frames"], list) else report["frames"]
        return keep
    return {
        "summary": report.get("summary"),
        "routes": report.get("routes"),
        "already_done": len(report.get("already_done") or []),
        "results": [{k: r.get(k) for k in ("status", "message", "output")} for r in (report.get("results") or [])[:5]],
    }


def _trim_returned(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k not in ("streams", "tags", "raw")}


def _descendants(root: int) -> list[int]:
    children: dict[int, list[int]] = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        with contextlib.suppress(OSError, ValueError, IndexError):
            fields = stat.read_text().rsplit(")", 1)[1].split()
            children.setdefault(int(fields[1]), []).append(int(stat.parent.name))
    found, todo = [], [root]
    while todo:
        pid = todo.pop()
        found.append(pid)
        todo.extend(children.get(pid, []))
    return found


def _rss_kb(pid: int) -> int:
    with contextlib.suppress(OSError, ValueError):
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return 0


class TreeMemory:
    """
    Samples the summed resident memory of a process and its descendants (Linux only) until stopped,
    or until the file `until` exists (the child makes it when the measured work ends, so the
    checks of its outputs aren't counted).
    """

    def __init__(self, pid: int, interval: float = 0.2, until: Path | None = None) -> None:
        self.pid, self.interval, self.peak_kb, self.until = pid, interval, 0, until
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.available = Path("/proc/self/status").is_file()

    def __enter__(self) -> TreeMemory:
        if self.available:
            self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self.available:
            self._thread.join()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.until is not None and self.until.exists():
                return
            total = sum(_rss_kb(pid) for pid in _descendants(self.pid))
            self.peak_kb = max(self.peak_kb, total)
            self._stop.wait(self.interval)

    @property
    def peak_mb(self) -> float | None:
        return round(self.peak_kb / 1024, 1) if self.available and self.peak_kb else None


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Stops `proc` and everything it started: its process group on POSIX, ``taskkill /T`` on Windows."""
    if hasattr(os, "killpg"):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30, check=False)
    with contextlib.suppress(OSError):
        proc.kill()


def execute(
    case: CorpusCase,
    entry: CorpusEntry,
    data_dir: Path,
    scratch: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Runs `case` in a child process with a timeout and returns its result, not yet assessed."""
    work = scratch.resolve() / re.sub(r"[^A-Za-z0-9._-]+", "_", case.key)[:200]
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    spec = {
        "task": case.task,
        "input": str((data_dir / entry.path).resolve()),
        "work": str(work),
        "capability": case.capability,
        "entry": asdict(entry),
    }
    spec_path = work / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    run_py = Path(__file__).resolve().parent / "run.py"
    started = time.perf_counter()
    with (work / "child.out").open("wb") as out, (work / "child.err").open("wb") as err:
        proc = subprocess.Popen(
            [sys.executable, str(run_py), "--corpus-child", str(spec_path)],
            stdout=out,
            stderr=err,
            start_new_session=True,
            env=env,
        )
        with TreeMemory(proc.pid, until=work / "measured") as memory:
            try:
                proc.wait(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc)
                proc.wait()
    wall = time.perf_counter() - started
    record: dict[str, Any] = {
        "task": case.task,
        "path": case.path,
        "mode": "capability" if case.capability else "default",
        "work": str(work),
        "child_wall_s": round(wall, 2),
        "peak_tree_rss_mb": memory.peak_mb,
    }
    result_path = work / "result.json"
    if timed_out:
        record.update(outcome="timeout", timeout_s=timeout)
    elif result_path.is_file():
        record.update(json.loads(result_path.read_text(encoding="utf-8")))
    else:
        record.update(
            outcome="crash",
            child_exit=proc.returncode,
            child_stderr=(work / "child.err").read_text(encoding="utf-8", errors="replace")[-3000:],
        )
    return record


def outcome(record: dict[str, Any]) -> str:
    """
    What happened, from the user's side: ``success``, ``already`` (nothing to do: already in that
    format or container), ``refused`` (a user-facing refusal), ``failed`` (an error sashimi didn't
    expect, a traceback, a crash) or ``timeout``.
    """
    if record.get("outcome") in ("timeout", "crash"):
        return str(record["outcome"])
    errors = record.get("errors") or []
    if any(not e.get("user_error") for e in errors):
        return "failed"
    report = record.get("cli_report")
    if record.get("exit_code") is None and "returned" in record:
        return "success"
    if errors:
        return "refused"
    if isinstance(report, dict) and "summary" in report:
        summary = report["summary"] or {}
        if summary.get("success"):
            return "success"
        if report.get("already_done"):
            return "already"
        results = report.get("results") or []
        if results and all(r.get("status") == "skipped" for r in results):
            return "refused"
        if results and any(r.get("status") == "error" for r in results):
            message = " ".join(str(r.get("message")) for r in results)
            return "failed" if message.startswith("Failed") else "refused"
        return "refused" if record.get("exit_code") else "already"
    if record.get("exit_code") in (0, None):
        return "success"
    text = f"{record.get('stderr_tail') or ''}{record.get('stdout_tail') or ''}"
    return "failed" if "Traceback" in text or not text.strip() else "refused"


def message(record: dict[str, Any]) -> str:
    """The message a user saw (the CLI's) or the exception's."""
    report = record.get("cli_report")
    if isinstance(report, dict):
        for item in report.get("results") or []:
            if item.get("status") != "success" and item.get("message"):
                return str(item["message"])
    errors = record.get("errors") or []
    if errors:
        return str(errors[0].get("user_message") or errors[0].get("message"))
    if record.get("exit_code") not in (0, None):
        return (record.get("stderr_tail") or record.get("stdout_tail") or "").strip()[-500:]
    return ""


def refused_by_limit(record: dict[str, Any]) -> bool:
    """True when a limit refused the case, so it is worth running again with every limit off."""
    if any(e.get("limit") for e in record.get("errors") or []):
        return True
    return outcome(record) == "refused" and "limit" in message(record).lower()


def _close(a: float, b: float, rel: float = 0.02, absolute: float = 0.25) -> bool:
    return abs(a - b) <= max(absolute, rel * max(abs(a), abs(b)))


def _main_output(record: dict[str, Any]) -> dict[str, Any] | None:
    outputs = record.get("outputs") or {}
    if not outputs:
        return None
    return max(outputs.values(), key=lambda o: o.get("bytes", 0))


def _expected_seconds(
    task: CorpusTask, entry: CorpusEntry, src: dict[str, Any], record: dict[str, Any]
) -> float | None:
    rule = task.expect.get("duration")
    total = src.get("duration") or entry.duration
    if str(record.get("input_used", "")).startswith("lossless excerpt"):
        total = STRETCH_SECONDS
    argv = task.argv(entry) if task.argv else []
    if "--duration" in argv and rule == "same":
        return float(argv[argv.index("--duration") + 1])
    if rule == "same":
        return float(total) if total else None
    if isinstance(rule, tuple) and rule[0] == "clip":
        return min(float(rule[1]), float(total)) if total else float(rule[1])
    if rule in ("section", "section-copy"):
        start, length = section(entry)
        return min(length, max(0.0, float(total) - start)) if total else length
    return None


def assess(task: CorpusTask, entry: CorpusEntry, src: dict[str, Any], record: dict[str, Any]) -> list[str]:
    """
    Checks a successful case's output against what the task should give and returns what looks
    wrong (empty when nothing does). These are leads for a person to look at, not verdicts: sizes
    and lengths have tolerances, and a rule can be wrong for an unusual file.
    """
    if outcome(record) != "success" or not task.outputs:
        return []
    out = _main_output(record)
    if out is None:
        return ["success reported but no output written"]
    if not out.get("readable", False):
        return [f"output unreadable: {out.get('error')}"]
    problems: list[str] = []
    exp = task.expect
    if out.get("decoded") is False:
        problems.append(f"output image doesn't decode: {out.get('decode_error')}")
    real_errors = [e for e in out.get("decode_first_errors") or [] if not any(b in e for b in BENIGN_DECODE)]
    if out.get("decode_errors") and real_errors:
        problems.append(f"{out['decode_errors']} decode error(s): {real_errors[0][:160]}")
    if "format" in exp and out.get("format") and out["format"] != exp["format"]:
        problems.append(f"format {out['format']}, expected {exp['format']}")
    if "container" in exp and out.get("container") and exp["container"] not in str(out["container"]):
        problems.append(f"container {out['container']}, expected {exp['container']}")
    if exp.get("zip"):
        names = out.get("names") or []
        if "favicon.ico" not in names:
            problems.append("favicon pack has no favicon.ico")
    if exp.get("audio_only") and out.get("streams") and set(out["streams"]) != {"audio"}:
        problems.append(f"audio output has streams {out['streams']}")
    shown = src.get("display")
    stored = [src.get("width"), src.get("height")]
    size = out.get("display") or ([out.get("width"), out.get("height")] if out.get("width") else None)
    if entry.kind == "image" and out.get("format"):
        size = out.get("display") if "orientation" in out else size
    dims = exp.get("dims")
    if size and shown and all(shown) and dims:
        w, h = size
        sw, sh = shown
        if dims == "display" and not (abs(w - sw) <= 2 and abs(h - sh) <= 2):
            if not (entry.kind == "video" and abs(w / h - sw / sh) <= 0.02 * (sw / sh)):
                problems.append(f"shown as {w}x{h}, source shows as {sw}x{sh}")
        elif dims == "swapped" and not (abs(w - sh) <= 2 and abs(h - sw) <= 2):
            if not (entry.kind == "video" and abs(w / h - sh / sw) <= 0.02 * (sh / sw)):
                problems.append(f"rotated output is {w}x{h}, expected {sh}x{sw}")
        elif dims == "square" and not (w == h and abs(w - min(sw, sh)) <= 2):
            problems.append(f"square crop is {w}x{h}, expected {min(sw, sh)}x{min(sw, sh)}")
        elif dims == "stored" and all(stored) and [out.get("width"), out.get("height")] != stored:
            problems.append(f"stored size {out.get('width')}x{out.get('height')}, source {stored[0]}x{stored[1]}")
        elif isinstance(dims, tuple) and dims[0] == "max_width":
            want_w = min(dims[1], sw)
            if abs(w - want_w) > 2 or abs(w / h - sw / sh) > 0.03 * (sw / sh):
                problems.append(f"{w}x{h}, expected width {want_w} at the source's shape {sw}x{sh}")
    if "width" in exp and out.get("width") and out["width"] != exp["width"]:
        problems.append(f"width {out['width']}, expected {exp['width']}")
    if "height" in exp and out.get("height"):
        want = exp["height"]
        if shown and all(shown):
            want = min(want, shown[1])
        if abs(out["height"] - want) > 2:
            problems.append(f"height {out['height']}, expected {want}")
    if exp.get("animated") and out.get("frames", 2) < 2:
        problems.append("animation has one frame")
    frames_rule = exp.get("frames")
    src_frames = src.get("frames") or entry.frames
    if frames_rule and out.get("frames") and src_frames > 1:
        if out["frames"] == 1:
            problems.append(f"animation of {src_frames} frames became a still")
        elif frames_rule == "same" and out["frames"] != src_frames and not out.get("duration_ms"):
            problems.append(f"{out['frames']} frames, source has {src_frames}")
        elif frames_rule == "half" and out["frames"] > math.ceil(src_frames / 2):
            problems.append(f"{out['frames']} frames, expected at most {math.ceil(src_frames / 2)} of {src_frames}")
    timing = exp.get("timing")
    src_ms = src.get("duration_ms")
    if timing and src_ms:
        got_ms = out.get("duration_ms") or (round(out["duration"] * 1000) if out.get("duration") else None)
        want_ms = src_ms if timing == "same" else src_ms / 2
        if got_ms is not None and abs(got_ms - want_ms) > max(60, 0.05 * want_ms):
            problems.append(f"plays {got_ms} ms, expected about {want_ms:.0f} ms")
    want_s = _expected_seconds(task, entry, src, record)
    got_s = out.get("duration")
    if got_s is None and out.get("duration_ms"):
        got_s = out["duration_ms"] / 1000
    if want_s and got_s is not None:
        rule = exp.get("duration")
        if rule == "section-copy":
            gap = float(entry.facts.get("max_keyframe_gap") or entry.duration or 0)
            if got_s < want_s - 0.25 or got_s > want_s + gap + 0.5:
                problems.append(f"lasts {got_s:.2f} s, expected {want_s:.2f} s (+ up to {gap:.1f} s to a keyframe)")
        elif not _close(got_s, want_s, rel=0.03, absolute=0.3):
            problems.append(f"lasts {got_s:.2f} s, expected {want_s:.2f} s")
    if exp.get("preview") and (src.get("duration") or 0) >= 12:
        played = out.get("duration_ms")
        if played is not None and abs(played - 10_000) > 250:
            problems.append(f"preview plays {played} ms, expected 10000 (5 clips x 2 s)")
    if exp.get("no_location"):
        if out.get("gps") or out.get("xmp_gps") or out.get("location_tags"):
            problems.append("location still present after strip")
    if exp.get("streams") == "same" and src.get("streams") and out.get("streams"):
        if sorted(s for s in out["streams"] if s != "data") != sorted(
            s for s in src["streams"] if s not in ("data", "attachment")
        ):
            problems.append(f"streams {out['streams']}, source {src['streams']}")
    return problems


def normalised_costs(entry: CorpusEntry, record: dict[str, Any]) -> dict[str, float]:
    """CPU seconds per input second, per input megapixel and per input frame, where they make sense."""
    cpu = record.get("cpu_s")
    if not cpu:
        return {}
    costs: dict[str, float] = {}
    width, height = entry.facts.get("width"), entry.facts.get("height")
    if entry.duration:
        seconds = entry.duration
        if str(record.get("input_used", "")).startswith("lossless excerpt"):
            seconds = STRETCH_SECONDS
        task = TASKS_BY_NAME[record["task"]]
        argv = task.argv(entry) if task.argv else []
        if "--duration" in argv:
            seconds = float(argv[argv.index("--duration") + 1])
        costs["cpu_s_per_input_s"] = round(cpu / seconds, 4)
        costs["input_s_per_cpu_s"] = round(seconds / cpu, 3)
    if width and height:
        megapixels = width * height / 1e6
        costs["cpu_s_per_megapixel"] = round(cpu / megapixels, 4)
        if entry.kind == "image" and entry.frames > 1:
            costs["cpu_s_per_frame"] = round(cpu / entry.frames, 5)
    return costs


SCHEMA = "sashimi.corpus-benchmark.v1"


def report_path(results_dir: Path, machine: str, date: str, version: str, tag: str | None) -> Path:
    suffix = f"_{tag}" if tag else ""
    return results_dir / machine / f"{date}_sashimi-{version}_corpus{suffix}.json"


def resume_path(results_dir: Path, machine: str, version: str, tag: str | None) -> Path | None:
    """This machine's newest corpus report for `version` and `tag`, whatever day it started."""
    suffix = f"_{tag}" if tag else ""
    folder = results_dir / machine
    if not folder.is_dir():
        return None
    pattern = re.compile(r"\d{4}-\d{2}-\d{2}" + re.escape(f"_sashimi-{version}_corpus{suffix}.json"))
    found = sorted(p for p in folder.iterdir() if pattern.fullmatch(p.name))
    return found[-1] if found else None


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts of each outcome per task (default limits only), and the CPU seconds spent."""
    by_task: dict[str, dict[str, int]] = {}
    for record in results:
        if record.get("mode") != "default":
            continue
        counts = by_task.setdefault(record["task"], {})
        counts[record["result"]] = counts.get(record["result"], 0) + 1
        if record.get("suspects"):
            counts["suspect"] = counts.get("suspect", 0) + 1
    return {
        "cases": len(results),
        "by_task": by_task,
        "total_cpu_s": round(sum(r.get("cpu_s") or 0 for r in results), 1),
    }


def render_markdown(report: dict[str, Any]) -> str:
    """A short Markdown view of a corpus report: outcomes per task, then every case that needs a look."""
    env = report["environment"]
    lines = [
        f"# sashimi corpus benchmark — `{env['machine']}`",
        "",
        f"sashimi {env['sashimi_version']} (git {env['git']['commit']}, dirty={env['git']['dirty']}), "
        f"{env['ffmpeg_version']}, Python {env['python_version']}, Pillow {env['pillow_version']}.",
        f"Started {env['timestamp_utc']}; power at start {json.dumps(env.get('power_start'))}, "
        f"at end {json.dumps(env.get('power_end'))}.",
        f"Status: {report['status']}; {report['summary']['cases']} cases, {report['summary']['total_cpu_s']} CPU s.",
        "",
        "| Task | success | already | refused | failed | timeout | crash | suspect |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for task, counts in report["summary"]["by_task"].items():
        cells = [
            str(counts.get(k, 0)) for k in ("success", "already", "refused", "failed", "timeout", "crash", "suspect")
        ]
        lines.append(f"| `{task}` | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Cases to look at",
        "",
        "| Task | File | Mode | Result | CPU s | Note |",
        "|---|---|---|---|---:|---|",
    ]
    for r in report["results"]:
        if r["result"] == "success" and not r.get("suspects"):
            continue
        note = "; ".join(r.get("suspects") or []) or r.get("message", "")
        note = note.replace("|", "/").replace("\n", " ")[:300]
        lines.append(f"| {r['task']} | {r['path']} | {r['mode']} | {r['result']} | {r.get('cpu_s') or ''} | {note} |")
    return "\n".join(lines) + "\n"


def _save(report: dict[str, Any], path: Path) -> None:
    from scripts.bench.run import _atomic_write_text

    report["summary"] = summarise(report["results"])
    _atomic_write_text(path, json.dumps(report, indent=1, default=str) + "\n")
    _atomic_write_text(path.with_suffix(".md"), render_markdown(report))


def _prune(work: Path, keep_bytes: int) -> None:
    """Deletes a case's outputs larger than `keep_bytes`, and any excerpt it made."""
    for path in work.glob("excerpt*"):
        path.unlink()
    for path in (work / "out").rglob("*"):
        if path.is_file() and path.stat().st_size > keep_bytes:
            path.unlink()


def main(args: Any) -> int:
    """Runs the corpus benchmark described by the parsed ``run.py`` arguments."""
    from scripts.bench.run import collect_environment, detect_machine_id

    if args.ffmpeg_dir:
        os.environ["PATH"] = f"{Path(args.ffmpeg_dir).expanduser()}{os.pathsep}{os.environ.get('PATH', '')}"
    manifest = Path(args.corpus)
    extra = [Path(p) for p in args.corpus_extra or []]
    entries = load_corpus(manifest, *extra)
    by_path = {e.path: e for e in entries}
    data_dir = manifest.resolve().parent
    cases = select_cases(entries, task_glob=args.task, path_glob=args.clip)
    if args.list:
        for case in cases:
            print(f"{case.task:22s} {case.path}")
        print(f"{len(cases)} cases over {len(entries)} files", file=sys.stderr)
        return 0
    if not cases:
        print("No corpus cases matched the given filters.", file=sys.stderr)
        return 2
    results_dir = Path(args.results_dir) if args.results_dir else data_dir / "bench-results"
    scratch = (Path(args.scratch_dir) if args.scratch_dir else data_dir / "bench-scratch").resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    machine = args.machine or detect_machine_id()
    env = collect_environment(machine, 0)
    env["default_workers"] = "CLI default"
    env["ffmpeg_path"] = shutil.which("ffmpeg")
    env["power_start"] = power_state()
    env["corpus"] = {"manifest": manifest.name, "files": len(entries), "timeout_s": args.timeout}
    path = report_path(results_dir, machine, env["timestamp_utc"][:10], env["sashimi_version"], args.tag)
    report: dict[str, Any] = {"schema": SCHEMA, "status": "running", "environment": env, "results": []}
    resumed = resume_path(results_dir, machine, env["sashimi_version"], args.tag) if args.resume else None
    if resumed is not None:
        prior = json.loads(resumed.read_text(encoding="utf-8"))
        for key in ("python_version", "pillow_version", "ffmpeg_version", "git"):
            if prior["environment"].get(key) != env.get(key):
                print(f"Warning: resuming a run made with a different {key}.", file=sys.stderr)
        prior["environment"].setdefault("resumed", []).append({"at": env["timestamp_utc"], "power": env["power_start"]})
        report, path = prior, resumed
        report["status"] = "running"
    done = {f"{r['task']}|{r['path']}|{r['mode']}" for r in report["results"]}
    sources: dict[str, dict[str, Any]] = {}
    keep_bytes = int(args.keep_outputs_mb * 1_000_000)
    queue = list(cases)
    print(f"{len(queue)} corpus cases, results to {path}", file=sys.stderr)
    index = 0
    while index < len(queue):
        case = queue[index]
        index += 1
        if case.key in done:
            continue
        entry = by_path[case.path]
        task = TASKS_BY_NAME[case.task]
        if case.path not in sources:
            sources[case.path] = report.setdefault("sources", {}).get(case.path) or describe_source(
                entry, data_dir / entry.path
            )
            report["sources"][case.path] = sources[case.path]
        record = execute(case, entry, data_dir, scratch, timeout=args.timeout, env=dict(os.environ))
        record["result"] = outcome(record)
        record["message"] = message(record)
        record["suspects"] = assess(task, entry, sources[case.path], record)
        record["costs"] = normalised_costs(entry, record)
        record["input_bytes"] = entry.size
        record["categories"] = list(entry.categories)
        record["role"] = entry.role
        _prune(Path(record["work"]), keep_bytes)
        report["results"].append(record)
        done.add(case.key)
        if not case.capability and not args.no_capability and refused_by_limit(record):
            queue.insert(index, CorpusCase(case.task, case.path, capability=True))
        flag = f" SUSPECT: {'; '.join(record['suspects'])[:160]}" if record["suspects"] else ""
        print(
            f"[{index}/{len(queue)}] {case.task} {case.path} ({record['mode']}): {record['result']} "
            f"cpu={record.get('cpu_s')} rss={record.get('peak_tree_rss_mb')}MB{flag}",
            file=sys.stderr,
        )
        if record["result"] != "success" and record["message"]:
            print(f"    {record['message'][:240]}", file=sys.stderr)
        _save(report, path)
    report["status"] = "completed"
    report["environment"]["power_end"] = power_state()
    report["environment"]["timestamp_end_utc"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    _save(report, path)
    print(f"Saved {path} and {path.with_suffix('.md')}", file=sys.stderr)
    return 0
