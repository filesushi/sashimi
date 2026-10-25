# sashimi Benchmark Harness (`scripts/bench/`)

Reproducible benchmark suite for `sashimi` over the 20 test-media files in `data/` (`data/derf/` Xiph real-footage clips and `data/synthetic_noise/` pure-entropy noise clips).

## Machine Identifiers & Result Grouping

Saved benchmark reports are grouped by machine under `scripts/bench/results/<machine>/`:

- `wsl2-desktop-ryzen3600` — AMD Ryzen 5 3600 (6C/12T, 47 GiB RAM, WSL2)
- `ubuntu-laptop-xps13` — Dell XPS 13 (Ubuntu)
- `ubuntu-server-xeon` — Xeon E-2386G (Ubuntu server / VM)

`scripts/bench/run.py` auto-detects these three machines from `/proc/cpuinfo`, DMI product name, and kernel release, or takes `--machine <slug>` / `SASHIMI_BENCH_MACHINE=<slug>`.

Each run writes:
- `scripts/bench/results/<machine>/<YYYY-MM-DD>_sashimi-<version>[_<tag>].json` (and `.md`)
- `scripts/bench/results/<machine>/latest.json` (and `LATEST.md`)

**Live checkpointing & resume:** Reports are atomically checkpointed to disk after every single benchmark case (`"status": "running"` → `"completed"`). If a run is interrupted, re-running with `--resume` loads all completed cases from the report and continues from where it stopped.

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
| `probe` | 44 | `sashimi.probe` & `sashimi.metadata.read` on all 20 files + `probe-unfinalized-mkv` streaming packet-scan (0.6.1) on 8–300 MB unfinalized captures |
| `sheets` | 21 | Static (`jpeg`, `webp`, `png` at 2560px) and animated (`webp`, `gif` at 1280px 3×3) contact sheets |
| `preview` | 12 | `preview-gif` (two-pass), `preview-webp`, and `preview-webp-9x16` (5×2s clips @ 15 fps, 360p) |
| `gif-methods` | 24 | 6 `palette_mode` (`diff`, `full`, `single`) × `dither` (`bayer`, `sierra2_4a`, `floyd_steinberg`, `none`) combinations across easy, foliage, water, and pure noise |
| `animations` | 29 | GIF vs Animated WebP at 480p, 720p (180f), 1080p (150f), plus the 552.96M pixel budget corners (`gif-720p-20s-30fps` 600f and `gif-1080p-8.8s-30fps` 264f) |
| `transcode` | 32 | `mp4-auto` (`copy` vs `encode`), H.264 presets (`ultrafast`, `veryfast`, `medium`), VP9 speeds (`8` vs `4`), and trim (`copy` vs `accurate`) |
| `codecs` | 50 | Paired H.264 vs HEVC input decode overhead across sheets, animations, and `mp4-auto` |
| `workers` | 32 | Worker scaling (`max_workers` ∈ {1, 2, 4, 6}) on static sheets, animated sheets, 720p GIF, and 720p MP4 re-encode |
| `image` | 12 | `image.edit`, `image.select_frames`, `image.frame_strip` on 60-frame 480p GIFs, and `image.convert` on 1920px PNG contact sheets to lossless WebP |
