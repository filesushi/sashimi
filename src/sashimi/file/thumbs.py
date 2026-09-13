import logging
import pathlib
import sys
import subprocess
import tempfile
import concurrent.futures
import os
import datetime
from typing import Iterator, Optional, List, Tuple, Dict, Any, cast, Union

import numpy as np
import ffmpeg  # type: ignore
from PIL import Image, ImageDraw, ImageFont
from pilmoji import Pilmoji  # type: ignore
from pymediainfo import MediaInfo

from sashimi.config.schemas.thumbs import StaticThumbsConfig

logger = logging.getLogger("sashimi")

# --- Type Aliases ---
MetaDataDict = Dict[str, Any]
StatusTuple = Tuple[str, str]
ResultTuple = Tuple[pathlib.Path, str, str]
YieldType = Union[int, ResultTuple]


def get_grid_dimensions(aspect_ratio: float | str) -> str:
    """Calculates the optimal grid layout based on aspect ratio."""
    if isinstance(aspect_ratio, str):
        if ":" in aspect_ratio:
            w, h = aspect_ratio.split(":")
            ratio = float(w) / float(h)
        else:
            ratio = float(aspect_ratio)
    else:
        ratio = float(aspect_ratio)

    if ratio < 0.55:
        return "8x2"
    elif ratio < 0.65:
        return "7x2"
    elif ratio < 0.75:
        return "6x2"
    elif ratio < 0.95:
        return "5x2"
    elif ratio < 1.25:
        return "6x3"
    elif ratio < 1.55:
        return "6x4"
    elif ratio < 1.85:
        return "6x5"
    elif ratio < 2.15:
        return "6x6"
    elif ratio < 2.55:
        return "6x7"
    else:
        return "6x8"


def is_video_file(file_path: pathlib.Path, extensions: List[str]) -> bool:
    return file_path.suffix.lower() in extensions


