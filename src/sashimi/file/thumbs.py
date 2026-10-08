import logging
import pathlib
import sys
import subprocess
import tempfile
import concurrent.futures
import os
from typing import Iterator, Optional, List, Tuple, Dict, Any

import numpy as np
import ffmpeg  # type: ignore
from PIL import Image

from sashimi.config.schemas.thumbs import StaticThumbsConfig
from sashimi.file._sheet import (
    BaseSheetGenerator,
    MetaDataDict,
    ResultTuple,
    StatusTuple,
    YieldType,
    atomic_output,
    discover_videos,
    get_grid_dimensions,
    is_video_file,
    _format_duration,
    _format_size,
    _load_font,
)

logger = logging.getLogger("sashimi")


def _extract_single_frame_task(
    timestamp: float, temp_dir: pathlib.Path, file_path: pathlib.Path, thumb_w: int, thumb_h: int
) -> Optional[Tuple[pathlib.Path, float]]:
    output_filename = temp_dir / f"frame_{timestamp:.3f}.jpg"
    try:
        # 'V:0' is the first real video stream; it excludes embedded cover art (attached pictures).
        process_stream = (
            ffmpeg.input(str(file_path), ss=timestamp)["V:0"]
            .filter("scale", thumb_w, thumb_h)
            .filter("setsar", 1)
            .output(str(output_filename), vframes=1, format="image2", vcodec="mjpeg", qscale=2)
            .global_args("-nostdin")
            .overwrite_output()
        )

        cmd_args = ffmpeg.compile(process_stream)

        logger.debug(f"Running FFmpeg: {' '.join(cmd_args)}")

        # Fix: Explicitly type the kwargs dictionary so mypy knows it's safe to mutate
        run_kwargs: Dict[str, Any] = {"capture_output": True}
        if sys.platform == "win32":
            run_kwargs["creationflags"] = 0x00000040

        # Fix: Ignore the dynamic unpacking type error on subprocess
        subprocess.run(cmd_args, **run_kwargs)  # type: ignore

        if not output_filename.exists() or output_filename.stat().st_size == 0:
            return None
        return output_filename, timestamp
    except Exception:
        return None


# --- Public API ---
def generate_contact_sheets(
    paths: List[pathlib.Path], config: StaticThumbsConfig, recursive: bool = False, force: bool = False
) -> Iterator[YieldType]:
    """Yields total count of valid files first, then ResultTuples."""
    sorted_files, rejected = discover_videos(paths, config.video_extensions, recursive)
    yield len(sorted_files)

    yield from rejected

    for file_path in sorted_files:
        yield (file_path, "starting", "Processing...")
        yield (file_path, *_process_file(file_path, config, force=force))


def _process_file(file_path: pathlib.Path, config: StaticThumbsConfig, force: bool) -> StatusTuple:
    output_path = file_path.parent / f"{file_path.name}.jpeg"
    if not force and output_path.exists():
        return ("exists", f"Contact sheet already exists: {output_path.name}")
    try:
        sheet_generator = ContactSheetGenerator(file_path, config)
        sheet_generator.generate(output_path)
        return ("success", f"Contact sheet created: {output_path.name}")
    except Exception as e:
        if isinstance(e, ValueError) and "No video track found" in str(e):
            return ("skipped", "No video track found in container.")
        if isinstance(e, ValueError) and ("too short" in str(e) or "duration might be zero" in str(e)):
            return ("skipped", str(e))
        if isinstance(e, (IOError, OSError)) and "valid frames" in str(e):
            return ("error", "File appears corrupted or unreadable (0 frames extracted).")
        return ("error", f"Failed: {type(e).__name__}: {e}")


class ContactSheetGenerator(BaseSheetGenerator):
    config: StaticThumbsConfig

    def __init__(self, file_path: pathlib.Path, config: StaticThumbsConfig):
        super().__init__(file_path, config)

        available_grid_width = self.config.static_width - (self.grid_cols + 1) * self.config.gap
        self.thumb_w = max(1, available_grid_width // self.grid_cols)
        self.thumb_h = max(1, round(self.thumb_w / self.aspect_ratio))

        self.grid_h = self.grid_rows * self.thumb_h + (self.grid_rows + 1) * self.config.gap
        self.total_height = self.header_h + self.grid_h

    @property
    def sheet_width(self) -> int:
        return self.config.static_width

    def _calculate_timestamps(self) -> List[float]:
        duration = self.metadata.get("duration", 0.0)
        if duration < 1.0:
            raise ValueError(f"Video duration ({duration:.2f}s) is too short.")

        start_sec = duration * (self.config.start_offset_percent / 100.0)
        end_sec = duration - (duration * (self.config.end_offset_percent / 100.0))

        if start_sec >= end_sec:
            start_sec, end_sec = 0.0, duration

        if self.num_samples == 1:
            return [start_sec + (end_sec - start_sec) / 2.0]

        timestamps = np.linspace(start_sec, end_sec, self.num_samples, endpoint=True)
        return np.clip(timestamps, 0.01, max(0.01, duration - 0.01)).tolist()

    def generate(self, output_path: pathlib.Path):
        timestamps = self._calculate_timestamps()
        final_image = Image.new("RGB", (self.config.static_width, self.total_height), self.config.grid_bg_color)
        final_image.paste(self._render_header(self.config.static_width), (0, 0))

        with tempfile.TemporaryDirectory(prefix="sashimi_thumbs_") as temp_dir_str:
            temp_dir = pathlib.Path(temp_dir_str)
            frames_by_slot: Dict[int, Tuple[pathlib.Path, float]] = {}
            num_workers = min(4, (os.cpu_count() or 1))

            logger.debug(f"Dispatching {len(timestamps)} FFmpeg extraction tasks across {num_workers} workers.")

            with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = {
                    executor.submit(
                        _extract_single_frame_task, ts, temp_dir, self.file_path, self.thumb_w, self.thumb_h
                    ): slot
                    for slot, ts in enumerate(timestamps)
                }
                for future in concurrent.futures.as_completed(futures):
                    if res := future.result():
                        frames_by_slot[futures[future]] = res

            if not frames_by_slot:
                raise IOError("Failed to extract any valid frames.")

            # Each frame goes in its own slot, so a failed extraction leaves a gap instead of shifting the grid.
            for slot, (frame_path, ts) in frames_by_slot.items():
                row, col = divmod(slot, self.grid_cols)
                paste_x = self.config.gap + col * (self.thumb_w + self.config.gap)
                paste_y = self.header_h + self.config.gap + row * (self.thumb_h + self.config.gap)
                try:
                    with Image.open(frame_path) as thumb_orig:
                        thumb = thumb_orig.convert("RGB")
                    if thumb.size != (self.thumb_w, self.thumb_h):
                        thumb = thumb.resize((self.thumb_w, self.thumb_h))
                    final_image.paste(self._draw_timestamp(thumb, ts).convert("RGB"), (paste_x, paste_y))
                except Exception as e:
                    logger.debug(f"Failed to place frame at {ts:.2f}s: {e}")

        with atomic_output(output_path) as temp_output:
            final_image.save(temp_output, "JPEG", quality=self.config.jpeg_quality, optimize=True, progressive=True)
        final_image.close()
