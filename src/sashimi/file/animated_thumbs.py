import logging
import pathlib
import sys
import subprocess
import tempfile
import concurrent.futures
import os
import warnings
from typing import Iterator, Optional, List, Tuple, Dict, Any

import numpy as np
import ffmpeg  # type: ignore
from PIL import Image

try:
    warnings.filterwarnings(
        "ignore",
        message="Subtitle stream parsing is not supported by moviepy and will be ignored",
        category=UserWarning,
        module="moviepy.video.io.ffmpeg_reader",
    )
    from moviepy import VideoFileClip, clips_array, ImageClip  # type: ignore
except ImportError as e:
    raise ImportError(f"Failed to import moviepy components. {e}") from e

from sashimi.config.schemas.thumbs import AnimatedThumbsConfig
from sashimi.file._sheet import (
    BaseSheetGenerator,
    MetaDataDict,
    ResultTuple,
    StatusTuple,
    YieldType,
    atomic_output,
    discover_videos,
)

logger = logging.getLogger("sashimi")


def _extract_single_clip_task(
    timestamp: float,
    duration: float,
    temp_dir: pathlib.Path,
    file_path: pathlib.Path,
    thumb_w: int,
    thumb_h: int,
    idx: int,
) -> Optional[Tuple[pathlib.Path, float]]:
    """Extracts a short, scaled video snippet using FFmpeg to avoid MoviePy memory leaks."""
    output_filename = temp_dir / f"clip_{idx:03d}.mp4"
    try:
        # 'V:0' is the first real video stream; it excludes embedded cover art (attached pictures).
        process_stream = (
            ffmpeg.input(str(file_path), ss=timestamp)["V:0"]
            .filter("scale", thumb_w, thumb_h)
            .filter("setsar", 1)
            .output(str(output_filename), t=duration)
            .global_args("-nostdin")
            .overwrite_output()
        )

        cmd_args = ffmpeg.compile(process_stream)

        logger.debug(f"Running FFmpeg: {' '.join(cmd_args)}")

        run_kwargs: Dict[str, Any] = {"capture_output": True, "check": True}
        if sys.platform == "win32":
            run_kwargs["creationflags"] = 0x00000040

        subprocess.run(cmd_args, **run_kwargs)

        if not output_filename.exists() or output_filename.stat().st_size == 0:
            return None
        return output_filename, timestamp

    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg crash at {timestamp:.2f}s: {e.stderr.decode('utf8', errors='ignore')}")
        return None
    except Exception as e:
        logger.error(f"Extraction task failed at {timestamp:.2f}s: {e}")
        return None


def generate_animated_thumbs(
    paths: List[pathlib.Path], config: AnimatedThumbsConfig, recursive: bool = False, force: bool = False
) -> Iterator[YieldType]:
    """Yields total count of valid files first, then ResultTuples."""
    sorted_files, rejected = discover_videos(paths, config.video_extensions, recursive)
    yield len(sorted_files)

    yield from rejected

    for file_path in sorted_files:
        yield (file_path, "starting", "Processing...")
        yield (file_path, *_process_file(file_path, config, force=force))


def _process_file(file_path: pathlib.Path, config: AnimatedThumbsConfig, force: bool) -> StatusTuple:
    output_path = file_path.parent / f"{file_path.name}.{config.output_format}"
    if not force and output_path.exists():
        return ("exists", f"Contact sheet already exists: {output_path.name}")
    try:
        sheet_generator = AnimatedContactSheetGenerator(file_path, config)
        sheet_generator.generate(output_path)
        return ("success", f"Animated sheet created: {output_path.name}")
    except Exception as e:
        if isinstance(e, ValueError) and "No video track found" in str(e):
            return ("skipped", "No video track found in container.")
        if isinstance(e, ValueError) and "too short" in str(e):
            return ("skipped", str(e))
        if isinstance(e, (IOError, OSError)) and "valid clips" in str(e):
            return ("error", "File appears corrupted or unreadable (0 clips extracted).")
        return ("error", f"Failed: {type(e).__name__}: {e}")


