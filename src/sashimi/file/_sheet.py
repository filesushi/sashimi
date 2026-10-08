"""Machinery shared by the static and animated contact sheet engines."""

import contextlib
import datetime
import logging
import os
import pathlib
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union, cast

from PIL import Image, ImageDraw, ImageFont
from pilmoji import Pilmoji  # type: ignore
from pymediainfo import MediaInfo

from sashimi.config.schemas.thumbs import BaseThumbsConfig

logger = logging.getLogger("sashimi")

# --- Type Aliases ---
MetaDataDict = Dict[str, Any]
StatusTuple = Tuple[str, str]
ResultTuple = Tuple[pathlib.Path, str, str]
YieldType = Union[int, ResultTuple]


def parse_ratio(aspect_ratio: float | str) -> float:
    """Parses an aspect ratio given as a number, '1.778' or '16:9'."""
    if isinstance(aspect_ratio, str) and ":" in aspect_ratio:
        w, h = aspect_ratio.split(":")
        return float(w) / float(h)
    return float(aspect_ratio)


def get_grid_dimensions(aspect_ratio: float | str) -> str:
    """Calculates the optimal grid layout based on aspect ratio."""
    ratio = parse_ratio(aspect_ratio)

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


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def discover_videos(
    paths: List[pathlib.Path], extensions: List[str], recursive: bool
) -> Tuple[List[pathlib.Path], List[ResultTuple]]:
    """Expands input paths into a sorted list of video files, plus result tuples for rejected inputs."""
    files_to_process: set[pathlib.Path] = set()
    rejected: List[ResultTuple] = []

    for path in paths:
        if not path.exists():
            rejected.append((path, "error", "Path does not exist"))
            continue
        if path.is_dir():
            file_generator = path.rglob("*") if recursive else path.glob("*")
            for file_path in file_generator:
                if file_path.is_file() and is_video_file(file_path, extensions):
                    files_to_process.add(file_path)
        elif path.is_file():
            if is_video_file(path, extensions):
                files_to_process.add(path)
            else:
                rejected.append((path, "skipped", "Not a video file"))

    sorted_files = sorted(files_to_process)
    logger.info(f"Found {len(sorted_files)} video files to process.")
    if rejected:
        logger.debug(f"Rejected {len(rejected)} inputs (missing/non-video).")
    return sorted_files, rejected


@contextlib.contextmanager
def atomic_output(output_path: pathlib.Path) -> Iterator[pathlib.Path]:
    """
    Yields a hidden temporary path next to `output_path` and moves it into place only on success,
    so an interrupted or failed write never leaves a partial file that later runs treat as done.
    The original suffix is kept so writers that infer the format from the extension still work.
    """
    temp_path = output_path.with_name(f".{output_path.name}.partial{output_path.suffix}")
    try:
        yield temp_path
        os.replace(temp_path, output_path)
    finally:
        temp_path.unlink(missing_ok=True)


