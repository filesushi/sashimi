# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Shared CLI plumbing: consoles, the batch progress/results renderer, and option validation errors.

Every message is printed as one line however long it is (a terminal wraps it on screen, a pipe or
a log gets it whole), and nothing is coloured by guesswork, so a path or a command can always be
copied as it is. Tables are printed only when every row fits the width on one line; otherwise the
same rows are printed one per line.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import pathlib
import time
import traceback
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape

if TYPE_CHECKING:
    from pydantic import ValidationError

    from sashimi.api._batch import YieldType

console = Console(soft_wrap=True, highlight=False)
err_console = Console(stderr=True, soft_wrap=True, highlight=False)

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


def refusal(error: Any) -> str:
    """A user error's message, followed by how to change the limit it names (``sashimi config set limits.…``), if any."""
    from sashimi.cli._limits import with_hint

    return with_hint(error.user_message, getattr(error, "limit", None))


def fail(message: str, code: int = 2, *, markup: bool = False) -> NoReturn:
    """Prints `message` as an error and exits with `code`. The message is plain text unless `markup`."""
    err_console.print(f"[bold red]Error:[/bold red] {message if markup else escape(message)}")
    raise typer.Exit(code=code)


@contextlib.contextmanager
def refusals() -> Iterator[None]:
    """Turns options a batch function refuses (:class:`~sashimi.errors.UserInputError`) into an error and exit code 2."""
    from sashimi.errors import UserInputError

    try:
        yield
    except UserInputError as e:
        fail(refusal(e))


def exit_on_invalid_options(err: ValidationError, flags: dict[str, str]) -> NoReturn:
    """Reports config validation errors (from CLI options or TOML files) without a traceback."""
    seen = set()
    for detail in err.errors():
        field = str(detail["loc"][0]) if detail["loc"] else "options"
        if field in seen:
            continue
        seen.add(field)
        if detail["type"] == "sashimi_sentence":
            err_console.print(f"[bold red]Error:[/bold red] {escape(detail['msg'])}")
            continue
        flag = flags.get(field)
        name = f"{flag} ({field})" if flag else field
        err_console.print(f"[bold red]Invalid value for {escape(name)}:[/bold red] {escape(detail['msg'])}")
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
    hint = f" Remove one with `sashimi config unset{' --project' if project else ''} {dotted}`." if dotted else ""
    err_console.print(
        f"[yellow]Warning: {path} has settings sashimi doesn't use: {', '.join(keys)}; they're ignored.{hint}[/yellow]",
        highlight=False,
    )


def print_fields(rows: Iterable[tuple[str, str]]) -> None:
    """
    Prints label and value rows (plain text) as two aligned columns, one line per row however long the
    value, so a path is never cut short or broken; a value of several lines keeps its lines, aligned.
    """
    from rich.cells import cell_len

    rows = list(rows)
    width = max((cell_len(label) for label, _ in rows), default=0)
    indent = "\n" + " " * (width + 6)
    for label, value in rows:
        pad = " " * (width - cell_len(label))
        console.print(f"  {pad}[bold cyan]{escape(label)}[/bold cyan]    {indent.join(escape(value).splitlines())}")


class LineLogHandler(logging.Handler):
    """Log records on stderr as one line each (time, level, message), never wrapped or coloured by guesswork."""

    def __init__(self, level: int = logging.NOTSET) -> None:
        super().__init__(level)
        self.console = Console(stderr=True, soft_wrap=True, highlight=False)

    def emit(self, record: logging.LogRecord) -> None:
        from datetime import datetime

        from rich.text import Text

        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
            name = record.levelname
            line = Text.assemble(
                (datetime.fromtimestamp(record.created).strftime("[%X] "), "log.time"),
                (name.ljust(8), f"logging.level.{name.lower()}"),
                " ",
                message,
            )
            self.console.print(line, markup=False)
        except Exception:
            self.handleError(record)


def print_json(payload: Any) -> None:
    """Prints a JSON report on stdout (logging and warnings go to stderr)."""
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def every(count: int) -> str:
    """How a question names `count` files: ``it``, ``both``, ``all 3``."""
    return "it" if count == 1 else "both" if count == 2 else f"all {count}"


