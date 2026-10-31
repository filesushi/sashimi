# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""`sashimi metadata` (show what a file holds) and `sashimi strip` (remove it without re-encoding)."""

from __future__ import annotations

import pathlib
from typing import Annotated, List, Optional

import typer

from sashimi.cli import _options as opt
from rich.markup import escape

from sashimi.cli._output import BatchRunner, console, display_path, fail, print_fields, refusal, refusals


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _exposure(info) -> str | None:
    parts = []
    if info.exposure_time:
        seconds = info.exposure_time
        inverse = round(1 / seconds)
        whole = seconds < 0.5 and abs(1 / seconds - inverse) < 0.05
        parts.append(f"1/{inverse} s" if whole else f"{_number(seconds)} s")
    if info.f_number:
        parts.append(f"f/{_number(info.f_number)}")
    if info.iso:
        parts.append(f"ISO {info.iso}")
    return ", ".join(parts) or None


def _location(info) -> str | None:
    if not info.has_location:
        return None
    if info.latitude is None or info.longitude is None:
        return "yes"
    text = f"{info.latitude:.6f}, {info.longitude:.6f}"
    if info.altitude is not None:
        text += f", {info.altitude:.0f} m above sea level"
    if info.image_direction is not None:
        north = f" ({info.image_direction_ref} north)" if info.image_direction_ref else ""
        text += f", facing {info.image_direction:.0f}°{north}"
    return text


def _joined(make: str | None, model: str | None) -> str | None:
    if make and model and model.lower().startswith(make.lower()):
        return model
    return " ".join(part for part in (make, model) if part) or None


def _taken(info) -> str | None:
    from sashimi.core.metadata import offset_of

    if not info.date_taken:
        return None
    text = info.date_taken
    if info.date_taken_subseconds:
        text += f".{info.date_taken_subseconds}"
    if info.date_taken_offset and offset_of(info.date_taken) is None:
        text += f" {info.date_taken_offset}"
    return text


def _focal(info) -> str | None:
    if not info.focal_length and not info.focal_length_35mm:
        return None
    if not info.focal_length:
        return f"{_number(info.focal_length_35mm)} mm in 35 mm terms"
    text = f"{_number(info.focal_length)} mm"
    return text + (f" ({_number(info.focal_length_35mm)} mm in 35 mm terms)" if info.focal_length_35mm else "")


def _detail(detail) -> str:
    track = f" (stream {detail.stream})" if detail.stream is not None else ""
    return f"{detail.name}{track}: {detail.value}"


def _rows(info) -> list[tuple[str, str]]:
    rows = [
        ("Found in", ", ".join(info.carriers) or f"nothing sashimi counts (looked for {', '.join(info.looked_for)})"),
        ("Location", _location(info)),
        ("Place", info.place),
        ("Camera", _joined(info.camera_make, info.camera_model)),
        ("Lens", _joined(info.lens_make, info.lens_model)),
        ("Exposure", _exposure(info)),
        ("Focal length", _focal(info)),
        ("Taken", _taken(info)),
        ("Created", info.creation_time),
        ("Software", info.software),
        ("Title", info.title),
        ("Artist", info.artist),
        ("Album", info.album),
        ("Comment", info.comment),
        ("Colour profile", info.color_profile or ("yes" if info.has_color_profile else None)),
        ("Thumbnail", f"{info.thumbnail_size[0]}x{info.thumbnail_size[1]}" if info.thumbnail_size else None),
        ("Maker note", info.maker_note),
        ("Shown turned", f"{info.display_rotation}° clockwise (display rotation)" if info.display_rotation else None),
    ]
    return [(label, value) for label, value in rows if value]