def _format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "N/A"
    try:
        delta = datetime.timedelta(seconds=int(float(seconds)))
        total_seconds = int(delta.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"
    except (ValueError, TypeError):
        return "N/A"


def _format_size(bytes_size: Optional[Any]) -> str:
    if bytes_size is None:
        return "N/A"
    try:
        bs = int(bytes_size)
        if bs < 1024:
            return f"{bs} B"
        elif bs < 1024**2:
            return f"{bs / 1024:.1f} KB"
        elif bs < 1024**3:
            return f"{bs / 1024**2:.1f} MB"
        return f"{bs / 1024**3:.1f} GB"
    except (ValueError, TypeError):
        return "N/A"


def _load_font(font_path: Optional[str], font_size: int) -> ImageFont.FreeTypeFont:
    if font_path:
        try:
            return ImageFont.truetype(font_path, font_size)
        except Exception:
            pass

    common_fonts = [
        "msgothic.ttc",
        "meiryo.ttc",
        "segoeui.ttf",
        "Arial Unicode.ttf",
        "AppleGothic.ttf",
        "PingFang.ttc",
        "NotoSansCJK-Regular.ttc",
        "DejaVuSans.ttf",
    ]
    for fname in common_fonts:
        try:
            return ImageFont.truetype(fname, font_size)
        except IOError:
            continue

    try:
        return cast(ImageFont.FreeTypeFont, ImageFont.load_default(size=font_size))
    except Exception:
        return cast(ImageFont.FreeTypeFont, ImageFont.load_default())


def _extract_single_frame_task(
    timestamp: float, temp_dir: pathlib.Path, file_path: pathlib.Path, thumb_w: int
) -> Optional[Tuple[pathlib.Path, float]]:
    output_filename = temp_dir / f"frame_{timestamp:.3f}.jpg"
    try:
        process_stream = (
            ffmpeg.input(str(file_path), ss=timestamp)
            .filter("scale", thumb_w, -1)
            .output(str(output_filename), vframes=1, format="image2", vcodec="mjpeg", qscale=2)
            .global_args("-nostdin")
            .overwrite_output()
        )

        cmd_args = ffmpeg.compile(process_stream)

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


class ContactSheetGenerator:
    def __init__(self, file_path: pathlib.Path, config: StaticThumbsConfig):
        self.file_path = file_path
        self.config = config

        self.timestamp_font = _load_font(self.config.timestamp_font_path, self.config.timestamp_font_size)
        self.header_font = _load_font(self.config.header_font_path, self.config.header_font_size)

        self.metadata = self._get_metadata()
        self.grid_cols, self.grid_rows = self._determine_grid()
        self.num_samples = self.grid_cols * self.grid_rows

        self._calculate_header_height()

        available_grid_width = self.config.output_width - (self.grid_cols + 1) * self.config.gap
        self.thumb_w = max(1, available_grid_width // self.grid_cols)

        video_width = max(1, int(self.metadata.get("width", 16)))
        video_height = max(1, int(self.metadata.get("height", 9)))
        video_aspect_ratio = video_width / video_height if video_height > 0 else 16 / 9

        self.thumb_h = max(1, int(self.thumb_w / video_aspect_ratio))
        self.grid_h = self.grid_rows * self.thumb_h + (self.grid_rows + 1) * self.config.gap
        self.total_height = self.header_h + self.grid_h

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
            self._header_line_height = line_height
            self._header_padding_top = int(line_height * 0.5)
            self._header_line_spacing = int(line_height * 1.2)
        except Exception:
            self.header_h = int(self.config.output_width * self.config.header_height_ratio)
            self._header_padding_top = 5
            self._header_line_spacing = int(self.config.header_font_size * 1.4)

    def _get_metadata(self) -> MetaDataDict:
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

    def _draw_header(self, image: Image.Image):
        draw = ImageDraw.Draw(image)
        draw.rectangle([0, 0, self.config.output_width, self.header_h], fill=self.config.header_bg_color)

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
        final_image = Image.new("RGB", (self.config.output_width, self.total_height), self.config.grid_bg_color)
        self._draw_header(final_image)

        with tempfile.TemporaryDirectory(prefix="sashimi_thumbs_") as temp_dir_str:
            temp_dir = pathlib.Path(temp_dir_str)
            tasks = []
            extracted_results = []
            num_workers = min(4, (os.cpu_count() or 1))

            with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
                for ts in timestamps:
                    tasks.append(
                        executor.submit(_extract_single_frame_task, ts, temp_dir, self.file_path, self.thumb_w)
                    )
                for future in concurrent.futures.as_completed(tasks):
                    if res := future.result():
                        extracted_results.append(res)

            frames_extracted = sorted(extracted_results, key=lambda x: x[1])
            if not frames_extracted:
                raise IOError("Failed to extract any valid frames.")

            curr_row, curr_col = 0, 0
            x_offset, y_offset = self.config.gap, self.header_h + self.config.gap

            for frame_idx, (frame_path, ts) in enumerate(frames_extracted):
                if frame_idx >= self.num_samples:
                    break
                try:
                    with Image.open(frame_path) as thumb_orig:
                        thumb_rgba = self._draw_timestamp(thumb_orig, ts)
                        thumb_rgb = thumb_rgba.convert("RGB")
                        paste_x = int(x_offset + curr_col * (self.thumb_w + self.config.gap))
                        paste_y = int(y_offset + curr_row * (self.thumb_h + self.config.gap))

                        if paste_x + self.thumb_w <= final_image.width and paste_y + self.thumb_h <= final_image.height:
                            final_image.paste(thumb_rgb, (paste_x, paste_y))

                        thumb_rgb.close()
                        if thumb_rgba is not thumb_orig:
                            thumb_rgba.close()

                    curr_col += 1
                    if curr_col >= self.grid_cols:
                        curr_col = 0
                        curr_row += 1
                except Exception:
                    continue

        final_image.save(output_path, "JPEG", quality=self.config.jpeg_quality, optimize=True, progressive=True)
        final_image.close()
