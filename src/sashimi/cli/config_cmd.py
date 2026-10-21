# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""`sashimi config`: view and change the CLI's TOML defaults."""

from __future__ import annotations

import json
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any, NoReturn, Optional

import typer

from sashimi.cli._output import console, fail

if TYPE_CHECKING:
    from pydantic import BaseModel

app = typer.Typer(
    help="Show and change the CLI's defaults in sashimi.toml.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


class Operation(str, Enum):
    convert = "convert"
    remux = "remux"
    resize = "resize"
    rotate = "rotate"
    favicon = "favicon"
    edit = "edit"
    frames = "frames"
    extract = "extract"
    strip_ = "strip"
    thumbs = "thumbs"
    preview = "preview"
    limits = "limits"


ProjectScope = Annotated[
    bool,
    typer.Option(
        "--project",
        "-p",
        help="Use the project file instead of your own settings: the sashimi.toml that applies in this folder "
        "([cyan]sashimi config path[/cyan] shows it), created here if there is none. It's used only when you run sashimi in "
        "its folder or a folder inside it.",
    ),
]


def _scope(project: bool) -> str:
    return "project" if project else "user"


def _where(path: Any) -> str:
    """When a settings file applies, for messages."""
    from sashimi.cli import settings

    if path == settings.get_user_config_path():
        return "your settings, used wherever you run sashimi"
    return f"a project file, used when you run sashimi in {path.parent} or a folder inside it"


def _settings_error(error: Exception) -> NoReturn:
    from rich.markup import escape

    fail(escape(str(error)))


def _parse_value(text: str) -> Any:
    """TOML literal if it parses (42, 2.5, true, "auto", [".mp4"]), otherwise a plain string."""
    import tomllib

    try:
        return tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        return text


def _split_key(key: str) -> tuple[str, str]:
    from sashimi.cli.settings import SECTIONS

    section, _, option = key.partition(".")
    if not option or section not in SECTIONS:
        fail(f"{key!r}: use section.option, where section is {', '.join(SECTIONS[:-1])} or {SECTIONS[-1]}.")
    return section, option


def _check(section: str, option: str, value: Any) -> None:
    """Refuses unknown keys and invalid values before anything is written."""
    from pydantic import ValidationError

    from sashimi.cli import settings
    from sashimi.config import CONTACT_SHEET
    from sashimi.errors import InvalidOptionsError

    if option not in settings.known_keys(section):
        fail(f"Unknown option {section}.{option}.")
    if option == "output_dir":
        if not isinstance(value, str) or not value.strip() or "\0" in value:
            fail(f"{section}.output_dir must be a folder, e.g. sheets or /home/me/sheets.")
        return
    if option == "output_name":
        from rich.markup import escape

        from sashimi.api._naming import ROLES, NameTemplate

        if not isinstance(value, str):
            fail(escape(f"{section}.output_name must be a pattern, e.g. '{{input_name}}.jpeg'."))
        try:
            template = NameTemplate(value)
        except InvalidOptionsError as e:
            fail(escape(f"{section}.output_name: {e.user_message}"))
        if section not in ROLES and "role" in template.fields:
            fail(escape(f"{section}.output_name: {section} outputs have no role, so the pattern can't use {{role}}."))
        return
    if section == "limits":
        from sashimi.cli._limits import parse

        try:
            parse(option, value)
        except ValueError as e:
            fail(f"limits.{option}: {e}.")
        return
    if section == "thumbs" and option == "video_extensions":
        if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            fail('video_extensions must be a list, e.g. [".mp4", ".mkv"].')
        return
    if option in settings.PATH_KEYS:
        if not isinstance(value, str):
            fail(f"{section}.{option} must be a path in quotes.")
        return
    if option == "target":
        try:
            CONTACT_SHEET.model_for(animated=section == "animated_thumbs", target=value)
        except InvalidOptionsError as e:
            fail(f"{section}.{option}: {e.user_message}")
        return
    still = [m for m in CONTACT_SHEET.models if not m.model_fields["animated"].default]
    animated = [m for m in CONTACT_SHEET.models if m.model_fields["animated"].default]
    targets: list[tuple[type[BaseModel], str]] = []
    if section == "thumbs":
        targets += [(model, option) for model in still if option in model.model_fields]
    targets += [(model, option) for model in animated if option in model.model_fields]
    for model, field in targets:
        try:
            model.model_validate({field: value})
        except ValidationError as e:
            fail(f"{section}.{option}: {e.errors()[0]['msg']}")


@app.command("show")
def show(json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False):
    """
    Show what your sashimi.toml files set, and which files they are: your own settings, and the
    project file that applies in this folder, which wins where both set something. For built-in
    defaults see [cyan]sashimi config defaults[/cyan].
    """
    from sashimi.cli import settings

    raw, path = settings.load_raw_config()
    user, project = settings.get_user_config_path(), settings.find_project_config_path()
    if json_output:
        files = {"user": str(user) if user.is_file() else None, "project": str(project) if project else None}
        print(json.dumps({"source": str(path), "files": files, "settings": raw}, indent=2, default=str))
        return
    import tomlkit
    from rich.markup import escape

    if not user.is_file() and project is None:
        console.print("[dim]# Nothing set yet; `sashimi config set` saves to your own settings.[/dim]")
        console.print(f"[dim]# Your settings: {escape(str(user))} (not created yet)[/dim]")
        return
    console.print(f"[dim]# Your settings: {escape(str(user))}{'' if user.is_file() else ' (not created yet)'}[/dim]")
    if project is not None:
        console.print(f"[dim]# Project file: {escape(str(project))} (wins over your settings here)[/dim]")
    console.print("[dim]# What applies here, both merged:[/dim]")
    console.print(tomlkit.dumps(raw), markup=False, highlight=False)


@app.command("path")
def path():
    """Show where your own settings file and the project file that applies here are."""
    from rich.markup import escape

    from sashimi.cli import settings

    user, project = settings.get_user_config_path(), settings.find_project_config_path()
    made = "" if user.is_file() else ", not created yet"
    console.print(f"User:    {escape(str(user))}  [dim](used everywhere; sashimi config set writes here{made})[/dim]")
    if project is not None:
        console.print(
            f"Project: {escape(str(project))}  [dim](used in {escape(str(project.parent))} and folders inside it, "
            "over your settings; sashimi config set --project writes here)[/dim]"
        )
    else:
        console.print("Project: none here  [dim](sashimi config set --project creates ./sashimi.toml)[/dim]")


@app.command("set", no_args_is_help=True)
def set_(
    key: Annotated[str, typer.Argument(help="section.option, e.g. thumbs.cols", show_default=False)],
    value: Annotated[str, typer.Argument(help="Value, e.g. 4, true, auto, '[\".mp4\"]'", show_default=False)],
    project: ProjectScope = False,
):
    """
    Keep a default in your own settings (~/.sashimi/sashimi.toml), used wherever you run sashimi,
    e.g. [cyan]sashimi config set thumbs.width 1920[/cyan], [cyan]sashimi config set
    animated_thumbs.cols 4[/cyan] or [cyan]sashimi config set limits.max_pixels 200M[/cyan]. Every
    processing command has a section for where its outputs go: [cyan]sashimi config set
    thumbs.output_name 'sheets/{input_name}.jpeg'[/cyan] names them (here in a sheets folder
    inside each video's folder), and [cyan]sashimi config set resize.output_dir /home/me/small[/cyan]
    collects them in one folder (a relative one is relative to where you run sashimi). With
    --project the setting goes in the folder's own sashimi.toml instead, and applies only when you
    run sashimi in that folder or a folder inside it.
    """
    from rich.markup import escape

    from sashimi.cli import settings

    section, option = _split_key(key)
    parsed = _parse_value(value)
    _check(section, option, parsed)
    try:
        written = settings.set_config_value(section, option, parsed, scope=_scope(project))
    except (settings.SettingsFileError, OSError) as e:
        _settings_error(e)
    console.print(f"[green]Set[/green] {section}.{option} = {escape(repr(parsed))} in {escape(str(written))}")
    console.print(f"[dim]({escape(_where(written))})[/dim]")
    if option == "output_dir":
        import pathlib

        where = (
            "that folder"
            if pathlib.Path(parsed).expanduser().is_absolute()
            else "that folder, relative to wherever you run sashimi"
        )
        console.print(
            f"[dim]{section} now writes into {where}; --output-dir changes it for one run, and --no-output-dir "
            "writes next to each input.[/dim]"
        )
        folders = [part for part in pathlib.PurePosixPath(parsed.replace("\\", "/")).parts if part != "."]
        if not pathlib.Path(parsed).expanduser().is_absolute() and folders and ".." not in folders:
            from rich.markup import escape as _escape

            from sashimi.api._naming import default_output_name

            pattern = "/".join([*folders, default_output_name(section)])
            console.print(
                _escape(
                    "For a folder beside each input instead, wherever you run sashimi, put it in the name "
                    f"pattern: sashimi config set {section}.output_name '{pattern}'"
                ),
                style="dim",
            )
    if not project:
        _note_project_override(section, option)


def _note_project_override(section: str, option: str) -> None:
    """Says so when the project file that applies here sets `option` too, since it wins here."""
    from rich.markup import escape

    from sashimi.cli import settings

    found = settings.find_project_config_path()
    if found is None:
        return
    table = settings.file_settings(found).get(section)
    if isinstance(table, dict) and option in table:
        console.print(
            f"[yellow]Note: {escape(str(found))} sets {section}.{option} too ({escape(repr(table[option]))}), and "
            f"wins when you run sashimi in {escape(str(found.parent))} or a folder inside it. Remove it there with "
            f"sashimi config unset --project {section}.{option}[/yellow]"
        )


@app.command("unset", no_args_is_help=True)
def unset(
    key: Annotated[str, typer.Argument(help="section.option to remove.", show_default=False)],
    project: ProjectScope = False,
):
    """Remove a default from your own settings (or with --project, the project file), falling back to the built-in default."""
    from rich.markup import escape

    from sashimi.cli import settings

    section, option = _split_key(key)
    target = settings.find_project_config_path() if project else settings.get_user_config_path()
    try:
        removed = settings.remove_config_value(section, option, scope=_scope(project))
    except (settings.SettingsFileError, OSError) as e:
        _settings_error(e)
    if removed:
        console.print(f"[green]Removed[/green] {section}.{option} from {escape(str(target))}")
    elif target is None:
        console.print(f"[yellow]There's no project file here, so {section}.{option} wasn't removed.[/yellow]")
    else:
        console.print(f"[yellow]{section}.{option} isn't set in {escape(str(target))}.[/yellow]")
    if not project:
        found = settings.find_project_config_path()
        table = settings.file_settings(found).get(section) if found is not None else None
        if found is not None and isinstance(table, dict) and option in table:
            console.print(
                f"[yellow]Note: {escape(str(found))} sets {section}.{option} ({escape(repr(table[option]))}), and "
                f"it applies when you run sashimi in {escape(str(found.parent))} or a folder inside it; remove it "
                f"with sashimi config unset --project {section}.{option}[/yellow]"
            )


@app.command("defaults", no_args_is_help=True)
def defaults(
    operation: Annotated[
        Operation,
        typer.Argument(
            metavar="COMMAND",
            help="The command to describe: convert, remux, resize, rotate, favicon, edit, frames, extract, strip, "
            "thumbs or preview; or limits.",
            show_default=False,
        ),
    ],
    fmt: Annotated[
        Optional[str],
        typer.Argument(help="Only this output format (or extract container), e.g. webp.", show_default=False),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
):
    """
    Show each option's default, accepted values and meaning, per output format, e.g.
    [cyan]sashimi config defaults convert webp[/cyan]; [cyan]sashimi config defaults limits[/cyan]
    shows the limits. Every command's first table is where its outputs go (output names and
    folder). Values from sashimi.toml are shown for those, thumbs and limits.
    """
    from sashimi.cli import _defaults, settings

    models = _defaults.models_for(operation.value, fmt)
    if not models and fmt is not None:
        choices = sorted({title.rpartition(" → ")[2].lower() for title, _ in _defaults.models_for(operation.value)})
        if not choices:
            fail(f"{operation.value} has no options per output format.")
        fail(f"{operation.value} has no {fmt!r} output; choose from {', '.join(choices)}.")
    raw = settings.load_raw_config()[0]
    sections: list[tuple[str, Any, str, list[dict[str, Any]]]] = []
    if operation is not Operation.limits:
        sections.append((_defaults.NAMES_TITLE, None, _defaults.NAMES_DOC, _defaults.name_rows(operation.value, raw)))
    for title, model in models:
        overrides = None
        if operation is Operation.limits:
            overrides = _defaults.limit_overrides(raw)
        elif operation is Operation.thumbs:
            overrides = _defaults.toml_overrides(model.model_fields["animated"].default, raw)
        doc = " ".join((model.__doc__ or "").strip().split("\n\n")[0].split())
        sections.append((title, model, doc, _defaults.rows(operation.value, model, overrides)))

    if json_output:
        report = [
            {"title": title, "model": model.__name__ if model else None, "description": doc, "fields": fields}
            for title, model, doc, fields in sections
        ]
        print(json.dumps(report, indent=2))
        return

    from rich.markup import escape
    from rich.table import Table

    for title, model, doc, fields in sections:
        heading = f"[bold]{escape(title)}[/bold]" + (f"  [dim]{model.__name__}[/dim]" if model else "")
        cells = []
        for row in fields:
            name = row["field"]
            option = row["option"] or (f"limits.{name}" if operation is Operation.limits else name)
            if row["option"] and row["option"].split("/")[0] != "--" + name.replace("_", "-"):
                option += f" [dim]{name}[/dim]"
            default = escape(_defaults.format_value(row["default"], name, operation.value))
            if "model_default" in row:
                default += f" [dim](Python: {escape(_defaults.format_value(row['model_default'], name))})[/dim]"
            if "sashimi_toml" in row:
                toml_value = _defaults.format_value(row["sashimi_toml"], name, operation.value)
                default += f"\n[yellow]sashimi.toml: {escape(toml_value)}[/yellow]"
            cells.append((option, default, escape(row["accepted"]), escape(row["description"])))
        table = Table(
            title=heading,
            caption=escape(doc),
            title_justify="left",
            caption_justify="left",
            expand=False,
        )
        widths = _widths(cells)
        table.add_column("Option", style="cyan", no_wrap=True, min_width=widths[0])
        table.add_column("Default", style="green", min_width=widths[1])
        table.add_column("Accepted", min_width=widths[2])
        table.add_column("Description", min_width=widths[3])
        for cell in cells:
            table.add_row(*cell)
        if sum(widths) + 3 * len(widths) + 1 <= console.width:
            console.print(table)
        else:
            _print_stacked(heading, escape(doc), cells)
        console.print()


def _widths(cells: list[tuple[str, str, str, str]]) -> list[int]:
    """
    The narrowest each column of a defaults table can be without cutting a word or a value: the
    option in full, the longest word elsewhere, and at least 30 for the description, so it reads.
    """
    from rich.measure import Measurement
    from rich.text import Text

    headers = ("Option", "Default", "Accepted", "Description")
    widest = [len(header) for header in headers]
    for cell in cells:
        for index, text in enumerate(cell):
            renderable = Text.from_markup(text)
            if index == 0:
                widest[index] = max(widest[index], renderable.cell_len)
            else:
                widest[index] = max(widest[index], Measurement.get(console, console.options, renderable).minimum)
    widest[3] = max(widest[3], 30)
    return widest


def _print_stacked(heading: str, caption: str, cells: list[tuple[str, str, str, str]]) -> None:
    """A defaults table for a terminal too narrow for its columns: one block per option, so nothing is cut."""
    from rich.padding import Padding
    from rich.table import Table
    from rich.text import Text

    console.print(heading)
    console.print(f"[dim]{caption}[/dim]")
    for option, default, accepted, description in cells:
        console.print()
        console.print(option, style="cyan")
        grid = Table.grid(padding=(0, 1))
        grid.add_column(style="bold", no_wrap=True)
        grid.add_column(overflow="fold")
        grid.add_row("Default:", f"[green]{default}[/green]")
        grid.add_row("Accepted:", accepted)
        console.print(Padding(grid, (0, 0, 0, 2)))
        console.print(Padding(Text.from_markup(description, overflow="fold"), (0, 0, 0, 2)))