class AnimatedContactSheetGenerator(BaseSheetGenerator):
    config: AnimatedThumbsConfig

    def __init__(self, file_path: pathlib.Path, config: AnimatedThumbsConfig):
        super().__init__(file_path, config)

        # Video encoders need even dimensions.
        available_grid_width = self.config.animated_width - (self.grid_cols + 1) * self.config.gap
        self.thumb_w = max(2, (available_grid_width // self.grid_cols) // 2 * 2)
        self.thumb_h = max(2, round(self.thumb_w / self.aspect_ratio / 2) * 2)

        self.actual_grid_width = (self.thumb_w + self.config.gap) * self.grid_cols + self.config.gap

    @property
    def sheet_width(self) -> int:
        return self.config.animated_width

    def _calculate_timestamps(self) -> List[float]:
        """Returns clip start times; each clip runs for `sample_duration` seconds."""
        duration = self.metadata.get("duration", 0.0)
        sample = self.config.sample_duration

        if duration < sample:
            raise ValueError(f"Video duration ({duration:.2f}s) is too short for a {sample}s sample.")

        start_sec = duration * (self.config.start_offset_percent / 100.0)
        end_sec = duration - (duration * (self.config.end_offset_percent / 100.0))

        # Same fallback as the static engine: if the offsets leave no room for a clip, use the whole video.
        if end_sec - start_sec < sample:
            start_sec, end_sec = 0.0, duration

        last_start = end_sec - sample
        if self.num_samples == 1:
            return [start_sec + (last_start - start_sec) / 2.0]

        sample_start_times = np.linspace(start_sec, last_start, self.num_samples, endpoint=True)
        return np.clip(sample_start_times, 0.0, duration - sample).tolist()

    def _pad_cell(self, img: Image.Image, col_idx: int, row_idx: int) -> Image.Image:
        """Adds the grid gap to the right/bottom of every cell, and to the left/top of the first column/row."""
        img = img.convert("RGB")
        if self.config.gap <= 0:
            return img

        left_pad = self.config.gap if col_idx == 0 else 0
        top_pad = self.config.gap if row_idx == 0 else 0
        bg = Image.new(
            "RGB",
            (img.width + self.config.gap + left_pad, img.height + self.config.gap + top_pad),
            self.config.grid_bg_color,
        )
        bg.paste(img, (left_pad, top_pad))
        return bg

    def generate(self, output_path: pathlib.Path):
        timestamps = self._calculate_timestamps()
        sample = self.config.sample_duration

        with tempfile.TemporaryDirectory(prefix="sashimi_athumbs_") as temp_dir_str:
            temp_dir = pathlib.Path(temp_dir_str)
            clips_by_slot: Dict[int, Tuple[pathlib.Path, float]] = {}

            num_workers = min(self.num_samples, (os.cpu_count() or 1) + 2)

            logger.debug(f"Dispatching {len(timestamps)} FFmpeg extraction tasks across {num_workers} workers.")

            with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = {
                    executor.submit(
                        _extract_single_clip_task,
                        ts,
                        sample,
                        temp_dir,
                        self.file_path,
                        self.thumb_w,
                        self.thumb_h,
                        slot,
                    ): slot
                    for slot, ts in enumerate(timestamps)
                }
                for future in concurrent.futures.as_completed(futures):
                    if res := future.result():
                        clips_by_slot[futures[future]] = res

            if not clips_by_slot:
                raise IOError("Failed to extract any valid clips.")

            clips: List[Any] = []
            try:
                clip_matrix = []
                for r in range(self.grid_rows):
                    row_clips = []
                    for c in range(self.grid_cols):
                        slot = r * self.grid_cols + c
                        if slot not in clips_by_slot:
                            # A failed extraction leaves a blank cell rather than shifting later clips.
                            blank = Image.new("RGB", (self.thumb_w, self.thumb_h), self.config.grid_bg_color)
                            row_clips.append(ImageClip(np.array(self._pad_cell(blank, c, r))).with_duration(sample))
                            continue

                        clip_path, start_time = clips_by_slot[slot]
                        base_clip = VideoFileClip(str(clip_path))
                        clips.append(base_clip)

                        def make_filter(st, col_idx, row_idx):
                            def filter_frame(gf, t):
                                img = Image.fromarray(gf(t))
                                if img.size != (self.thumb_w, self.thumb_h):
                                    img = img.resize((self.thumb_w, self.thumb_h))
                                img_ts = self._draw_timestamp(img, st + t)
                                return np.array(self._pad_cell(img_ts, col_idx, row_idx))

                            return filter_frame

                        row_clips.append(base_clip.transform(make_filter(start_time, c, r)))
                    clip_matrix.append(row_clips)

                header_img = self._render_header(self.actual_grid_width)
                header_clip = ImageClip(np.array(header_img)).with_duration(sample)

                grid_clip = clips_array(clip_matrix)
                final_composition = clips_array([[header_clip], [grid_clip]])

                write_logger = "bar" if logger.getEffectiveLevel() <= logging.INFO else None

                with atomic_output(output_path) as temp_output:
                    if self.config.output_format == "gif":
                        # loop=0 repeats forever; None omits the loop extension so the GIF plays once.
                        final_composition.write_gif(
                            str(temp_output),
                            fps=self.config.output_fps,
                            loop=0 if self.config.loop else None,
                            logger=write_logger,
                        )
                    elif self.config.output_format == "webp":
                        # The WebP muxer's '-loop' is a play count: 0 repeats forever, 1 plays once.
                        final_composition.write_videofile(
                            str(temp_output),
                            fps=self.config.output_fps,
                            codec="libwebp",
                            audio=False,
                            preset="default",
                            logger=write_logger,
                            ffmpeg_params=["-loop", "0" if self.config.loop else "1"],
                        )
            finally:
                for clip in clips:
                    try:
                        clip.close()
                    except Exception as err:
                        logger.exception(err)
