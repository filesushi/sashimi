# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""An intact HEIC or AVIF, with nothing installed to read it, is refused for what it is: never as damaged, never written as a video."""

import json
import pathlib
import subprocess
import sys

import pytest
from PIL import Image

pillow_heif = pytest.importorskip("pillow_heif")

BLOCK_HEIF = "import sys; sys.modules['pillow_heif'] = None\n"
BLOCK_AVIF = (
    "from PIL import Image; Image.init()\n"
    "Image.OPEN.pop('AVIF', None); Image.ID[:] = [name for name in Image.ID if name != 'AVIF']\n"
    "import sys; sys.modules['pillow_heif'] = None\n"
)
API = """
import json, sys
from sashimi import image, metadata
from sashimi.config import IMAGE_CONVERT
routes = {
    "read": lambda: metadata.read(sys.argv[1]),
    "strip": lambda: metadata.strip(sys.argv[1], sys.argv[2] + ".stripped", "remove_all"),
    "convert": lambda: image.convert(sys.argv[1], sys.argv[2] + ".webp", IMAGE_CONVERT.from_params(target="webp")),
    "frames": lambda: image.frames_info(sys.argv[1]),
}
found = {}
for name, route in routes.items():
    try:
        route()
        found[name] = "ok"
    except Exception as e:
        found[name] = getattr(e, "advice", f"{type(e).__name__}: {e}")
print(json.dumps(found))
"""
CLI = "from sashimi.cli.main import app; import sys; app(sys.argv[1:])\n"


def run(code: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True, timeout=60)


@pytest.fixture
def heic(tmp_path) -> pathlib.Path:
    pillow_heif.register_heif_opener()
    path = tmp_path / "photo.heic"
    Image.new("RGB", (64, 64), (9, 99, 199)).save(path, "HEIF")
    return path


def as_mif1(path: pathlib.Path, *compatible: bytes) -> None:
    data = path.read_bytes()
    size = int.from_bytes(data[:4], "big")
    brands = b"".join(compatible)
    box = (16 + len(brands)).to_bytes(4, "big") + b"ftypmif1\0\0\0\0" + brands
    path.write_bytes(box + data[size:])


@pytest.fixture
def avif(tmp_path) -> pathlib.Path:
    path = tmp_path / "photo.avif"
    if "AVIF" not in Image.SAVE and (Image.init() or "AVIF" not in Image.SAVE):
        pytest.skip("this Pillow doesn't write AVIF")
    Image.new("RGB", (64, 64), (9, 99, 199)).save(path, "AVIF")
    return path


def test_every_call_says_a_heic_needs_the_extra(tmp_path, heic):
    done = run(BLOCK_HEIF + API, str(heic), str(tmp_path / "out"))
    assert done.returncode == 0, done.stderr
    for route, said in json.loads(done.stdout).items():
        assert "heif extra" in said and "pip install 'sashimi-convert[heif]'" in said, route
    assert not list(tmp_path.glob("out*"))


@pytest.mark.parametrize(
    "command",
    [
        ["strip"],
        ["convert", "{}", "webp"],
        ["resize", "--width", "32"],
        ["rotate", "{}", "90"],
        ["edit", "--rotate", "90"],
        ["favicon"],
        ["metadata"],
        ["info"],
    ],
    ids=lambda command: command[0],
)
def test_every_image_command_says_a_heic_needs_the_extra(tmp_path, heic, command):
    args = [part.replace("{}", str(heic)) for part in command]
    if "{}" not in " ".join(command):
        args.insert(1, str(heic))
    done = run(BLOCK_HEIF + CLI, *args)
    said = " ".join((done.stdout + done.stderr).split())
    assert done.returncode != 0
    assert "heif extra" in said and "sashimi-convert[heif]" in said, said
    assert "damaged" not in said
    assert [path.name for path in tmp_path.iterdir()] == ["photo.heic"]


def test_a_folder_run_names_the_extra_on_each_file(tmp_path, heic):
    done = run(BLOCK_HEIF + CLI, "strip", str(tmp_path))
    said = " ".join((done.stdout + done.stderr).split())
    assert "heif extra" in said and "damaged" not in said
    assert [path.name for path in tmp_path.iterdir()] == ["photo.heic"]


@pytest.mark.parametrize("brands", [None, (b"mif1", b"avif", b"miaf")], ids=["avif brand", "mif1 brand"])
def test_an_avif_without_a_reader_says_what_it_needs_and_writes_nothing(tmp_path, avif, brands):
    if brands:
        as_mif1(avif, *brands)
    done = run(BLOCK_AVIF + API, str(avif), str(tmp_path / "out"))
    assert done.returncode == 0, done.stderr
    for route, said in json.loads(done.stdout).items():
        assert "AVIF" in said and "pip install" in said, route
    assert not list(tmp_path.glob("out*"))


def test_info_on_an_avif_without_a_reader_says_what_it_needs(tmp_path, avif):
    done = run(BLOCK_AVIF + CLI, "info", str(avif))
    said = " ".join((done.stdout + done.stderr).split())
    assert done.returncode != 0 and "AVIF" in said and "pip install" in said, said


def test_a_heic_stated_as_mif1_is_still_a_heic(tmp_path, heic):
    as_mif1(heic, b"mif1", b"heic")
    done = run(BLOCK_HEIF + API, str(heic), str(tmp_path / "out"))
    assert "heif extra" in json.loads(done.stdout)["strip"]


def test_a_damaged_heic_with_the_extra_is_still_called_damaged(tmp_path, heic):
    from sashimi import metadata
    from sashimi.errors import UnsupportedMediaError

    heic.write_bytes(heic.read_bytes()[:40])
    with pytest.raises(UnsupportedMediaError) as refused:
        metadata.strip(heic, tmp_path / "out.heic", "remove_all")
    assert "damaged" in refused.value.user_message


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"\0\0\0\x18ftypheic\0\0\0\0mif1heic", "HEIF"),
        (b"\0\0\0\x18ftypmif1\0\0\0\0heicmiaf", "HEIF"),
        (b"\0\0\0\x18ftypavif\0\0\0\0mif1miaf", "AVIF"),
        (b"\0\0\0\x18ftypmif1\0\0\0\0avifmiaf", "AVIF"),
        (b"\0\0\0\x18ftypisom\0\0\0\0mif1avc1", None),
        (b"\0\0\0\x18ftypmp42\0\0\0\0isomheic", None),
        (b"\xff\xd8\xff\xe0", None),
    ],
)
def test_what_a_files_brands_say(head, expected):
    from sashimi.core._faults import stated_still_format

    assert stated_still_format(head) == expected
