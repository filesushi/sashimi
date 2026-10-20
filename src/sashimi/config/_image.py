# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, BeforeValidator, Field, StringConstraints, field_validator, model_validator

from sashimi.config._base import RGB, ConfigFamily, Flip, ParamsModel, Rotation, SourceMetadata, TargetName, sentence

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


class _Metadata(SourceMetadata):
    keep_icc: bool = True
    """Keep the colour profile, so wide-gamut photos keep their colours."""


_Quality = Annotated[int, Field(ge=1, le=100)]


class _JpegSave(ParamsModel):
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


class _PngSave(ParamsModel):
    target: Annotated[Literal["png"], TargetName] = "png"
    """The output format."""
    png_compress_level: int = Field(default=6, ge=0, le=9)
    """zlib level, 0 (none) to 9 (smallest). Lossless either way."""
    optimize: bool = False
    """Search for the smallest encoding. Can take tens of seconds on large images."""


class _WebpSave(ParamsModel):
    target: Annotated[Literal["webp"], TargetName] = "webp"
    """The output format."""
    quality: _Quality = 85
    """Lossy quality, or compression effort when ``lossless``."""
    lossless: bool = False
    """Lossless compression: exact pixels, usually bigger files."""
    webp_method: int = Field(default=4, ge=0, le=6)
    """Encoder effort, 0 (fastest) to 6 (smallest)."""


class _GifSave(ParamsModel):
    target: Annotated[Literal["gif"], TargetName] = "gif"
    """The output format."""
    optimize: bool = False
    """Drop unused palette entries."""


class _BmpSave(ParamsModel):
    target: Annotated[Literal["bmp"], TargetName] = "bmp"
    """The output format."""


class _TiffSave(ParamsModel):
    target: Annotated[Literal["tiff"], TargetName] = "tiff"
    """The output format."""
    tiff_compression: Literal["none", "lzw", "deflate", "jpeg"] = "lzw"
    """LZW and Deflate are lossless; JPEG is lossy and much smaller."""
    quality: _Quality = 85
    """Used only with JPEG compression, which also flattens transparency."""


class JpegImageConfig(_JpegSave, _Metadata, _ImageBase):
    """A JPEG image. Transparency is always flattened onto ``background_color``."""


class PngImageConfig(_PngSave, _Metadata, _Transparency, _ImageBase):
    """A PNG image (always lossless)."""


class WebpImageConfig(_WebpSave, _Metadata, _Transparency, _ImageBase):
    """A WebP image."""


class GifImageConfig(_GifSave, SourceMetadata, _Transparency, _ImageBase):
    """A static GIF (the first frame of an animated input). Of the source's metadata, GIFs hold only comments."""


class BmpImageConfig(_BmpSave, _ImageBase):
    """An uncompressed BMP. Transparency is always flattened onto ``background_color``."""


class TiffImageConfig(_TiffSave, _Metadata, _Transparency, _ImageBase):
    """A TIFF image."""


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


def _split_sizes(v: object) -> object:
    if isinstance(v, str):
        return tuple(part for part in v.replace(" ", "").split(",") if part)
    return v


def _check_icon_sizes(v: tuple[int, ...]) -> tuple[int, ...]:
    if not v or any(not 16 <= s <= 256 for s in v):
        raise ValueError("icon sizes must be between 16 and 256 pixels")
    return tuple(sorted(set(v)))


class IcoConfig(ParamsModel):
    """A multi-size .ico (favicon). Sizes are square icon edges in pixels."""

    sizes: tuple[int, ...] = (16, 32, 48, 64, 128, 256)
    """Icon sizes to include, 16 to 256 pixels."""
    fit: Literal["pad", "crop"] = "pad"
    """Non-square images: centre them on a square ("pad") or crop the centre ("crop")."""
    resample: Resample = "lanczos"
    """Filter used when scaling."""
    background_color: RGB | None = None
    """Fill transparent areas (and the padding) with this colour. Blank keeps them transparent."""

    _split = field_validator("sizes", mode="before")(_split_sizes)
    _check_sizes = field_validator("sizes")(_check_icon_sizes)


