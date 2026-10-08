import pathlib
import time
import json
import sys
import logging
from typing import Annotated, List, Iterable, Iterator, Literal, NoReturn, Optional, cast
from enum import Enum

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    MofNCompleteColumn,
    TaskProgressColumn,
    SpinnerColumn,
)

from rich.logging import RichHandler
from sashimi.api import make_thumbs_generator, make_animated_thumbs_generator, flatten, extract
from sashimi.file.thumbs import YieldType

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()
err_console = Console(stderr=True)

# Maps config fields to the `thumbs` CLI flags that set them, for readable validation errors.
_CONFIG_FIELD_FLAGS = {
    "static_width": "--width",
    "animated_width": "--width",
    "cols": "--cols",
    "rows": "--rows",
    "gap": "--gap",
    "jpeg_quality": "--quality",
    "output_format": "--format",
    "sample_duration": "--duration",
    "output_fps": "--fps",
}


@app.callback()
def main_callback(
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            help="Increase logging verbosity. -v for INFO, -vv for DEBUG.",
        ),
    ] = 0,
):
    """
    Sashimi: The robust video contact sheet generator.
    """
    # Detect json flag directly from argv so we can suppress logs
    is_json = "--json" in sys.argv

    if verbose >= 2:
        log_level = logging.DEBUG
    elif verbose == 1:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING

    # Force logs to CRITICAL if outputting JSON so we don't break parsers[cite: 8]
    if is_json:
        log_level = logging.CRITICAL

    # Route logs to stderr so standard output stays clean for progress bars and JSON[cite: 8]
    log_console = Console(stderr=True)

    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                rich_tracebacks=True,
                show_path=False,
                console=log_console,
            )
        ],
    )

    logger = logging.getLogger("sashimi")
    logger.setLevel(log_level)

    if verbose > 0:
        logger.info(f"Logging level set to {logging.getLevelName(log_level)}")


class OutputFormat(str, Enum):
    webp = "webp"
    gif = "gif"


class ConvertFormat(str, Enum):
    jpeg = "jpeg"
    jpg = "jpg"
    png = "png"


