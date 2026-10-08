from typing import Any, Tuple, Optional
from pydantic import BaseModel, Field, field_validator


def _normalize_percentage(v: Any) -> float:
    """Intelligently converts strings ('60%'), ints (60), or floats (0.6) to a 0.0-1.0 float."""
    if isinstance(v, str):
        v = v.strip()
        if v.endswith("%"):
            return float(v[:-1]) / 100.0
        return float(v)
    if isinstance(v, (int, float)):
        if v > 1.0:  # Assume they passed 85 instead of 0.85
            return float(v) / 100.0
        return float(v)
    raise ValueError("Invalid percentage format")


class MediaConfig(BaseModel):
    """Universal settings applicable to all visual media."""

    width: Optional[int] = Field(
        default=None, ge=1, description="Target width in pixels. If height is omitted, scales proportionally."
    )
    height: Optional[int] = Field(
        default=None, ge=1, description="Target height in pixels. If width is omitted, scales proportionally."
    )
    quality: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Universal quality ratio (0.0 to 1.0).")

    @field_validator("quality", mode="before")
    @classmethod
    def parse_quality(cls, v):
        if v is None:
            return None
        return _normalize_percentage(v)


class ImageConfig(MediaConfig):
    """Settings applicable to static images."""

    # Future placeholder for shared image ops like crop_mode, rotation, etc.
    pass


class ToJpegConfig(ImageConfig):
    """Configuration for converting images to JPEG."""

    quality: float = Field(default=0.85, ge=0.0, le=1.0)  # Overrides optional to required default
    progressive: bool = Field(default=False)
    bg_color: Tuple[int, int, int] = Field(default=(255, 255, 255))


class ToPngConfig(ImageConfig):
    """Configuration for converting images to PNG."""

    optimize: bool = Field(default=True)
    compress_level: int = Field(default=6, ge=0, le=9)
