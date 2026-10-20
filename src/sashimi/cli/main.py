# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
from typing import Annotated, Optional

import typer

from sashimi._version import __version__
from sashimi.cli import config_cmd, convert_cmd, edit_cmd, files_cmd, thumbs_cmd


def _version_callback(value: bool) -> None:
    if value:
        from sashimi.cli._output import console

        console.print(f"sashimi {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="sashimi",
    help="Convert and edit images, video and audio, make video contact sheets and previews, and trim videos losslessly.",
    add_completion=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    no_args_is_help=True,
    rich_markup_mode="rich",
    epilog=f"[dim]Powered by sashimi v{__version__}[/dim]",
)


@app.callback()
def main_callback(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show the version and exit."),
    ] = None,
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            metavar="",
            show_default=False,
            help="More logging on stderr: -v for progress notes, -vv for debugging.",
        ),
    ] = 0,
):
    """
    sashimi: convert and edit images, video and audio, make video contact sheets and previews, and
    trim videos losslessly. Logging and warnings go to stderr, so --json output on stdout stays clean.
    """
    if verbose >= 2:
        log_level = logging.DEBUG
    elif verbose == 1:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING

    from rich.console import Console
    from rich.logging import RichHandler

    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False, console=Console(stderr=True))],
    )
    logger = logging.getLogger("sashimi")
    logger.setLevel(log_level)
    if verbose > 0:
        logger.info(f"Logging level set to {logging.getLevelName(log_level)}")


CONVERT, SHEETS, INSPECT, FILES, CONFIG = "Convert", "Sheets & previews", "Inspect", "Files", "Configuration"

app.command(
    name="convert",
    no_args_is_help=True,
    rich_help_panel=CONVERT,
    short_help="Convert images, videos and audio to another format.",
)(convert_cmd.convert_cmd)
app.command(
    name="resize",
    no_args_is_help=True,
    rich_help_panel=CONVERT,
    short_help="Shrink images to fit a width and/or height.",
)(convert_cmd.resize_cmd)
app.command(name="rotate", no_args_is_help=True, rich_help_panel=CONVERT, short_help="Rotate and/or flip videos.")(
    convert_cmd.rotate_cmd
)
app.command(
    name="edit",
    no_args_is_help=True,
    rich_help_panel=CONVERT,
    short_help="Rotate, flip, crop and adjust images, including every frame of GIFs and WebPs.",
)(edit_cmd.edit_cmd)
app.command(
    name="frames",
    no_args_is_help=True,
    rich_help_panel=CONVERT,
    short_help="Keep or drop frames of animated GIFs and WebPs, and change their speed.",
)(edit_cmd.frames_cmd)
app.command(
    name="favicon",
    no_args_is_help=True,
    rich_help_panel=CONVERT,
    short_help="Make a favicon pack (.zip): ICO, PNG icons, manifest and HTML tags.",
)(convert_cmd.favicon_cmd)
app.command(
    name="extract",
    no_args_is_help=True,
    rich_help_panel=CONVERT,
    short_help="Cut a section out of a video, losslessly.",
)(files_cmd.extract_cmd)
app.command(name="trim", no_args_is_help=True, hidden=True, short_help="Alias of extract.")(files_cmd.extract_cmd)
app.command(
    name="remux",
    no_args_is_help=True,
    rich_help_panel=CONVERT,
    short_help="Change a video's container (e.g. MP4 to MKV) without re-encoding.",
)(files_cmd.remux_cmd)
app.command(
    name="thumbs",
    no_args_is_help=True,
    rich_help_panel=SHEETS,
    short_help="Make video contact sheets (static or animated).",
    cls=thumbs_cmd.ThumbsCommand,
)(thumbs_cmd.thumbs_cmd)
app.command(
    name="preview",
    no_args_is_help=True,
    rich_help_panel=SHEETS,
    short_help="Make preview GIFs/WebPs from short clips across each video.",
)(thumbs_cmd.preview_cmd)
app.command(
    name="info",
    no_args_is_help=True,
    rich_help_panel=INSPECT,
    short_help="Show a media file's format, duration and codecs, or an image's frames.",
)(files_cmd.info_cmd)
app.command(
    name="flatten",
    no_args_is_help=True,
    rich_help_panel=FILES,
    short_help="Move files out of subdirectories into one directory.",
)(files_cmd.flatten_cmd)
app.add_typer(
    config_cmd.app,
    name="config",
    rich_help_panel=CONFIG,
    help="See every option's default, and change the CLI's in sashimi.toml.",
)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
