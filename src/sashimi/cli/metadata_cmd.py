# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""`sashimi metadata` (show what a file holds) and `sashimi strip` (remove it without re-encoding)."""

from __future__ import annotations

import pathlib
from typing import Annotated, List, Optional

import typer

from sashimi.cli import _options as opt
from sashimi.cli._output import BatchRunner, console, display_path, fail, refusals

_LABELS = (
    ("carriers", "Found in"),
    ("has_location", "Location"),
    ("place", "Place"),
    ("camera_make", "Camera make"),
    ("camera_model", "Camera model"),
    ("date_taken", "Taken"),
    ("software", "Software"),
    ("title", "Title"),
    ("artist", "Artist"),
    ("album", "Album"),
    ("creation_time", "Created"),
    ("comment", "Comment"),
    ("has_color_profile", "Colour profile"),
)


def metadata_cmd(
    path: Annotated[pathlib.Path, typer.Argument(help="Image, video or audio file.", show_default=False)],
    json_output: opt.JsonOutput = False,
):
    """
    Show the metadata a file holds: where it was found (EXIF, XMP, IPTC, QuickTime tags, ID3, …),
    any location, the camera, dates, software, and titles and comments. It shows what sashimi
    found, not a promise that nothing else is there.
    """
    opt.exists_or_fail(path)
    opt.file_or_fail(path, "metadata")
    from sashimi.config import Limits
    from sashimi.core.metadata import read
    from sashimi.errors import SashimiError, UserInputError

    try:
        info = read(path, limits=Limits.unlimited())
    except UserInputError as e:
        fail(e.user_message, code=1)
    except SashimiError as e:
        fail(str(e).splitlines()[0], code=1)
    if json_output:
        print(info.model_dump_json(indent=2))
        return

    from rich.table import Table

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    table.add_row("File", display_path(path))
    for field, label in _LABELS:
        value = getattr(info, field)
        if field == "carriers":
            table.add_row(label, ", ".join(value) if value else "nothing sashimi knows of")
        elif field == "has_location":
            if value and info.latitude is not None and info.longitude is not None:
                table.add_row(label, f"{info.latitude:.6f}, {info.longitude:.6f}")
            elif value:
                table.add_row(label, "yes")
        elif field == "has_color_profile":
            if value:
                table.add_row(label, "yes")
        elif value:
            table.add_row(label, str(value))
    for key, value in info.tags.items():
        table.add_row(key, value)
    console.print(table)


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
    max_pixels: opt.MaxPixels = None,
):
    """
    Remove metadata (camera, dates, GPS, titles, comments) from images, videos and audio without
    re-encoding: the pictures and sound are copied as they are. Writes
    [cyan]clip.mov.stripped.mov[/cyan] for clip.mov, in the file's own format, next to each file,
    under --output-dir, or to -o for a single file; [cyan]--output-name '{input_name}' -f[/cyan]
    strips files in place. Removal is best effort: it covers the places sashimi knows metadata
    is kept. A folder of both images and videos or audio asks first unless -y.
    """
    from sashimi.api.strip import strip_files_generator
    from sashimi.cli import _limits

    opt.single_file_or_fail(paths, output)
    limits = _limits.resolve(max_pixels=max_pixels)
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
