# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""`sashimi extract`, `flatten` and `info`."""

from __future__ import annotations

import json
import pathlib
import time
from typing import Annotated, Optional

import typer

from sashimi.cli import _options as opt
from sashimi.cli._output import STATUS_STYLES, console, display_path, fail


def extract_cmd(
    path: Annotated[pathlib.Path, typer.Argument(help="Video file to cut from.", show_default=False)],
    start_arg: Annotated[Optional[str], typer.Argument(help="Start time (or use --start).", show_default=False)] = None,
    end_arg: Annotated[Optional[str], typer.Argument(help="End time (or use --end).", show_default=False)] = None,
    start: Annotated[
        Optional[str], typer.Option("--start", "-s", help="Start time, e.g. 42s, 1h22m, 00:24:09.")
    ] = None,
    end: Annotated[Optional[str], typer.Option("--end", "-e", help="End time, e.g. 50m42s, 00:50:42.")] = None,
    output: Annotated[
        Optional[pathlib.Path],
        opt.option("--output", "-o", help="Output path [default: <name>_extracted.<ext> next to the input]."),
    ] = None,
    force: opt.Force = False,
    json_output: opt.JsonOutput = False,
    workers: opt.Workers = None,
    accurate: Annotated[
        bool,
        typer.Option(
            "--accurate",
            help="Re-encode for an exact cut (slower, slightly lossy) instead of starting at the previous keyframe.",
            rich_help_panel=opt.VIDEO,
        ),
    ] = False,
    crf: opt.Crf = None,
    preset: opt.PresetOpt = None,
    speed: opt.Speed = None,
    audio_bitrate: opt.AudioBitrate = None,
    mute: opt.Mute = False,
):
    """
    Cut a section out of a video. By default it doesn't re-encode: instant and lossless, but cuts
    land on keyframes, so the clip may start before the requested time. [cyan]--accurate[/cyan]
    re-encodes for an exact cut; the encoder options only apply with it.

    [dim]Examples:  sashimi extract clip.mkv 00:24:09 00:50:42   ·   sashimi extract clip.mp4 -s 42s -e 1h22m --accurate[/dim]
    """
    final_start, final_end = start or start_arg, end or end_arg
    if not final_start or not final_end:
        fail("Both a start and an end time are needed, e.g. [cyan]sashimi extract clip.mp4 1m30s 2m[/cyan].")
    opt.exists_or_fail(path)
    encoder_flags = {"--crf": crf, "--preset": preset, "--speed": speed, "--audio-bitrate": audio_bitrate}
    encoder_flags["--mute"] = mute or None
    if not accurate and (used := [flag for flag, value in encoder_flags.items() if value is not None]):
        fail(f"{', '.join(used)} only {'applies' if len(used) == 1 else 'apply'} with --accurate (a re-encoded cut).")

    from sashimi.api.extract import extract

    if not json_output:
        console.print(
            f"[bold]✂️  Extracting [yellow]{final_start}[/yellow] ➔ [yellow]{final_end}[/yellow] from [cyan]{display_path(path)}[/cyan]...[/bold]"
        )
    began = time.perf_counter()
    status, message, target = extract(
        path,
        final_start,
        final_end,
        output_path=output,
        force=force,
        accurate=accurate,
        crf=crf,
        preset=opt.enum_value(preset),
        speed=speed,
        audio_bitrate_kbps=audio_bitrate,
        mute=mute,
        max_workers=workers,
    )
    elapsed = time.perf_counter() - began

    if json_output:
        print(json.dumps({"status": status, "message": message, "output": str(target), "seconds": round(elapsed, 2)}))
    else:
        console.print(f"{STATUS_STYLES.get(status, status)}: {message} [dim]({elapsed:.1f}s)[/dim]")
    if status == "error":
        raise typer.Exit(code=1)


def flatten_cmd(
    source_dir: Annotated[
        pathlib.Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, help="Directory to flatten.", show_default=False),
    ],
    force: Annotated[bool, typer.Option("-f", "--force", help="Don't ask for confirmation.")] = False,
):
    """
    Move every file from subdirectories into the directory itself, renaming on name clashes,
    then remove the emptied subdirectories. [bold yellow]Changes your files; asks first unless -f.[/bold yellow]
    """
    console.print(f"Directory to flatten: [cyan]{source_dir}[/cyan]")
    if not force:
        console.print(
            "\n[bold yellow]WARNING:[/bold yellow] This moves all files from subdirectories into "
            f"'[cyan]{source_dir.name}[/cyan]' and deletes empty subdirectories. Clashing names are renamed."
        )
        typer.confirm("Proceed?", abort=True)

    from sashimi.api.flatten import flatten

    try:
        stats = flatten(source_dir)
    except (FileNotFoundError, NotADirectoryError) as e:
        fail(str(e), code=1)
    console.print("[bold green]Flattening complete![/bold green]")
    console.print(
        f"Moved [green]{stats['moved']}[/green] files ([yellow]{stats['renamed']}[/yellow] renamed due to clashes); "
        f"removed [blue]{stats['removed_dirs']}[/blue] empty subdirectories."
    )


def info_cmd(
    path: Annotated[pathlib.Path, typer.Argument(help="Media file to inspect.", show_default=False)],
    json_output: opt.JsonOutput = False,
):
    """Show a media file's container, duration, dimensions, frame rate and codecs (via ffprobe)."""
    opt.exists_or_fail(path)
    from sashimi import probe
    from sashimi.core.sheets._render import format_duration, format_size
    from sashimi.errors import SashimiError, UserInputError

    try:
        info = probe(path)
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
    rows = [
        ("File", display_path(path)),
        ("Container", info.container),
        ("Duration", f"{format_duration(info.duration)} ({info.duration:.2f} s)" if info.duration else "—"),
        ("Size", format_size(info.size_bytes)),
        ("Bit rate", f"{info.bit_rate // 1000} kb/s" if info.bit_rate else "—"),
    ]
    if info.has_video:
        rotation = f" (rotated {info.rotation}°)" if info.rotation else ""
        rows += [
            ("Dimensions", f"{info.width}x{info.height}{rotation}"),
            ("Aspect ratio", f"{info.display_aspect_ratio:.3f}" if info.display_aspect_ratio else "—"),
            ("Frame rate", f"{info.fps:.3f}".rstrip("0").rstrip(".") + " fps" if info.fps else "—"),
            ("Video", info.video_codec or "—"),
        ]
    rows += [("Audio", info.audio_codec or "none"), ("Streams", ", ".join(info.codecs) or "—")]
    for label, value in rows:
        table.add_row(label, value)
    console.print(table)
