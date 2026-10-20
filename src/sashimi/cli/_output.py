# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Shared CLI plumbing: consoles, the batch progress/results renderer, and option validation errors."""

from __future__ import annotations

import json
import pathlib
import time
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape

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
DRY_RUN_STYLES = {**STATUS_STYLES, "success": "[bold cyan]Plan[/bold cyan]"}
STATUS_PRIORITY = {"error": 0, "skipped": 1, "exists": 2, "success": 3}


def display_path(path: pathlib.Path) -> str:
    try:
        return str(path.absolute().relative_to(pathlib.Path.cwd()))
    except ValueError:
        return str(path)


def fail(message: str, code: int = 2) -> NoReturn:
    err_console.print(f"[bold red]Error:[/bold red] {message}")
    raise typer.Exit(code=code)


def exit_on_invalid_options(err: ValidationError, flags: dict[str, str]) -> NoReturn:
    """Reports config validation errors (from CLI options or TOML files) without a traceback."""
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
        err_console.print(f"[bold red]Invalid value for {name}:[/bold red] {detail['msg']}")
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


def print_json(payload: Any) -> None:
    """Prints a JSON report on stdout (logging and warnings go to stderr)."""
    print(json.dumps(payload, indent=2, ensure_ascii=False))


class BatchRunner:
    """
    Consumes a batch generator and renders a Rich progress bar (with per-file progress for long
    conversions), then one line for a single file or a summary and table for several, or the
    JSON report (:func:`sashimi.api.build_report`). With `dry_run` the results are the plan.

    Exit codes: 0 when every file succeeded or already existed, and when a directory search
    skipped files it can't use; 1 when any file failed, or a file named on the command line was
    skipped (unsupported, too short, not animated, …); 2 when the options were refused before any
    file was processed.
    """

    def __init__(self, command: str, title: str, json_output: bool, limit_output: int = 50, *, dry_run: bool = False):
        self.command = command
        self.title = f"{title} (dry run)" if dry_run else title
        self.json_output = json_output
        self.limit_output = limit_output
        self.dry_run = dry_run
        self._progress: Any = None
        self._task: Any = None
        self._current = ""

    def file_progress(self, path: pathlib.Path, fraction: float, stage: str) -> None:
        """Pass as ``on_file_progress`` to a batch generator."""
        if self._progress is not None and self._task is not None and stage != "done":
            self._progress.update(self._task, description=f"{stage.capitalize()} {self._current}: {fraction:.0%}")

    def run(self, generator: Iterator[YieldType], inputs: Iterable[pathlib.Path] = ()) -> None:
        """Runs the batch. `inputs` are the paths given on the command line, for the exit code."""
        from sashimi.api._consume import build_report
        from sashimi.errors import UserInputError

        start_time = time.perf_counter()
        results: list[tuple[pathlib.Path, str, str, pathlib.Path | None]] = []
        try:
            first = next(generator, 0)
        except UserInputError as e:
            fail(e.user_message)
        total_files = first if isinstance(first, int) else 0
        started: set[pathlib.Path] = set()

        def record(item: YieldType) -> bool:
            """Collects a final result; returns True if it should advance the progress bar."""
            if not (isinstance(item, tuple) and len(item) == 4):
                return False
            file_path, status, _, _ = item
            if status == "starting":
                started.add(file_path)
                name = file_path.name
                self._current = name if len(name) <= 35 else name[:32] + "..."
                return False
            results.append(item)
            return file_path in started

        quiet = self.json_output or self.dry_run or total_files <= 0 or (total_files == 1 and not console.is_terminal)
        if quiet:
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

            if total_files > 1:
                console.print(f"[bold]{self.title}: {total_files} files[/bold]")
            columns = (
                SpinnerColumn(),
                TextColumn("[cyan]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                MofNCompleteColumn(),
                TextColumn("-"),
                TimeElapsedColumn(),
            )
            with Progress(*columns, console=console, transient=total_files == 1) as progress:
                self._progress = progress
                self._task = progress.add_task("Starting...", total=total_files)
                for item in generator:
                    if isinstance(item, tuple) and len(item) == 4 and item[1] == "starting":
                        record(item)
                        progress.update(self._task, description=f"Processing: {self._current}")
                    elif record(item):
                        progress.update(self._task, advance=1, description="Finishing...")
                self._progress = None

        elapsed = time.perf_counter() - start_time
        report = build_report(self.command, total_files, results, elapsed, dry_run=self.dry_run)
        named = {path for path in inputs if not path.is_dir()}
        failed = report["summary"]["errors"] > 0 or any(
            status == "skipped" and path in named for path, status, _, _ in results
        )
        if self.json_output:
            print_json(report)
            raise typer.Exit(code=1 if failed else 0)

        styles = DRY_RUN_STYLES if self.dry_run else STATUS_STYLES
        if not results:
            console.print("[yellow]No matching files found to process.[/yellow]")
        elif len(results) == 1:
            path, status, message, _ = results[0]
            style = styles.get(status, status)
            timing = "" if self.dry_run else f" [dim]({elapsed:.1f}s)[/dim]"
            console.print(f"{style}: [cyan]{escape(display_path(path))}[/cyan]: {escape(message)}{timing}")
        elif self.dry_run:
            for path, status, message, _ in sorted(results, key=lambda r: r[0])[: self.limit_output]:
                console.print(
                    f"{styles.get(status, status)}: [cyan]{escape(display_path(path))}[/cyan]: {escape(message)}"
                )
            if len(results) > self.limit_output:
                console.print(
                    f"[dim]Showing the first {self.limit_output} of {len(results)}. Use --limit-output to show more.[/dim]"
                )
        else:
            self._print_summary(results, report["summary"])
        if failed:
            raise typer.Exit(code=1)

    def _print_summary(self, results: list, summary: dict[str, Any]) -> None:
        from rich.panel import Panel
        from rich.table import Table

        elapsed, avg = summary["time_taken_seconds"], summary["avg_time_seconds"]
        grid = Table.grid(padding=(0, 4))
        for justify, style in (("right", "bold"), ("left", None), ("right", "bold"), ("left", None)):
            grid.add_column(justify=justify, style=style)  # type: ignore[arg-type]
        grid.add_row("[green]Success:[/green]", str(summary["success"]), "Total time:", f"{elapsed:.1f}s")
        grid.add_row("[red]Errors:[/red]", str(summary["errors"]), "Average:", f"{avg:.1f}s/file" if avg else "N/A")
        grid.add_row("[yellow]Exists:[/yellow]", str(summary["exists"]), "", "")
        grid.add_row("[bright_black]Skipped:[/bright_black]", str(summary["skipped"]), "", "")
        console.print()
        console.print(Panel(grid, title=f"{self.title} summary", border_style="blue", expand=False))
        console.print()

        results.sort(key=lambda r: (STATUS_PRIORITY.get(r[1], 4), r[0]))
        table = Table(show_header=True, header_style="bold magenta", expand=False)
        table.add_column("File", style="cyan", overflow="ellipsis")
        table.add_column("Status", style="magenta")
        table.add_column("Details", style="green", overflow="fold")
        for path, status, message, _ in results[: self.limit_output]:
            style = STATUS_STYLES.get(status, f"[bold white]{status.capitalize()}[/bold white]")
            table.add_row(escape(display_path(path)), style, escape(message))
        console.print(table)
        if len(results) > self.limit_output:
            console.print(
                f"[dim]Showing first {self.limit_output} of {len(results)} results. "
                "Use --limit-output to show more.[/dim]"
            )