FAVICON_PNG_SIZES = (16, 32, 48, 180, 192, 512)
"""The PNG sizes in a favicon pack: browser tabs (16, 32, 48), Apple touch icon (180), web app (192, 512)."""


def _single_line(value: str) -> str:
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("must be a single line of text")
    return value


_ManifestText = Annotated[str, StringConstraints(strip_whitespace=True), AfterValidator(_single_line)]


class FaviconPackConfig(ParamsModel):
    """
    A favicon pack (.zip): ``favicon.ico``, PNG icons at 16, 32, 48, 180, 192 and 512 pixels, a
    ``site.webmanifest`` and ``favicon.html``, the ``<head>`` tags that use them.
    """

    ico_sizes: tuple[int, ...] = (16, 32, 48)
    """Sizes inside favicon.ico, 16 to 256 pixels."""
    fit: Literal["pad", "crop"] = "pad"
    """Non-square images: centre them on a square ("pad") or crop the centre ("crop")."""
    resample: Resample = "lanczos"
    """Filter used when scaling."""
    background_color: RGB = WHITE
    """Fills the Apple touch icon (180 px), which must be opaque because iOS shows transparency as black."""
    name: _ManifestText = ""
    """The web app's name in site.webmanifest. Blank leaves it out."""
    short_name: _ManifestText = ""
    """A short name for home screens (about 12 characters). Blank leaves it out."""
    theme_color: RGB | None = None
    """Browser UI colour, in the manifest and a theme-color tag. Blank leaves it out."""
    manifest_background_color: RGB | None = None
    """Splash screen colour in the manifest. Blank leaves it out."""

    _split = field_validator("ico_sizes", mode="before")(_split_sizes)
    _check_sizes = field_validator("ico_sizes")(_check_icon_sizes)


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


_Adjustment = Annotated[float, Field(ge=0.0)]
EDIT_QUALITY = 92
"""The quality edits are re-saved at by default (JPEG, WebP, and TIFF with JPEG compression)."""


class _Edit(ParamsModel):
    rotate: Rotation = 0
    """Clockwise rotation in degrees, after the photo's own orientation (EXIF) is applied."""
    flip: Flip = "none"
    """Mirror the picture, after rotating."""
    crop_left: int = Field(default=0, ge=0)
    """Left edge of the crop, in pixels of the rotated and flipped picture."""
    crop_top: int = Field(default=0, ge=0)
    """Top edge of the crop, in pixels of the rotated and flipped picture."""
    crop_width: int | None = Field(default=None, ge=1)
    """Width of the crop in pixels. Blank keeps everything right of the left edge."""
    crop_height: int | None = Field(default=None, ge=1)
    """Height of the crop in pixels. Blank keeps everything below the top edge."""
    brightness: _Adjustment = 1.0
    """0 is black, 1 unchanged, 2 twice as bright. The same as CSS ``brightness()``."""
    contrast: _Adjustment = 1.0
    """0 is flat grey, 1 unchanged, 2 twice the contrast. The same as CSS ``contrast()``."""
    saturation: _Adjustment = 1.0
    """0 is greyscale, 1 unchanged, 2 twice as vivid. The same as CSS ``saturate()``."""

    @property
    def crops(self) -> bool:
        """True if the crop box is set (it may still be the whole picture)."""
        return bool(self.crop_left or self.crop_top or self.crop_width is not None or self.crop_height is not None)

    @property
    def adjusts(self) -> bool:
        """True if brightness, contrast or saturation changes the colours."""
        return (self.brightness, self.contrast, self.saturation) != (1.0, 1.0, 1.0)


class JpegEditConfig(_JpegSave, _Metadata, _Edit):
    """
    An edited JPEG: EXIF orientation, then rotate and flip, then crop, then brightness, contrast
    and saturation. Re-saved at high quality; transparency (from a HEIC source) is flattened onto white.
    """

    quality: _Quality = EDIT_QUALITY
    """1 (smallest) to 100 (best). Edits are re-saved at high quality by default."""


class PngEditConfig(_PngSave, _Metadata, _Edit):
    """An edited PNG (lossless). Transparency is kept."""


