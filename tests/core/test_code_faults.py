# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
A fault in sashimi's code or a library's is told apart from a damaged file, at every point of a
route, and what a caller's progress callback raises crosses every handler unchanged.
"""

import errno
import io
import logging
import pathlib
import random
import struct

import PIL.Image as PILImage
import pytest
from PIL import Image
from typer.testing import CliRunner

from sashimi import image, metadata
from sashimi.api import convert_files_generator
from sashimi.cli.main import app
from sashimi.config import IMAGE_CONVERT, IMAGE_EDIT, IMAGE_FRAMES
from sashimi.core import _faults, _imagemeta, edit as edit_core
from sashimi.core import image as image_core
from sashimi.core import metadata as metadata_core
from sashimi.errors import ProcessingError, SashimiError, UnsupportedMediaError

FAULTS = [
    AttributeError("module 'PIL' has no attribute 'SomethingRenamed'"),
    TypeError("save() got an unexpected keyword argument"),
    KeyError("gone"),
    IndexError("list index out of range"),
    LookupError("unknown encoding"),
    ValueError("a route's own check fell over"),
    AssertionError("can't happen"),
    RuntimeError("a library changed"),
]
FAULT_IDS = [type(fault).__name__ for fault in FAULTS]


def png(path: pathlib.Path) -> pathlib.Path:
    Image.new("RGB", (8, 8), (9, 9, 9)).save(path)
    return path


def raising(fault: Exception):
    def broken(*_args, **_kwargs):
        raise fault

    return broken


ROUTES = {
    "convert": lambda path, out: image.convert(path, out / "a.webp", IMAGE_CONVERT.from_params(target="webp")),
    "edit": lambda path, out: image.edit(path, out / "a.png", IMAGE_EDIT.from_params(target="png", rotate=90)),
    "strip": lambda path, out: metadata.strip(path, out / "a.png", "remove_all"),
    "read": lambda path, _out: metadata.read(path),
}
POINTS = {
    "convert": [
        (image_core, "_save"),
        (Image.Image, "save"),
        (image_core, "apply_mode"),
        (image_core, "read_carriers"),
    ],
    "edit": [(Image.Image, "save"), (edit_core, "apply_mode"), (edit_core, "read_carriers")],
    "strip": [(metadata_core, "strip_image"), (_imagemeta, "_strip_png")],
    "read": [(metadata_core, "read_carriers"), (metadata_core, "_image_info"), (metadata_core, "_ImageReading")],
}
CASES = [(route, module, name) for route, points in POINTS.items() for module, name in points]


@pytest.mark.parametrize("fault", FAULTS, ids=FAULT_IDS)
@pytest.mark.parametrize(("route", "module", "name"), CASES, ids=[f"{r}-{n}" for r, _, n in CASES])
def test_a_fault_of_the_code_never_reads_as_a_damaged_file(tmp_path, monkeypatch, caplog, route, module, name, fault):
    source = png(tmp_path / "in.png")
    monkeypatch.setattr(module, name, raising(fault))
    with caplog.at_level(logging.WARNING, logger="sashimi"), pytest.raises(ProcessingError) as raised:
        ROUTES[route](source, tmp_path)
    assert type(fault).__name__ in str(raised.value)
    assert "damaged" not in str(raised.value)
    assert any(type(fault).__name__ in record.getMessage() for record in caplog.records)
    assert [path.name for path in tmp_path.iterdir() if path.is_file()] == ["in.png"]


def test_one_carrier_that_trips_the_code_is_not_called_unreadable(tmp_path, monkeypatch):
    source = tmp_path / "in.jpg"
    exif = Image.Exif()
    exif[0x010F] = "Maker"
    Image.new("RGB", (8, 8)).save(source, exif=exif)
    monkeypatch.setattr(metadata_core, "ExifRead", raising(AttributeError("renamed")))
    with pytest.raises(ProcessingError, match="AttributeError"):
        metadata.read(source)


@pytest.mark.parametrize("fault", FAULTS, ids=FAULT_IDS)
def test_a_folder_shows_a_fault_as_errors_and_fails(tmp_path, monkeypatch, fault, no_live_log_of_sashimi):
    for number in range(3):
        png(tmp_path / f"p{number}.png")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(image_core, "_save", raising(fault))
    result = CliRunner().invoke(app, ["convert", ".", "webp"], env={"COLUMNS": "200"})
    assert result.exit_code == 1
    assert result.output.count(type(fault).__name__) >= 3
    assert "damaged" not in result.output and "Skipped" not in result.output.replace("Skipped: ", "")


def test_a_damaged_file_still_reads_as_damaged(tmp_path):
    whole = io.BytesIO()
    Image.new("RGB", (256, 256), (9, 200, 9)).save(whole, "PNG")
    damaged = tmp_path / "cut.png"
    damaged.write_bytes(whole.getvalue()[:60])
    with pytest.raises(UnsupportedMediaError) as raised:
        image.convert(damaged, tmp_path / "a.webp", IMAGE_CONVERT.from_params(target="webp"))
    assert not isinstance(raised.value, ProcessingError)


def test_a_damaged_file_in_a_folder_is_skipped_and_the_run_succeeds(tmp_path, monkeypatch, no_live_log_of_sashimi):
    png(tmp_path / "good.png")
    (tmp_path / "bad.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"junk" * 10)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["convert", ".", "webp"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "bad.png" in result.output and "Skipped" in result.output


def test_a_full_disk_is_the_systems_fault(tmp_path, monkeypatch):
    source = png(tmp_path / "in.png")
    monkeypatch.setattr(image_core, "_save", raising(OSError(errno.ENOSPC, "No space left on device")))
    with pytest.raises(ProcessingError, match="No space left"):
        image.convert(source, tmp_path / "a.webp", IMAGE_CONVERT.from_params(target="webp"))


def test_an_encoder_failing_in_pillow_is_not_the_files_damage():
    with pytest.raises(OSError) as raised:
        Image.new("RGBA", (8, 8)).save(io.BytesIO(), "JPEG")
    assert not image_core.file_at_fault(raised.value)


def test_pillow_failing_to_read_bytes_is_the_files_damage():
    whole = io.BytesIO()
    Image.new("RGB", (64, 64)).save(whole, "JPEG")
    with pytest.raises(OSError) as raised:
        Image.open(io.BytesIO(whole.getvalue()[:300])).load()
    assert image_core.file_at_fault(raised.value)


def test_pillow_changing_a_pixel_is_not_the_files_damage():
    with pytest.raises(ValueError) as raised:
        Image.new("RGB", (8, 8)).convert("XX")
    assert not image_core.file_at_fault(raised.value)


class Stop(Exception):
    pass


STOPS = [
    Stop("mine"),
    RuntimeError("stop"),
    ValueError("stop"),
    KeyError("stop"),
    TypeError("stop"),
    AttributeError("stop"),
    TimeoutError("stop"),
    InterruptedError("stop"),
    OSError(errno.ENOSPC, "stop"),
    MemoryError("stop"),
    SashimiError("stop"),
]
STOP_IDS = [type(stop).__name__ for stop in STOPS]


def animation(path: pathlib.Path) -> pathlib.Path:
    frames = [Image.new("RGB", (8, 8), (number * 40, 200, 9)) for number in range(4)]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100)
    return path


PROGRESS_ROUTES = {
    "still to webp": lambda path, out, report: image.convert(
        png(path.with_suffix(".png")), out, IMAGE_CONVERT.from_params(target="webp"), on_progress=report
    ),
    "animation to webp": lambda path, out, report: image.convert(
        animation(path), out, IMAGE_CONVERT.from_params(target="webp"), on_progress=report
    ),
    "edit still": lambda path, out, report: image.edit(
        png(path.with_suffix(".png")), out, IMAGE_EDIT.from_params(target="png", rotate=90), on_progress=report
    ),
    "edit animation": lambda path, out, report: image.edit(
        animation(path), out, IMAGE_EDIT.from_params(target="gif", rotate=90), on_progress=report
    ),
    "select frames": lambda path, out, report: image.select_frames(
        animation(path), out, IMAGE_FRAMES.from_params(target="gif", keep=[0, 2]), on_progress=report
    ),
    "frame strip": lambda path, out, report: image.frame_strip(animation(path), out, on_progress=report),
    "icon": lambda path, out, report: image.to_ico(
        png(path.with_suffix(".png")), out.with_suffix(".ico"), on_progress=report
    ),
    "strip": lambda path, out, report: metadata.strip(
        png(path.with_suffix(".png")), out.with_suffix(".png"), "remove_all", on_progress=report
    ),
}


@pytest.mark.parametrize("stop", STOPS, ids=STOP_IDS)
@pytest.mark.parametrize("route", sorted(PROGRESS_ROUTES))
def test_what_a_progress_callback_raises_passes_through_unchanged(tmp_path, route, stop):
    def report(_fraction: float, _stage: str) -> None:
        raise stop

    with pytest.raises(type(stop)) as raised:
        PROGRESS_ROUTES[route](tmp_path / "in.gif", tmp_path / "out.webp", report)
    assert raised.value is stop


@pytest.mark.parametrize("stop", STOPS, ids=STOP_IDS)
def test_what_a_batch_progress_callback_raises_passes_through_unchanged(tmp_path, stop):
    def report(_path: pathlib.Path, _fraction: float, _stage: str) -> None:
        raise stop

    png(tmp_path / "a.png")
    png(tmp_path / "b.png")
    batch = convert_files_generator(tmp_path, "webp", on_file_progress=report)
    with pytest.raises(type(stop)) as raised:
        list(batch)
    assert raised.value is stop


class CoreWithout:
    """``PIL.Image.core`` as a Pillow built without some codecs shows it, or with one that fails on any file."""

    def __init__(self, core, missing=(), failing=()):
        self._core, self._missing, self._failing = core, set(missing), set(failing)

    def __getattr__(self, name):
        if name in self._missing:
            raise AttributeError(name)
        if name in self._failing:
            return raising(ValueError("a changed Pillow fails on every file"))
        return getattr(self._core, name)


@pytest.fixture
def pillow(monkeypatch):
    """Changes what Pillow can do for one test, forgetting what sashimi concluded about it either side."""

    def forget():
        known = getattr(_faults, "can_read", None)
        if known is not None:
            known.cache_clear()

    def change(missing=(), failing=()):
        monkeypatch.setattr(PILImage, "core", CoreWithout(PILImage.core, missing, failing))
        forget()

    forget()
    yield change
    forget()


def folder_of(tmp_path, extension):
    Image.init()
    for number in range(3):
        Image.frombytes("RGB", (16, 16), bytes(range(256)) * 3).save(tmp_path / f"p{number}.{extension}")
    return tmp_path


FOLDER_COMMANDS = {
    "convert": ["convert", ".", "webp"],
    "resize": ["resize", ".", "-w", "8"],
    "rotate": ["rotate", ".", "90"],
    "edit": ["edit", ".", "--rotate", "90"],
    "favicon": ["favicon", "."],
}
ENVIRONMENT_FAULTS = {
    "no JPEG decoder": ("jpg", {"missing": ["jpeg_decoder"]}),
    "no PNG decoder": ("png", {"missing": ["zip_decoder"]}),
    "no GIF decoder": ("gif", {"missing": ["gif_decoder"]}),
    "a JPEG decoder failing on every file": ("jpg", {"failing": ["jpeg_decoder"]}),
    "a PNG decoder failing on every file": ("png", {"failing": ["zip_decoder"]}),
    "a GIF decoder failing on every file": ("gif", {"failing": ["gif_decoder"]}),
}


def assert_read_as_faults(result, count=3):
    assert result.exit_code == 1, result.output
    assert result.output.count("Internal error") >= count, result.output
    assert "damaged" not in result.output and "isn't an image" not in result.output, result.output


@pytest.mark.parametrize("command", sorted(FOLDER_COMMANDS))
@pytest.mark.parametrize("fault", sorted(ENVIRONMENT_FAULTS))
def test_a_pillow_that_cant_read_a_format_never_reads_as_a_folder_of_damaged_files(
    tmp_path, monkeypatch, pillow, no_live_log_of_sashimi, command, fault
):
    extension, change = ENVIRONMENT_FAULTS[fault]
    folder_of(tmp_path, extension)
    monkeypatch.chdir(tmp_path)
    pillow(**change)
    result = CliRunner().invoke(app, FOLDER_COMMANDS[command], env={"COLUMNS": "200"})
    assert_read_as_faults(result)


@pytest.mark.parametrize(
    ("codec", "extension", "command"),
    [
        ("jpeg_encoder", "png", ["convert", ".", "jpeg"]),
        ("zip_encoder", "jpg", ["convert", ".", "png"]),
        ("gif_encoder", "png", ["convert", ".", "gif"]),
        ("zip_encoder", "jpg", ["favicon", "."]),
    ],
)
def test_a_pillow_without_an_encoder_never_reads_as_a_folder_of_damaged_files(
    tmp_path, monkeypatch, pillow, no_live_log_of_sashimi, codec, extension, command
):
    folder_of(tmp_path, extension)
    monkeypatch.chdir(tmp_path)
    pillow(missing=[codec])
    assert_read_as_faults(CliRunner().invoke(app, command, env={"COLUMNS": "200"}))


def test_a_pillow_without_a_format_doesnt_call_its_files_not_images(
    tmp_path, monkeypatch, pillow, no_live_log_of_sashimi
):
    folder_of(tmp_path, "jpg")
    monkeypatch.chdir(tmp_path)
    Image.init()
    monkeypatch.delitem(Image.OPEN, "JPEG")
    pillow()
    assert_read_as_faults(CliRunner().invoke(app, ["convert", ".", "webp"], env={"COLUMNS": "200"}))


def wrongly_called(method, *wrong):
    real = getattr(PILImage.Image, method)

    def call(self, *_args, **_kwargs):
        return real(self, *wrong)

    return call


@pytest.mark.parametrize(
    ("method", "wrong", "command"),
    [
        ("convert", ("XX",), ["convert", ".", "jpeg"]),
        ("resize", ((4, 4), 999), ["resize", ".", "-w", "8"]),
        ("transpose", (999,), ["edit", ".", "--rotate", "90"]),
    ],
    ids=["convert", "resize", "transpose"],
)
def test_a_bad_argument_to_pillow_never_reads_as_a_folder_of_damaged_files(
    tmp_path, monkeypatch, no_live_log_of_sashimi, method, wrong, command
):
    for number in range(3):
        Image.new("RGBA", (32, 32), (9, 9, 9, 128)).save(tmp_path / f"p{number}.png")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(PILImage.Image, method, wrongly_called(method, *wrong))
    assert_read_as_faults(CliRunner().invoke(app, command, env={"COLUMNS": "200"}))


def test_a_helper_of_pillows_writer_failing_never_reads_as_a_folder_of_damaged_files(
    tmp_path, monkeypatch, no_live_log_of_sashimi
):
    from PIL import GifImagePlugin

    code = "def _normalize_palette(*args, **kwargs):\n    raise ValueError('a changed Pillow')\n"
    namespace = {"__name__": "PIL.GifImagePlugin"}
    exec(compile(code, "GifImagePlugin.py", "exec"), namespace)
    monkeypatch.setattr(GifImagePlugin, "_normalize_palette", namespace["_normalize_palette"])
    folder_of(tmp_path, "png")
    monkeypatch.chdir(tmp_path)
    assert_read_as_faults(CliRunner().invoke(app, ["convert", ".", "gif"], env={"COLUMNS": "200"}))


def test_a_fault_in_pillow_is_logged_as_a_warning_naming_its_class(tmp_path, caplog, pillow):
    source = tmp_path / "in.jpg"
    Image.new("RGB", (16, 16)).save(source)
    pillow(missing=["jpeg_decoder"])
    with caplog.at_level(logging.WARNING, logger="sashimi"), pytest.raises(ProcessingError, match="OSError"):
        image.convert(source, tmp_path / "a.webp", IMAGE_CONVERT.from_params(target="webp"))
    assert any("OSError" in record.getMessage() for record in caplog.records)


def noise(size=(64, 48), mode="RGB"):
    width, height = size
    channels = len(mode)
    return Image.frombytes(mode, size, random.Random(width).randbytes(width * height * channels))


def saved(fmt, *pictures, **options):
    out = io.BytesIO()
    first, *rest = pictures
    if rest:
        first.save(out, fmt, save_all=True, append_images=rest, **options)
    else:
        first.save(out, fmt, **options)
    return out.getvalue()


def sample_files():
    one, two, three = noise(), noise((64, 48)).rotate(90), noise((64, 48)).rotate(180)
    exif = Image.Exif()
    exif[0x010F] = "Maker"
    files = {
        "jpg": saved("JPEG", one, exif=exif),
        "progressive.jpg": saved("JPEG", one, progressive=True),
        "png": saved("PNG", one),
        "apng.png": saved("PNG", one, two, three),
        "webp": saved("WEBP", one),
        "animated.webp": saved("WEBP", one, two, three),
        "gif": saved("GIF", one.convert("P"), two.convert("P"), three.convert("P")),
        "tif": saved("TIFF", one, exif=exif),
        "pages.tif": saved("TIFF", one, two),
    }
    if "AVIF" in Image.SAVE:
        files["avif"] = saved("AVIF", noise((128, 96)), quality=60, speed=9)
    return files


SAMPLES = sample_files()


def damage(data: bytes, rng: random.Random) -> bytes:
    out = bytearray(data)
    kind = rng.randrange(5)
    if kind == 0:
        return bytes(out[: rng.randrange(1, len(out))])
    if kind == 1:
        for _ in range(rng.randrange(1, 20)):
            at = rng.randrange(len(out))
            out[at] ^= 1 << rng.randrange(8)
    elif kind == 2:
        at = rng.randrange(len(out))
        out[at : at + 64] = bytes(len(out[at : at + 64]))
    elif kind == 3:
        at = rng.randrange(len(out))
        out[at : at + 64] = b"\xff" * len(out[at : at + 64])
    else:
        start = rng.randrange(min(256, len(out)))
        for _ in range(rng.randrange(1, 12)):
            out[min(len(out) - 1, start + rng.randrange(64))] = rng.randrange(256)
    return bytes(out)


DAMAGE_ROUTES = {
    "read": lambda path, out: metadata.read(path),
    "strip all": lambda path, out: metadata.strip(path, out / "s", "remove_all"),
    "strip location": lambda path, out: metadata.strip(path, out / "s", "remove_location"),
    "webp": lambda path, out: image.convert(path, out / "o.webp", IMAGE_CONVERT.from_params(target="webp")),
    "jpeg": lambda path, out: image.convert(path, out / "o.jpg", IMAGE_CONVERT.from_params(target="jpeg")),
    "png": lambda path, out: image.convert(path, out / "o.png", IMAGE_CONVERT.from_params(target="png")),
    "gif": lambda path, out: image.convert(path, out / "o.gif", IMAGE_CONVERT.from_params(target="gif")),
    "edit": lambda path, out: image.edit(path, out / "e.png", IMAGE_EDIT.from_params(target="png", rotate=90)),
    "frames": lambda path, out: image.select_frames(
        path, out / "f.gif", IMAGE_FRAMES.from_params(target="gif", keep=[0])
    ),
}


@pytest.mark.filterwarnings("ignore")
@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_seeded_damage_to_a_real_format_never_reads_as_a_fault_of_the_code(tmp_path, caplog, name):
    rng = random.Random(f"damage {name}")
    path = tmp_path / f"damaged.{name.rpartition('.')[2]}"
    out = tmp_path / "out"
    out.mkdir()
    refused = 0
    with caplog.at_level(logging.WARNING, logger="sashimi"):
        for _ in range(60):
            path.write_bytes(damage(SAMPLES[name], rng))
            for route in DAMAGE_ROUTES.values():
                try:
                    route(path, out)
                except SashimiError as e:
                    refused += 1
                    assert "Internal error" not in str(e), f"{name} {path.read_bytes()[:64]!r}"
    assert refused
    assert not [record for record in caplog.records if "Internal error" in record.getMessage()]


def two_page_tiff_with_no_width_on_its_second_page():
    data = bytearray(SAMPLES["pages.tif"])
    second = struct.unpack_from("<I", data, 4)[0]
    second = struct.unpack_from("<I", data, second + 2 + 12 * struct.unpack_from("<H", data, second)[0])[0]
    for number in range(struct.unpack_from("<H", data, second)[0]):
        at = second + 2 + 12 * number
        if struct.unpack_from("<H", data, at)[0] == 256:
            struct.pack_into("<H", data, at, 0xC000)
    return bytes(data)


def avif_whose_picture_pillow_cant_decode():
    whole = saved("AVIF", noise((256, 192)), quality=60, speed=9)
    start = whole.index(b"mdat") + 4
    for at in range(start, len(whole), 16):
        data = bytearray(whole)
        data[at : at + 64] = bytes(len(data[at : at + 64]))
        try:
            Image.open(io.BytesIO(data)).load()
        except RuntimeError:
            return bytes(data)
        except Exception:
            continue
    raise AssertionError("no damage made this Pillow raise RuntimeError")


def gif_with_a_frame_wider_than_its_canvas():
    small, wide = noise((20, 20)).convert("P", palette=Image.Palette.ADAPTIVE), noise((40, 20))
    wide = wide.quantize(palette=small)
    first, second = saved("GIF", small), saved("GIF", wide)
    start = second.index(b"\x2c", 13 + 3 * 256)
    return first[:-1] + second[start:]


@pytest.mark.filterwarnings("ignore")
def test_a_tiff_page_with_no_dimensions_reads_as_damaged(tmp_path):
    path = tmp_path / "t.tif"
    path.write_bytes(two_page_tiff_with_no_width_on_its_second_page())
    for target, name in (("webp", "o.webp"), ("png", "o.png")):
        with pytest.raises(UnsupportedMediaError, match=""):
            image.convert(path, tmp_path / name, IMAGE_CONVERT.from_params(target=target))


@pytest.mark.skipif("AVIF" not in Image.SAVE, reason="this Pillow has no AVIF")
def test_an_avif_with_a_damaged_picture_reads_as_damaged(tmp_path, caplog):
    path = tmp_path / "a.avif"
    path.write_bytes(avif_whose_picture_pillow_cant_decode())
    with caplog.at_level(logging.WARNING, logger="sashimi"), pytest.raises(UnsupportedMediaError) as raised:
        image.convert(path, tmp_path / "o.webp", IMAGE_CONVERT.from_params(target="webp"))
    assert not isinstance(raised.value, ProcessingError) and raised.value.user_message == image_core.DAMAGED_IMAGE
    assert not [record for record in caplog.records if "Internal error" in record.getMessage()]


@pytest.mark.parametrize("target", ["webp", "gif", "png", "apng", "avif"])
def test_a_gif_frame_past_its_canvas_is_clipped_as_browsers_clip_it(tmp_path, target):
    if target == "avif" and "AVIF" not in Image.SAVE:
        pytest.skip("this Pillow has no AVIF")
    path = tmp_path / "wide.gif"
    path.write_bytes(gif_with_a_frame_wider_than_its_canvas())
    with Image.open(path) as source:
        assert source.size == (20, 20) and source.n_frames == 2
    result = image.convert(path, tmp_path / "o.out", IMAGE_CONVERT.from_params(target=target))
    assert result.frames == 2
    with Image.open(tmp_path / "o.out") as out:
        assert out.size == (20, 20)


def test_a_gif_frame_far_past_its_canvas_reads_as_damaged(tmp_path):
    from sashimi.core import _animation

    data = bytearray(gif_with_a_frame_wider_than_its_canvas())
    second = data.rindex(b"\x2c\0\0\0\0\x28\0\x14\0")
    data[second + 5 : second + 9] = struct.pack("<HH", 8000, 8000)
    path = tmp_path / "huge.gif"
    path.write_bytes(data)
    assert _animation.MAX_OVERFLOW_PIXELS < 8000 * 8000
    with pytest.raises(UnsupportedMediaError) as raised:
        image.convert(path, tmp_path / "o.webp", IMAGE_CONVERT.from_params(target="webp"))
    assert not isinstance(raised.value, ProcessingError)


@pytest.mark.parametrize("target", ["jpeg", "png", "webp", "gif", "bmp", "tiff", "apng"])
@pytest.mark.parametrize("route", ["convert", "edit"])
def test_a_palette_png_with_several_transparent_entries_reaches_every_format(tmp_path, route, target):
    source = tmp_path / "in.png"
    picture = Image.new("P", (8, 8), 1)
    picture.putpalette(list(range(48)))
    picture.paste(2, (0, 0, 4, 8))
    picture.save(source, transparency=bytes([0, 128, 255, 64]))
    out = tmp_path / f"o.{target}"
    if route == "convert":
        image.convert(source, out, IMAGE_CONVERT.from_params(target=target))
    else:
        image.edit(source, out, IMAGE_EDIT.from_params(target=target, rotate=90))
    with Image.open(out) as written:
        written.load()


@pytest.mark.parametrize("target", ["apng", "png"])
def test_identical_frames_too_long_for_pillows_apng_writer_still_convert(tmp_path, target):
    still = Image.new("P", (8, 8), 1)
    still.putpalette(list(range(48)))
    other = Image.new("P", (8, 8), 2)
    other.putpalette(list(range(48)))
    data = saved("GIF", still, other, duration=100, disposal=1)
    first = data.index(b"\x21\xf9\x04")
    second = data.index(b"\x21\xf9\x04", first + 1)
    block = bytearray(data[first:second])
    held = b""
    for delay in [6553] * 11 + [6544]:
        struct.pack_into("<H", block, 4, delay)
        held += bytes(block)
    path = tmp_path / "held.gif"
    path.write_bytes(data[:first] + held + data[second:])
    result = image.convert(path, tmp_path / f"o.{target}", IMAGE_CONVERT.from_params(target=target))
    with Image.open(tmp_path / f"o.{target}") as written:
        assert written.n_frames >= 2 and result.source_frames == 13


@pytest.mark.skipif("AVIF" not in Image.SAVE, reason="this Pillow has no AVIF")
@pytest.mark.parametrize("mode", ["remove_all", "remove_location"])
def test_stripping_an_avif_pillow_cant_open_reads_as_damaged_not_as_ffmpegs_error(tmp_path, mode):
    data = bytearray(saved("AVIF", noise((64, 48)), quality=60, speed=9))
    at = data.index(b"meta")
    data[at + 4 : at + 12] = b"\xff" * 8
    path = tmp_path / "a.avif"
    path.write_bytes(data)
    with pytest.raises(UnsupportedMediaError) as raised:
        metadata.strip(path, tmp_path / "s.avif", mode)
    assert not isinstance(raised.value, ProcessingError) and raised.value.user_message == image_core.DAMAGED_IMAGE
