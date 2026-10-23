# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
GIF palettes built in a first pass, so no GIF route holds every frame in memory.

The first pass runs the frames through ``palettegen`` and writes the palette, one small PNG, to
stdout; each frame's time goes to stderr for progress (``-progress`` reports nothing until an
output writes, and ``palettegen`` writes only at the end). The second pass reads it back as a second input, inline as a
``data:`` URI, and runs ``paletteuse``. No palette file is written anywhere, so none can be left
behind, and stdin stays free for routes that pipe their frames in. The output is byte-identical
to the one-pass ``split,palettegen,paletteuse`` graph for the same settings.
"""

import base64

from sashimi.core._ffmpeg import FRAME_PROGRESS, OUTPUT_FLAGS, ProgressCallback

PALETTE_SHARE = 1 / 3
"""The part of a two-pass GIF's progress given to the first pass."""

PALETTE_STAGE = "analysing"


def palette_output(filters: str, stats_mode: str) -> list[str]:
    """Pass 1's filter and output: `filters`, then the palette as one PNG on stdout."""
    graph = f"{FRAME_PROGRESS},palettegen=stats_mode={stats_mode}"
    return [
        "-vf",
        f"{filters},{graph}" if filters else graph,
        "-frames:v",
        "1",
        *OUTPUT_FLAGS,
        "-f",
        "image2pipe",
        "-c:v",
        "png",
        "pipe:1",
    ]


def palette_input(palette: bytes) -> list[str]:
    """The palette PNG from pass 1 as pass 2's second input, inline, so no file is needed."""
    encoded = base64.b64encode(palette).decode("ascii")
    return [
        "-protocol_whitelist",
        "data",
        "-format_whitelist",
        "png_pipe",
        "-f",
        "png_pipe",
        "-i",
        f"data:image/png;base64,{encoded}",
    ]


def palette_use(filters: str, paletteuse: str) -> list[str]:
    """Pass 2's graph: `filters` on the first input, then ``paletteuse`` with the second."""
    head = f"[0:v]{filters}[frames];[frames]" if filters else "[0:v]"
    return ["-filter_complex", f"{head}[1:v]paletteuse={paletteuse}[gif]", "-map", "[gif]"]


def one_pass(filters: str, stats_mode: str, paletteuse: str) -> str:
    """The one-pass graph, which holds every frame until the palette is built."""
    head = f"{filters},split[a][b]" if filters else "split[a][b]"
    return f"{head};[a]palettegen=stats_mode={stats_mode}[p];[b][p]paletteuse={paletteuse}"


def scaled(on_progress: ProgressCallback | None, start: float, share: float) -> ProgressCallback | None:
    """`on_progress` for one pass, mapped onto its part of the file's progress."""
    if on_progress is None:
        return None

    def report(fraction: float, stage: str) -> None:
        on_progress(start + share * fraction, stage)

    return report
