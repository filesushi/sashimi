# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
What an output kept of its source: whether the picture or sound is identical, and what else was
dropped or removed. Every processing function returns it (as ``.fidelity`` on its result).

The statement comes from what happened, not from a table of formats: the encoder actually used,
the source's and the output's metadata and tracks as sashimi reads them, and, for images saved
without loss, the decoded pixels of the output compared with the source's.
"""

import logging
import os
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, field_validator

from sashimi.core._metaread import clean_text

__all__ = [
    "Fidelity",
    "FidelityLevel",
    "Outcome",
    "Inventory",
    "fidelity",
    "image_inventory",
    "media_fidelity",
    "media_inventory",
    "derived",
    "differences",
    "same_picture",
    "same_animation",
    "shown_length",
]

FidelityLevel = Literal["lossless", "content_lossless", "lossy"]
MAX_QUOTED = 80
"""The most characters of a file's own text (a track's handler name) that one label quotes."""
MAX_ENTRY = 500
"""The most characters one entry of a :class:`Fidelity` list holds."""
MAX_ENTRIES = 20
"""The most entries one :class:`Fidelity` list holds; one more says how many were left out."""
logger = logging.getLogger("sashimi")

Content = Literal["picture", "sound", "picture and sound"]


def one_line(text: str, limit: int = MAX_ENTRY) -> str:
    """`text` clean of control characters, on one line, at most `limit` characters. Pure."""
    return " ".join(clean_text(text).split())[:limit]


def quoted_label(text: str) -> str:
    """A file's own name for something, fit to quote in a :class:`Fidelity` entry. Pure."""
    return one_line(text, MAX_QUOTED)


