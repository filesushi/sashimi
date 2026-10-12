"""Static and animated contact sheets."""

from sashimi.config import AnimatedGifSheetConfig, AnimatedWebpSheetConfig, SheetConfig
from sashimi.core._atomic import PathLike
from sashimi.core._ffmpeg import ProgressCallback
from sashimi.core.sheets.animated import render_animated
from sashimi.core.sheets.static import render_still

__all__ = ["render"]


def render(
    input_path: PathLike,
    output_path: PathLike,
    config: SheetConfig,
    *,
    display_name: str | None = None,
    header_font: PathLike | None = None,
    timestamp_font: PathLike | None = None,
    emoji_dir: PathLike | None = None,
    max_workers: int | None = None,
    scratch_dir: PathLike | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    Renders a contact sheet: a header (name, size, duration, dimensions) above a grid of tiles
    with timestamps. Tiles are stills, or with an animated config, short looping clips. The
    config's model (see :data:`~sashimi.config.CONTACT_SHEET`) sets the output format.

    `display_name` replaces the input's file name in the header. `header_font` and
    `timestamp_font` are local TrueType/OpenType files (default: the first common system font
    found, else Pillow's built-in one). With `emoji_dir`, a folder of Twemoji-style PNGs such as
    ``1f3ac.png``, and the ``emoji`` extra, emoji in the header are drawn from it; nothing is
    downloaded. These are server paths, so they are arguments rather than config options.

    Runs up to `max_workers` single-threaded ffmpeg processes at once (default min(4, CPUs)), plus
    one encoder for animated sheets. Raises MediaTooShortError for videos shorter than a second (or
    than one clip), and MediaTooLargeError for sheets over the size limits or videos too wide for
    tiles of :data:`~sashimi.config.MIN_TILE_SIZE` pixels.
    """
    if isinstance(config, (AnimatedWebpSheetConfig, AnimatedGifSheetConfig)):
        render_animated(
            input_path,
            output_path,
            config,
            display_name=display_name,
            header_font=header_font,
            timestamp_font=timestamp_font,
            emoji_dir=emoji_dir,
            max_workers=max_workers,
            scratch_dir=scratch_dir,
            on_progress=on_progress,
        )
    else:
        render_still(
            input_path,
            output_path,
            config,
            display_name=display_name,
            header_font=header_font,
            timestamp_font=timestamp_font,
            emoji_dir=emoji_dir,
            max_workers=max_workers,
            scratch_dir=scratch_dir,
            on_progress=on_progress,
        )
