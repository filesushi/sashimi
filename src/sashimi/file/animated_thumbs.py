import logging
import pathlib
import sys
import subprocess
import tempfile
import concurrent.futures
import os
import warnings
from typing import Iterator, Optional, List, Tuple, Dict, Any, Union

import numpy as np
import ffmpeg  # type: ignore
from PIL import Image, ImageDraw
from pilmoji import Pilmoji  # type: ignore
from pymediainfo import MediaInfo

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


from sashimi.file.thumbs import get_grid_dimensions, is_video_file, _format_duration, _format_size, _load_font

logger = logging.getLogger("sashimi")


MetaDataDict = Dict[str, Any]
StatusTuple = Tuple[str, str]
ResultTuple = Tuple[pathlib.Path, str, str]
YieldType = Union[int, ResultTuple]


def _extract_single_clip_task(
    timestamp: float, duration: float, temp_dir: pathlib.Path, file_path: pathlib.Path, thumb_w: int, idx: int
) -> Optional[Tuple[pathlib.Path, float]]:
    """Extracts a short, scaled video snippet using FFmpeg to avoid MoviePy memory leaks."""
    output_filename = temp_dir / f"clip_{idx:03d}.mp4"
    try:
        process_stream = (
            ffmpeg.input(str(file_path), ss=timestamp)
            .filter("scale", thumb_w, -2)
            .output(str(output_filename), t=duration, an=None)
            .global_args("-nostdin")
            .overwrite_output()
        )

        cmd_args = ffmpeg.compile(process_stream)

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
    """Yields total count of valid files first, then ResultTuples[cite: 3]."""
    files_to_process: set[pathlib.Path] = set()
    skipped_files: List[ResultTuple] = []

    for path in paths:
        if not path.exists():
            skipped_files.append((path, "error", "Path does not exist"))
            continue
        if path.is_dir():
            file_generator = path.rglob("*") if recursive else path.glob("*")
            for file_path in file_generator:
                if file_path.is_file() and is_video_file(file_path, config.video_extensions):
                    files_to_process.add(file_path)
        elif path.is_file():
            if is_video_file(path, config.video_extensions):
                files_to_process.add(path)
            else:
                skipped_files.append((path, "skipped", "Not a video file"))

    sorted_files = sorted(list(files_to_process))
    yield len(sorted_files)

    for skipped_item in skipped_files:
        yield skipped_item

    if not sorted_files:
        return

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