def and_list(items: Sequence[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``. Pure."""
    items = list(items)
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


class Fidelity(BaseModel):
    """
    What an output kept of its source, at one of three levels:

    - ``lossless``: the picture or sound is identical and nothing else sashimi can see was
      dropped (anything removed as asked is listed in ``removed``);
    - ``content_lossless``: the picture or sound is identical, but something else was dropped
      (``lost``: a colour profile, tags, tracks, animation), or sashimi can't check everything
      else the source holds (``checked`` is False), so it claims no more than the picture or
      sound;
    - ``lossy``: the picture or sound itself changed (``changed``: re-encoded lossily, scaled,
      tone-mapped, reduced to 256 colours or to 8 bits, frames dropped).
    """

    model_config = ConfigDict(frozen=True)

    level: FidelityLevel
    """``lossless``, ``content_lossless`` or ``lossy``, as above."""
    content: Content
    """What the output's content is: ``picture``, ``sound`` or ``picture and sound``."""
    changed: list[str] = []
    """How the picture or sound changed, for people: ``re-encoded as H.264 (CRF 23)``, ``tone-mapped from HDR (PQ)``."""
    lost: list[str] = []
    """What the output couldn't keep, for people: ``colour profile``, ``chapters``, ``animation (20 frames)``."""
    removed: list[str] = []
    """What was left out as asked (``metadata``, ``keep_icc``, ``animated``, ``mute``): ``location``, ``sound``."""
    adjusted: list[str] = []
    """What was set as asked without changing the picture or sound, for people: ``display rotation set to 90° clockwise``."""
    kept: list[str] = []
    """
    What a metadata removal left in place because showing the file needs it, for people:
    ``orientation (turned 90° clockwise)``, ``colour profile (Display P3)``, ``resolution (72 dpi)``.
    """
    notes: list[str] = []
    """Sentences explaining ``lost``, such as why a copy dropped a track and which format keeps it."""
    checked: bool = True
    """
    False when the source may hold data sashimi can't read (vendor boxes in MP4 and MOV, HEIC
    items, unknown chunks), so it doesn't claim that everything besides the content was kept.
    """

    @field_validator("changed", "lost", "removed", "adjusted", "kept", "notes")
    @classmethod
    def _bounded(cls, entries: list[str]) -> list[str]:
        """Whatever a file's text put in an entry, every entry is clean, on one line and capped, and so is the list."""
        kept = [one_line(entry) for entry in entries[:MAX_ENTRIES]]
        return [*kept, f"{len(entries) - MAX_ENTRIES} more"] if len(entries) > MAX_ENTRIES else kept

    @property
    def text(self) -> str:
        """
        The statement in a few words, for a result line: ``lossless``, ``picture identical;
        colour profile dropped``, ``lossy: saved as JPEG at quality 85; location removed as asked``.
        """
        if self.level == "lossy":
            parts = [f"lossy: {', '.join(self.changed)}" if self.changed else "lossy"]
        elif self.level == "lossless":
            parts = ["lossless"]
        else:
            parts = [f"{self.content} identical"]
        parts += self.adjusted
        if self.lost:
            parts.append(f"{and_list(self.lost)} dropped")
        if self.removed:
            parts.append(f"{and_list(self.removed)} removed as asked")
        if self.kept:
            parts.append(f"kept: {and_list(self.kept)}")
        text = "; ".join(parts)
        if self.notes:
            text += ". " + " ".join(self.notes).rstrip(".")
        return text


class Outcome(BaseModel):
    """The result of a processing function: what the output kept of its source (:class:`Fidelity`)."""

    model_config = ConfigDict(frozen=True)

    fidelity: Fidelity
    """What the output kept of its source."""


def _unique(items: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def fidelity(
    content: Content,
    *,
    changed: Iterable[str] = (),
    lost: Iterable[str] = (),
    removed: Iterable[str] = (),
    notes: Iterable[str] = (),
    checked: bool = True,
    adjusted: Iterable[str] = (),
    kept: Iterable[str] = (),
) -> Fidelity:
    """A :class:`Fidelity`, its level worked out from what changed, what was lost and what was checked. Pure."""
    changed, lost, removed = _unique(changed), _unique(lost), _unique(removed)
    if changed:
        level: FidelityLevel = "lossy"
    elif lost or not checked:
        level = "content_lossless"
    else:
        level = "lossless"
    return Fidelity(
        level=level,
        content=content,
        changed=changed,
        lost=lost,
        removed=removed,
        adjusted=_unique(adjusted),
        kept=_unique(kept),
        notes=_unique(notes),
        checked=checked,
    )


UNCHECKED = "sashimi couldn't check this output against its source"
"""What ``Fidelity.changed`` or ``notes`` says when the check after writing an output failed."""


def checked_after_writing(content: Content, check: Callable[[], Fidelity], *, identical: bool = False) -> Fidelity:
    """
    The result of `check`, which runs once an output is written. The output stands whatever the
    check finds, so a failed check is reported rather than raised: as ``lossy`` and unchecked,
    since nothing can be claimed for it, or ``content_lossless`` when the route copies the picture
    or sound as they are (`identical`).
    """
    try:
        return check()
    except Exception as e:
        logger.warning(f"{UNCHECKED}: {type(e).__name__}: {e}")
        if identical:
            return fidelity(content, notes=[UNCHECKED], checked=False)
        return fidelity(content, changed=[UNCHECKED], checked=False)


@dataclass
class Inventory:
    """
    What a file holds besides its picture or sound, as names for people (``EXIF``, ``colour
    profile``, ``chapters``, ``2 audio tracks``), with ``metadata`` naming those that are metadata
    (which ``remove_all`` removes) and ``checked`` saying whether the reader saw everything.
    """

    items: list[str] = field(default_factory=list)
    metadata: set[str] = field(default_factory=set)
    location: bool = False
    checked: bool = True
    labels: dict[str, str] = field(default_factory=dict)
    hidden_picture: bool = False

    def add(self, name: str, *, metadata: bool = False, label: str | None = None) -> None:
        """Adds `name`; `label` is how people see it when it differs (``animation (20 frames)`` for ``animation``)."""
        if name not in self.items:
            self.items.append(name)
        if metadata:
            self.metadata.add(name)
        if label is not None:
            self.labels[name] = label

    def label(self, name: str) -> str:
        return self.labels.get(name, name)


LOCATION = "location"
COLOUR_PROFILE = "colour profile"
_CARRIER_NAMES = {
    "EXIF": "EXIF",
    "GPS": None,
    "XMP": "XMP",
    "extended XMP": "extended XMP",
    "IPTC": "IPTC",
    "Photoshop": "Photoshop data",
    "comment": "comments",
    "PNG text": "PNG text",
}
_CHECKED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "GIF", "TIFF", "BMP"})
_PNG_KNOWN = frozenset(
    {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"iCCP", b"acTL", b"fcTL"}
    | {b"fdAT", b"gAMA", b"cHRM", b"sRGB", b"pHYs"}
)
_PNG_NAMES = {b"tIME": "modification time", b"bKGD": "background colour", b"sBIT": "significant bits"}
_WEBP_KNOWN = frozenset({b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF", b"ICCP", b"EXIF", b"XMP "})
_JPEG_STRUCTURAL = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCC})
_JPEG_STRUCTURAL |= frozenset({0xCD, 0xCE, 0xCF, 0xDA, 0xDB, 0xDD, 0xD8, 0xD9})


HIDDEN_PICTURE = "embedded thumbnail"
"""A picture stored in a file's metadata, which no re-encoded output keeps."""
EXTRA_IMAGES = "extra images (such as a gain map)"
"""Pictures a phone adds to show the main one: a gain map or a preview, which go with most phone photos."""
ANOTHER_PICTURE = "another picture (such as a stereo pair's other eye)"
"""A picture in its own right: a stereo pair's other eye, a panorama's or multi-angle set's other shots."""


def _add_extra_pictures(data: bytes, inventory: Inventory) -> None:
    """Names what an MPF segment lists after the main picture, by the type it states and whether the file has a gain map."""
    from sashimi.core._imagemeta import mpf_extra_kinds

    kinds = mpf_extra_kinds(data)
    if "shows the main picture" in kinds or not kinds:
        inventory.add(EXTRA_IMAGES)
    if "a picture of its own" in kinds:
        inventory.add(ANOTHER_PICTURE)


def _jpeg_structure(data: bytes, inventory: Inventory) -> None:
    from sashimi.core._imagemeta import jpeg_pieces, motion_video, tail_summary

    pieces, trailer = jpeg_pieces(data)
    for marker, segment in pieces:
        if marker is None or marker in _JPEG_STRUCTURAL or 0xD0 <= marker <= 0xD7:
            continue
        payload = segment[4:]
        if marker == 0xE0 and payload.startswith((b"JFIF\0", b"JFXX\0")):
            continue
        if marker == 0xE1 and payload.startswith((b"Exif\0", b"http://ns.adobe.com/")):
            continue
        if marker == 0xE2 and payload.startswith(b"ICC_PROFILE\0"):
            continue
        if marker == 0xE2 and payload.startswith(b"MPF\0"):
            _add_extra_pictures(data, inventory)
            continue
        if marker == 0xED and payload.startswith(b"Photoshop 3.0\0"):
            continue
        if marker == 0xEE and payload.startswith(b"Adobe"):
            continue
        if marker == 0xFE:
            continue
        name = f"APP{marker - 0xE0}" if 0xE0 <= marker <= 0xEF else f"marker 0x{marker:02X}"
        inventory.add(f"other data ({name})", metadata=True)
    if trailer.strip(b"\0"):
        stated = b"MotionPhoto" in data or b"MicroVideo" in data
        motion = stated and (motion_video(data) is not None or tail_summary(data)[1])
        inventory.add("motion photo video" if motion else "data after the picture")


def _png_structure(data: bytes, inventory: Inventory) -> None:
    from sashimi.core._imagemeta import png_chunks

    for kind, _, _ in png_chunks(data):
        if kind in _PNG_KNOWN:
            continue
        name = _PNG_NAMES.get(kind, f"other data ({kind.decode('latin-1', 'replace')} chunk)")
        inventory.add(name, metadata=True)


def _webp_structure(data: bytes, inventory: Inventory) -> None:
    from sashimi.core._imagemeta import webp_chunks

    for fourcc, _, _ in webp_chunks(data):
        if fourcc not in _WEBP_KNOWN:
            inventory.add(f"other data ({fourcc.decode('latin-1', 'replace').strip()} chunk)", metadata=True)


def _gif_structure(data: bytes, inventory: Inventory) -> None:
    from sashimi.core._imagemeta import GIF_XMP_ID, gif_blocks

    for kind, start, _ in gif_blocks(data):
        if kind != "extension":
            continue
        label = data[start + 1]
        if label in (0xF9, 0xFE):
            continue
        if label == 0xFF:
            app = data[start + 3 : start + 3 + data[start + 2]] if start + 3 <= len(data) else b""
            if app in (b"NETSCAPE2.0", b"ANIMEXTS1.0", GIF_XMP_ID):
                continue
            inventory.add(f"other data ({app[:8].decode('latin-1', 'replace')})", metadata=True)
        else:
            inventory.add("other data (GIF text)", metadata=True)


def _stated_resolution(img: Image.Image) -> str | None:
    """The resolution the image states outside EXIF (see :func:`~sashimi.core._imagemeta.stated_dpi`), as ``72x72``."""
    from sashimi.core._imagemeta import stated_dpi

    dpi = stated_dpi(img)
    if dpi is None:
        return None
    x, y = (round(value) for value in dpi)
    return f"{x}x{y}" if x > 0 or y > 0 else None


def image_inventory(path: str | os.PathLike[str], *, limits: Any = None) -> Inventory:
    """
    What the image at `path` holds besides its pixels: its metadata carriers (as
    :func:`sashimi.metadata.read` finds them), colour profile, resolution, gamma, frames or pages,
    extra images and anything else in the file's structure. Decodes no pixels.
    """
    from sashimi.core._imagemeta import holds_hidden_picture, location_shown, read_carriers
    from sashimi.core._animation import frame_count
    from sashimi.core.image import _open_image
    from sashimi.core.metadata import _image_info

    inventory = Inventory()
    with _open_image(path, limits, load=False) as img:
        fmt = str(img.format)
        carriers = read_carriers(path, img)
        info = _image_info(path, img, carriers)
        looked_at = location_shown(carriers)
        shown = info.has_location and (looked_at == carriers or _image_info(path, img, looked_at).has_location)
        inventory.hidden_picture = holds_hidden_picture(carriers)
        frames = frame_count(img)
        dpi = _stated_resolution(img)
        colour_tags = any(key in img.info for key in ("gamma", "chromaticity", "srgb"))
        inventory.checked = fmt in _CHECKED_IMAGE_FORMATS
        if frames > 1 and fmt == "TIFF":
            inventory.add("pages", label=f"{frames - 1} more {'page' if frames == 2 else 'pages'}")
        elif frames > 1 and fmt != "MPO":
            inventory.add("animation", label=f"animation ({frames} frames)")
    for name in info.carriers:
        label = _CARRIER_NAMES.get(name, name)
        if label:
            inventory.add(label, metadata=True)
    if shown:
        inventory.add(LOCATION, metadata=True)
        inventory.location = True
    if info.has_color_profile:
        inventory.add(COLOUR_PROFILE)
    if dpi:
        inventory.add(f"resolution {dpi}", metadata=True, label="resolution (DPI)")
    if colour_tags:
        inventory.add("gamma and colour-space tags")
    if inventory.checked:
        with open(os.fspath(path), "rb") as f:
            data = f.read()
        try:
            if fmt == "JPEG":
                _jpeg_structure(data, inventory)
            elif fmt == "PNG":
                _png_structure(data, inventory)
            elif fmt == "WEBP":
                _webp_structure(data, inventory)
            elif fmt == "GIF":
                _gif_structure(data, inventory)
        except (ValueError, IndexError):
            inventory.checked = False
    elif fmt == "MPO":
        with open(os.fspath(path), "rb") as f:
            _add_extra_pictures(f.read(), inventory)
    return inventory


_TAG_FAMILIES = frozenset({"ID3", "Vorbis comments", "RIFF INFO", "Matroska tags", "QuickTime/MP4 tags", "tags"})
_CHECKED_CONTAINERS = frozenset({"matroska", "webm"})


def _count(number: int, singular: str) -> str:
    if number == 1:
        return singular
    return f"{number} {singular.replace('another ', 'more ').removeprefix('a ').removeprefix('an ')}s"


def media_inventory(path: str | os.PathLike[str]) -> Inventory:
    """
    What the video or audio file at `path` holds besides the first video and audio streams' pictures
    and sound: further tracks (by kind), tags, chapters, cover art, data tracks, location, display
    rotation, HDR and Dolby Vision. ``checked`` is True only for Matroska and WebM, whose every
    element ffprobe shows; MP4, MOV and other containers can hold vendor data it doesn't.
    """
    from sashimi.core.metadata import _media_info, _probe_full
    from sashimi.core.probe import parse_ffprobe_json

    data = _probe_full(path)
    info = parse_ffprobe_json(data)
    meta = _media_info(data)
    inventory = Inventory(checked=bool(info.container_names & _CHECKED_CONTAINERS))
    streams = [s for s in info.streams if not s.attached_pic]
    videos = [s for s in streams if s.type == "video"]
    audios = [s for s in streams if s.type == "audio"]
    subtitles = [s for s in streams if s.type == "subtitle"]
    attachments = [s for s in streams if s.type == "attachment"]
    if len(videos) > 1:
        inventory.add(_count(len(videos) - 1, "another video track"))
    if len(audios) > 1:
        inventory.add(_count(len(audios) - 1, "another audio track"))
    if subtitles:
        inventory.add("subtitles")
    if attachments:
        inventory.add(_count(len(attachments), "an attached file"))
    for carrier in meta.carriers:
        if carrier in _TAG_FAMILIES:
            inventory.add("tags", metadata=True)
        elif carrier == "timed metadata track":
            continue
        else:
            inventory.add(carrier, metadata=True)
    from sashimi.core.video import _track_name

    seen: dict[str, int] = {}
    for stream in info.streams:
        if stream.type == "data" and not stream.chapter_track:
            name = _track_name(stream).removeprefix("the ")
            seen[name] = seen.get(name, 0) + 1
            inventory.add(f"{name} {seen[name]}", metadata=True, label=name)
    if meta.has_location:
        inventory.add(LOCATION, metadata=True)
        inventory.location = True
    if info.rotation:
        inventory.add("display rotation")
    if any(_dolby_vision(stream) for stream in data.get("streams", [])):
        inventory.add("Dolby Vision settings")
    return inventory


def _dolby_vision(stream: dict[str, Any]) -> bool:
    return stream.get("codec_type") == "video" and any(
        str(side.get("side_data_type", "")).startswith("DOVI") for side in stream.get("side_data_list", [])
    )


def reencode_losses(source: Inventory, output: Inventory) -> list[str]:
    """What a re-encoded output left out with nothing in the inventories to say so: a picture hidden in metadata it kept. Pure."""
    kept_metadata = any(name in output.items for name in ("EXIF", "XMP", "Photoshop data", "PNG text"))
    return [HIDDEN_PICTURE] if source.hidden_picture and kept_metadata and not output.hidden_picture else []


def differences(
    source: Inventory,
    output: Inventory,
    *,
    removed_metadata: Literal["keep", "remove_location", "remove_all"] = "keep",
    removed: Iterable[str] = (),
    kept: Iterable[str] = (),
) -> tuple[list[str], list[str]]:
    """
    What `output` lacks of `source`: ``(lost, removed)``. What the ``metadata`` mode
    `removed_metadata` removes (location for ``remove_location``, every metadata item for
    ``remove_all``) and the names in `removed` count as removed as asked; names in `kept` are
    taken as kept whatever the output shows (a re-encode's picture carries its own rotation). Pure.
    """
    kept_names = set(kept)
    pending = [name for name in removed if name not in kept_names and name not in output.items]
    asked: list[str] = []
    lost: list[str] = []
    for name in source.items:
        if name in output.items or name in kept_names or source.label(name) in kept_names:
            continue
        label = source.label(name)
        if name in pending or label in pending:
            pending.remove(name if name in pending else label)
            asked.append(label)
        elif removed_metadata == "remove_all" and name in source.metadata:
            asked.append(label)
        elif removed_metadata == "remove_location" and name == LOCATION:
            asked.append(label)
        else:
            lost.append(label)
    asked += pending
    if source.location and removed_metadata != "keep" and LOCATION not in asked and not output.location:
        asked.append(LOCATION)
    return counted(lost), counted(asked)


def counted(labels: Iterable[str]) -> list[str]:
    """Labels with repeats counted once: three ``Apple metadata track`` become ``3 Apple metadata tracks``. Pure."""
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    words = []
    for label, count in counts.items():
        if count == 1:
            words.append(label)
            continue
        head, found, rest = label.removeprefix("a ").partition(" track")
        words.append(f"{count} {head} tracks{rest}" if found else f"{label} ({count})")
    return words


def _comparable(img: Image.Image, high: bool) -> Image.Image:
    if high:
        return img if img.mode == "I" else img.convert("I")
    rgba = img if img.mode == "RGBA" else img.convert("RGBA")
    alpha = rgba.getchannel("A")
    if alpha.getextrema()[0] == 0:
        black = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
        rgba = Image.composite(rgba, black, alpha.point(lambda a: 255 if a else 0))
    return rgba


_HIGH = frozenset({"I;16", "I;16B", "I;16L", "I;16N", "I", "F"})
_BAND = 512


def same_picture(source: Image.Image, output: Image.Image) -> bool:
    """
    Whether two pictures show the same pixels: the same size and, compared as 8-bit RGBA (or as
    32-bit integers when either has more than 8 bits per sample, or as CMYK when both are), the
    same values; pixels that are fully transparent in both count as the same whatever their
    colour. Compared in bands, so memory stays small.
    """
    if source.size != output.size:
        return False
    if (source.mode == "CMYK") != (output.mode == "CMYK"):
        return False
    high = source.mode in _HIGH or output.mode in _HIGH
    width, height = source.size
    for top in range(0, height, _BAND):
        box = (0, top, width, min(height, top + _BAND))
        a, b = source.crop(box), output.crop(box)
        if source.mode == "CMYK":
            if a.tobytes() != b.tobytes():
                return False
            continue
        try:
            if "F" in (a.mode, b.mode):
                a, b = a.convert("F"), b.convert("F")
            else:
                a, b = _comparable(a, high), _comparable(b, high)
        except ValueError:
            return False
        if a.tobytes() != b.tobytes():
            return False
    return True


def _distinct(frames: Iterable[Image.Image]) -> Iterator[Image.Image]:
    last: Image.Image | None = None
    for frame in frames:
        if last is None or not same_picture(last, frame):
            yield frame
        last = frame


def same_animation(source: Iterable[Image.Image], output: Image.Image) -> bool:
    """
    Whether an animation's frames (`output`, opened) show the same pictures, in the same order,
    as `source` (full RGBA frames as they should appear), compared as :func:`same_picture` does.
    Runs of identical frames count once on both sides, since encoders may merge them (libwebp
    does); timing is compared separately (:func:`shown_length`).
    """
    from sashimi.core._animation import frames_of

    theirs = _distinct(frame for _, frame, _ in frames_of(output))
    for frame in _distinct(source):
        other = next(theirs, None)
        if other is None or not same_picture(frame, other):
            return False
    return next(theirs, None) is None


def shown_length(img: Image.Image) -> float:
    """
    How long an animation plays in browsers, in milliseconds (frames under 20 ms count as 100 ms).
    Pillow states a WebP frame's duration only once the frame is loaded, so WebP frames are.
    """
    from sashimi.core._animation import frame_count, shown_ms

    total = 0.0
    for index in range(frame_count(img)):
        img.seek(index)
        if img.format == "WEBP":
            img.load()
        total += shown_ms(int(img.info.get("duration") or 0))
    return total


def derived(
    input_path: str | os.PathLike[str],
    content: Content,
    changed: Iterable[str],
    *,
    media: bool = False,
    notes: Iterable[str] = (),
) -> Fidelity:
    """
    The statement for an output made from its source rather than converted from it (an icon, a
    contact sheet, a preview): always lossy, as `changed` says, with the source's metadata
    named as dropped when it has any.
    """
    try:
        source = media_inventory(input_path) if media else image_inventory(input_path)
    except Exception:
        source = Inventory(checked=False)
    lost = ["the source's metadata"] if source.metadata else []
    return fidelity(content, changed=changed, lost=lost, notes=notes)


def media_fidelity(
    input_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    content: Content,
    *,
    changed: Iterable[str] = (),
    removed_metadata: Literal["keep", "remove_location", "remove_all"] = "keep",
    removed: Iterable[str] = (),
    kept: Iterable[str] = (),
    notes: Iterable[str] = (),
    lost: Iterable[str] = (),
    adjusted: Iterable[str] = (),
) -> Fidelity:
    """
    What a video or audio output kept of its source: `changed` (how the picture or sound was
    re-encoded or reduced), and the source's and the output's tracks, tags and chapters as
    ffprobe shows them (:func:`media_inventory`), compared as :func:`differences` does.
    """
    source = media_inventory(input_path)
    output = media_inventory(output_path)
    missing, asked = differences(source, output, removed_metadata=removed_metadata, removed=removed, kept=kept)
    return fidelity(
        content,
        changed=changed,
        lost=[*missing, *lost],
        removed=asked,
        notes=notes,
        checked=source.checked,
        adjusted=adjusted,
    )