def _stdin_is_terminal() -> bool:
    import sys

    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


class BatchRunner:
    """
    Consumes a batch generator and renders a Rich progress bar (with per-file progress for long
    conversions), then one line for a single file or a summary and table for several, or the
    JSON report (:func:`sashimi.api.build_report`). With `dry_run` the results are the plan.
    Problems come first: errors, then skipped files, then existing outputs, then successes.

    Before a run over a folder whose files take different routes (photos and videos through
    ``rotate``), it asks on stderr, unless `yes`; without a terminal to ask on it stops with exit
    code 2. A dry run shows the counts instead of asking.

    Exit codes: 0 when every file succeeded or already existed, when a file named on the command
    line is already what the command makes ("Already JPEG"), and when a directory search skipped
    files it can't use; 1 when any file failed, a file named on the command line was
    skipped (unsupported, too short, not animated, its output name taken by another input's, …),
    or the question was answered no; 2 when the options were refused before any file was
    processed, including a mixed folder without -y and without a terminal.
    """

    def __init__(
        self,
        command: str,
        title: str,
        json_output: bool,
        limit_output: int = 50,
        *,
        dry_run: bool = False,
        yes: bool = False,
        verb: str = "",
    ):
        self.command = command
        self.title = f"{title} (dry run)" if dry_run else title
        self.json_output = json_output
        self.limit_output = limit_output
        self.dry_run = dry_run
        self.yes = yes
        self.verb = verb or f"{command} {{every}}"
        self._progress: Any = None
        self._task: Any = None
        self._current = ""

    def file_progress(self, path: pathlib.Path, fraction: float, stage: str) -> None:
        """Pass as ``on_file_progress`` to a batch generator."""
        if self._progress is not None and self._task is not None and stage != "done":
            self._progress.update(self._task, description=f"{stage.capitalize()} {self._current}: {fraction:.0%}")

    def _plan(self, generator: Any) -> Any:
        """The batch's plan, or None for a plain generator; asks about mixed folders."""
        from sashimi.api._batch import Batch
        from sashimi.errors import UserInputError

        if not isinstance(generator, Batch):
            return None
        try:
            plan = generator.plan()
        except UserInputError as e:
            fail(refusal(e))
        if not (plan.searched and plan.mixed):
            return plan
        roots = [path for path in self._inputs if path.is_dir()]
        shown = display_path(roots[0]) if len(roots) == 1 else ""
        where = (
            "These folders contain"
            if len(roots) != 1
            else "This folder contains"
            if shown == "."
            else f"{shown} contains"
        )
        doing = self.verb.format(every=every(len(plan.to_process)))
        question = f"{where} {plan.describe()}; {doing}?"
        if self.dry_run:
            err_console.print(f"[bold]{escape(question[:-1])}[/bold] (without -y, sashimi asks first).")
            return plan
        if self.yes:
            return plan
        if not _stdin_is_terminal():
            fail(
                f"{where} {plan.describe()}, which {self.command} handles differently. "
                f"Pass -y to {doing}, or run in a terminal to be asked."
            )
        if not typer.confirm(question, default=False, err=True):
            err_console.print("Nothing was changed.")
            raise typer.Exit(code=1)
        return plan

    def run(self, generator: Iterator[YieldType], inputs: Iterable[pathlib.Path] = ()) -> None:
        """Runs the batch. `inputs` are the paths given on the command line, for the exit code and the question."""
        from sashimi.api._consume import build_report
        from sashimi.errors import UserInputError

        from sashimi.api._batch import FFMPEG_REFUSALS, LIMIT_HINT
        from sashimi.cli._limits import hint

        token = LIMIT_HINT.set(hint)
        self._refusals: dict[str, None] = {}
        gathered = FFMPEG_REFUSALS.set(None if self.json_output else self._refusals)
        try:
            self._run(generator, inputs)
        finally:
            LIMIT_HINT.reset(token)
            FFMPEG_REFUSALS.reset(gathered)

    def _run(self, generator: Iterator[YieldType], inputs: Iterable[pathlib.Path]) -> None:
        from sashimi.api._consume import build_report
        from sashimi.errors import UserInputError

        self._inputs = list(inputs)
        plan = self._plan(generator)
        start_time = time.perf_counter()
        results: list[tuple[pathlib.Path, str, str, pathlib.Path | None]] = []
        try:
            first = next(generator, 0)
        except UserInputError as e:
            fail(refusal(e))
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
        report = build_report(self.command, total_files, results, elapsed, dry_run=self.dry_run, plan=plan)
        done = set(plan.already_done) if plan is not None else set()
        named = {path for path in self._inputs if not path.is_dir() and path not in done}
        failed = report["summary"]["errors"] > 0 or any(
            status == "skipped" and path in named for path, status, _, _ in results
        )
        if self.json_output:
            print_json(report)
            raise typer.Exit(code=1 if failed else 0)

        clashed = {item.source for item in plan.clashes} if plan is not None else set()
        styles = DRY_RUN_STYLES if self.dry_run else STATUS_STYLES
        results.sort(key=lambda r: (STATUS_PRIORITY.get(r[1], 4), r[0] not in clashed, r[0]))
        if not results:
            console.print(f"[yellow]{escape(self._nothing_found(plan))}[/yellow]")
        elif len(results) == 1:
            path, status, message, _ = results[0]
            style = self._style(styles, status, path in clashed)
            timing = "" if self.dry_run else f" [dim]({elapsed:.1f}s)[/dim]"
            console.print(f"{style}: [cyan]{escape(display_path(path))}[/cyan]: {escape(message)}{timing}")
        elif self.dry_run:
            for path, status, message, _ in results[: self.limit_output]:
                style = self._style(styles, status, path in clashed)
                console.print(f"{style}: [cyan]{escape(display_path(path))}[/cyan]: {escape(message)}")
            self._more(results)
        else:
            self._print_summary(results, report["summary"], clashed)
        self._notes(plan)
        for message in self._refusals:
            console.print(f"[red]{escape(message)}[/red]")
        if failed:
            raise typer.Exit(code=1)

    @staticmethod
    def _style(styles: dict[str, str], status: str, clashed: bool) -> str:
        style = styles.get(status, f"[bold white]{status.capitalize()}[/bold white]")
        return f"{style} [yellow](name clash)[/yellow]" if clashed else style

    def _more(self, results: list) -> None:
        if len(results) <= self.limit_output:
            return
        hidden: dict[str, int] = {}
        for _, status, _, _ in results[self.limit_output :]:
            hidden[status] = hidden.get(status, 0) + 1
        counts = ", ".join(f"{number} {status}" for status, number in hidden.items())
        console.print(
            f"[dim]Showing the first {self.limit_output} of {len(results)} ({counts} not shown). "
            "Use --limit-output to show more.[/dim]"
        )

    def _nothing_found(self, plan: Any) -> str:
        """What to say when no file was found, naming what the command works on."""
        if plan is None or not plan.accepts:
            return "No matching files found to process."
        command = "thumbs" if self.command == "animated_thumbs" else self.command
        found = f"Found no files {command} works on ({plan.accepts})"
        if plan.in_output_dir:
            return f"{found} outside the output folder."
        if plan.searched and not plan.recursive:
            return f"{found}; -r searches subfolders too."
        return f"{found}."

    def _notes(self, plan: Any) -> None:
        """
        Lines under the results: earlier outputs of this command left out, files left out because
        they're in the output folder, then the pattern that made names clash.
        """
        if plan is None:
            return
        if plan.in_output_dir:
            count = len(plan.in_output_dir)
            shown = ", ".join(display_path(path) for path in plan.in_output_dir[:3])
            more = f" and {count - 3} more" if count > 3 else ""
            what = "1 file" if count == 1 else f"{count} files"
            console.print(
                f"[dim]Left out {what} already in the output folder: {escape(shown)}{more}. "
                "Name a file to process it anyway.[/dim]"
            )
        if plan.left_out:
            shown = ", ".join(display_path(path) for path, _ in plan.left_out[:3])
            more = f" and {len(plan.left_out) - 3} more" if len(plan.left_out) > 3 else ""
            count = len(plan.left_out)
            what = "1 earlier output" if count == 1 else f"{count} earlier outputs"
            console.print(
                f"[dim]Left out {what} of this command, made from other files of this run: {escape(shown)}{more}. "
                "To process one anyway, give it on its own.[/dim]"
            )
        if plan.clash_note:
            console.print(f"[yellow]{escape(plan.clash_note)}[/yellow]")
        if plan.folder_clash is not None:
            note, command = self._folder_note(plan)
            console.print(f"[yellow]{escape(note)}[/yellow]")
            if command is not None:
                console.print(f"  {escape(command)}", soft_wrap=True, highlight=False)

    def _folder_note(self, plan: Any) -> tuple[str, str | None]:
        """
        Says that files from different folders met in the output folder, and how to keep each folder's layout,
        with the command that does it (printed on a line of its own, never wrapped, so it can be copied whole).
        """
        clash = plan.folder_clash
        where = display_path(clash.output_dir)
        head = (
            f"Files from different folders also meet in {where}."
            if plan.clash_note
            else f"{len(clash.files)} files from different folders were skipped because they'd meet in {where}."
        )
        apart = "To keep them apart, run each folder on its own with its own --output-dir."
        if clash.holder is None:
            return f"{head} {apart}", None
        holder = str(clash.holder)
        if clash.same_files is False:
            return (
                f"{head} {apart} Naming {holder} with -r would keep each folder's layout too, but it wouldn't "
                "process the same files (-n shows which it would).",
                None,
            )
        if clash.same_files is None:
            return (
                f"{head} {apart} Naming {holder} with -r would keep each folder's layout too; -n shows what else it "
                "would process.",
                None,
            )
        command = self._rerun(clash.holder, plan.recursive)
        if command is None:
            how = "instead" if plan.recursive else "with -r instead"
            return f"{head} To keep each folder's layout there, run the command on {holder} {how}.", None
        how = " instead" if plan.recursive else ", with -r"
        return f"{head} To keep each folder's layout there, name the folder that holds them{how}:", command

    def _rerun(self, holder: pathlib.Path, recursive: bool) -> str | None:
        """The command as typed, with `holder` (and ``-r``) in place of the paths; None when that can't be told."""
        from sashimi.cli._options import _example, typed_command

        typed = typed_command()
        if typed is None:
            return None
        name, args = typed
        inputs = list(self._inputs)
        matched = [index for index, arg in enumerate(args) if arg and pathlib.Path(arg) in inputs]
        if not matched or sorted(pathlib.Path(args[index]) for index in matched) != sorted(inputs):
            return None
        word = str(holder)
        if word.startswith("-"):
            word = os.path.join(".", word)
        words = [arg for index, arg in enumerate(args) if index not in matched[1:]]
        first = matched[0]
        words[first] = word
        if not recursive:
            dashes = words.index("--") if "--" in words else len(words)
            words.insert(first + 1 if first < dashes else dashes, "-r")
        return _example(name, *words)

    def _print_summary(self, results: list, summary: dict[str, Any], clashed: set[pathlib.Path]) -> None:
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

        rows = [
            (display_path(path), self._style(STATUS_STYLES, status, path in clashed), message)
            for path, status, message, _ in results[: self.limit_output]
        ]
        if _table_fits(rows, ("File", "Status", "Details")):
            table = Table(show_header=True, header_style="bold magenta", expand=False)
            table.add_column("File", style="cyan", no_wrap=True)
            table.add_column("Status", style="magenta", no_wrap=True)
            table.add_column("Details", style="green", no_wrap=True)
            for shown, style, message in rows:
                table.add_row(escape(shown), style, escape(message))
            console.print(table)
        else:
            for shown, style, message in rows:
                console.print(f"{style}: [cyan]{escape(shown)}[/cyan]: {escape(message)}")
        self._more(results)


def _table_fits(rows: list[tuple[str, str, str]], headers: tuple[str, ...]) -> bool:
    """
    Whether a results table of `rows` (path, status markup, message) fits the console's width with
    every cell on one line, so no path or message is broken or cut short.
    """
    from rich.cells import cell_len
    from rich.text import Text

    widths = [cell_len(header) for header in headers]
    for shown, style, message in rows:
        cells = (shown, Text.from_markup(style).plain, message)
        for index, text in enumerate(cells):
            if "\n" in text:
                return False
            widths[index] = max(widths[index], cell_len(text))
    return sum(widths) + 3 * len(widths) + 1 <= console.width
