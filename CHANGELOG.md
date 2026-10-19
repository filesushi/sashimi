# Changelog

sashimi follows [Semantic Versioning](https://semver.org/). Before 1.0, minor versions may break
the Python API; every break is listed here. See [MIGRATION.md](MIGRATION.md) for upgrade steps.

## 0.6.0 — unreleased

The first release on PyPI, as **`sashimi-convert`**. Builds labelled 0.5.1 were shared privately
but never published; 0.6.0 contains all of their changes, listed here too. No conversion output
changes. The breaking changes are validation and error types; [MIGRATION.md](MIGRATION.md) shows
what to change.

### Breaking

- **MP3 and M4A sample rates are limited to what the encoders accept.** `Mp3AudioConfig.sample_rate`
  takes 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100 or 48000 (LAME's rates), and
  `M4aAudioConfig.sample_rate` takes those plus 64000, 88200 and 96000 (the AAC encoder's), where
  both took any integer from 8000 to 192000. Any other rate, such as 96000 for MP3, now raises
  `InvalidOptionsError` in `from_params` ("Invalid value for sample_rate: input should be 8000,
  11025, … or 48000."), instead of passing validation and failing in ffmpeg with
  `ProcessingError`. Every rate still accepted encodes exactly as before. The fields are integer
  `Literal`s, so `model_json_schema()` lists them as an `enum`, and their `model_fields` metadata
  no longer carries `Ge`/`Le` bounds. WAV and FLAC still take 8000-192000, and OGG the Opus rates.
- **A missing or broken ffprobe is no longer reported as unreadable media.** `sashimi.probe()`,
  and everything that probes first (`video.convert`, `video.trim`, `video.transform`,
  `audio.convert`, `sheets.render`, the batch API), raised `UnsupportedMediaError` ("This file
  doesn't look like media we can read.") when the configured ffprobe couldn't be run. They now
  raise `BinaryNotFoundError`, a `ProcessingError`, so batch results report "Failed: Cannot run
  …" instead of skipping the file.

### Added

- `MP3_SAMPLE_RATES` and `AAC_SAMPLE_RATES` in `sashimi.config`, next to `OPUS_SAMPLE_RATES`.
- Tests that pin what FileSushi relies on (`tests/core/test_filesushi_contract.py`): `ConfigFamily.key`,
  `model_for` ignoring non-discriminating options, an `annotated_types.Le` in `model_fields` for
  every number with an upper bound, the `KEYFRAME_NOTE` text, and the `UserInputError(user_message,
  detail=…)` constructor of every user error. A real-ffmpeg test encodes every accepted MP3, AAC and
  Opus sample rate.
- Every config field has a description, available as `model_fields[name].description` and in
  `model_json_schema()` (the docstrings under each field, via pydantic's
  `use_attribute_docstrings`).
- `GridValue` (`cols`, `rows`) carries its 1-20 bound in the type, so it appears in the schema.
- `sashimi config defaults <command> [format]` lists each option's flag, default, accepted values
  and description, per output format (`--json` for scripts); for `thumbs` it also shows values
  set in `sashimi.toml`.
- The CLI warns about `sashimi.toml` keys it doesn't use (stale 0.2-era names such as
  `output_width` or `start_offset`, typos, unknown sections), naming the file and the
  `sashimi config unset` command that removes them. Such keys were silently ignored before.
- `sashimi thumbs --help` shows the `sashimi.toml` values that replace built-in defaults, e.g.
  `[default: 85, animated 75; sashimi.toml: 80]`.

### Changed

- Package metadata uses PEP 639: `License-Expression: Apache-2.0`, with `LICENSE` and `AUTHORS` as
  licence files, and no licence classifier. Building needs hatchling 1.27 or later, and the
  wheel and sdist are written as core metadata 2.4 whichever hatchling builds them.
- Copyright is held by the FileSushi Authors, listed in `AUTHORS`; every source file starts with
  an SPDX copyright and licence header, which `tests/test_licensing.py` enforces.
- The package's author is "The FileSushi Authors <admin@filesushi.com>", and its project URLs
  (homepage, repository, changelog, issues) point at `github.com/filesushi/sashimi`.
- The sdist leaves out `CLAUDE.md` and `docs/FILESUSHI_HANDBACK.md`; the wheel already did.
- The MP3 and M4A `sample_rate` descriptions say what leaving it unset does: the source's rate if
  the encoder supports it, otherwise the nearest one it does.
- `BinaryNotFoundError` is also raised when ffmpeg or ffprobe exists but can't be started (for
  example, a path to a file that isn't executable), which used to escape as a bare `OSError`.
- `ConfigFamily` documents `key` (the discriminating option's name) and that `model_for` ignores
  options that don't pick the model.
- `scripts/check.sh` changes nothing: it runs `ruff format --check`, uses the locked dependencies
  without re-locking (`uv run --frozen`), and gives each Python version its own environment under
  `.venvs/`, leaving `.venv` and `uv.lock` alone. It also lints `scripts/`.
- The CLI no longer writes `~/.sashimi/sashimi.toml` on first use. Only `sashimi config set`
  creates a settings file, holding just the values you set. The old full template pinned
  that version's defaults, so later changes to built-in defaults never reached you. Existing
  files still work; delete one to go back to the built-in defaults.
- `sashimi config show` lists only what your files set; built-in defaults are in
  `sashimi config defaults`.

### Fixed

- CLI help shows option defaults again. Rich read `[default: …]` in help text as a markup tag and
  removed it, leaving lines like "Sheet width in pixels ." Help defaults are now pinned to the
  config models by `tests/cli/test_help_defaults.py`, and the options that lacked one
  (`--subsampling`, `--start`, `--duration`, `--height`, the thumbs colours and fonts) have one.
- `resize --help` no longer mentions animated WebP or video widths.
- A contact sheet whose ffmpeg can't be run raises `BinaryNotFoundError`, instead of
  `UnsupportedMediaError` ("We couldn't read any frames from this video.").

## 0.5.0 — 2026-10-12

0.5.0 makes the configs something a web app can import on its own: one model per operation and
output format, with real defaults and the product limits as field bounds, in a module that needs
only pydantic. Every break is listed below; [MIGRATION.md](MIGRATION.md) shows the new calls.

### Added

- **`sashimi.config`**: every config model, limit and user-facing constant. Importing it loads
  pydantic and `sashimi.errors` only (no Pillow, no subprocess, no ffmpeg lookup).
- One model per operation and output format, each with only the fields that affect its output and
  the default actually used (`Mp4VideoConfig.crf` is `23`, not `None`):
  - images: `JpegImageConfig`, `PngImageConfig`, `WebpImageConfig`, `GifImageConfig`,
    `BmpImageConfig`, `TiffImageConfig`, and `…ResizeConfig` versions that require `max_width` or
    `max_height`; `IcoConfig`;
  - video: `GifVideoConfig`, `WebpVideoConfig`, `WebmVideoConfig`, `Mp4VideoConfig`;
    `Mp4TransformConfig`, `WebmTransformConfig`; `CopyTrimConfig`, `H264TrimConfig`, `Vp9TrimConfig`;
  - audio: `Mp3AudioConfig`, `M4aAudioConfig`, `OggAudioConfig`, `WavAudioConfig`, `FlacAudioConfig`;
  - sheets: `JpegSheetConfig`, `PngSheetConfig`, `WebpSheetConfig`, `AnimatedWebpSheetConfig`,
    `AnimatedGifSheetConfig`.
  Union aliases name each group (`ImageConvertConfig`, `VideoConvertConfig`, `TrimConfig`,
  `SheetConfig`, …).
- `ConfigFamily` entry points that pick the model: `IMAGE_CONVERT`, `IMAGE_RESIZE`, `VIDEO_CONVERT`,
  `VIDEO_TRANSFORM` (default `mp4`), `VIDEO_TRIM` (by `container` and `accurate`), `AUDIO_CONVERT`
  and `CONTACT_SHEET` (by `animated`, then `target`). Each has `from_params(params, **fields)`,
  `model_for(**discriminators)`, `models` and `targets`.
- Public constants: `MAX_GIF_SECONDS` (30), `DEFAULT_GIF_SECONDS` (10), `MAX_VIDEO_SECONDS` (600),
  `MAX_AUDIO_SECONDS` (3600), `DURATION_TOLERANCE_SECONDS` (1), `DEFAULT_MAX_PIXELS`,
  `MAX_SHEET_HEIGHT`, `MAX_SHEET_PIXELS`, `MAX_FRAME_BYTES`, `MIN_TILE_SIZE` (16),
  `OPUS_SAMPLE_RATES`, `KEYFRAME_NOTE`.
- Video → animated WebP (`WebpVideoConfig`: `quality`, `lossless`, `loop`, `fps`, `max_width`), in
  the library, the batch API and `sashimi convert clip.mkv webp`.
- Frame-accurate trims (`VIDEO_TRIM.from_params(..., accurate=True)`, `sashimi extract --accurate`)
  re-encode with the H.264 or VP9 encoder options; `extract` reports the clip's real length and
  explains keyframes when a copied clip comes out longer than asked.
- `sashimi.sheets.render(src, dst, config, *, display_name, header_font, timestamp_font,
  emoji_dir, max_workers, scratch_dir, on_progress)`: one entry point for still and animated sheets.
- `sashimi.video.check_copy_compatible(info, container)`; `MediaInfo.streams` (each stream's
  `type` and `codec`, as `StreamInfo`); `sashimi.StreamInfo`.
- `sashimi.api.source_target(path)`; `resize_images` accepts a function from format name to config.

### Changed

- **Limits that don't depend on the file are checked by the config.** `from_params` refuses a GIF
  or animated WebP `duration_seconds` over 30, an explicit video `duration_seconds` over 600
  (convert and accurate trims, including `end_seconds − start_seconds`), and an explicit audio one
  over 3600, before any file is touched. Whole files over 10 / 60 minutes are still refused at run
  time with `MediaTooLongError`.
- **Re-encodes stop at the probed length.** Without `duration_seconds`, video convert (WebM/MP4),
  transform, accurate trims and audio convert pass `-t` = the rest of the probed input plus 1 s,
  capped at 600 / 3600 s (was the cap itself). Command lines change accordingly (e.g. `-t 104` for
  a 103 s video instead of `-t 600`).
- **Copy trims check the container first.** A source whose streams the chosen container can't
  hold (H.264 into WebM, a timecode track into MKV, …) raises `InvalidOptionsError` ("This video
  can't be trimmed to WEBM without re-encoding. Try MKV instead.") instead of failing in the muxer
  with `ProcessingError`.
- **Contact sheets refuse tiles under 16 px** on either side: settings that make tiles too narrow
  raise `InvalidOptionsError` (checked by the config when `cols` is a number), and videos too wide
  for 16 px-high tiles raise `MediaTooLargeError` ("This video is too tall or too wide to make a
  contact sheet.").
- Defaults that were left to the encoder are now passed explicitly, with byte-identical output:
  FLAC `-compression_level 5`, JPEG `subsampling="4:2:0"`, WebP `method=4`.
- Validation messages read as sentences: limits use their own wording ("GIFs can be at most 30
  seconds long.", "Video clips can be at most 10 minutes long.", "Audio clips can be at most 60
  minutes long."), cross-field rules too ("Choose a rotation or a flip.", "Give an end time or a
  length, not both.", "The end time must be after the start time.", "Set a maximum width or height
  to resize to."), and messages that start with an acronym keep its case. A missing or unknown
  format is "Missing option: target." / "Invalid value for target: must be jpeg, png, … or tiff."
- Integer and boolean choice fields (`rotate`, `wav_bit_depth`, Opus `sample_rate`, `accurate`,
  `animated`) accept the strings a web form posts (`"90"`, `"true"`, `"on"`).
- CLI: flags that the chosen output doesn't use are dropped with one warning ("--lossless, --crf
  don't apply to PNG output; ignored."); `resize` needs `--width` or `--height`, and without
  `--format` warns once per format about flags that format doesn't use; `extract` refuses encoder
  flags without `--accurate` (exit code 2); `thumbs` reports impossible formats with the library's
  sentences.

### Removed (breaking)

- `VideoConvertConfig`, `TrimConfig`, `TransformConfig`, `AudioConvertConfig`,
  `ImageConvertConfig`, `ImageResizeConfig`, `ContactSheetConfig`, `AnimatedSheetConfig` and
  `SheetStyle` as classes, and their re-exports from `sashimi.video`, `.audio`, `.image` and
  `.sheets`. The names that survive in `sashimi.config` are unions of the new per-target models.
- Fields a target ignored are gone from its model, so passing them raises "Unknown option: …"
  (e.g. `fps` for MP4, `quality` for PNG, `bitrate_kbps` for WAV/FLAC, `lossless` for JPEG,
  `crf` for a copy trim, `quality` for PNG still sheets and GIF animated sheets).
- `None` no longer means "the target's default" for `crf`, `preset`, `speed`,
  `audio_bitrate_kbps`, `bitrate_kbps`, `fps`, `max_width` (GIF/WebP), `duration_seconds`
  (GIF/WebP), `quality` (animated WebP), `optimize`, `jpeg_subsampling`, `webp_method` and
  `flac_compression_level`; omitting a key or passing `None` still gives the default.
- The `effective_max_width`, `effective_fps`, `effective_bitrate_kbps` and `encoder_target`
  properties (the fields hold the real values now); `TrimConfig.accurate` validation of encoder
  options (they're simply not fields of `CopyTrimConfig`).
- `header_font_path`, `timestamp_font_path` and `emoji_dir` config fields: pass `header_font=`,
  `timestamp_font=` and `emoji_dir=` to `sheets.render` (or `make_thumbs`). The CLI flags and TOML
  keys are unchanged.
- `sashimi.sheets.contact_sheet` and `animated_sheet`: use `sheets.render`.
- `sashimi.image.resize`: `image.convert` does both; build the config with `IMAGE_RESIZE` (which
  requires a target and a size). `sashimi.image.DEFAULT_MAX_PIXELS` and `KEYFRAME_NOTE` in
  `sashimi.video` moved to `sashimi.config`.
- `sashimi.api.make_animated_thumbs`/`make_animated_thumbs_generator`: `make_thumbs` takes any
  sheet config, plus `header_font`, `timestamp_font` and `emoji_dir`.
- `sashimi.core._params`: its contents are in `sashimi.config` (`ParamsModel`, `RGB`, `RGBA`,
  `parse_color`).
- CLI settings helpers `resolve_static_config`/`resolve_animated_config`: use
  `settings.resolve_sheet(animated, overrides)`.

## 0.4.0 — 2026-10-09

sashimi is now a three-layer library: a single-file **core** for embedding in apps and servers,
the **batch API** (`sashimi.api`) on top of it, and the **CLI** on top of that. Its distribution
name is **`sashimi-convert`** (the name `sashimi` is taken on PyPI); the import name is still
`sashimi`. 0.4.0 wasn't published on PyPI; the first release there is 0.6.0.

### Added

- `sashimi.video`: `convert` (video → GIF, WebM, MP4; animated GIF → WebM, MP4), `trim` (lossless,
  keyframe-aligned cut with an explicit output container), `transform` (rotate/flip), and pure
  command builders `build_convert_command`, `build_trim_command`, `build_transform_command`.
  `KEYFRAME_NOTE` is a user-facing sentence explaining keyframe-aligned trims.
- `sashimi.audio`: `convert` extracts the first audio track of a video, or converts audio, to MP3,
  M4A (AAC), WAV, OGG (Opus) or FLAC; plus `build_convert_command`.
- `sashimi.image`: `convert` (to JPEG, PNG, WebP, static GIF), `resize` (fit within a box, keeping
  the input format by default), `to_ico` (multi-size favicons), `heif_available()`.
- `sashimi.sheets`: `contact_sheet` (JPEG) and `animated_sheet` (GIF/WebP) with explicit input and
  output paths, a `display_name` for the header, and `max_workers`.
- `sashimi.probe(path) -> MediaInfo`: content-based probing with ffprobe (container, duration,
  display-oriented width/height, rotation, fps, codecs, has_audio/has_video).
- `sashimi.errors`: `SashimiError` → `UserInputError` (with `.user_message`; subclasses
  `UnsupportedMediaError`, `NoAudioTrackError`, `MediaTooLongError`, `MediaTooShortError`,
  `MediaTooLargeError`, `InvalidOptionsError`) and `ProcessingError` (→ `BinaryNotFoundError`).
- Config models accept parameter dicts via `Model.from_params(params, **fields)`: unknown keys are
  rejected and `None`/omitted keys mean "use the default". Models are immutable.
- `on_progress(fraction, stage)` callbacks; `max_workers` on everything that runs in parallel
  (default `min(4, CPUs)`, where CPUs respects CPU affinity and cgroup quotas).
- `sashimi.set_ffmpeg_paths(ffmpeg=..., ffprobe=...)` to use bundled binaries.
- Optional extras: `[cli]` (typer, rich, tomlkit), `[emoji]` (pilmoji, offline only), `[heif]`
  (pillow-heif, for HEIC/HEIF input).
- Every config exposes the encoder's main settings, with defaults that keep FileSushi's exact
  commands: video `crf`, `preset`, `speed`, `audio_bitrate_kbps`, `mute`, and GIF `loop`, `dither`,
  `palette_mode`; audio `sample_rate`, `wav_bit_depth`, `flac_compression_level`; image
  `optimize` (all formats), `background_color`, `resample`, `jpeg_subsampling`, `webp_method`,
  `tiff_compression`, `keep_exif`, `keep_icc`, plus BMP/TIFF targets; ICO `fit` and `resample`;
  sheets `target` (JPEG/PNG/WebP for static sheets), `show_header`, `show_timestamps`. Colours
  accept `"#rrggbb"`, `"r,g,b"` or tuples.
- `sashimi.api`: `convert_files`/`convert_files_generator` (images, video and audio, routed by each
  file's type), `resize_images`, `transform_videos`, `convert_images`, `to_jpeg`, `to_png` and
  `parse_time_to_seconds`; batch generators accept `on_file_progress(path, fraction, stage)`.
- CLI:
  - `sashimi --version`, a "Powered by" footer, and commands grouped into help panels;
  - every command that needs arguments prints its help when run without them;
  - `convert` handles video (→ gif, webm, mp4) and audio (→ mp3, m4a, wav, ogg, flac) as well as
    images (→ jpeg, png, webp, gif, bmp, tiff, ico), and exposes every option above as a flag;
  - new `resize`, `rotate`, `info` (media details, `--json`), and `config show|set|unset|path`
    (validated before writing); `trim` is an alias of `extract`, which gains `--json`;
  - `thumbs` exposes every sheet option (`--format`, colours, fonts, offsets, `--no-header`, …);
  - long conversions show per-file progress; skipped files say why ("Already MP3").
- `py.typed`; `__all__` on every public module; the version lives only in `sashimi/_version.py`.
- `scripts/sandbox_check.sh`: runs every converter in a locked-down Alpine container.

### Changed

- **Animated contact sheets no longer use moviepy.** Clips are decoded by ffmpeg, composited with
  Pillow and encoded by a single ffmpeg process. Output looks the same but isn't pixel-identical;
  WebP is now encoded with `libwebp_anim` at quality 75.
- **Contact sheets probe with ffprobe instead of MediaInfo**; libmediainfo is no longer needed.
- **Emoji in sheet headers are off by default.** Headers draw plain text with local fonts. With the
  `[emoji]` extra and `emoji_dir=` pointing at Twemoji-style PNGs, emoji are drawn from that folder;
  nothing is ever downloaded.
- Every ffmpeg and ffprobe call bounds its threads (`-threads`, `-filter_threads`), only opens local
  files (`-protocol_whitelist file`), only runs allowlisted demuxers (`-format_whitelist`), and
  writes bit-exact output, so the same input and config give byte-identical results. Containers
  outside the allowlist (e.g. HLS playlists, images) are refused, so `sashimi.probe()` and
  `sashimi info` no longer read still images.
- Video conversion, rotation and audio conversion always pass `-t`: the requested length, or the
  10 / 60 minute limit when converting the whole file, so a file whose header understates its
  length can't be encoded past the limit.
- Static sheet frames are piped from ffmpeg as raw RGB instead of round-tripping through JPEG files.
- The library never reads or writes config files and never touches `HOME`. TOML config
  (`~/.sashimi/sashimi.toml`, project `sashimi.toml`) is now read only by the CLI.
- The batch API no longer calls `.resolve()` on inputs, so symlinks are used as given.
- Batch status for a non-matching file is now "Unsupported file type" (was "Not a video file");
  `convert` says "Already <FORMAT>" or "Can't convert .ext to <FORMAT>".
- The CLI's `convert` takes `--width/--height` as maximums for images and `--width` for video.
- Image conversion never upscales: `--width`/`--height` (and `max_width`/`max_height`) are now
  upper bounds, and giving both fits the image inside the box instead of stretching it.
- Image quality is now an integer 1–100 everywhere (the CLI still accepts `85%`, `0.85` or `85`).
- `optimize` now defaults per format: on for JPEG and WebP, off for PNG and GIF (PNG optimisation is
  slow on large images). The CLI's `--optimize/--no-optimize` follows the same default; 0.2's CLI
  optimised PNGs by default, so pass `--optimize` for the old behaviour.
- Image conversion applies EXIF orientation by default (`auto_orient=False` disables it) and keeps
  ICC colour profiles.
- Grid `cols`/`rows` are limited to 1–20; sheet widths to 320–8000 (static) and 320–3840 (animated).
  Sheets taller than 16,383 px or larger than 64 MP (e.g. from extremely tall videos) are refused
  with `MediaTooLargeError`.
- Dependencies: core is now `pydantic` + `pillow`. Removed `moviepy`, `pymediainfo`,
  `ffmpeg-python`; `typer`, `rich` and `tomlkit` moved to the `[cli]` extra; `pilmoji` to `[emoji]`.

### Removed (breaking)

- `sashimi.file` (all engines) and `sashimi.config` (schemas, TOML manager, resolution). Their
  replacements are the core modules above; TOML handling lives in `sashimi.cli.settings`.
- `StaticThumbsConfig`, `AnimatedThumbsConfig`, `SashimiConfig`, `GlobalConfig`, `ToJpegConfig`,
  `ToPngConfig`, `resolve_config`, `set_config_value`, `remove_config_value` from the public API.
- The long keyword-argument lists of `make_thumbs`, `make_animated_thumbs` and their generators:
  pass a `ContactSheetConfig` / `AnimatedSheetConfig` instead.
- `make_to_jpeg_generator` / `make_to_png_generator`: use `convert_images_generator`.
- The `mediainfo_template`, `video_extensions` and `header_height_ratio` sheet options
  (extensions are now the batch API's `extensions=` argument).

### Fixed

- Animated sheets no longer spawn `cpu_count() + 2` concurrent ffmpeg processes, which failed with
  "Resource temporarily unavailable" under container pid limits.
- Batch inputs that are symlinks to extension-less files (e.g. `original.blob`) are processed.
- Stray `[cite: …]` text and `# <-- NEW` comments removed.
- Outputs are created with the usual permissions (0666 minus the umask, e.g. 0644) instead of 0600.
- `ContactSheetConfig.target` accepts `jpg` and any letter case, like the image configs.
- Contact sheets of a video whose dimensions can't be read raise `UnsupportedMediaError` instead
  of a bare `AssertionError`.

## 0.2.0

Initial public structure: static and animated contact sheets, image conversion, `extract` and
`flatten`, TOML configuration and the `sashimi` CLI.
