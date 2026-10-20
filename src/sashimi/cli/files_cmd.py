# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""`sashimi extract`, `remux`, `flatten` and `info`."""

from __future__ import annotations

import pathlib
from enum import Enum
from typing import Annotated, Any, List, Optional

import typer

from sashimi.cli import _options as opt
from sashimi.cli._output import BatchRunner, STATUS_STYLES, console, display_path, err_console, fail, print_json


class Container(str, Enum):
    mkv = "mkv"
    mp4 = "mp4"
    webm = "webm"
    mov = "mov"


def extract_cmd(
    path: Annotated[pathlib.Path, typer.Argument(help="Video file (or directory) to cut from.", show_default=False)],
    start_arg: Annotated[Optional[str], typer.Argument(help="Start time (or use --start).", show_default=False)] = None,
    end_arg: Annotated[Optional[str], typer.Argument(help="End time (or use --end).", show_default=False)] = None,
    start: Annotated[
        Optional[str], opt.option("--start", "-s", help="Start time, e.g. 42s, 1h22m, 00:24:09 [default: 0].")
    ] = None,
    end: Annotated[
        Optional[str], opt.option("--end", "-e", help="End time, e.g. 50m42s, 00:50:42 [default: the end].")
    ] = None,
    duration: Annotated[
        Optional[str],
        opt.option(
            "--duration", "-d", help="Length of the clip instead of an end time, e.g. 30s [default: to the end]."
        ),
    ] = None,
    output_format: Annotated[
        Optional[Container],
        opt.format_option("Container: mkv, mp4, webm or mov [default: from -o, else the input's, else mkv]."),
    ] = None,
    output: Annotated[Optional[pathlib.Path], opt.output_option("<name>.extracted.<ext> next to the input")] = None,
    output_dir: opt.OutputDir = None,
    recursive: opt.Recursive = False,
    force: opt.Force = False,
    dry_run: opt.DryRun = False,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
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
    metadata: opt.MetadataOpt = None,
):
    """
    Cut a section out of a video, from START (default the beginning) to END, or for --duration;
    with neither, to the end. By default it doesn't re-encode: instant and lossless, but cuts
    land on keyframes, so the clip may start before the requested time. [cyan]--accurate[/cyan]
    re-encodes for an exact cut; the encoder options only apply with it. Writes
    [cyan]<name>.extracted.<ext>[/cyan] next to the video, under --output-dir, or to -o.

    [dim]Examples:  sashimi extract clip.mkv 00:24:09 00:50:42   ·   sashimi extract clip.mp4 -s 1h -d 90s --accurate   ·   sashimi extract clip.mp4 10m[/dim]
    """
    final_start, final_end = start or start_arg, end or end_arg
    if final_start is None and final_end is None and duration is None:
        fail("Give a start time, an end time or a --duration, e.g. [cyan]sashimi extract clip.mp4 1m30s 2m[/cyan].")
    opt.single_file_or_fail([path], output)
    encoder_flags = {"--crf": crf, "--preset": preset, "--speed": speed, "--audio-bitrate": audio_bitrate}
    encoder_flags["--mute"] = mute or None
    if not accurate and (used := [flag for flag, value in encoder_flags.items() if value is not None]):
        fail(f"{', '.join(used)} only {'applies' if len(used) == 1 else 'apply'} with --accurate (a re-encoded cut).")

    from sashimi.api.extract import extract_generator
    from sashimi.cli import _limits
    from sashimi.errors import UserInputError

    limits = _limits.resolve()
    runner = BatchRunner("extract", "Extract", json_output, limit_output, dry_run=dry_run)
    try:
        gen = extract_generator(
            path,
            final_start,
            final_end,
            duration=duration,
            container=opt.enum_value(output_format),
            accurate=accurate,
            crf=crf,
            preset=opt.enum_value(preset),
            speed=speed,
            audio_bitrate_kbps=audio_bitrate,
            mute=mute,
            metadata=opt.enum_value(metadata) or "keep",
            recursive=recursive,
            force=force,
            max_workers=workers,
            on_file_progress=runner.file_progress,
            output=output,
            output_dir=output_dir,
            dry_run=dry_run,
            limits=limits,
        )
    except UserInputError as e:
        fail(e.user_message)
    runner.run(gen, [path])


def remux_cmd(
    paths: Annotated[List[pathlib.Path], typer.Argument(help="Video files or directories.", show_default=False)],
    output_format: Annotated[
        Optional[Container], opt.format_option("Container: mkv, mp4, webm or mov [default: mkv, or from -o].")
    ] = None,
    output: Annotated[Optional[pathlib.Path], opt.output_option("<name>.mkv next to the input")] = None,
    output_dir: opt.OutputDir = None,
    recursive: opt.Recursive = False,
    force: opt.Force = False,
    dry_run: opt.DryRun = False,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
    metadata: opt.MetadataOpt = None,
):
    """
    Copy each video into another container without re-encoding: instant, lossless, and every
    stream is kept (all audio tracks, subtitles, attachments). MP4 subtitles are converted to
    SRT for MKV. Videos the container can't hold are reported, and nothing is written for them.
    Writes [cyan]<name>.mkv[/cyan] next to each video, under --output-dir, or to -o for a single
    video. Source files are never changed or removed.
    """
    from sashimi.api.remux import remux_files_generator
    from sashimi.cli import _limits

    opt.single_file_or_fail(paths, output)
    limits = _limits.resolve()
    suffixes = {".mkv": "mkv", ".mp4": "mp4", ".m4v": "mp4", ".webm": "webm", ".mov": "mov"}
    container = opt.format_from_output(opt.enum_value(output_format), output, suffixes) or "mkv"
    runner = BatchRunner("remux", f"Remux to {container.upper()}", json_output, limit_output, dry_run=dry_run)
    gen = remux_files_generator(
        paths,
        container,  # type: ignore[arg-type]
        metadata=opt.enum_value(metadata) or "keep",
        recursive=recursive,
        force=force,
        on_file_progress=runner.file_progress,
        output=output,
        output_dir=output_dir,
        dry_run=dry_run,
        limits=limits,
    )
    runner.run(gen, paths)