def _process_generator_output(
    generator: Iterator[YieldType], json_output: bool, limit_output: int, command_name: str
) -> None:
    """Consumes the generator and renders either a Rich UI or raw JSON payload."""
    start_time = time.perf_counter()
    results_collector = []

    first = next(generator, 0)
    total_files = first if isinstance(first, int) else 0

    counts = {"success": 0, "error": 0, "exists": 0, "skipped": 0}
    # Only files announced with "starting" count towards the progress total; rejected inputs
    # (missing paths, non-video files) are still collected and reported.
    started: set = set()

    def record(item: YieldType) -> Optional[pathlib.Path]:
        """Collects a final result tuple; returns its path if it should advance the progress bar."""
        if not (isinstance(item, tuple) and len(item) == 3):
            return None
        file_path, status, message = item
        if status == "starting":
            started.add(file_path)
            return None
        counts[status] = counts.get(status, 0) + 1
        results_collector.append((file_path, status, message))
        return file_path if file_path in started else None

    if json_output or total_files <= 0:
        if total_files <= 0 and not json_output:
            console.print("[yellow]No valid files found to process.[/yellow]")
        for item in generator:
            record(item)
    else:
        progress_columns = (
            SpinnerColumn(),
            TextColumn("[cyan]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
        )
        with Progress(*progress_columns, console=console, transient=False) as progress:
            task_id = progress.add_task("Initializing...", total=total_files)

            for item in generator:
                if isinstance(item, tuple) and len(item) == 3 and item[1] == "starting":
                    display_name = item[0].name
                    if len(display_name) > 35:
                        display_name = display_name[:32] + "..."
                    progress.update(task_id, description=f"Processing: {display_name}")
                if record(item) is not None:
                    progress.update(task_id, advance=1, description="Finishing...")

    elapsed_time = time.perf_counter() - start_time
    successful_runs = counts["success"]
    avg_time = elapsed_time / successful_runs if successful_runs > 0 else 0.0

    if json_output:
        payload = {
            "summary": {
                "total_found": total_files,
                "success": counts["success"],
                "exists": counts["exists"],
                "skipped": counts["skipped"],
                "errors": counts["error"],
                "time_taken_seconds": round(elapsed_time, 2),
                "avg_processing_time_seconds": round(avg_time, 2),
            },
            "results": [
                {"file": str(fpath), "status": status, "message": msg} for fpath, status, msg in results_collector
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        raise typer.Exit(code=0 if counts["error"] == 0 else 1)

    if results_collector:
        summary_grid = Table.grid(padding=(0, 4))
        summary_grid.add_column(justify="right", style="bold")
        summary_grid.add_column(justify="left")
        summary_grid.add_column(justify="right", style="bold")
        summary_grid.add_column(justify="left")

        summary_grid.add_row("[green]Success:[/green]", str(counts["success"]), "Total Time:", f"{elapsed_time:.1f}s")
        summary_grid.add_row(
            "[red]Errors:[/red]", str(counts["error"]), "Avg Time:", f"{avg_time:.1f}s/file" if avg_time else "N/A"
        )
        summary_grid.add_row("[yellow]Exists:[/yellow]", str(counts["exists"]), "", "")
        summary_grid.add_row("[bright_black]Skipped:[/bright_black]", str(counts["skipped"]), "", "")

        console.print()
        console.print(Panel(summary_grid, title=f"{command_name} Summary", border_style="blue", expand=False))
        console.print()

        status_colors = {
            "success": "[bold green]Success[/bold green]",
            "exists": "[bold yellow]Exists (Skipped)[/bold yellow]",
            "skipped": "[bold bright_black]Skipped[/bold bright_black]",
            "error": "[bold red]Error[/bold red]",
        }

        status_priority = {"error": 0, "skipped": 1, "exists": 2, "success": 3}
        results_collector.sort(key=lambda x: (status_priority.get(x[1], 4), x[0]))

        table = Table(show_header=True, header_style="bold magenta", expand=False)
        table.add_column("File", style="cyan", overflow="ellipsis")
        table.add_column("Status", style="magenta")
        table.add_column("Details", style="green", overflow="fold")

        for i, (fpath, status, msg) in enumerate(results_collector):
            if i >= limit_output:
                break
            status_style = status_colors.get(status, f"[bold white]{status.capitalize()}[/bold white]")
            try:
                display_path = str(fpath.relative_to(pathlib.Path.cwd().resolve()))
            except ValueError:
                display_path = str(fpath)
            table.add_row(display_path, status_style, msg)

        console.print(table)

        if len(results_collector) > limit_output:
            console.print(
                f"[dim]Showing first {limit_output} of {len(results_collector)} results. Use --limit-output to show more.[/dim]"
            )

    if counts["error"]:
        raise typer.Exit(code=1)


def _exit_on_invalid_config(err: ValidationError) -> NoReturn:
    """Reports config validation errors (from CLI options or TOML files) without a traceback."""
    seen = set()
    for detail in err.errors():
        field = str(detail["loc"][1]) if len(detail["loc"]) > 1 else str(detail["loc"][0])
        if field in seen:
            continue
        seen.add(field)
        flag = _CONFIG_FIELD_FLAGS.get(field)
        name = f"{flag} ({field})" if flag else field
        err_console.print(f"[bold red]Invalid value for {name}:[/bold red] {detail['msg']}")
    raise typer.Exit(code=2)


@app.command(name="thumbs")
def thumbs_cmd(
    paths: Annotated[List[pathlib.Path], typer.Argument(help="Paths to process.")],
    animated: Annotated[bool, typer.Option("-a", "--animated", help="Generate animated WebP/GIF grids")] = False,
    recursive: Annotated[bool, typer.Option("-r", "--recursive", help="Search directories recursively")] = False,
    force: Annotated[bool, typer.Option("-f", "--force", help="Overwrite existing contact sheets")] = False,
    width: Annotated[Optional[int], typer.Option("--width", help="Total width of the final image")] = None,
    cols: Annotated[Optional[str], typer.Option("--cols", help="Number of columns in grid (or 'auto')")] = None,
    rows: Annotated[Optional[str], typer.Option("--rows", help="Number of rows in grid (or 'auto')")] = None,
    gap: Annotated[Optional[int], typer.Option("--gap", help="Pixel gap between thumbnails")] = None,
    quality: Annotated[Optional[int], typer.Option("--quality", help="[Static] JPEG output quality (1-100)")] = None,
    output_format: Annotated[
        Optional[OutputFormat], typer.Option("--format", "-o", help="[Animated] Output format")
    ] = None,
    duration: Annotated[
        Optional[float], typer.Option("--duration", help="[Animated] Duration of each clip in seconds")
    ] = None,
    fps: Annotated[Optional[int], typer.Option("--fps", help="[Animated] Output framerate")] = None,
    no_loop: Annotated[
        bool, typer.Option("--no-loop", help="[Animated] Disable infinite looping for WebP/GIF")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Output raw JSON for scripting")] = False,
    limit_output: Annotated[int, typer.Option("--limit-output", "-l", help="Max rows to show in table")] = 50,
):
    """Generates video contact sheets."""
    mode_name = "Animated Thumbs" if animated else "Static Thumbs"
    if not json_output:
        icon = "🎬" if animated else "📸"
        console.print(f"[bold]{icon} Starting {mode_name.lower()} generation...[/bold]")

    mode_only_options = (
        {"--quality": quality}
        if animated
        else {"--format": output_format, "--duration": duration, "--fps": fps, "--no-loop": no_loop or None}
    )
    ignored = [flag for flag, value in mode_only_options.items() if value is not None]
    if ignored:
        other_mode = "static" if animated else "animated (-a)"
        err_console.print(
            f"[yellow]Warning: {', '.join(ignored)} only appl{'ies' if len(ignored) == 1 else 'y'} "
            f"to {other_mode} mode and will be ignored.[/yellow]"
        )

    try:
        if animated:
            format_val = cast(Literal["webp", "gif"], output_format.value) if output_format else None
            loop_val = False if no_loop else None
            gen = make_animated_thumbs_generator(
                paths=paths,
                recursive=recursive,
                force=force,
                output_format=format_val,
                animated_width=width,
                cols=cols,
                rows=rows,
                gap=gap,
                sample_duration=duration,
                output_fps=fps,
                loop=loop_val,
            )
        else:
            gen = make_thumbs_generator(
                paths=paths,
                recursive=recursive,
                force=force,
                static_width=width,
                cols=cols,
                rows=rows,
                gap=gap,
                jpeg_quality=quality,
            )
    except ValidationError as e:
        _exit_on_invalid_config(e)

    _process_generator_output(gen, json_output, limit_output, mode_name)


@app.command(name="flatten")
def flatten_cmd(
    source_dir: Annotated[
        pathlib.Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            resolve_path=True,
            help="The directory path to flatten.",
        ),
    ],
    force: Annotated[
        bool, typer.Option("-f", "--force", help="Force the operation without interactive confirmation.")
    ] = False,
):
    """
    Flattens a directory structure by moving all files from subdirectories
    into the source directory root.

    WARNING: This operation modifies the directory structure and renames
    files in case of conflicts. It can be destructive. Use with caution.
    Empty subdirectories will be removed.
    """
    console.print(f"Target directory to flatten: [cyan]{source_dir}[/cyan]")

    if not force:
        console.print(
            "\n[bold yellow]WARNING:[/bold yellow] This will move all files from subdirectories "
            f"into '[cyan]{source_dir.name}[/cyan]' and delete empty subdirectories."
        )
        console.print("Files with conflicting names will be renamed.")
        typer.confirm("Are you sure you want to proceed?", abort=True)

    console.print("\nStarting flattening process...")
    try:
        stats = flatten(source_dir)
        console.print("[bold green]Flattening complete![/bold green]")

        console.print(
            f"Moved [green]{stats['moved']}[/green] files ([yellow]{stats['renamed']}[/yellow] renamed due to conflicts)."
        )
        console.print(f"Removed [blue]{stats['removed_dirs']}[/blue] empty subdirectories.")

    except (FileNotFoundError, NotADirectoryError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1) from None
    except Exception as e:
        console.print(f"[bold red]An unexpected error occurred:[/bold red] {e}")
        raise typer.Exit(code=1) from None


@app.command(name="extract")
def extract_cmd(
    path: Annotated[pathlib.Path, typer.Argument(help="Path to the video file to extract a segment from.")],
    start_arg: Annotated[Optional[str], typer.Argument(help="Start time offset (positional alternative).")] = None,
    end_arg: Annotated[Optional[str], typer.Argument(help="End time offset (positional alternative).")] = None,
    start: Annotated[
        Optional[str], typer.Option("--start", "-s", help="Start time offset (e.g., 42s, 1h22m, 00:24:09).")
    ] = None,
    end: Annotated[Optional[str], typer.Option("--end", "-e", help="End time offset (e.g., 50m42s, 00:50:42).")] = None,
    output: Annotated[
        Optional[pathlib.Path],
        typer.Option("--output", "-o", help="Explicit path for the output file. Defaults to appending '_extracted'."),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Force overwrite if the destination file already exists.")
    ] = False,
):
    """
    Extracts a specific portion of a video file without re-encoding.
    """
    final_start = start or start_arg
    final_end = end or end_arg

    if not final_start or not final_end:
        console.print("[bold red]Error: Both start and end times must be specified.[/bold red]")
        console.print(
            "Usage examples:\n  sashimi extract video.mp4 00:24:09 00:50:42\n  sashimi extract video.mp4 -s 42s -e 1h22m"
        )
        raise typer.Exit(code=1)

    resolved_path = path.resolve()

    if not resolved_path.exists():
        console.print(f"[bold red]Error: Input file does not exist: {resolved_path}[/bold red]")
        raise typer.Exit(code=1)

    console.print("[bold]🎬 Starting video extraction...[/bold]")
    console.print(f"Input File: [cyan]{resolved_path}[/cyan]")
    console.print(f"Requested Range: [yellow]{final_start}[/yellow] ➔ [yellow]{final_end}[/yellow]")

    start_time = time.perf_counter()

    # Route directly through the API facade
    status, message, target_output = extract(
        input_path=resolved_path, start_str=final_start, end_str=final_end, output_path=output, force=force
    )

    elapsed_time = time.perf_counter() - start_time

    success_count = 1 if status == "success" else 0
    error_count = 1 if status == "error" else 0
    exists_count = 1 if status == "exists" else 0

    summary_text = (
        f"Success:    {success_count}    Total Time:    {elapsed_time:.1f}s\n"
        f" Errors:    {error_count}\n"
        f" Exists:    {exists_count}"
    )

    console.print("\n")
    console.print(Panel(summary_text, title="Extraction Summary", expand=False))
    console.print("\nVideo Extraction Details")

    table = Table()
    table.add_column("File", style="cyan", min_width=30)
    table.add_column("Status", style="magenta")
    table.add_column("Details", style="green")

    status_colors = {
        "success": "[bold green]Success[/bold green]",
        "exists": "[bold yellow]Exists (Skipped)[/bold yellow]",
        "error": "[bold red]Error[/bold red]",
    }

    status_styled = status_colors.get(status, f"[bold white]{status.capitalize()}[/bold white]")

    try:
        display_path = str(resolved_path.relative_to(pathlib.Path.cwd().resolve()))
    except ValueError:
        display_path = str(resolved_path)

    table.add_row(display_path, status_styled, message)
    console.print(table)

    if status == "error":
        raise typer.Exit(code=1)

    console.print("\n[bold]✨ Extraction complete.[/bold]")


@app.command(name="convert")
def convert_cmd(
    path: Annotated[pathlib.Path, typer.Argument(help="Path to directory or file to process.")],
    target_format: Annotated[ConvertFormat, typer.Argument(help="Target output format.")],
    recursive: Annotated[bool, typer.Option("-r", "--recursive", help="Search directories recursively")] = False,
    force: Annotated[bool, typer.Option("-f", "--force", help="Overwrite existing files")] = False,
    width: Annotated[Optional[int], typer.Option("--width", "-w", help="Target width in pixels")] = None,
    height: Annotated[Optional[int], typer.Option("--height", "-h", help="Target height in pixels")] = None,
    quality: Annotated[
        Optional[str], typer.Option("--quality", "-q", help="[JPEG] Output quality (e.g. '85%', '0.85', '85')")
    ] = None,
    progressive: Annotated[bool, typer.Option("--progressive", help="[JPEG] Save as progressive JPEG")] = False,
    optimize: Annotated[
        bool, typer.Option("--optimize/--no-optimize", help="[PNG] Optimize palette/compression")
    ] = True,
    compress_level: Annotated[int, typer.Option("--compress-level", help="[PNG] Compression level 0-9")] = 6,
    json_output: Annotated[bool, typer.Option("--json", help="Output raw JSON for scripting")] = False,
    limit_output: Annotated[int, typer.Option("--limit-output", "-l", help="Max rows to show in table")] = 50,
):
    """
    Converts images to the specified format, with optional proportional scaling.
    """
    from sashimi.api.convert import make_to_jpeg_generator, make_to_png_generator

    if not json_output:
        console.print(f"[bold]🔄 Starting image conversion to {target_format.value.upper()}...[/bold]")

    if target_format in (ConvertFormat.jpeg, ConvertFormat.jpg):
        gen = make_to_jpeg_generator(
            paths=path,
            recursive=recursive,
            force=force,
            width=width,
            height=height,
            quality=quality,
            progressive=progressive,
        )
        mode_name = "Convert to JPEG"
    elif target_format == ConvertFormat.png:
        gen = make_to_png_generator(
            paths=path,
            recursive=recursive,
            force=force,
            width=width,
            height=height,
            optimize=optimize,
            compress_level=compress_level,
        )
        mode_name = "Convert to PNG"

    _process_generator_output(gen, json_output, limit_output, mode_name)


def main():
    app()


if __name__ == "__main__":
    main()
