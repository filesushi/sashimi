from typing import Any, Dict, List, Optional, Tuple, Literal, Union
from pydantic import BaseModel, Field, field_validator


class BaseThumbsConfig(BaseModel):
    """Shared configuration mechanics for all contact sheet generation."""

    # Grid & Layout Options
    gap: int = Field(default=5, ge=0, description="Pixel gap between thumbnails")

    cols: Union[int, Literal["auto"]] = Field(default="auto", description="Explicit column count override or 'auto'")
    rows: Union[int, Literal["auto"]] = Field(default="auto", description="Explicit row count override or 'auto'")

    @field_validator("cols", "rows", mode="before")
    @classmethod
    def _parse_grid_dimension(cls, v: Any) -> Union[int, Literal["auto"]]:
        """Accepts a positive integer (or numeric string), or 'auto' in any case. None means 'auto'."""
        if v is None or (isinstance(v, str) and v.strip().lower() == "auto"):
            return "auto"
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
            return v
        raise ValueError(f"must be a positive integer or 'auto', got {v!r}")

    # Temporal Options
    start_offset_percent: float = Field(default=7.0, ge=0.0, le=100.0, description="Skip first N% of video")
    end_offset_percent: float = Field(default=7.0, ge=0.0, le=100.0, description="Skip last N% of video")

    # Styling & Visuals
    grid_bg_color: Tuple[int, int, int] = Field(default=(20, 20, 20))
    header_height_ratio: float = Field(default=0.15)
    header_font_path: Optional[str] = Field(default=None)
    header_font_size: int = Field(default=18)
    header_bg_color: Tuple[int, int, int] = Field(default=(0, 0, 0))
    header_text_color: Tuple[int, int, int] = Field(default=(255, 255, 255))
    timestamp_font_path: Optional[str] = Field(default=None)
    timestamp_font_size: int = Field(default=14)
    timestamp_bg_color: Tuple[int, int, int, int] = Field(default=(0, 0, 0, 180))
    timestamp_text_color: Tuple[int, int, int] = Field(default=(255, 255, 255))

    # Operational Parameters
    video_extensions: List[str] = Field(
        default_factory=lambda: [
            ".mp4",
            ".avi",
            ".mpg",
            ".ogm",
            ".wmv",
            ".asf",
            ".mkv",
            ".mov",
            ".m4v",
            ".flv",
            ".ts",
            ".webm",
            ".mpeg",
        ]
    )
    mediainfo_template: Dict[str, Dict[str, Any]] = Field(
        default_factory=lambda: {
            "General": {
                "file_extension": None,
                "format": None,
                "format_profile": None,
                "internet_media_type": None,
                "file_size": None,
                "duration": None,
                "overall_bit_rate": None,
            },
            "Video": {
                "track_id": None,
                "format": None,
                "format_info": None,
                "format_profile": None,
                "codec_id": None,
                "duration": None,
                "bit_rate_mode": None,
                "bit_rate": None,
                "width": None,
                "height": None,
                "display_aspect_ratio": None,
                "frame_rate": None,
                "color_space": None,
                "chroma_subsampling": None,
                "bit_depth": None,
            },
        }
    )


class StaticThumbsConfig(BaseThumbsConfig):
    """Configuration strictly for static JPEG contact sheets."""

    static_width: int = Field(default=2560, ge=1, description="Width for static JPEG sheets")
    jpeg_quality: int = Field(default=85, ge=1, le=100)


class AnimatedThumbsConfig(BaseThumbsConfig):
    """Configuration strictly for animated WebP/GIF contact sheets."""

    animated_width: int = Field(default=1280, ge=1, description="Width for animated sheets")
    output_format: Literal["webp", "gif"] = Field(default="webp")
    sample_duration: float = Field(default=2.0, gt=0.0)
    output_fps: int = Field(default=12, gt=0)
    loop: bool = Field(default=True)
