# sashimi Benchmark Harness (`scripts/bench/`)

Reproducible benchmark suite for `sashimi` over the 20 test-media files in `data/` (`data/derf/` Xiph real-footage clips and `data/synthetic_noise/` pure-entropy noise clips).

## Machine Identifiers & Result Grouping

Saved benchmark reports are grouped by machine under `scripts/bench/results/<machine>/`:

- `wsl2-desktop-ryzen3600` — AMD Ryzen 5 3600 (6C/12T, 47 GiB RAM, WSL2)
- `ubuntu-laptop-xps13` — Dell XPS 13 (Ubuntu)
- `ubuntu-server-xeon` — Xeon E-2386G (Ubuntu server / VM)

`scripts/bench/run.py` auto-detects these three machines from `/proc/cpuinfo`, DMI product name, and kernel release, or takes `--machine <slug>` / `SASHIMI_BENCH_MACHINE=<slug>`.

Each run writes:
- `scripts/bench/results/<machine>/<YYYY-MM-DD>_sashimi-<version>[_<tag>].json` (and `.md`), dated by the day the run started (UTC)
- `scripts/bench/results/<machine>/latest.json` (and `LATEST.md`), a copy of the newest report for local comparisons; git ignores these copies, so commit the dated report

The first baseline, `wsl2-desktop-ryzen3600/2026-10-25_sashimi-0.6.1.*`, is labelled 0.6.1: that was the pre-release name of what became 0.7.0, and the file keeps the label of what ran.

**Live checkpointing & resume:** Reports are atomically checkpointed to disk after every single benchmark case (`"status": "running"` → `"completed"`). If a run is interrupted, re-running with `--resume` continues this machine's newest dated report for the same sashimi version and tag, whatever day it was started on: it keeps that report's environment block (start time, start load) and runs only the cases it doesn't have. It warns when Python, Pillow, FFmpeg, the git commit or the default workers differ from the run it continues.

**Platforms:** CPU time and peak memory come from Python's `resource` module (Linux, macOS, WSL2). On Windows, which has no `resource` module, the harness still runs and records wall time and outputs, with CPU time and peak memory as unavailable (`null` in JSON, `n/a` in the tables).

The results are not in the sdist (`pyproject.toml` excludes `scripts/bench/results/`); the harness and its tests are. `--rebuild-missing` downloads the Xiph clips from media.xiph.org with a generic User-Agent; nothing else uses the network.

## Commands

```sh
# Verify all 20 files in data/ against sizes, SHA-256 checksums, and sashimi.probe()
uv run python scripts/bench/run.py --verify

# Rebuild any missing files in data/ (downloads/encodes Xiph derf .y4m or generates noise)
uv run python scripts/bench/run.py --rebuild-missing --verify

# List all suites, tasks, and fixtures
uv run python scripts/bench/run.py --list

# Fast smoke benchmark (5 cases) without saving
uv run python scripts/bench/run.py --suite smoke --no-save

# Run full benchmark suite (256 cases across 9 suites; checkpoints after every case)
uv run python scripts/bench/run.py --machine wsl2-desktop-ryzen3600

# Resume an interrupted run
uv run python scripts/bench/run.py --machine wsl2-desktop-ryzen3600 --resume

# Run specific suite(s), task globs, or clip globs
uv run python scripts/bench/run.py --suite gif-methods --suite codecs --tag methods
uv run python scripts/bench/run.py --suite workers --clip "*ducks*" --no-save

# Compare two saved JSON reports (across machines or across sashimi/ffmpeg versions)
uv run python scripts/bench/run.py --compare \
    scripts/bench/results/wsl2-desktop-ryzen3600/latest.json \
    scripts/bench/results/ubuntu-server-xeon/latest.json
```

## Suites

| Suite | Cases | What it measures |
|---|---:|---|
| `probe` | 44 | `sashimi.probe` & `sashimi.metadata.read` on all 20 files + `probe-unfinalized-mkv` streaming packet-scan (0.7.0) on 8–300 MB unfinalized captures |
| `sheets` | 21 | Static (`jpeg`, `webp`, `png` at 2560px) and animated (`webp`, `gif` at 1280px 3×3) contact sheets |
| `preview` | 12 | `preview-gif` (two-pass), `preview-webp`, and `preview-webp-9x16` (5×2s clips @ 15 fps, 360p) |
| `gif-methods` | 24 | 6 `palette_mode` (`diff`, `full`, `single`) × `dither` (`bayer`, `sierra2_4a`, `floyd_steinberg`, `none`) combinations across easy, foliage, water, and pure noise |
| `animations` | 29 | GIF vs Animated WebP at 480p, 720p (180f), 1080p (150f), plus the 552.96M pixel budget corners (`gif-720p-20s-30fps` 600f and `gif-1080p-8.8s-30fps` 264f) |
| `transcode` | 32 | `mp4-auto` (`copy` vs `encode`), H.264 presets (`ultrafast`, `veryfast`, `medium`), VP9 speeds (`8` vs `4`), and trim (`copy` vs `accurate`) |
| `codecs` | 50 | Paired H.264 vs HEVC input decode overhead across sheets, animations, and `mp4-auto` |
| `workers` | 32 | Worker scaling (`max_workers` ∈ {1, 2, 4, 6}) on static sheets, animated sheets, 720p GIF, and 720p MP4 re-encode |
| `image` | 12 | `image.edit`, `image.select_frames`, `image.frame_strip` on 60-frame 480p GIFs, and `image.convert` on 1920px PNG contact sheets to lossless WebP |

## Corpus benchmark (`--corpus`)

`--corpus <manifest.json>` runs every task that applies to each file of a corpus manifest through the CLI with its defaults (`scripts/bench/corpus.py`): probing, `info`/`metadata`, image conversions, resize, rotate, edit, strip, favicons and frame tools, and for videos sheets, previews, conversions (re-encodes of inputs over 2 minutes on a 60 s stretch), extract, remux, rotate and strip. A manifest is a JSON list of entries with `path` (relative to the manifest's folder), `kind` (`image` or `video`), `categories` and `facts`; `--corpus-extra` adds `tests` notes from other manifests. Tasks are chosen from each entry's kind, facts and categories, so nothing names a file, and the corpus may hold media that must stay out of the repository.

Each case runs in its own process with a wall-clock timeout (`--timeout`, default 600 s) and records the outcome as a user sees it (success, already done, refused, failed, timeout or crash), the message and exit code, the exception type and limit behind a refusal, CPU seconds, peak memory (the largest process, and the process tree sampled until the measured work ends), and a description of every output (opened, decoded and compared with the source by `corpus.assess`, whose flags are leads to look at, not verdicts). A refusal by a limit is run again with every limit set to `none`. Results and outputs go beside the manifest (`bench-results/<machine>/<date>_sashimi-<version>_corpus[_<tag>].json` and `.md`, `bench-scratch/`), never under `scripts/bench/results/`; `--resume` continues the newest report, `--list` lists the cases, and `--task`/`--clip` filter tasks and paths.

```sh
uv run python scripts/bench/run.py --corpus data/manifest.json --corpus-extra data/synthetic/manifest.json \
    --ffmpeg-dir ~/.local/opt/ffmpeg/bin --machine ubuntu-laptop-xps13 --resume
```
