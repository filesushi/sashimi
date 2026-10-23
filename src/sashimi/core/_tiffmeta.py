# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Editing TIFF structures (TIFF files and EXIF blocks) without touching image data. Tags are
removed by writing a new directory at the end and zeroing the old one and the removed values,
so every offset the kept tags and the strips use stays valid.
"""

import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass

TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
EXIF_IFD = 0x8769
GPS_IFD = 0x8825
INTEROP_IFD = 0xA005
ORIENTATION = 0x0112
SUB_IFDS = frozenset({EXIF_IFD, GPS_IFD, INTEROP_IFD})
THUMBNAIL_OFFSET = 0x0201
THUMBNAIL_LENGTH = 0x0202
PIXEL_X = 0xA002
PIXEL_Y = 0xA003
_MAX_ENTRIES = 4096
_MAX_IFDS = 4096


class TiffStructureError(ValueError):
    """The bytes aren't a TIFF structure this module can edit (damaged, or BigTIFF)."""


@dataclass
class Entry:
    tag: int
    type: int
    count: int
    raw: bytes
    position: int

    @property
    def size(self) -> int:
        return TYPE_SIZES.get(self.type, 1) * self.count


class TiffEditor:
    """A TIFF structure being edited: read directories, drop tags, append values and directories."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 8 or data[:2] not in (b"II", b"MM"):
            raise TiffStructureError("not a TIFF structure")
        self.order = "<" if data[:2] == b"II" else ">"
        self.buf = bytearray(data)
        magic = self.u16(2)
        if magic == 43:
            raise TiffStructureError("BigTIFF")
        if magic != 42:
            raise TiffStructureError(f"TIFF magic {magic}")

    def u16(self, at: int) -> int:
        return int(struct.unpack_from(self.order + "H", self.buf, at)[0])

    def u32(self, at: int) -> int:
        return int(struct.unpack_from(self.order + "I", self.buf, at)[0])

    def put_u32(self, at: int, value: int) -> None:
        struct.pack_into(self.order + "I", self.buf, at, value)

    @property
    def first(self) -> int:
        return self.u32(4)

    def directory(self, offset: int) -> tuple[list[Entry], int, int]:
        """The entries of the directory at `offset`, its next-directory offset and its length in bytes."""
        if offset < 8 or offset + 2 > len(self.buf):
            raise TiffStructureError(f"directory offset {offset} outside the data")
        count = self.u16(offset)
        if count > _MAX_ENTRIES or offset + 2 + 12 * count > len(self.buf):
            raise TiffStructureError(f"directory at {offset} runs past the data")
        entries = []
        for index in range(count):
            at = offset + 2 + 12 * index
            tag, kind = struct.unpack_from(self.order + "HH", self.buf, at)
            entries.append(Entry(tag, kind, self.u32(at + 4), bytes(self.buf[at + 8 : at + 12]), at))
        end = offset + 2 + 12 * count
        following = self.u32(end) if end + 4 <= len(self.buf) else 0
        return entries, following, 2 + 12 * count + 4

    def chain(self) -> list[int]:
        """The offsets of the main directories (pages, or IFD0 and the thumbnail's IFD1 of EXIF)."""
        offsets: list[int] = []
        offset = self.first
        while offset and len(offsets) < _MAX_IFDS:
            if offset in offsets:
                raise TiffStructureError("directory loop")
            offsets.append(offset)
            _, offset, _ = self.directory(offset)
        return offsets

    def value_offset(self, entry: Entry) -> int | None:
        """Where an entry's value is, or None when it is stored in the entry itself."""
        if entry.size <= 4:
            return None
        return int(struct.unpack(self.order + "I", entry.raw)[0])

    def value(self, entry: Entry) -> bytes:
        offset = self.value_offset(entry)
        if offset is None:
            return entry.raw[: entry.size]
        return bytes(self.buf[offset : offset + entry.size])

    def pointer(self, entry: Entry) -> int:
        """An entry's single number (a SHORT, LONG or IFD offset)."""
        if entry.type in (3, 8):
            return int(struct.unpack(self.order + "H", entry.raw[:2])[0])
        return int(struct.unpack(self.order + "I", entry.raw)[0])

    def zero(self, start: int, length: int) -> None:
        start = max(0, min(start, len(self.buf)))
        end = max(start, min(start + length, len(self.buf)))
        self.buf[start:end] = bytes(end - start)

    def erase_value(self, entry: Entry) -> None:
        offset = self.value_offset(entry)
        if offset is not None and offset >= 8:
            self.zero(offset, entry.size)

    def erase_directory(self, offset: int, seen: set[int] | None = None) -> None:
        """Zeroes a sub-directory, its values and the sub-directories it points to."""
        seen = set() if seen is None else seen
        if offset in seen:
            return
        seen.add(offset)
        try:
            entries, _, length = self.directory(offset)
        except TiffStructureError:
            return
        for entry in entries:
            if entry.tag in SUB_IFDS:
                self.erase_directory(self.pointer(entry), seen)
            self.erase_value(entry)
        self.zero(offset, length)

    def append(self, payload: bytes) -> int:
        if len(self.buf) % 2:
            self.buf.append(0)
        offset = len(self.buf)
        self.buf += payload
        return offset

    def entry(self, tag: int, kind: int, value: bytes) -> tuple[int, int, int, bytes]:
        """A new entry for `value` (bytes in the file's byte order), appending it when it doesn't fit inline."""
        count = len(value) // TYPE_SIZES.get(kind, 1)
        if len(value) <= 4:
            raw = value.ljust(4, b"\0")
        else:
            raw = struct.pack(self.order + "I", self.append(value))
        return tag, kind, count, raw

    def write_directory(self, entries: Iterable[tuple[int, int, int, bytes]], following: int) -> int:
        rows = sorted(entries, key=lambda row: row[0])
        payload = struct.pack(self.order + "H", len(rows))
        for tag, kind, count, raw in rows:
            payload += struct.pack(self.order + "HHI", tag, kind, count) + raw
        payload += struct.pack(self.order + "I", following)
        return self.append(payload)

    def patch_short_or_long(self, entry: Entry, value: int) -> None:
        if entry.count != 1:
            return
        if entry.type == 3 and value <= 0xFFFF:
            struct.pack_into(self.order + "H", self.buf, entry.position + 8, value)
        elif entry.type == 4:
            struct.pack_into(self.order + "I", self.buf, entry.position + 8, value)

    def rewrite(
        self,
        offset: int,
        keep: Callable[[Entry], bool],
        replace: dict[int, tuple[int, bytes]] | None = None,
        add: Iterable[tuple[int, int, int, bytes]] = (),
    ) -> int | None:
        """
        Rewrites the directory at `offset` with the entries `keep` accepts, the `replace`d values
        (tag: type and bytes) and the `add`ed entries (tag, type, count, value field), zeroing
        what is dropped. Returns the new directory's offset, or None when nothing changed (the
        directory stays where it is).
        """
        replace = replace or {}
        added = list(add)
        entries, following, length = self.directory(offset)
        dropped = [entry for entry in entries if not keep(entry) or entry.tag in replace]
        if not dropped and not added:
            return None
        rows = []
        for entry in entries:
            if entry in dropped:
                if entry.tag in SUB_IFDS:
                    self.erase_directory(self.pointer(entry))
                self.erase_value(entry)
                if entry.tag in replace and keep(entry):
                    kind, value = replace[entry.tag]
                    rows.append(self.entry(entry.tag, kind, value))
            else:
                rows.append((entry.tag, entry.type, entry.count, entry.raw))
        self.zero(offset, length)
        return self.write_directory([*rows, *added], following)

    def relink(self, old: int, new: int) -> None:
        """Points whatever pointed at directory `old` (the header or the previous directory) at `new`."""
        if self.first == old:
            self.put_u32(4, new)
            return
        for offset in self.chain():
            entries, following, length = self.directory(offset)
            if following == old:
                self.put_u32(offset + length - 4, new)
                return


def _sub_directory(editor: TiffEditor, entries: list[Entry], tag: int) -> int | None:
    for entry in entries:
        if entry.tag == tag:
            return editor.pointer(entry)
    return None


def edit_exif(
    data: bytes,
    *,
    drop_gps: bool = False,
    drop_thumbnail: bool = False,
    reset_orientation: bool = False,
    size: tuple[int, int] | None = None,
) -> bytes:
    """
    An EXIF block (a TIFF structure, without the ``Exif\\0\\0`` prefix) with the GPS directory
    removed, the thumbnail (IFD1) removed, the orientation set to 1 and the pixel dimensions set to
    `size`, as asked. Everything else, maker notes included, keeps its bytes and offsets.
    """
    editor = TiffEditor(data)
    first = editor.first
    entries, following, length = editor.directory(first)
    for entry in entries:
        if entry.tag == ORIENTATION and reset_orientation:
            editor.patch_short_or_long(entry, 1)
    if size is not None:
        exif_ifd = _sub_directory(editor, entries, EXIF_IFD)
        if exif_ifd is not None:
            try:
                sub, _, _ = editor.directory(exif_ifd)
            except TiffStructureError:
                sub = []
            for entry in sub:
                if entry.tag == PIXEL_X:
                    editor.patch_short_or_long(entry, size[0])
                elif entry.tag == PIXEL_Y:
                    editor.patch_short_or_long(entry, size[1])
    if drop_thumbnail and following:
        try:
            thumb, _, thumb_length = editor.directory(following)
        except TiffStructureError:
            thumb, thumb_length = [], 0
        values = {entry.tag: entry for entry in thumb}
        start, count = values.get(THUMBNAIL_OFFSET), values.get(THUMBNAIL_LENGTH)
        if start is not None and count is not None:
            editor.zero(editor.pointer(start), editor.pointer(count))
        for entry in thumb:
            editor.erase_value(entry)
        if thumb_length:
            editor.zero(following, thumb_length)
        editor.put_u32(first + length - 4, 0)
    if drop_gps:
        new = editor.rewrite(first, lambda entry: entry.tag != GPS_IFD)
        if new is not None:
            editor.put_u32(4, new)
    return bytes(editor.buf)


def gps_directory(data: bytes) -> bool:
    """True if an EXIF block has a GPS directory."""
    try:
        editor = TiffEditor(data)
        entries, _, _ = editor.directory(editor.first)
    except (TiffStructureError, struct.error):
        return False
    return any(entry.tag == GPS_IFD for entry in entries)


_DISPLAY_TAGS = frozenset({ORIENTATION, 0x011A, 0x011B, 0x0128, 0x0213})
"""IFD0 tags of an EXIF block that only say how to show the picture: orientation, resolution, YCbCr positioning."""


def exif_orientation(data: bytes) -> int | None:
    """The orientation an EXIF block records (1-8), if any."""
    try:
        editor = TiffEditor(data)
        entries, _, _ = editor.directory(editor.first)
    except (TiffStructureError, struct.error):
        return None
    for entry in entries:
        if entry.tag == ORIENTATION and entry.type == 3:
            value = editor.pointer(entry)
            return value if 1 <= value <= 8 else None
    return None


def exif_is_trivial(data: bytes) -> bool:
    """True if an EXIF block holds nothing but how to show the picture (orientation, resolution)."""
    try:
        editor = TiffEditor(data)
        entries, following, _ = editor.directory(editor.first)
    except (TiffStructureError, struct.error):
        return False
    return not following and all(entry.tag in _DISPLAY_TAGS for entry in entries)


def orientation_only(orientation: int, order: str = "<") -> bytes:
    """An EXIF block holding only an orientation."""
    head = (b"II" if order == "<" else b"MM") + struct.pack(order + "HI", 42, 8)
    return (
        head
        + struct.pack(order + "HHHI", 1, ORIENTATION, 3, 1)
        + struct.pack(order + "HH", orientation, 0)[:4]
        + struct.pack(order + "I", 0)
    )
