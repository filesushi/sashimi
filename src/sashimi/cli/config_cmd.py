"""`sashimi config`: view and change the CLI's TOML defaults."""

from __future__ import annotations

import json
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any, Optional

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
    resize = "resize"
    rotate = "rotate"
    extract = "extract"
    thumbs = "thumbs"


GlobalScope = Annotated[
    bool, typer.Option("--global", "-g", help="Use ~/.sashimi/sashimi.toml instead of the project's sashimi.toml.")
]


def _parse_value(text: str) -> Any:
    """TOML literal if it parses (42, 2.5, true, "auto", [".mp4"]), otherwise a plain string."""
    import tomllib

    try:
        return tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        return text


def _split_key(key: str) -> tuple[str, str]:
    section, _, option = key.partition(".")
    if not option or section not in ("global", "thumbs", "animated_thumbs"):
        fail(f"{key!r}: use section.option, where section is thumbs, animated_thumbs or global.")
    return section, option


def _check(section: str, option: str, value: Any) -> None:
    """Refuses unknown keys and invalid values before anything is written."""
    from pydantic import ValidationError

    from sashimi.cli import settings
    from sashimi.config import CONTACT_SHEET
    from sashimi.errors import InvalidOptionsError

    if option not in settings.known_keys(section):
        fail(f"Unknown option {section}.{option}.")
    if section == "global":
        return
    if section == "thumbs" and option == "video_extensions":
        if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            fail('video_extensions must be a list, e.g. [".mp4", ".mkv"].')
        return
    if option in settings.PATH_KEYS:
        if not isinstance(value, str):
            fail(f"{section}.{option} must be a path in quotes.")
        return
    if option in ("target", "output_format"):
        try:
            CONTACT_SHEET.model_for(animated=section == "animated_thumbs" or option == "output_format", target=value)
        except InvalidOptionsError as e:
            fail(f"{section}.{option}: {e.user_message}")
        return
    still = [m for m in CONTACT_SHEET.models if not m.model_fields["animated"].default]
    animated = [m for m in CONTACT_SHEET.models if m.model_fields["animated"].default]
    targets: list[tuple[type[BaseModel], str]] = []
    if section == "thumbs":
        field = settings._STATIC_RENAMES.get(option, option)
        targets += [(model, field) for model in still if field in model.model_fields]
    field = settings._ANIMATED_RENAMES.get(option, option)
    targets += [(model, field) for model in animated if field in model.model_fields]
    for model, field in targets:
        try:
            model.model_validate({field: value})
        except ValidationError as e:
            fail(f"{section}.{option}: {e.errors()[0]['msg']}")


@app.command("show")
def show(json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False):
    """Show what your sashimi.toml files set; for built-in defaults see [cyan]sashimi config defaults[/cyan]."""
    from sashimi.cli import settings

    raw, path = settings.load_raw_config()
    if json_output:
        print(json.dumps({"source": str(path), "settings": raw}, indent=2, default=str))
        return
    import tomlkit

    if not path.is_file():
        console.print("[dim]# no sashimi.toml yet; `sashimi config set` creates one[/dim]")
    else:
        console.print(f"[dim]# effective settings; most specific file: {path}[/dim]")
    console.print(tomlkit.dumps(raw), markup=False, highlight=False)


@app.command("path")
def path():
    """Show where the global and project config files are."""
    from sashimi.cli import settings

    project = settings.find_project_config_path()
    console.print(f"Global:  {settings.get_global_config_path()}")
    console.print(f"Project: {project if project else '(none; `sashimi config set` creates ./sashimi.toml)'}")


@app.command("set", no_args_is_help=True)
def set_(
    key: Annotated[str, typer.Argument(help="section.option, e.g. thumbs.cols", show_default=False)],
    value: Annotated[str, typer.Argument(help="Value, e.g. 4, true, auto, '[\".mp4\"]'", show_default=False)],
    global_: GlobalScope = False,
):
    """Set a default, e.g. [cyan]sashimi config set thumbs.static_width 1920[/cyan]."""
    from sashimi.cli import settings

    section, option = _split_key(key)
    parsed = _parse_value(value)
    _check(section, option, parsed)
    settings.set_config_value(section, option, parsed, scope="global" if global_ else "project")
    console.print(f"[green]Set[/green] {section}.{option} = {parsed!r}")


@app.command("unset", no_args_is_help=True)
def unset(
    key: Annotated[str, typer.Argument(help="section.option to remove.", show_default=False)],
    global_: GlobalScope = False,
):
    """Remove a default, falling back to the next file (or the built-in default)."""
    from sashimi.cli import settings

    section, option = _split_key(key)
    if settings.remove_config_value(section, option, scope="global" if global_ else "project"):
        console.print(f"[green]Removed[/green] {section}.{option}")
    else:
        console.print(f"[yellow]{section}.{option} wasn't set there.[/yellow]")


@app.command("defaults", no_args_is_help=True)
def defaults(
    operation: Annotated[Operation, typer.Argument(help="The command to describe.", show_default=False)],
    fmt: Annotated[
        Optional[str],
        typer.Argument(help="Only this output format (or extract container), e.g. webp.", show_default=False),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
):
    """
    Show each option's default, accepted values and meaning, per output format, e.g.
    [cyan]sashimi config defaults convert webp[/cyan]. For thumbs, values from sashimi.toml are shown too.
    """
    from sashimi.cli import _defaults, settings

    models = _defaults.models_for(operation.value, fmt)
    if not models:
        choices = sorted({title.rpartition(" → ")[2].lower() for title, _ in _defaults.models_for(operation.value)})
        fail(f"{operation.value} has no {fmt!r} output; choose from {', '.join(choices)}.")
    raw = settings.load_raw_config()[0] if operation is Operation.thumbs else None
    sections = []
    for title, model in models:
        overrides = None
        if raw is not None:
            overrides = _defaults.toml_overrides(model.model_fields["animated"].default, raw)
        doc = " ".join((model.__doc__ or "").strip().split("\n\n")[0].split())
        sections.append((title, model, doc, _defaults.rows(operation.value, model, overrides)))

    if json_output:
        report = [
            {"title": title, "model": model.__name__, "description": doc, "fields": fields}
            for title, model, doc, fields in sections
        ]
        print(json.dumps(report, indent=2))
        return

    from rich.markup import escape
    from rich.table import Table

    for title, model, doc, fields in sections:
        table = Table(
            title=f"[bold]{escape(title)}[/bold]  [dim]{model.__name__}[/dim]",
            caption=escape(doc),
            title_justify="left",
            caption_justify="left",
            expand=False,
        )
        table.add_column("Option", style="cyan", no_wrap=True)
        table.add_column("Default", style="green")
        table.add_column("Accepted")
        table.add_column("Description")
        for row in fields:
            name = row["field"]
            option = row["option"] or name
            if row["option"] and row["option"].split("/")[0] != "--" + name.replace("_", "-"):
                option += f" [dim]{name}[/dim]"
            default = escape(_defaults.format_value(row["default"], name))
            if "model_default" in row:
                default += f" [dim](Python: {escape(_defaults.format_value(row['model_default'], name))})[/dim]"
            if "sashimi_toml" in row:
                default += (
                    f"\n[yellow]sashimi.toml: {escape(_defaults.format_value(row['sashimi_toml'], name))}[/yellow]"
                )
            table.add_row(option, default, escape(row["accepted"]), escape(row["description"]))
        console.print(table)
        console.print()
