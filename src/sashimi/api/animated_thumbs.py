import time
import pathlib
from typing import Iterator, List, Union, Dict, Any, Optional, Tuple, Literal

from sashimi.config import resolve_config
from sashimi.file.animated_thumbs import generate_animated_thumbs as _generate_animated_thumbs
from sashimi.file.animated_thumbs import YieldType


def make_animated_thumbs_generator(
    paths: Union[str, pathlib.Path, List[Union[str, pathlib.Path]]],
    recursive: bool = False,
    force: bool = False,
    output_format: Optional[Literal["webp", "gif"]] = None,
    output_width: Optional[int] = None,
    gap: Optional[int] = None,
    cols: Optional[int] = None,
    rows: Optional[int] = None,
    sample_duration: Optional[float] = None,
    output_fps: Optional[int] = None,
    loop: Optional[bool] = None,
    start_offset_percent: Optional[float] = None,
    end_offset_percent: Optional[float] = None,
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
    Generate animated contact sheets (WebP/GIF) as a generator.
    Yields the total file count first, followed by status tuples.
    """
    if not isinstance(paths, list):
        paths = [paths]
    resolved_paths = [pathlib.Path(p).resolve() for p in paths]

    overrides: Dict[str, Any] = {}
    if output_format is not None:
        overrides["output_format"] = output_format
    if output_width is not None:
        overrides["output_width"] = output_width
    if gap is not None:
        overrides["gap"] = gap
    if cols is not None:
        overrides["cols"] = cols
    if rows is not None:
        overrides["rows"] = rows
    if sample_duration is not None:
        overrides["sample_duration"] = sample_duration
    if output_fps is not None:
        overrides["output_fps"] = output_fps
    if loop is not None:
        overrides["loop"] = loop
    if start_offset_percent is not None:
        overrides["start_offset_percent"] = start_offset_percent
    if end_offset_percent is not None:
        overrides["end_offset_percent"] = end_offset_percent
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

    # Inject the overrides directly under the new configuration section alias
    config = resolve_config(cli_overrides={"animated_thumbs": overrides})

    return _generate_animated_thumbs(
        paths=resolved_paths, config=config.animated_thumbs, recursive=recursive, force=force
    )


def make_animated_thumbs(
    paths: Union[str, pathlib.Path, List[Union[str, pathlib.Path]]],
    recursive: bool = False,
    force: bool = False,
    show_progress: bool = True,  # <-- NEW
    output_format: Optional[Literal["webp", "gif"]] = None,
    output_width: Optional[int] = None,
    gap: Optional[int] = None,
    cols: Optional[int] = None,
    rows: Optional[int] = None,
    sample_duration: Optional[float] = None,
    output_fps: Optional[int] = None,
    loop: Optional[bool] = None,
    start_offset_percent: Optional[float] = None,
    end_offset_percent: Optional[float] = None,
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
) -> Dict[str, Any]:  # <-- NEW return signature
    """
    Synchronously generate animated contact sheets and return a summary payload.
    Perfect for Jupyter notebooks and scripts.
    """
    start_time = time.perf_counter()

    gen = make_animated_thumbs_generator(
        paths=paths,
        recursive=recursive,
        force=force,
        output_format=output_format,
        output_width=output_width,
        gap=gap,
        cols=cols,
        rows=rows,
        sample_duration=sample_duration,
        output_fps=output_fps,
        loop=loop,
        start_offset_percent=start_offset_percent,
        end_offset_percent=end_offset_percent,
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
        total_files = next(gen)
    except StopIteration:
        total_files = 0
        
    results = []
    counts = {"success": 0, "error": 0, "exists": 0, "skipped": 0}
    
    pbar = None
    if show_progress and total_files > 0:
        try:
            from tqdm.auto import tqdm
            pbar = tqdm(total=total_files, desc="Generating Animated Thumbs")
        except ImportError:
            pass  # Fail gracefully if tqdm isn't installed in the environment

    try:
        for item in gen:
            if isinstance(item, tuple) and len(item) == 3:
                file_path, status, message = item
                
                if status == "starting":
                    if pbar:
                        # Update the progress bar text without advancing the counter
                        pbar.set_postfix_str(f"Processing: {file_path.name[:25]}")
                    continue
                
                # It's a final state (success, error, exists, skipped)
                counts[status] = counts.get(status, 0) + 1
                results.append({
                    "file": str(file_path),
                    "status": status,
                    "message": message
                })
                
                # Only advance the bar if it was an actively processed file
                if pbar and status not in ("skipped", "exists"):
                    pbar.update(1)
    finally:
        if pbar:
            pbar.close()
            
    time_taken = time.perf_counter() - start_time
    
    return {
        "summary": {
            "total_found": total_files,
            "success": counts["success"],
            "exists": counts["exists"],
            "skipped": counts["skipped"],
            "errors": counts["error"],
            "time_taken_seconds": round(time_taken, 2),
            "avg_time_seconds": round(time_taken / counts["success"], 2) if counts["success"] > 0 else 0.0
        },
        "results": results
    }