def flatten_cmd(
    source_dir: Annotated[
        pathlib.Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, help="Directory to flatten.", show_default=False),
    ],
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Show what would move, and change nothing.")] = False,
    force: Annotated[bool, typer.Option("-f", "--force", help="Don't ask for confirmation.")] = False,
    json_output: opt.JsonOutput = False,
    limit_output: opt.LimitOutput = 50,
):
    """
    Move every file from subdirectories into the directory itself, then remove the emptied
    subdirectories. The whole plan is made first; clashing names get the subfolder as a prefix
    (a/x.txt becomes a_x.txt), and nothing is ever overwritten. Symbolic links, and the files
    and folders they point to, stay where they are so links keep working.
    [bold yellow]Changes your files; asks first unless -f or --dry-run.[/bold yellow]
    """
    from sashimi.api.flatten import flatten, plan_flatten
    from sashimi.errors import SashimiError, UserInputError

    try:
        plan = plan_flatten(source_dir)
    except UserInputError as e:
        fail(e.user_message, code=1)
    except SashimiError as e:
        fail(str(e).splitlines()[0], code=1)
    if not dry_run and not force:
        if not plan.moves:
            err_console.print("Nothing to move.")
        else:
            err_console.print(
                f"[bold yellow]This moves {len(plan.moves)} files from the subdirectories of "
                f"'{display_path(source_dir)}' into it ({len(plan.renamed)} renamed) and deletes the "
                "emptied subdirectories.[/bold yellow] Use --dry-run to see the plan."
            )
            typer.confirm("Proceed?", abort=True, err=True)

    report = flatten(source_dir, dry_run=dry_run)
    if json_output:
        print_json(report)
    else:
        from rich.markup import escape

        rows = report["results"]
        for row in rows[:limit_output]:
            status = STATUS_STYLES.get(row["status"], row["status"])
            name = escape(display_path(pathlib.Path(row["file"])))
            console.print(f"{status}: [cyan]{name}[/cyan]: {escape(row['message'])}")
        if len(rows) > limit_output:
            console.print(
                f"[dim]Showing the first {limit_output} of {len(rows)}. Use --limit-output to show more.[/dim]"
            )
        summary = report["summary"]
        console.print(
            f"[bold]{'Would move' if dry_run else 'Moved'} {summary['success']} files[/bold] "
            f"({len(report['renamed'])} renamed), left {summary['skipped']} in place, {summary['errors']} errors; "
            f"{'would remove' if dry_run else 'removed'} {len(report['removed_dirs'])} empty subdirectories."
        )
    if report["summary"]["errors"]:
        raise typer.Exit(code=1)


def info_cmd(
    path: Annotated[pathlib.Path, typer.Argument(help="Media file to inspect.", show_default=False)],
    json_output: opt.JsonOutput = False,
):
    """
    Show a media file's container, duration, dimensions, frame rate and codecs (via ffprobe), or
    an image's format, size and frames, with each frame's duration, numbered from 1 as
    [bold]sashimi frames[/bold] takes them.
    """
    opt.exists_or_fail(path)
    from sashimi import probe
    from sashimi.config import Limits
    from sashimi.core.edit import frames_info
    from sashimi.core.sheets._render import format_duration, format_size
    from sashimi.errors import SashimiError, UnsupportedMediaError, UserInputError

    try:
        image_info = frames_info(path, limits=Limits.unlimited())
    except UnsupportedMediaError:
        image_info = None
    except UserInputError as e:
        fail(e.user_message, code=1)
    if image_info is not None:
        _print_image_info(path, image_info, json_output, format_size(path.stat().st_size))
        return

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


MAX_FRAME_RUNS = 10


def _frame_runs(durations: list[int]) -> str:
    """Frame durations as runs numbered from 1 ("1-3: 70 ms, 4: 60 ms"), or their range if there are many."""
    runs: list[tuple[int, int, int]] = []
    for number, duration in enumerate(durations, start=1):
        if runs and runs[-1][2] == duration:
            runs[-1] = (runs[-1][0], number, duration)
        else:
            runs.append((number, number, duration))
    if len(runs) > MAX_FRAME_RUNS:
        return f"{min(durations)}-{max(durations)} ms (each frame's: sashimi info --json)"
    return ", ".join(f"{a}: {d} ms" if a == b else f"{a}-{b}: {d} ms" for a, b, d in runs)


def _print_image_info(path: pathlib.Path, info: Any, json_output: bool, size: str) -> None:
    if json_output:
        print(info.model_dump_json(indent=2))
        return
    from rich.markup import escape
    from rich.table import Table

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    rows = [
        ("File", display_path(path)),
        ("Format", info.format.upper()),
        ("Size", size),
        ("Dimensions", f"{info.width}x{info.height}"),
        ("Frames", str(info.frames)),
    ]
    if info.animated:
        loop = "plays once" if info.loop is None else "loops forever" if info.loop == 0 else f"loops {info.loop} times"
        rows += [
            ("Duration", f"{sum(info.durations_ms) / 1000:.2f} s, {loop}"),
            ("Frame durations", _frame_runs(info.durations_ms)),
        ]
    for label, value in rows:
        table.add_row(label, escape(value))
    console.print(table)
