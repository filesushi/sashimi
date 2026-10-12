from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from sashimi.config._base import RGB, RGBA, ConfigFamily, FormBool, ParamsModel, TargetName, parse_flag
from sashimi.config._base import normalize_target, sentence
from sashimi.config._limits import MIN_TILE_SIZE, TILES_TOO_SMALL
from sashimi.errors import InvalidOptionsError

GridValue = Annotated[int, Field(ge=1, le=20)] | Literal["auto"]


class _SheetStyle(ParamsModel):
    cols: GridValue = "auto"
    """Columns, 1-20, or "auto" to choose from the video's aspect ratio."""
    rows: GridValue = "auto"
    """Rows, 1-20, or "auto"."""
    gap: int = Field(default=5, ge=0, le=100)
    """Pixels between tiles."""
    start_offset_percent: float = Field(default=7.0, ge=0.0, le=100.0)
    """Skip this much of the start of the video (credits, logos)."""
    end_offset_percent: float = Field(default=7.0, ge=0.0, le=100.0)
    """Skip this much of the end of the video (credits)."""
    grid_bg_color: RGB = (20, 20, 20)
    """Background behind the tiles."""
    show_header: bool = True
    """The block above the grid with the file name, size, duration and dimensions."""
    header_font_size: int = Field(default=18, ge=6, le=200)
    """Header text size in pixels."""
    header_bg_color: RGB = (0, 0, 0)
    """Header background."""
    header_text_color: RGB = (255, 255, 255)
    """Header text colour."""
    show_timestamps: bool = True
    """The time label in each tile's corner."""
    timestamp_font_size: int = Field(default=14, ge=6, le=200)
    """Time label text size in pixels."""
    timestamp_bg_color: RGBA = (0, 0, 0, 180)
    """Time label background, with alpha."""
    timestamp_text_color: RGB = (255, 255, 255)
    """Time label text colour."""

    @field_validator("cols", "rows", mode="before")
    @classmethod
    def _grid_dimension(cls, v: object) -> GridValue:
        if isinstance(v, str) and v.strip().lower() == "auto":
            return "auto"
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 20:
            return v
        raise ValueError(f"must be a whole number from 1 to 20, or 'auto' (got {v!r})")

    def _tile_width(self, width: int, even: bool) -> int | None:
        if not isinstance(self.cols, int):
            return None
        tile = (width - (self.cols + 1) * self.gap) // self.cols
        return tile // 2 * 2 if even else tile


class _StillSheet(_SheetStyle):
    animated: Annotated[Literal[False], FormBool] = False
    """A still sheet of single frames."""
    width: int = Field(default=2560, ge=320, le=8000)
    """Width of the sheet in pixels."""

    @model_validator(mode="after")
    def _check_tiles(self) -> Self:
        tile = self._tile_width(self.width, even=False)
        if tile is not None and tile < MIN_TILE_SIZE:
            raise sentence(TILES_TOO_SMALL)
        return self


class _AnimatedSheet(_SheetStyle):
    animated: Annotated[Literal[True], FormBool] = True
    """An animated sheet of short clips."""
    width: int = Field(default=1280, ge=320, le=3840)
    """Approximate width of the sheet in pixels (tiles are rounded to even sizes)."""
    sample_duration: float = Field(default=2.0, gt=0.0, le=10.0)
    """Length of each tile's clip in seconds."""
    fps: int = Field(default=12, ge=1, le=30)
    """Frames per second."""
    loop: bool = True
    """Loop forever, or play once."""

    @model_validator(mode="after")
    def _check_tiles(self) -> Self:
        tile = self._tile_width(self.width, even=True)
        if tile is not None and tile < MIN_TILE_SIZE:
            raise sentence(TILES_TOO_SMALL)
        return self


class JpegSheetConfig(_StillSheet):
    """A still contact sheet saved as JPEG."""

    target: Annotated[Literal["jpeg"], TargetName] = "jpeg"
    """The output format."""
    quality: int = Field(default=85, ge=1, le=100)
    """1 (smallest) to 100 (best)."""


class PngSheetConfig(_StillSheet):
    """A still contact sheet saved as PNG."""

    target: Annotated[Literal["png"], TargetName] = "png"
    """The output format."""


class WebpSheetConfig(_StillSheet):
    """A still contact sheet saved as WebP."""

    target: Annotated[Literal["webp"], TargetName] = "webp"
    """The output format."""
    quality: int = Field(default=85, ge=1, le=100)
    """1 (smallest) to 100 (best)."""


class AnimatedWebpSheetConfig(_AnimatedSheet):
    """An animated contact sheet saved as WebP: every tile plays a short clip."""

    target: Annotated[Literal["webp"], TargetName] = "webp"
    """The output format."""
    quality: int = Field(default=75, ge=1, le=100)
    """1 (smallest) to 100 (best)."""


class AnimatedGifSheetConfig(_AnimatedSheet):
    """An animated contact sheet saved as GIF: every tile plays a short clip."""

    target: Annotated[Literal["gif"], TargetName] = "gif"
    """The output format."""


StillSheetConfig = JpegSheetConfig | PngSheetConfig | WebpSheetConfig
"""Any still contact sheet config."""
AnimatedSheetConfig = AnimatedWebpSheetConfig | AnimatedGifSheetConfig
"""Any animated contact sheet config."""
SheetConfig = StillSheetConfig | AnimatedSheetConfig
"""Any contact sheet config."""

_STILL: Mapping[str, type[ParamsModel]] = {"jpeg": JpegSheetConfig, "png": PngSheetConfig, "webp": WebpSheetConfig}
_ANIMATED: Mapping[str, type[ParamsModel]] = {"webp": AnimatedWebpSheetConfig, "gif": AnimatedGifSheetConfig}


class _SheetFamily(ConfigFamily[SheetConfig]):
    @property
    def targets(self) -> tuple[str, ...]:
        return ("jpeg", "png", "webp", "gif")

    def _pick(self, data: Mapping[str, Any]) -> tuple[type[ParamsModel], dict[str, Any]]:
        animated = parse_flag("animated", data.get("animated", False))
        table, default = (_ANIMATED, "webp") if animated else (_STILL, "jpeg")
        target = normalize_target(data.get("target"))
        if target is not None and target not in table and target in {**_STILL, **_ANIMATED}:
            if animated:
                raise InvalidOptionsError("Animated contact sheets can be WebP or GIF.")
            raise InvalidOptionsError("Still contact sheets can be JPEG, PNG or WebP. GIF sheets must be animated.")
        name, model = self._lookup("target", target, table, default)
        return model, {**data, "target": name, "animated": animated}


CONTACT_SHEET: ConfigFamily[SheetConfig] = _SheetFamily(
    "contact sheet",
    {
        "jpeg": JpegSheetConfig,
        "png": PngSheetConfig,
        "webp": WebpSheetConfig,
        "animated webp": AnimatedWebpSheetConfig,
        "animated gif": AnimatedGifSheetConfig,
    },
)
"""
Contact sheets, by ``animated`` (default false) and ``target``: still sheets are jpeg (the
default), png or webp; animated sheets are webp (the default) or gif.
"""