class AnimatedContactSheetGenerator:
    def __init__(self, file_path: pathlib.Path, config: AnimatedThumbsConfig):
        self.file_path = file_path
        self.config = config

        self.timestamp_font = _load_font(self.config.timestamp_font_path, self.config.timestamp_font_size)
        self.header_font = _load_font(self.config.header_font_path, self.config.header_font_size)

        self.metadata = self._get_metadata()
        self.grid_cols, self.grid_rows = self._determine_grid()
        self.num_samples = self.grid_cols * self.grid_rows

        self._calculate_header_height()

        available_grid_width = self.config.output_width - (self.grid_cols + 1) * self.config.gap
        raw_thumb_w = max(2, available_grid_width // self.grid_cols)

        self.thumb_w = (raw_thumb_w // 2) * 2

        self.actual_grid_width = (self.thumb_w + self.config.gap) * self.grid_cols + self.config.gap

    def _determine_grid(self) -> Tuple[int, int]:
        if self.config.cols and self.config.rows:
            return self.config.cols, self.config.rows

        ratio = self.metadata.get("display_aspect_ratio", "1.778")
        grid_str = get_grid_dimensions(ratio)
        cols, rows = grid_str.split("x")
        return int(cols), int(rows)

    def _calculate_header_height(self):
        try:
            line_height = self.config.header_font_size * 1.2
            try:
                dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
                bbox = dummy_draw.textbbox((0, 0), "Aj", font=self.header_font)
                if bbox:
                    line_height = max(line_height, bbox[3] - bbox[1])
            except Exception:
                pass

            total_lines_equiv = 0.5 + (4 * 1.2) + 0.5
            self.header_h = max(int(self.config.header_font_size * 1.5), int(line_height * total_lines_equiv))
            self._header_padding_top = int(line_height * 0.5)
            self._header_line_spacing = int(line_height * 1.2)
        except Exception:
            self.header_h = int(self.config.output_width * self.config.header_height_ratio)
            self._header_padding_top = 5
            self._header_line_spacing = int(self.config.header_font_size * 1.4)

    def _get_metadata(self) -> MetaDataDict:
        """Pulls metadata via pymediainfo identically to the static engine."""
        metadata_out: MetaDataDict = {"filename": self.file_path.name}
        try:
            media_info = MediaInfo.parse(str(self.file_path), encoding_errors="ignore")
            if not any(track.track_type == "Video" for track in media_info.tracks):
                raise ValueError("No video track found in container.")

            raw_duration_ms = None
            for track in media_info.tracks:
                track_type = track.track_type
                template = self.config.mediainfo_template.get(track_type, {})
                for key in template.keys():
                    if value := getattr(track, key, None):
                        metadata_out[key] = value

                if hasattr(track, "duration") and track.duration:
                    try:
                        ms = float(track.duration)
                        if track_type == "General" or raw_duration_ms is None:
                            raw_duration_ms = ms
                    except ValueError:
                        pass

            if raw_duration_ms and raw_duration_ms > 0:
                metadata_out["duration"] = raw_duration_ms / 1000.0
            else:
                metadata_out["duration"] = 0.0

            metadata_out.setdefault("width", 1920)
            metadata_out.setdefault("height", 1080)
            metadata_out.setdefault("file_size", 0)

            if "display_aspect_ratio" not in metadata_out and float(metadata_out["height"]) > 0:
                metadata_out["display_aspect_ratio"] = str(
                    round(float(metadata_out["width"]) / float(metadata_out["height"]), 3)
                )

        except Exception as e:
            if isinstance(e, ValueError) and "No video track" in str(e):
                raise
            metadata_out.update(
                {"duration": 0.0, "width": 1920, "height": 1080, "file_size": 0, "display_aspect_ratio": "1.778"}
            )
        return metadata_out

    def _calculate_timestamps(self) -> List[float]:
        duration = self.metadata.get("duration", 0.0)

        if duration < self.config.sample_duration:
            raise ValueError(
                f"Video duration ({duration:.2f}s) is too short for a {self.config.sample_duration}s sample."
            )

        start_sec = duration * (self.config.start_offset_percent / 100.0)
        end_sec = duration * (self.config.end_offset_percent / 100.0)

        range_start = start_sec
        range_end = max(range_start + self.config.sample_duration, duration - end_sec)

        sample_start_times = np.linspace(
            range_start, max(range_start, range_end - self.config.sample_duration), self.num_samples, endpoint=True
        )
        return np.clip(sample_start_times, 0.01, max(0.01, duration - self.config.sample_duration - 0.01)).tolist()

    def _draw_header(self, image: Image.Image):
        lines = [
            f"{self.metadata.get('filename', 'N/A')}",
            f"File Size: {_format_size(self.metadata.get('file_size'))}",
            f"Duration: {_format_duration(self.metadata.get('duration'))}",
            f"Dimensions: {int(self.metadata.get('width', 0))}x{int(self.metadata.get('height', 0))}",
        ]

        current_y = getattr(self, "_header_padding_top", 5)

        with Pilmoji(image) as pilmoji:
            for line in lines:
                if line:
                    pilmoji.text((10, current_y), line, fill=self.config.header_text_color, font=self.header_font)
                    current_y += getattr(self, "_header_line_spacing", 25)

    def _draw_timestamp(self, image: Image.Image, timestamp: float) -> Image.Image:
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        draw = ImageDraw.Draw(image, "RGBA")

        time_str = _format_duration(timestamp)
        padding = 2

        try:
            bbox = draw.textbbox((0, 0), time_str, font=self.timestamp_font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
        except Exception:
            text_width, text_height = 50, 14

        box_w, box_h = text_width + 2 * padding, text_height + 2 * padding
        box_x = max(0, image.width - box_w - padding)
        box_y = max(0, image.height - box_h - padding)

        draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], fill=self.config.timestamp_bg_color)
        draw.text(
            (box_x + padding, box_y + padding),
            time_str,
            fill=self.config.timestamp_text_color,
            font=self.timestamp_font,
        )
        return image

    def generate(self, output_path: pathlib.Path):
        timestamps = self._calculate_timestamps()

        with tempfile.TemporaryDirectory(prefix="sashimi_athumbs_") as temp_dir_str:
            temp_dir = pathlib.Path(temp_dir_str)
            tasks = []
            extracted_results = []

            num_workers = min(self.num_samples, (os.cpu_count() or 1) + 2)

            with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
                for idx, ts in enumerate(timestamps):
                    tasks.append(
                        executor.submit(
                            _extract_single_clip_task,
                            ts,
                            self.config.sample_duration,
                            temp_dir,
                            self.file_path,
                            self.thumb_w,
                            idx,
                        )
                    )
                for future in concurrent.futures.as_completed(tasks):
                    if res := future.result():
                        extracted_results.append(res)

            frames_extracted = sorted(extracted_results, key=lambda x: x[1])
            if not frames_extracted:
                raise IOError("Failed to extract any valid clips.")

            while len(frames_extracted) < self.num_samples:
                frames_extracted.append(frames_extracted[-1])

            clips = []
            try:
                clips = [VideoFileClip(str(path)) for path, ts in frames_extracted]

                clip_matrix = []
                for r in range(self.grid_rows):
                    row_clips = []
                    for c in range(self.grid_cols):
                        idx = r * self.grid_cols + c
                        base_clip = clips[idx]
                        start_time = frames_extracted[idx][1]

                        def make_filter(st, col_idx, row_idx):
                            def filter_frame(gf, t):
                                frame = gf(t)
                                img = Image.fromarray(frame)
                                img_ts = self._draw_timestamp(img, st + t)

                                if self.config.gap > 0:
                                    left_pad = self.config.gap if col_idx == 0 else 0
                                    top_pad = self.config.gap if row_idx == 0 else 0

                                    new_w = img_ts.width + self.config.gap + left_pad
                                    new_h = img_ts.height + self.config.gap + top_pad

                                    bg = Image.new("RGB", (new_w, new_h), self.config.grid_bg_color)
                                    bg.paste(img_ts, (left_pad, top_pad))
                                    return np.array(bg)

                                return np.array(img_ts.convert("RGB"))

                            return filter_frame

                        filtered_clip = base_clip.transform(make_filter(start_time, c, r))
                        row_clips.append(filtered_clip)
                    clip_matrix.append(row_clips)

                header_img = Image.new("RGB", (self.actual_grid_width, self.header_h), self.config.header_bg_color)
                self._draw_header(header_img)

                header_clip = ImageClip(np.array(header_img))
                if hasattr(header_clip, "with_duration"):
                    header_clip = header_clip.with_duration(self.config.sample_duration)
                else:
                    header_clip = header_clip.set_duration(self.config.sample_duration)

                grid_clip = clips_array(clip_matrix)
                final_composition = clips_array([[header_clip], [grid_clip]])

                write_logger = "bar" if logger.getEffectiveLevel() <= logging.INFO else None
                ffmpeg_params = ["-loop", "0"] if (self.config.output_format == "webp" and self.config.loop) else None

                if self.config.output_format == "gif":
                    final_composition.write_gif(str(output_path), fps=self.config.output_fps, logger=write_logger)
                elif self.config.output_format == "webp":
                    final_composition.write_videofile(
                        str(output_path),
                        fps=self.config.output_fps,
                        codec="libwebp",
                        audio=False,
                        preset="default",
                        logger=write_logger,
                        ffmpeg_params=ffmpeg_params,
                    )
            finally:
                for c in clips:
                    try:
                        c.close()
                    except Exception as err:
                        logger.exception(err)
                        pass