class WebpEditConfig(_WebpSave, _Metadata, _Edit):
    """An edited WebP. Transparency and animation are kept; every frame of an animation is edited."""

    quality: _Quality = EDIT_QUALITY
    """Lossy quality, or compression effort when ``lossless``. Edits are re-saved at high quality by default."""


class GifEditConfig(_GifSave, SourceMetadata, _Edit):
    """An edited GIF. Transparency and animation are kept; every frame of an animation is edited."""


class BmpEditConfig(_BmpSave, _Edit):
    """An edited BMP. Transparency is flattened onto white."""


class TiffEditConfig(_TiffSave, _Metadata, _Edit):
    """An edited TIFF (the first page)."""

    quality: _Quality = EDIT_QUALITY
    """Used only with JPEG compression, which also flattens transparency."""


ImageEditConfig = JpegEditConfig | PngEditConfig | WebpEditConfig | GifEditConfig | BmpEditConfig | TiffEditConfig
"""Any image edit config: one model per output format."""

IMAGE_EDIT: ConfigFamily[ImageEditConfig] = ConfigFamily(
    "image edit",
    {
        "jpeg": JpegEditConfig,
        "png": PngEditConfig,
        "webp": WebpEditConfig,
        "gif": GifEditConfig,
        "bmp": BmpEditConfig,
        "tiff": TiffEditConfig,
    },
)
"""
Image editing, by ``target``: jpeg, png, webp, gif, bmp, tiff (``jpg``/``tif`` accepted). Pass the
source's own format to keep it (``sashimi.image.frames_info(path).edit_target``).
"""


def _parse_frames(value: Any) -> Any:
    if not isinstance(value, str):
        return tuple(value) if isinstance(value, list) else value
    frames: list[Any] = []
    for part in value.replace(" ", "").split(","):
        if not part:
            continue
        first, dash, last = part.partition("-")
        if dash and first.isdigit() and last.isdigit():
            if int(last) < int(first):
                raise ValueError(f"{part} counts down; write ranges low-high")
            frames += range(int(first), int(last) + 1)
        else:
            frames.append(part)
    return tuple(frames)


def _sorted_frames(value: tuple[int, ...] | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not value:
        raise ValueError("keep at least one frame")
    return tuple(sorted(set(value)))


class _Frames(ParamsModel):
    keep: (
        Annotated[
            tuple[Annotated[int, Field(ge=0)], ...],
            BeforeValidator(_parse_frames),
            AfterValidator(_sorted_frames),
        ]
        | None
    ) = None
    """Frames to keep, numbered from 0, e.g. "0-9,12". Blank keeps every frame. Kept frames play in their original order."""
    speed: float = Field(default=1.0, gt=0.0)
    """Playback speed: 2 plays twice as fast, 0.5 half as fast. Every frame's duration is divided by it."""


class GifFramesConfig(_GifSave, SourceMetadata, _Frames):
    """An animated GIF with some of its frames, at a new speed."""


class WebpFramesConfig(_WebpSave, _Metadata, _Frames):
    """An animated WebP with some of its frames, at a new speed."""

    quality: _Quality = EDIT_QUALITY
    """Lossy quality, or compression effort when ``lossless``. Frames are re-saved at high quality by default."""


FramesConfig = GifFramesConfig | WebpFramesConfig
"""Any frame selection config."""

IMAGE_FRAMES: ConfigFamily[FramesConfig] = ConfigFamily(
    "image frames", {"gif": GifFramesConfig, "webp": WebpFramesConfig}
)
"""Frame selection and speed for animated images, by ``target``: gif or webp. Pass the source's own format to keep it."""


class FrameStripConfig(ParamsModel):
    """
    Every frame of an animated image as small tiles in one still WebP (a sprite sheet), left to
    right and top to bottom, for UIs that let people pick frames.
    """

    tile_height: int = Field(default=96, ge=16)
    """Height of each frame's tile in pixels; widths follow the animation's shape."""
    columns: int = Field(default=10, ge=1)
    """Tiles per row. More are used if the strip would otherwise be too tall for WebP."""
    quality: _Quality = 80
    """1 (smallest) to 100 (best)."""
