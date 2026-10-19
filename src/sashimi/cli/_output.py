# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Shared CLI plumbing: consoles, the batch progress/results renderer, and option validation errors."""

from __future__ import annotations

import json
import pathlib
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, NoReturn

import typer
from rich.console import Console

if TYPE_CHECKING:
    from pydantic import ValidationError

    from sashimi.api._batch import YieldType

console = Console()
err_console = Console(stderr=True)

STATUS_STYLES = {
    "success": "[bold green]Success[/bold green]",
    "exists": "[bold yellow]Exists (Skipped)[/bold yellow]",
    "skipped": "[bold bright_black]Skipped[/bold bright_black]",
    "error": "[bold red]Error[/bold red]",
}
STATUS_PRIORITY = {"error": 0, "skipped": 1, "exists": 2, "success": 3}


def display_path(path: pathlib.Path) -> str:
    try:
        return str(path.absolute().relative_to(pathlib.Path.cwd()))
    except ValueError:
        return str(path)


def fail(message: str, code: int = 2) -> NoReturn:
    err_console.print(f"[bold red]Error:[/bold red] {message}")
    raise typer.Exit(code=code)


def exit_on_invalid_options(err: ValidationError, flags: dict[str, str], model: Any = None) -> NoReturn:
    """Reports config validation errors (from CLI options or TOML files) without a traceback."""
    bound_messages = getattr(model, "bound_messages", {})
    seen = set()
    for detail in err.errors():
        field = str(detail["loc"][0]) if detail["loc"] else "options"
        if field in seen:
            continue
        seen.add(field)
        if detail["type"] == "sashimi_sentence":
            err_console.print(f"[bold red]Error:[/bold red] {detail['msg']}")
            continue
        flag = flags.get(field)
        name = f"{flag} ({field})" if flag else field
        reason = bound_messages.get(field) if detail["type"].startswith("less_than") else None
        err_console.print(f"[bold red]Invalid value for {name}:[/bold red] {reason or detail['msg']}")
    raise typer.Exit(code=2)


def warn_ignored(ignored: list[str], reason: str) -> None:
    if ignored:
        verb = "applies" if len(ignored) == 1 else "apply"
        err_console.print(f"[yellow]Warning: {', '.join(ignored)} only {verb} to {reason}; ignored.[/yellow]")


def warn_unused(ignored: list[str], output: str) -> None:
    if ignored:
        verb = "doesn't" if len(ignored) == 1 else "don't"
        err_console.print(f"[yellow]Warning: {', '.join(ignored)} {verb} apply to {output}; ignored.[/yellow]")


def warn_unknown_settings(path: pathlib.Path, keys: list[str], project: bool) -> None:
    if not keys:
        return
    dotted = next((key for key in keys if "." in key), None)
    hint = f" Remove one with `sashimi config unset{'' if project else ' -g'} {dotted}`." if dotted else ""
    err_console.print(
        f"[yellow]Warning: {path} has settings sashimi doesn't use: {', '.join(keys)}; they're ignored.{hint}[/yellow]",
        highlight=False,
    )