class BaseSheetGenerator:
    """Metadata, grid and header/timestamp rendering shared by both contact sheet engines."""

    def __init__(self, file_path: pathlib.Path, config: BaseThumbsConfig):
        self.file_path = file_path
        self.config = config

        self.timestamp_font = _load_font(self.config.timestamp_font_path, self.config.timestamp_font_size)
        self.header_font = _load_font(self.config.header_font_path, self.config.header_font_size)

        self.metadata = self._get_metadata()
        self.aspect_ratio = parse_ratio(self.metadata["display_aspect_ratio"])
        self.grid_cols, self.grid_rows = self._determine_grid()
        self.num_samples = self.grid_cols * self.grid_rows

        logger.debug(f"Grid resolved to {self.grid_cols}x{self.grid_rows} ({self.num_samples} samples).")

        self._calculate_header_height()

    @property
    def sheet_width(self) -> int:
        raise NotImplementedError

    def _determine_grid(self) -> Tuple[int, int]:
        """Explicit integer cols/rows win individually; anything left as 'auto' comes from the aspect ratio."""
        auto_cols, auto_rows = (int(n) for n in get_grid_dimensions(self.aspect_ratio).split("x"))
        cols = self.config.cols if isinstance(self.config.cols, int) else auto_cols
        rows = self.config.rows if isinstance(self.config.rows, int) else auto_rows
        return cols, rows

    def _calculate_header_height(self) -> None:
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
            self.header_h = int(self.sheet_width * self.config.header_height_ratio)
            self._header_padding_top = 5
            self._header_line_spacing = int(self.config.header_font_size * 1.4)

    def _get_metadata(self) -> MetaDataDict:
        # MediaInfo failures (e.g. libmediainfo missing) propagate so they are reported as errors,
        # instead of masquerading as a zero-length video.
        media_info = MediaInfo.parse(str(self.file_path), encoding_errors="ignore")
        video_tracks = [track for track in media_info.tracks if track.track_type == "Video"]
        if not video_tracks:
            raise ValueError("No video track found in container.")

        metadata_out: MetaDataDict = {"filename": self.file_path.name}
        raw_duration_ms = None
        for track in media_info.tracks:
            track_type = track.track_type
            template = self.config.mediainfo_template.get(track_type, {})
            for key in template.keys():
                if value := getattr(track, key, None):
                    metadata_out[key] = value

            if ms := _to_float(getattr(track, "duration", None)):
                if track_type == "General" or raw_duration_ms is None:
                    raw_duration_ms = ms

        metadata_out["duration"] = raw_duration_ms / 1000.0 if raw_duration_ms and raw_duration_ms > 0 else 0.0
        metadata_out.setdefault("file_size", 0)

        # Geometry always comes from the primary video track, in display orientation:
        # FFmpeg auto-rotates decoded frames, so 90/270 degree rotations swap the axes.
        video = video_tracks[0]
        width = _to_float(video.width) or 1920.0
        height = _to_float(video.height) or 1080.0
        try:
            aspect = parse_ratio(video.display_aspect_ratio) if video.display_aspect_ratio else width / height
        except (TypeError, ValueError, ZeroDivisionError):
            aspect = width / height

        rotation = _to_float(getattr(video, "rotation", None)) or 0.0
        if round(abs(rotation)) % 180 == 90:
            width, height = height, width
            aspect = 1.0 / aspect

        metadata_out.update(
            width=int(width), height=int(height), display_aspect_ratio=str(round(aspect, 3)), rotation=rotation
        )

        logger.debug(
            f"Parsed metadata for {self.file_path.name}: "
            f"{metadata_out['width']}x{metadata_out['height']}, "
            f"Aspect: {metadata_out['display_aspect_ratio']}, Rotation: {rotation}, "
            f"Duration: {metadata_out['duration']}s"
        )

        return metadata_out

    def _render_header(self, width: int) -> Image.Image:
        lines = [
            f"{self.metadata.get('filename', 'N/A')}",
            f"File Size: {_format_size(self.metadata.get('file_size'))}",
            f"Duration: {_format_duration(self.metadata.get('duration'))}",
            f"Dimensions: {int(self.metadata.get('width', 0))}x{int(self.metadata.get('height', 0))}",
        ]
        try:
            return self._draw_header_lines(width, lines, emoji=True)
        except Exception as e:
            # Pilmoji fetches emoji images over the network; fall back to plain text when that fails.
            logger.debug(f"Emoji header rendering failed ({e}); falling back to plain text.")
            return self._draw_header_lines(width, lines, emoji=False)

    def _draw_header_lines(self, width: int, lines: List[str], emoji: bool) -> Image.Image:
        image = Image.new("RGB", (width, self.header_h), self.config.header_bg_color)
        current_y = self._header_padding_top

        if emoji:
            with Pilmoji(image, render_discord_emoji=False) as pilmoji:
                for line in lines:
                    pilmoji.text((10, current_y), line, fill=self.config.header_text_color, font=self.header_font)
                    current_y += self._header_line_spacing
        else:
            draw = ImageDraw.Draw(image)
            for line in lines:
                draw.text((10, current_y), line, fill=self.config.header_text_color, font=self.header_font)
                current_y += self._header_line_spacing
        return image

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
