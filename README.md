# sashimi

File conversion and video contact sheets for apps and servers, built on FFmpeg and Pillow.

```sh
pip install sashimi-convert            # library: pydantic + Pillow
pip install 'sashimi-convert[cli]'     # adds the `sashimi` command
```

The import name is `sashimi`. Video and audio work needs **`ffmpeg` and `ffprobe` on `PATH`**
(or call `sashimi.set_ffmpeg_paths(...)`); image conversion needs only Pillow.

Optional extras: `[heif]` reads HEIC/HEIF photos (pillow-heif), `[emoji]` draws emoji in contact
sheet headers from a local image folder (pilmoji; never downloads anything).

## What it does

| Module | Contents |
|---|---|
| `sashimi.config` | every config model, limit and user-facing constant; needs only pydantic |
| `sashimi.video` | `convert` (to GIF, animated WebP, WebM, MP4), `trim` (lossless or frame-accurate cut), `transform` (rotate/flip) |
| `sashimi.audio` | `convert` (extract from video, or convert audio, to MP3, M4A, WAV, OGG/Opus, FLAC) |
| `sashimi.image` | `convert` (to JPEG, PNG, WebP, GIF, BMP, TIFF; also resizes), `to_ico` |
| `sashimi.sheets` | `render` (still JPEG/PNG/WebP or animated WebP/GIF contact sheets) |
| `sashimi` | `probe(path) -> MediaInfo` |
| `sashimi.api` | batch helpers: discover files, write outputs next to inputs, progress, summaries |

```python
import sashimi
from sashimi.config import VIDEO_CONVERT
from sashimi.errors import UserInputError

try:
    config = VIDEO_CONVERT.from_params({"start_seconds": 12, "duration_seconds": 5}, target="gif")
    sashimi.video.convert("upload.blob", "out/clip.gif", config, max_workers=2)
except UserInputError as e:
    print(e.user_message)  # e.g. "GIFs can be at most 30 seconds long."
```

## Designed to be embedded

The single-file functions in `sashimi.video`, `.audio`, `.image` and `.sheets` are built to run
inside locked-down workers (read-only filesystems, no network, small process limits):

- **Explicit paths and formats.** Every function takes an input path and an output path. The
  output format comes from the config, never the output's suffix, and the input's type is
  detected from its content, so an input called `original.blob` is fine. Symlinks aren't resolved.
- **Atomic writes.** Output is written to a temporary file next to it (or in `scratch_dir=`) and
  renamed into place, so a failure never leaves a partial file. Frames are piped between ffmpeg
  and Python, so nothing else touches the disk.
- **No implicit I/O.** No config files, nothing written to `HOME`, no network. Settings come only
  from the config object you pass; defaults live in the pydantic models.
- **Bounded resources.** Parallel work is limited by `max_workers` (default `min(4, CPUs)`, where
  CPUs respects affinity and cgroup quotas). Every ffmpeg call gets explicit `-threads`, so a
  conversion at `max_workers=2` peaks at roughly 20 processes + threads (41 for an animated sheet).
- **Typed errors.** `UserInputError` (and subclasses such as `UnsupportedMediaError`,
  `NoAudioTrackError`, `MediaTooLongError`) carries a `.user_message`: one plain sentence, safe to
  show to end users. Internal failures raise `ProcessingError`, whose message is for logs.
- **Progress.** Pass `on_progress=lambda fraction, stage: ...` to follow long conversions.
- **Deterministic.** The same input and config produce the same output.
- **Hardened input handling.** ffmpeg and ffprobe only open local files (`-protocol_whitelist
  file`) and only run allowlisted demuxers (`-format_whitelist`), so containers such as HLS
  playlists are refused before anything parses them.

## Configs

`sashimi.config` has one model per operation and output format (`Mp4VideoConfig`,
`WebpImageConfig`, `OggAudioConfig`, `AnimatedGifSheetConfig`, …), each holding only the options
that affect that output, with the defaults actually used. It imports nothing but pydantic, so a web
app can validate forms against the same models the workers use, and read their defaults and
bounds and descriptions from `model_fields` (or `model_json_schema()`).

Each operation has a family that picks the model: `IMAGE_CONVERT`, `IMAGE_RESIZE`,
`VIDEO_CONVERT`, `VIDEO_TRANSFORM`, `VIDEO_TRIM` (by `container` and `accurate`), `AUDIO_CONVERT`
and `CONTACT_SHEET` (by `animated` and `target`). `FAMILY.from_params(params, target=...)` rejects
unknown keys, treats `None` or a missing key as "use the default", and raises
`InvalidOptionsError` with a readable `user_message` for anything invalid, including limits that
don't depend on the file (a GIF over 30 seconds, an MP4 `crf` over 51).

## Command line

```sh
sashimi --version
sashimi convert photos/ webp --width 1600 -q 80     # images: jpeg png webp gif bmp tiff ico
sashimi convert clip.mkv gif -s 1m30s -d 5 --fps 15  # videos: gif webm mp4
sashimi convert talk.mp4 mp3 --bitrate 128 --mono    # video or audio to mp3 m4a wav ogg flac
sashimi resize photos/ --width 800 --height 800      # shrink, keeping each format
sashimi rotate phone.mp4 90                          # rotate/flip and re-encode
sashimi extract clip.mkv 1m30s 2m                    # lossless cut (alias: trim)
sashimi extract clip.mp4 12 15 --accurate            # exact cut, re-encoded
sashimi thumbs videos/ -r                            # JPEG contact sheet next to each video
sashimi thumbs clip.mp4 -a -o gif                    # animated sheet
sashimi info clip.mkv                                # container, duration, codecs
sashimi flatten downloads/                           # move files out of subfolders
sashimi config set thumbs.cols 4                     # change CLI defaults
sashimi config defaults convert webp                 # every option's default for WebP output
```

Every command prints its help when run without arguments, and every library option has a flag
(`sashimi convert --help` lists them by group, with their defaults; `sashimi config defaults
<command> [format]` lists them per output format). `--json` gives machine-readable output. The CLI
reads defaults from `~/.sashimi/sashimi.toml` and a project `sashimi.toml`, which only
`sashimi config set` creates (`-g` for the global one); the library never reads either.

## Development

```sh
uv sync                       # dev environment, including all extras
sh scripts/check.sh           # ruff format --check, ruff check, mypy, pytest on Python 3.11-3.14
sh scripts/sandbox_check.sh   # run every converter in a FileSushi-style locked-down container
```

`scripts/check.sh` doesn't change the tree: it uses the locked dependencies as they are
(`uv run --frozen`) and runs each Python version in its own environment under `.venvs/`, so the
`.venv` that `uv sync` made is left alone.

`scripts/sandbox_check.sh` builds a `python:3.14-alpine` worker image from the wheel (wheels only,
`apk add ffmpeg`), then runs every converter under FileSushi's sandbox flags (`--network none
--read-only --pids-limit 128 --cpus 2 --memory 1g`) on an input named `original.blob`. Each job reports
its time, peak memory and peak process/thread count, and fails if the output is invalid, anything is
written outside `/vault/output` or left in `/tmp`, or a network call is attempted. `--skip-long` skips
the 10-minute, 300 MB animated-sheet budget case.

## License

Copyright 2026 The FileSushi Authors, who are listed in `AUTHORS`. Licensed under the Apache
License, Version 2.0; see `LICENSE`.