class BatchRunner:
    """
    Consumes a batch generator and renders a Rich progress bar (with per-file progress for long
    conversions) and a summary table, or a JSON payload. Exits 1 if any file failed.
    """

    def __init__(self, title: str, json_output: bool, limit_output: int = 50):
        self.title = title
        self.json_output = json_output
        self.limit_output = limit_output
        self._progress: Any = None
        self._task: Any = None
        self._current = ""

    def file_progress(self, path: pathlib.Path, fraction: float, stage: str) -> None:
        """Pass as ``on_file_progress`` to a batch generator."""
        if self._progress is not None and self._task is not None and stage != "done":
            self._progress.update(self._task, description=f"{stage.capitalize()} {self._current}: {fraction:.0%}")

    def run(self, generator: Iterator[YieldType]) -> None:
        start_time = time.perf_counter()
        results: list[tuple[pathlib.Path, str, str]] = []
        first = next(generator, 0)
        total_files = first if isinstance(first, int) else 0
        counts = {"success": 0, "error": 0, "exists": 0, "skipped": 0}
        started: set[pathlib.Path] = set()

        def record(item: YieldType) -> bool:
            """Collects a final result; returns True if it should advance the progress bar."""
            if not (isinstance(item, tuple) and len(item) == 3):
                return False
            file_path, status, message = item
            if status == "starting":
                started.add(file_path)
                name = file_path.name
                self._current = name if len(name) <= 35 else name[:32] + "..."
                return False
            counts[status] = counts.get(status, 0) + 1
            results.append((file_path, status, message))
            return file_path in started

        if self.json_output or total_files <= 0:
            if total_files <= 0 and not self.json_output:
                console.print("[yellow]No matching files found to process.[/yellow]")
            for item in generator:
                record(item)
        else:
            from rich.progress import (
                BarColumn,
                MofNCompleteColumn,
                Progress,
                SpinnerColumn,
                TaskProgressColumn,
                TextColumn,
                TimeElapsedColumn,
            )

            columns = (
                SpinnerColumn(),
                TextColumn("[cyan]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                MofNCompleteColumn(),
                TextColumn("•"),
                TimeElapsedColumn(),
            )
            with Progress(*columns, console=console, transient=False) as progress:
                self._progress = progress
                self._task = progress.add_task("Initializing...", total=total_files)
                for item in generator:
                    if isinstance(item, tuple) and len(item) == 3 and item[1] == "starting":
                        record(item)
                        progress.update(self._task, description=f"Processing: {self._current}")
                    elif record(item):
                        progress.update(self._task, advance=1, description="Finishing...")
                self._progress = None

        elapsed = time.perf_counter() - start_time
        avg = elapsed / counts["success"] if counts["success"] else 0.0
        if self.json_output:
            payload = {
                "summary": {
                    "total_found": total_files,
                    "success": counts["success"],
                    "exists": counts["exists"],
                    "skipped": counts["skipped"],
                    "errors": counts["error"],
                    "time_taken_seconds": round(elapsed, 2),
                    "avg_processing_time_seconds": round(avg, 2),
                },
                "results": [{"file": str(p), "status": s, "message": m} for p, s, m in results],
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            raise typer.Exit(code=0 if counts["error"] == 0 else 1)

        if results:
            self._print_summary(results, counts, elapsed, avg)
        if counts["error"]:
            raise typer.Exit(code=1)

    def _print_summary(self, results: list, counts: dict[str, int], elapsed: float, avg: float) -> None:
        from rich.panel import Panel
        from rich.table import Table

        grid = Table.grid(padding=(0, 4))
        for justify, style in (("right", "bold"), ("left", None), ("right", "bold"), ("left", None)):
            grid.add_column(justify=justify, style=style)  # type: ignore[arg-type]
        grid.add_row("[green]Success:[/green]", str(counts["success"]), "Total Time:", f"{elapsed:.1f}s")
        grid.add_row("[red]Errors:[/red]", str(counts["error"]), "Avg Time:", f"{avg:.1f}s/file" if avg else "N/A")
        grid.add_row("[yellow]Exists:[/yellow]", str(counts["exists"]), "", "")
        grid.add_row("[bright_black]Skipped:[/bright_black]", str(counts["skipped"]), "", "")
        console.print()
        console.print(Panel(grid, title=f"{self.title} Summary", border_style="blue", expand=False))
        console.print()

        results.sort(key=lambda r: (STATUS_PRIORITY.get(r[1], 4), r[0]))
        table = Table(show_header=True, header_style="bold magenta", expand=False)
        table.add_column("File", style="cyan", overflow="ellipsis")
        table.add_column("Status", style="magenta")
        table.add_column("Details", style="green", overflow="fold")
        for path, status, message in results[: self.limit_output]:
            style = STATUS_STYLES.get(status, f"[bold white]{status.capitalize()}[/bold white]")
            table.add_row(display_path(path), style, message)
        console.print(table)
        if len(results) > self.limit_output:
            console.print(
                f"[dim]Showing first {self.limit_output} of {len(results)} results. "
                "Use --limit-output to show more.[/dim]"
            )
