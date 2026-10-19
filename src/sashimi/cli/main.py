# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import sys
from typing import Annotated, Optional

import typer

from sashimi._version import __version__
from sashimi.cli import config_cmd, convert_cmd, files_cmd, thumbs_cmd


def _version_callback(value: bool) -> None:
    if value:
        from sashimi.cli._output import console

        console.print(f"sashimi {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="sashimi",
    help="Convert images, video and audio, make video contact sheets, and trim videos losslessly.",
    add_completion=False,
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
        typer.Option("--verbose", "-v", count=True, help="Increase logging verbosity. -v for INFO, -vv for DEBUG."),
    ] = 0,
):
    """
    sashimi: convert images, video and audio, make video contact sheets, and trim videos losslessly.
    """
    if verbose >= 2:
        log_level = logging.DEBUG
    elif verbose == 1:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING
    if "--json" in sys.argv:
        log_level = logging.CRITICAL

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


CONVERT, SHEETS, INSPECT, FILES, CONFIG = "Convert", "Contact sheets", "Inspect", "Files", "Configuration"

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
    name="extract",
    no_args_is_help=True,
    rich_help_panel=CONVERT,
    short_help="Cut a section out of a video, losslessly.",
)(files_cmd.extract_cmd)
app.command(name="trim", no_args_is_help=True, hidden=True, short_help="Alias of extract.")(files_cmd.extract_cmd)
app.command(
    name="thumbs",
    no_args_is_help=True,
    rich_help_panel=SHEETS,
    short_help="Make video contact sheets (static or animated).",
    cls=thumbs_cmd.ThumbsCommand,
)(thumbs_cmd.thumbs_cmd)
app.command(
    name="info",
    no_args_is_help=True,
    rich_help_panel=INSPECT,
    short_help="Show a media file's format, duration and codecs.",
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
