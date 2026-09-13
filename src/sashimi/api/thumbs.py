import pathlib
from typing import Iterator, List, Union, Dict, Any, Optional, Tuple

from sashimi.config import resolve_config
from sashimi.file.thumbs import generate_contact_sheets as _generate_contact_sheets
from sashimi.file.thumbs import YieldType


def make_thumbs_generator(
    paths: Union[str, pathlib.Path, List[Union[str, pathlib.Path]]],
    recursive: bool = False,
    force: bool = False,
    output_width: Optional[int] = None,
    gap: Optional[int] = None,
    cols: Optional[int] = None,
    rows: Optional[int] = None,
    start_offset_percent: Optional[float] = None,
    end_offset_percent: Optional[float] = None,
    jpeg_quality: Optional[int] = None,
    grid_bg_color: Optional[Tuple[int, int, int]] = None,
    header_height_ratio: Optional[float] = None,
    header_font_path: Optional[str] = None,
    header_font_size: Optional[int] = None,
    header_bg_color: Optional[Tuple[int, int, int]] = None,
    header_text_color: Optional[Tuple[int, int, int]] = None,
    timestamp_font_path: Optional[str] = None,
    timestamp_font_size: Optional[int] = None,
    timestamp_bg_color: Optional[Tuple[int, int, int, int]] = None,
    timestamp_text_color: Optional[Tuple[int, int, int]] = None,
    video_extensions: Optional[List[str]] = None,
    mediainfo_template: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Iterator[YieldType]:
    """
    Generate contact sheets for video files as a generator.
    Yields the total file count first, followed by status tuples.
    """
    if not isinstance(paths, list):
        paths = [paths]
    resolved_paths = [pathlib.Path(p).resolve() for p in paths]

    # Explicitly map provided arguments to overrides, dropping None values
    overrides: Dict[str, Any] = {}
    if output_width is not None:
        overrides["output_width"] = output_width
    if gap is not None:
        overrides["gap"] = gap
    if cols is not None:
        overrides["cols"] = cols
    if rows is not None:
        overrides["rows"] = rows
    if start_offset_percent is not None:
        overrides["start_offset_percent"] = start_offset_percent
    if end_offset_percent is not None:
        overrides["end_offset_percent"] = end_offset_percent
    if jpeg_quality is not None:
        overrides["jpeg_quality"] = jpeg_quality
    if grid_bg_color is not None:
        overrides["grid_bg_color"] = grid_bg_color
    if header_height_ratio is not None:
        overrides["header_height_ratio"] = header_height_ratio
    if header_font_path is not None:
        overrides["header_font_path"] = header_font_path
    if header_font_size is not None:
        overrides["header_font_size"] = header_font_size
    if header_bg_color is not None:
        overrides["header_bg_color"] = header_bg_color
    if header_text_color is not None:
        overrides["header_text_color"] = header_text_color
    if timestamp_font_path is not None:
        overrides["timestamp_font_path"] = timestamp_font_path
    if timestamp_font_size is not None:
        overrides["timestamp_font_size"] = timestamp_font_size
    if timestamp_bg_color is not None:
        overrides["timestamp_bg_color"] = timestamp_bg_color
    if timestamp_text_color is not None:
        overrides["timestamp_text_color"] = timestamp_text_color
    if video_extensions is not None:
        overrides["video_extensions"] = video_extensions
    if mediainfo_template is not None:
        overrides["mediainfo_template"] = mediainfo_template

    config = resolve_config(cli_overrides=overrides)

    return _generate_contact_sheets(paths=resolved_paths, config=config.thumbs, recursive=recursive, force=force)


def make_thumbs(
    paths: Union[str, pathlib.Path, List[Union[str, pathlib.Path]]],
    recursive: bool = False,
    force: bool = False,
    output_width: Optional[int] = None,
    gap: Optional[int] = None,
    cols: Optional[int] = None,
    rows: Optional[int] = None,
    start_offset_percent: Optional[float] = None,
    end_offset_percent: Optional[float] = None,
    jpeg_quality: Optional[int] = None,
    grid_bg_color: Optional[Tuple[int, int, int]] = None,
    header_height_ratio: Optional[float] = None,
    header_font_path: Optional[str] = None,
    header_font_size: Optional[int] = None,
    header_bg_color: Optional[Tuple[int, int, int]] = None,
    header_text_color: Optional[Tuple[int, int, int]] = None,
    timestamp_font_path: Optional[str] = None,
    timestamp_font_size: Optional[int] = None,
    timestamp_bg_color: Optional[Tuple[int, int, int, int]] = None,
    timestamp_text_color: Optional[Tuple[int, int, int]] = None,
    video_extensions: Optional[List[str]] = None,
    mediainfo_template: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Synchronously generate contact sheets and return a summary list.
    Perfect for Jupyter notebooks and scripts.

    Returns:
        A list of dictionaries containing 'file', 'status', and 'message'.
    """
    gen = make_thumbs_generator(
        paths=paths,
        recursive=recursive,
        force=force,
        output_width=output_width,
        gap=gap,
        cols=cols,
        rows=rows,
        start_offset_percent=start_offset_percent,
        end_offset_percent=end_offset_percent,
        jpeg_quality=jpeg_quality,
        grid_bg_color=grid_bg_color,
        header_height_ratio=header_height_ratio,
        header_font_path=header_font_path,
        header_font_size=header_font_size,
        header_bg_color=header_bg_color,
        header_text_color=header_text_color,
        timestamp_font_path=timestamp_font_path,
        timestamp_font_size=timestamp_font_size,
        timestamp_bg_color=timestamp_bg_color,
        timestamp_text_color=timestamp_text_color,
        video_extensions=video_extensions,
        mediainfo_template=mediainfo_template,
    )

    try:
        next(gen)
    except StopIteration:
        return []

    results = []
    for item in gen:
        if isinstance(item, tuple) and len(item) == 3:
            file_path, status, message = item
            if status != "starting":
                results.append({"file": str(file_path), "status": status, "message": message})

    return results
