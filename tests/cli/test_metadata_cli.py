# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""`sashimi metadata`: highlights, what isn't counted, a one-line list of sections, every field with --all, all of it in --json."""

import io
import json

import pytest
from PIL import Image
from typer.testing import CliRunner

from sashimi.cli.main import app
from tests import metadata_samples as samples
from tests.core.test_metadata_read import camera_exif

runner = CliRunner()


@pytest.fixture
def photo(tmp_path):
    out = io.BytesIO()
    samples.picture().save(out, "JPEG", exif=camera_exif().tobytes(), icc_profile=samples.icc())
    path = tmp_path / "photo.jpg"
    path.write_bytes(out.getvalue())
    return path


def invoke(*args):
    return runner.invoke(app, list(args), env={"COLUMNS": "200"})


def test_metadata_shows_highlights_what_is_not_counted_and_the_sections(photo):
    result = invoke("metadata", str(photo))
    assert result.exit_code == 0, result.output
    lines = {line.strip().split("    ")[0]: line for line in result.stdout.splitlines()}
    assert "EXIF, GPS" in lines["Found in"]
    assert "1/33 s, f/1.9, ISO 250" in lines["Exposure"]
    assert "2.69 mm (30 mm in 35 mm terms)" in lines["Focal length"]
    assert "2024:05:18 04:04:36.586 +03:00" in lines["Taken"]
    assert "facing 112° (true north)" in lines["Location"]
    assert "orientation: 6 (turned 90° clockwise)" in lines["Not counted"]
    assert "EXIF IFD0 (" in lines["Sections"] and "--all lists every field" in lines["Sections"]
    assert "Make" not in lines


def test_metadata_all_lists_every_field_one_per_line(photo):
    result = invoke("metadata", str(photo), "--all")
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "EXIF IFD0 (" in out and "EXIF Exif IFD (" in out and "EXIF GPS IFD (" in out
    assert any(line.split() == ["ExposureTime", "1/33"] for line in out.splitlines())
    assert any(line.split() == ["Maker", "Apple"] for line in out.splitlines())


def test_metadata_json_carries_every_section(photo):
    result = invoke("metadata", str(photo), "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert {"sections", "not_counted", "looked_for", "date_taken_offset", "lens_model"} <= set(data)
    names = [section["name"] for section in data["sections"]]
    assert "EXIF Exif IFD" in names and "ICC profile" in names


def test_metadata_of_a_stripped_photo_says_what_it_looked_for_and_what_is_left(photo, tmp_path):
    out = tmp_path / "clean.jpg"
    assert invoke("strip", str(photo), "-o", str(out)).exit_code == 0
    result = invoke("metadata", str(out))
    assert result.exit_code == 0, result.output
    assert "nothing sashimi counts (looked for EXIF, GPS, XMP" in result.stdout
    assert "orientation: 6 (turned 90° clockwise)" in result.stdout


def test_metadata_of_a_plain_png_has_no_sections_line(tmp_path):
    path = tmp_path / "plain.png"
    Image.new("RGB", (4, 4)).save(path)
    result = invoke("metadata", str(path))
    assert result.exit_code == 0, result.output
    assert "Sections" not in result.stdout and "Not counted" not in result.stdout
