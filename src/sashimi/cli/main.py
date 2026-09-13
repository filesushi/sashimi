import pathlib
import time
import json
from typing import Annotated, List, Iterable, Optional
from enum import Enum

import typer
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
    SpinnerColumn
)

from sashimi.api import make_thumbs_generator, make_animated_thumbs_generator
from sashimi.file.thumbs import YieldType

app = typer.Typer(
    help="Sashimi: The robust video contact sheet generator.",
    no_args_is_help=True,
    add_completion=False
)
console = Console()

class OutputFormat(str, Enum):
    webp = "webp"
    gif = "gif"


def _process_generator_output(
    generator: Iterable[YieldType], 
    json_output: bool, 
    limit_output: int,
    command_name: str
) -> None:
    """Consumes the generator and renders either a Rich UI or raw JSON payload."""
    start_time = time.perf_counter()
    results_collector = []
    
    try:
        total_files = next(generator)
    except StopIteration:
        total_files = 0
        
    counts = {"success": 0, "error": 0, "exists": 0, "skipped": 0}

    if total_files <= 0:
        if not json_output:
            console.print("[yellow]No valid video files found to process.[/yellow]")
    else:
        if json_output:
            for item in generator:
                if isinstance(item, tuple) and len(item) == 3:
                    file_path, status, message = item
                    if status != "starting":
                        counts[status] = counts.get(status, 0) + 1
                        results_collector.append((file_path, status, message))
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
                    if isinstance(item, tuple) and len(item) == 3:
                        file_path, status, message = item
                        
                        if status == "starting":
                            display_name = file_path.name
                            if len(display_name) > 35:
                                display_name = display_name[:32] + "..."
                            progress.update(task_id, description=f"Processing: {display_name}")
                        else:
                            counts[status] = counts.get(status, 0) + 1
                            results_collector.append((file_path, status, message))
                            if status != "error" or "Path does not exist" not in message:
                                progress.update(task_id, advance=1)
                            progress.update(task_id, description="Finishing...")

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
                "avg_processing_time_seconds": round(avg_time, 2)
            },
            "results": [
                {"file": str(fpath), "status": status, "message": msg}
                for fpath, status, msg in results_collector
            ]
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        raise typer.Exit(code=0 if counts["error"] == 0 else 1)

    if results_collector:
        summary_grid = Table.grid(padding=(0, 4))
        summary_grid.add_column(justify="right", style="bold")
        summary_grid.add_column(justify="left")
        summary_grid.add_column(justify="right", style="bold")
        summary_grid.add_column(justify="left")

        summary_grid.add_row(
            "[green]Success:[/green]", str(counts['success']),
            "Total Time:", f"{elapsed_time:.1f}s"
        )
        summary_grid.add_row(
            "[red]Errors:[/red]", str(counts['error']),
            "Avg Time:", f"{avg_time:.1f}s/file" if avg_time else "N/A"
        )
        summary_grid.add_row("[yellow]Exists:[/yellow]", str(counts['exists']), "", "")
        summary_grid.add_row("[bright_black]Skipped:[/bright_black]", str(counts['skipped']), "", "")
        
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
            console.print(f"[dim]Showing first {limit_output} of {len(results_collector)} results. Use --limit-output to show more.[/dim]")


@app.command(name="thumbs")
def thumbs_cmd(
    paths: Annotated[List[pathlib.Path], typer.Argument(help="Paths to process.")],
    animated: Annotated[bool, typer.Option("-a", "--animated", help="Generate animated WebP/GIF grids")] = False,
    recursive: Annotated[bool, typer.Option("-r", "--recursive", help="Search directories recursively")] = False,
    force: Annotated[bool, typer.Option("-f", "--force", help="Overwrite existing contact sheets")] = False,
    width: Annotated[Optional[int], typer.Option("--width", help="Total width of the final image")] = None,
    cols: Annotated[Optional[int], typer.Option("--cols", help="Number of columns in grid")] = None,
    rows: Annotated[Optional[int], typer.Option("--rows", help="Number of rows in grid")] = None,
    gap: Annotated[Optional[int], typer.Option("--gap", help="Pixel gap between thumbnails")] = None,
    quality: Annotated[Optional[int], typer.Option("--quality", help="[Static] JPEG output quality (1-100)")] = None,
    output_format: Annotated[Optional[OutputFormat], typer.Option("--format", "-o", help="[Animated] Output format")] = None,
    duration: Annotated[Optional[float], typer.Option("--duration", help="[Animated] Duration of each clip in seconds")] = None,
    fps: Annotated[Optional[int], typer.Option("--fps", help="[Animated] Output framerate")] = None,
    no_loop: Annotated[bool, typer.Option("--no-loop", help="[Animated] Disable infinite looping for WebP/GIF")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Output raw JSON for scripting")] = False,
    limit_output: Annotated[int, typer.Option("--limit-output", "-l", help="Max rows to show in table")] = 50,
):
    """Generates video contact sheets."""
    mode_name = "Animated Thumbs" if animated else "Static Thumbs"
    if not json_output:
        icon = "🎬" if animated else "📸"
        console.print(f"[bold]{icon} Starting {mode_name.lower()} generation...[/bold]")

    if animated:
        format_val = output_format.value if output_format else None
        loop_val = False if no_loop else None
        gen = make_animated_thumbs_generator(
            paths=paths, recursive=recursive, force=force, output_format=format_val,
            output_width=width, cols=cols, rows=rows, gap=gap, 
            sample_duration=duration, output_fps=fps, loop=loop_val
        )
    else:
        gen = make_thumbs_generator(
            paths=paths, recursive=recursive, force=force, 
            output_width=width, cols=cols, rows=rows, gap=gap, jpeg_quality=quality
        )

    _process_generator_output(gen, json_output, limit_output, mode_name)


def main():
    app()

if __name__ == "__main__":
    main()