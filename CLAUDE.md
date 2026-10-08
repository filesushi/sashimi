# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Sashimi is an API-first Python toolkit (>=3.11) for file processing: video contact sheets (static JPEG and animated WebP/GIF), image format conversion, video segment extraction, and directory flattening. It ships a Typer CLI (`sashimi`, entry point `sashimi.cli.main:app`) on top of a public Python API (`sashimi.api`). Uses `uv` for environments; hatchling for builds.

External runtime requirements: FFmpeg on PATH (frame/clip extraction shells out via `ffmpeg-python` + `subprocess`) and MediaInfo (via `pymediainfo`).

## Commands

```sh
./scripts/check.sh                                   # full gate: ruff format, ruff check, mypy, pytest on 3.11–3.14
uv run --group dev -- ruff format src/sashimi tests
uv run --group dev -- ruff check src/sashimi tests
uv run --group dev -- mypy src/sashimi
uv run pytest                                        # all tests
uv run pytest tests/config/test_manager.py::test_recursive_merge   # single test
uv run sashimi thumbs <path> -r                      # run the CLI
```

Ruff line length is 120; `F401` (unused imports) is ignored. Integration tests in `tests/file/test_thumbs_integration.py` generate a real video with FFmpeg's `testsrc` and skip if FFmpeg is missing. Config tests use `pyfakefs` (`fs` fixture) and must clear `manager.load_raw_config`'s `lru_cache` between tests.

## Architecture

Three layers, each feature (thumbs, animated_thumbs, convert, extract, flatten) has a module in both `api/` and `file/`:

- **`sashimi/file/`** — the actual processing engines. Batch operations are **generators** with a fixed protocol (`YieldType = int | (path, status, message)`): first yield the count of files that will be processed, then any rejected inputs (missing paths, non-video files) as final results, then for each file `(path, "starting", ...)` followed by a result tuple whose status is one of `success | error | exists | skipped`. Consumers advance progress only for paths they saw as `starting`. Outputs are written next to the source (e.g. `video.mp4` → `video.mp4.jpeg`) via `_sheet.atomic_output` (hidden `.partial` temp file, then rename); existing outputs yield `exists` unless `force=True`. The static (`thumbs.py`) and animated (`animated_thumbs.py`) engines both subclass `_sheet.BaseSheetGenerator`, which owns metadata (rotation-aware: 90/270° videos are treated in display orientation), grid choice, and header/timestamp rendering — fix shared behaviour there, not in both engines.
- **`sashimi/api/`** — public entry points. Each feature exposes a `make_*_generator(...)` that turns keyword arguments into overrides (dropping `None`), resolves config, and returns the `file/` generator; plus a synchronous wrapper (`make_thumbs`, `to_jpeg`, …) that runs it through `api/_consume.consume_generator` (optional `tqdm` bar — tqdm is not a dependency, imported lazily) and returns a `{"summary": {...}, "results": [...]}` dict.
- **`sashimi/cli/main.py`** — Typer commands (`thumbs`, `flatten`, `extract`, `convert`) that call the `api` generators and feed them to `_process_generator_output`, which renders either a Rich progress bar + summary table or, with `--json`, a JSON payload on stdout. Exit code is 1 if any result is an error (both modes); config validation errors exit 2 with a readable message. Logs go to stderr and are forced to CRITICAL when `--json` is in argv.

When adding a new batch operation, follow the same generator protocol so the CLI renderer and API summary wrappers work unchanged.

### Configuration (`sashimi/config/`)

Precedence: **CLI/API overrides > project `sashimi.toml`/`.sashimi.toml` (searched upward from CWD) > global `~/.sashimi/sashimi.toml` (auto-created from `defaults.py`) > in-memory defaults**, merged by `manager._recursive_merge` and validated by Pydantic models in `config/schemas/`.

- `manager.load_raw_config()` is `lru_cache`d; `set_config_value`/`remove_config_value` (tomlkit, preserves formatting) clear the cache after writing.
- `resolution.resolve_config()` returns a `SashimiConfig` (`global` alias → `global_settings`, `thumbs: StaticThumbsConfig`, `animated_thumbs: AnimatedThumbsConfig`). The TOML only has a single `[thumbs]` section; `resolve_config` clones it into `animated_thumbs`, and each schema ignores the other's width field (`static_width` vs `animated_width`).
- `unflatten_cli_args` routes each flat override key into every section whose Pydantic schema declares it (derived from `model_fields`), so new schema fields need no extra wiring. Already-nested input (keys `thumbs`/`animated_thumbs`/`global`) is passed through untouched; the thumbs APIs pass nested overrides.
- Grid `cols`/`rows` are a positive int or `"auto"` (case-insensitive; `None` → `"auto"`), and each dimension is overridden independently.
- `config/defaults.py` holds the default TOML string; keep it in sync with schema defaults.
- Convert configs (`schemas/convert.py`: `ToJpegConfig`, `ToPngConfig`) are built directly in `api/convert.py` and do not go through the TOML resolution system. `quality` accepts `"85%"`, `85`, or `0.85` and normalizes to 0.0–1.0.
