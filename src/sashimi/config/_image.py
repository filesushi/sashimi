from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from sashimi.config._base import RGB, ConfigFamily, ParamsModel, TargetName, sentence

Resample = Literal["lanczos", "bicubic", "bilinear", "hamming", "box", "nearest"]
WHITE = (255, 255, 255)


class _ImageBase(ParamsModel):
    max_width: int | None = Field(default=None, ge=1)
    """Scale down to at most this width. ``None`` keeps the source's width. Never upscales; keeps the aspect ratio."""
    max_height: int | None = Field(default=None, ge=1)
    """Scale down to at most this height. ``None`` keeps the source's height."""
    resample: Resample = "lanczos"
    """Filter used when scaling down."""
    background_color: RGB = WHITE
    """What transparent areas are flattened onto."""
    auto_orient: bool = True
    """Apply the EXIF orientation tag (phone photos), so the output looks the way the photo is shown."""


class _Transparency(ParamsModel):
    preserve_transparency: bool = True
    """Keep alpha; when False, transparent areas are flattened onto ``background_color``."""


class _Metadata(ParamsModel):
    keep_exif: bool = False
    """Copy EXIF metadata (camera, date, GPS). Off by default for privacy."""
    keep_icc: bool = True
    """Keep the colour profile, so wide-gamut photos keep their colours."""


_Quality = Annotated[int, Field(ge=1, le=100)]


class JpegImageConfig(_Metadata, _ImageBase):
    """A JPEG image. Transparency is always flattened onto ``background_color``."""

    target: Annotated[Literal["jpeg"], TargetName] = "jpeg"
    """The output format."""
    quality: _Quality = 85
    """1 (smallest) to 100 (best)."""
    optimize: bool = True
    """Extra Huffman-table pass: slightly smaller files, slightly slower."""
    progressive: bool = False
    """Progressive (interlaced) encoding."""
    jpeg_subsampling: Literal["4:4:4", "4:2:2", "4:2:0"] = "4:2:0"
    """Chroma subsampling. 4:4:4 keeps colour edges sharp; 4:2:0 is smallest."""


class PngImageConfig(_Metadata, _Transparency, _ImageBase):
    """A PNG image (always lossless)."""

    target: Annotated[Literal["png"], TargetName] = "png"
    """The output format."""
    png_compress_level: int = Field(default=6, ge=0, le=9)
    """zlib level, 0 (none) to 9 (smallest). Lossless either way."""
    optimize: bool = False
    """Search for the smallest encoding. Can take tens of seconds on large images."""


class WebpImageConfig(_Metadata, _Transparency, _ImageBase):
    """A WebP image."""

    target: Annotated[Literal["webp"], TargetName] = "webp"
    """The output format."""
    quality: _Quality = 85
    """Lossy quality, or compression effort when ``lossless``."""
    lossless: bool = False
    """Lossless compression: exact pixels, usually bigger files."""
    webp_method: int = Field(default=4, ge=0, le=6)
    """Encoder effort, 0 (fastest) to 6 (smallest)."""
    optimize: bool = True
    """Spend extra effort on compression."""


class GifImageConfig(_Transparency, _ImageBase):
    """A static GIF (the first frame of an animated input). GIFs carry no photo metadata."""

    target: Annotated[Literal["gif"], TargetName] = "gif"
    """The output format."""
    optimize: bool = False
    """Drop unused palette entries."""


class BmpImageConfig(_ImageBase):
    """An uncompressed BMP. Transparency is always flattened onto ``background_color``."""

    target: Annotated[Literal["bmp"], TargetName] = "bmp"
    """The output format."""


class TiffImageConfig(_Metadata, _Transparency, _ImageBase):
    """A TIFF image."""

    target: Annotated[Literal["tiff"], TargetName] = "tiff"
    """The output format."""
    tiff_compression: Literal["none", "lzw", "deflate", "jpeg"] = "lzw"
    """LZW and Deflate are lossless; JPEG is lossy and much smaller."""
    quality: _Quality = 85
    """Used only with JPEG compression, which also flattens transparency."""


class _NeedsSize(ParamsModel):
    @model_validator(mode="after")
    def _check_size(self) -> Self:
        if getattr(self, "max_width", None) is None and getattr(self, "max_height", None) is None:
            raise sentence("Set a maximum width or height to resize to.")
        return self


class JpegResizeConfig(_NeedsSize, JpegImageConfig):
    """Like :class:`JpegImageConfig`, but `max_width` or `max_height` is required."""


class PngResizeConfig(_NeedsSize, PngImageConfig):
    """Like :class:`PngImageConfig`, but `max_width` or `max_height` is required."""


class WebpResizeConfig(_NeedsSize, WebpImageConfig):
    """Like :class:`WebpImageConfig`, but `max_width` or `max_height` is required."""


class GifResizeConfig(_NeedsSize, GifImageConfig):
    """Like :class:`GifImageConfig`, but `max_width` or `max_height` is required."""


class BmpResizeConfig(_NeedsSize, BmpImageConfig):
    """Like :class:`BmpImageConfig`, but `max_width` or `max_height` is required."""


class TiffResizeConfig(_NeedsSize, TiffImageConfig):
    """Like :class:`TiffImageConfig`, but `max_width` or `max_height` is required."""


class IcoConfig(ParamsModel):
    """A multi-size .ico (favicon). Sizes are square icon edges in pixels."""

    sizes: tuple[int, ...] = (16, 32, 48, 64, 128, 256)
    """Icon sizes to include, 16 to 256 pixels."""
    fit: Literal["pad", "crop"] = "pad"
    """Non-square images: centre them on a transparent square ("pad") or crop the centre ("crop")."""
    resample: Resample = "lanczos"
    """Filter used when scaling."""

    @field_validator("sizes", mode="before")
    @classmethod
    def _split(cls, v: object) -> object:
        if isinstance(v, str):
            return tuple(part for part in v.replace(" ", "").split(",") if part)
        return v

    @field_validator("sizes")
    @classmethod
    def _check_sizes(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        if not v or any(not 16 <= s <= 256 for s in v):
            raise ValueError("icon sizes must be between 16 and 256 pixels")
        return tuple(sorted(set(v)))


ImageConvertConfig = (
    JpegImageConfig | PngImageConfig | WebpImageConfig | GifImageConfig | BmpImageConfig | TiffImageConfig
)
"""Any image config: one model per output format."""
ImageResizeConfig = (
    JpegResizeConfig | PngResizeConfig | WebpResizeConfig | GifResizeConfig | BmpResizeConfig | TiffResizeConfig
)
"""Any image resize config."""

IMAGE_CONVERT: ConfigFamily[ImageConvertConfig] = ConfigFamily(
    "image convert",
    {
        "jpeg": JpegImageConfig,
        "png": PngImageConfig,
        "webp": WebpImageConfig,
        "gif": GifImageConfig,
        "bmp": BmpImageConfig,
        "tiff": TiffImageConfig,
    },
)
"""Image conversion, by ``target``: jpeg, png, webp, gif, bmp, tiff (``jpg``/``tif`` accepted)."""

IMAGE_RESIZE: ConfigFamily[ImageResizeConfig] = ConfigFamily(
    "image resize",
    {
        "jpeg": JpegResizeConfig,
        "png": PngResizeConfig,
        "webp": WebpResizeConfig,
        "gif": GifResizeConfig,
        "bmp": BmpResizeConfig,
        "tiff": TiffResizeConfig,
    },
)
"""Image resizing, by ``target``: the image configs with `max_width` or `max_height` required."""