def metadata_cmd(
    path: Annotated[pathlib.Path, typer.Argument(help="Image, video or audio file.", show_default=False)],
    every: Annotated[
        bool,
        opt.option("--all", "-a", help="Also list every field read, by where it was found.", panel=opt.GENERAL),
    ] = False,
    json_output: opt.JsonOutput = False,
):
    """
    Show the metadata a file holds: where it was found (EXIF, XMP, IPTC, QuickTime tags, ID3, …),
    any location, the camera, lens and exposure, dates, software, and titles and comments, then
    what is still in the file that sashimi doesn't count (orientation, colour profile, encoder
    tags). --all lists every field, by where it was found; --json has them all. It shows what
    sashimi found, not a promise that nothing else is there.
    """
    opt.exists_or_fail(path)
    opt.file_or_fail(path, "metadata")
    from sashimi.config import Limits
    from sashimi.core.metadata import read
    from sashimi.errors import SashimiError, UserInputError

    try:
        info = read(path, limits=Limits.unlimited())
    except UserInputError as e:
        fail(refusal(e), code=1)
    except SashimiError as e:
        fail(str(e).splitlines()[0], code=1)
    if json_output:
        print(info.model_dump_json(indent=2))
        return

    rows = [("File", display_path(path)), *_rows(info)]
    if info.not_counted:
        rows.append(("Not counted", "\n".join(_detail(detail) for detail in info.not_counted)))
    if not every and info.sections:
        listed = ", ".join(f"{found.name} ({len(found.fields) + found.omitted})" for found in info.sections)
        rows.append(("Sections", f"{listed}; --all lists every field"))
    print_fields(rows)
    if every:
        for found in info.sections:
            heading = found.name
            extra = "" if found.counted else ", not counted"
            more = f"; {found.omitted} more not listed" if found.omitted else ""
            console.print(f"[bold]{escape(heading)}[/bold] ({len(found.fields)} fields{extra}{more})")
            print_fields([(field.name, field.value) for field in found.fields])
        if info.sections_omitted:
            console.print(f"{info.sections_omitted} more sections not listed.")


def strip_cmd(
    paths: Annotated[List[pathlib.Path], typer.Argument(help="Files or directories.", show_default=False)],
    mode: Annotated[
        Optional[opt.StripMode],
        opt.option(
            "--remove",
            metavar="WHAT",
            help="What to remove: remove_all (keep only what showing or playing the file needs) or "
            "remove_location (GPS and place names) [default: remove_all].",
            panel=opt.GENERAL,
            show_default=False,
        ),
    ] = None,
    strip_icc: opt.StripIcc = False,
    output: Annotated[Optional[pathlib.Path], opt.output_option()] = None,
    output_name: Annotated[Optional[str], opt.output_name_option("strip", "{input_name}.{role}.{output_ext}")] = None,
    output_dir: Annotated[Optional[pathlib.Path], opt.output_dir_option("strip")] = None,
    no_output_dir: opt.NoOutputDir = False,
    recursive: opt.Recursive = False,
    force: opt.Force = False,
    yes: opt.Yes = False,
    dry_run: opt.DryRun = False,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
):
    """
    Remove metadata (camera, dates, GPS, titles, comments) from images, videos and audio without
    re-encoding: the pictures and sound are copied as they are (HEIC and AVIF too, gain maps
    and depth included). Writes
    [cyan]clip.mov.stripped.mov[/cyan] for clip.mov, in the file's own format, next to each file,
    under --output-dir, or to -o for a single file; [cyan]--output-name '{input_name}' -f[/cyan]
    strips files in place. Removal is best effort: it covers the places sashimi knows metadata
    is kept. A folder of both images and videos or audio asks first unless -y.
    """
    from sashimi.api.strip import strip_files_generator
    from sashimi.cli import _limits

    opt.single_file_or_fail(paths, output)
    limits = _limits.resolve()
    chosen = opt.enum_value(mode) or "remove_all"
    runner = BatchRunner(
        "strip",
        "Remove metadata",
        json_output,
        limit_output,
        dry_run=dry_run,
        yes=yes or force,
        verb="remove metadata from {every}",
    )
    naming = opt.naming_args("strip", output_name, output)
    with refusals():
        gen = strip_files_generator(
            paths,
            chosen,  # type: ignore[arg-type]
            keep_icc=not strip_icc,
            recursive=recursive,
            force=force,
            on_file_progress=runner.file_progress,
            output=output,
            **opt.output_dir_args("strip", output_dir, no_output_dir, output),
            dry_run=dry_run,
            limits=limits,
            **naming,
        )
    runner.run(gen, paths)
