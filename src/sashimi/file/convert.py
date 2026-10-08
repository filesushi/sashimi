import pathlib
import logging
from typing import Iterator, List, Tuple, Union, Set, Optional
from PIL import Image

from sashimi.config.schemas.convert import ToJpegConfig, ToPngConfig

logger = logging.getLogger("sashimi")

YieldType = Union[int, Tuple[pathlib.Path, str, str]]


def _gather_files(paths: List[pathlib.Path], recursive: bool, valid_extensions: Set[str]) -> List[pathlib.Path]:
    """Recursively finds files matching the target input extensions."""
    files_to_process = set()
    for path in paths:
        if not path.exists():
            continue
        if path.is_file():
            if path.suffix.lower() in valid_extensions:
                files_to_process.add(path)
        elif path.is_dir():
            file_gen = path.rglob("*") if recursive else path.glob("*")
            for f in file_gen:
                if f.is_file() and f.suffix.lower() in valid_extensions:
                    files_to_process.add(f)
    return sorted(list(files_to_process))


def _resize_image(img: Image.Image, target_w: Optional[int], target_h: Optional[int]) -> Image.Image:
    """Proportionally scales an image if dimensions are provided."""
    if not target_w and not target_h:
        return img

    orig_w, orig_h = img.size
    new_w, new_h = orig_w, orig_h

    if target_w and target_h:
        new_w, new_h = target_w, target_h
    elif target_w:
        ratio = target_w / float(orig_w)
        new_w = target_w
        new_h = max(1, int(orig_h * ratio))
    elif target_h:
        ratio = target_h / float(orig_h)
        new_h = target_h
        new_w = max(1, int(orig_w * ratio))

    if (new_w, new_h) != (orig_w, orig_h):
        # Convert paletted images before scaling to prevent severe artifacting
        if img.mode == "P":
            img = img.convert("RGBA" if "transparency" in img.info else "RGB")
        return img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    return img


def to_jpeg_generator(
    paths: List[pathlib.Path], config: ToJpegConfig, recursive: bool, force: bool
) -> Iterator[YieldType]:
    valid_exts = {".png", ".webp", ".bmp", ".tiff", ".gif"}
    files = _gather_files(paths, recursive, valid_exts)

    yield len(files)
    if not files:
        return

    for path in files:
        yield (path, "starting", "Converting...")
        out_path = path.with_suffix(".jpg")

        if not force and out_path.exists():
            yield (path, "exists", f"File already exists: {out_path.name}")
            continue

        try:
            with Image.open(path) as img:
                img = _resize_image(img, config.width, config.height)

                # Safely flatten Alpha/Transparency layers before saving as JPEG
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    bg = Image.new("RGB", img.size, config.bg_color)
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    # split()[-1] safely extracts the alpha channel from both RGBA and LA modes
                    bg.paste(img, mask=img.split()[-1])
                    export_img = bg
                else:
                    export_img = img.convert("RGB")

                pil_quality = int(config.quality * 100)
                export_img.save(out_path, "JPEG", quality=pil_quality, progressive=config.progressive)

            yield (path, "success", f"Converted to {out_path.name}")
        except Exception as e:
            yield (path, "error", f"Failed to convert: {e}")


def to_png_generator(
    paths: List[pathlib.Path], config: ToPngConfig, recursive: bool, force: bool
) -> Iterator[YieldType]:
    valid_exts = {".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
    files = _gather_files(paths, recursive, valid_exts)

    yield len(files)
    if not files:
        return

    for path in files:
        yield (path, "starting", "Converting...")
        out_path = path.with_suffix(".png")

        if not force and out_path.exists():
            yield (path, "exists", f"File already exists: {out_path.name}")
            continue

        try:
            with Image.open(path) as img:
                img = _resize_image(img, config.width, config.height)
                export_img = img.convert("RGBA") if img.mode == "P" else img
                export_img.save(out_path, "PNG", optimize=config.optimize, compress_level=config.compress_level)

            yield (path, "success", f"Converted to {out_path.name}")
        except Exception as e:
            yield (path, "error", f"Failed to convert: {e}")
