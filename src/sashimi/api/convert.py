import time
import pathlib
from typing import Union, List, Dict, Any, Optional, Iterable, Iterator

from sashimi.config.schemas.convert import ToJpegConfig, ToPngConfig
from sashimi.file.convert import to_jpeg_generator, to_png_generator
from sashimi.file.convert import YieldType
from sashimi.api._consume import consume_generator

# --- GENERATOR FACTORIES (For the CLI) ---


def make_to_jpeg_generator(
    paths: Union[str, pathlib.Path, List[Union[str, pathlib.Path]]],
    recursive: bool = False,
    force: bool = False,
    width: Optional[int] = None,
    height: Optional[int] = None,
    quality: Optional[Union[float, str, int]] = None,
    progressive: Optional[bool] = None,
    bg_color: Optional[tuple[int, int, int]] = None,
) -> Iterator[YieldType]:
    if not isinstance(paths, list):
        paths = [paths]
    resolved_paths = [pathlib.Path(p).resolve() for p in paths]

    kwargs: Dict[str, Any] = {}
    if width is not None:
        kwargs["width"] = width
    if height is not None:
        kwargs["height"] = height
    if quality is not None:
        kwargs["quality"] = quality
    if progressive is not None:
        kwargs["progressive"] = progressive
    if bg_color is not None:
        kwargs["bg_color"] = bg_color
    config = ToJpegConfig(**kwargs)

    return to_jpeg_generator(resolved_paths, config, recursive, force)


def make_to_png_generator(
    paths: Union[str, pathlib.Path, List[Union[str, pathlib.Path]]],
    recursive: bool = False,
    force: bool = False,
    width: Optional[int] = None,
    height: Optional[int] = None,
    optimize: Optional[bool] = None,
    compress_level: Optional[int] = None,
) -> Iterator[YieldType]:
    if not isinstance(paths, list):
        paths = [paths]
    resolved_paths = [pathlib.Path(p).resolve() for p in paths]

    kwargs: Dict[str, Any] = {}
    if width is not None:
        kwargs["width"] = width
    if height is not None:
        kwargs["height"] = height
    if optimize is not None:
        kwargs["optimize"] = optimize
    if compress_level is not None:
        kwargs["compress_level"] = compress_level
    config = ToPngConfig(**kwargs)

    return to_png_generator(resolved_paths, config, recursive, force)


# --- SYNCHRONOUS WRAPPERS (For Scripts) ---


def to_jpeg(
    paths: Union[str, pathlib.Path, List[Union[str, pathlib.Path]]],
    recursive: bool = False,
    force: bool = False,
    show_progress: bool = True,
    width: Optional[int] = None,
    height: Optional[int] = None,
    quality: Optional[Union[float, str, int]] = None,
    progressive: Optional[bool] = None,
    bg_color: Optional[tuple[int, int, int]] = None,
) -> Dict[str, Any]:
    """Convert supported images to JPEG, with optional scaling."""
    gen = make_to_jpeg_generator(paths, recursive, force, width, height, quality, progressive, bg_color)
    return consume_generator(gen, show_progress, "Converting to JPEG")


def to_png(
    paths: Union[str, pathlib.Path, List[Union[str, pathlib.Path]]],
    recursive: bool = False,
    force: bool = False,
    show_progress: bool = True,
    width: Optional[int] = None,
    height: Optional[int] = None,
    optimize: Optional[bool] = None,
    compress_level: Optional[int] = None,
) -> Dict[str, Any]:
    """Convert supported images to PNG, with optional scaling."""
    gen = make_to_png_generator(paths, recursive, force, width, height, optimize, compress_level)
    return consume_generator(gen, show_progress, "Converting to PNG")
