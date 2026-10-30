# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""File kinds, judged by extension: what batch searches pick up, and how plans count files."""

import pathlib

VIDEO_EXTENSIONS = (
    ".mp4", ".avi", ".mpg", ".ogm", ".wmv", ".asf", ".mkv", ".mov", ".m4v", ".flv", ".ts", ".webm", ".mpeg", ".3gp",
    ".mk3d", ".m2ts", ".mts", ".3g2", ".vob", ".ogv", ".divx", ".f4v", ".y4m",
)  # fmt: skip
IMAGE_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif",
    ".jfif", ".jpe", ".jif", ".apng", ".avif", ".ico", ".tga", ".psd",
)  # fmt: skip
AUDIO_EXTENSIONS = (
    ".mp3", ".wav", ".ogg", ".opus", ".oga", ".flac", ".m4a", ".aac", ".weba", ".mka", ".m4b", ".mp2",
    ".aiff", ".aif", ".aifc", ".ac3", ".eac3", ".dts", ".amr", ".caf", ".au", ".wv", ".wma", ".m4r",
)  # fmt: skip

KINDS = {"image": ("image", "images"), "video": ("video", "videos"), "audio": ("audio file", "audio files")}
"""Each kind's name in the singular and plural, as plans and prompts count them."""


def kind_of(path: pathlib.PurePath) -> str | None:
    """``image``, ``video`` or ``audio`` by `path`'s extension, or None for anything else."""
    suffix = path.suffix.lower()
    for kind, extensions in (("image", IMAGE_EXTENSIONS), ("video", VIDEO_EXTENSIONS), ("audio", AUDIO_EXTENSIONS)):
        if suffix in extensions:
            return kind
    return None


def count_text(counts: dict[str, int]) -> str:
    """Counts by kind in words: ``44 videos and 23 images``."""
    parts = []
    order = list(KINDS)
    for kind, number in sorted(counts.items(), key=lambda item: order.index(item[0]) if item[0] in order else 9):
        singular, plural = KINDS.get(kind, ("file", "files"))
        parts.append(f"{number} {singular if number == 1 else plural}")
    if len(parts) <= 1:
        return "".join(parts) or "no files"
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def unsupported(command: str, accepts: str, path: pathlib.PurePath) -> str:
    """Why `command` skipped `path`, naming what it accepts: ``thumbs works on videos, not .jpg files``."""
    what = f"{path.suffix} files" if path.suffix else "files without an extension"
    return f"{command} works on {accepts}, not {what}."
