# Migrating from 0.5 to 0.6

0.6.0 is the first release on PyPI (`pip install sashimi-convert`). If you used a private 0.5.1
build, replace it with 0.6.0. Conversion outputs are unchanged, except contact sheets and MP4s of
H.264 videos, as described in the next sections. The single-file functions take the same
arguments; the batch API and the CLI changed more, below. Image editing and frame selection are
new (`sashimi.image.edit`, `select_frames`, `frames_info`; see the changelog).

## MP4 conversions copy when they can

`video.convert` with an MP4 config copies the video, and the audio when it is AAC, instead of
re-encoding, when the result would look the same: 8-bit 4:2:0 H.264 with even dimensions, no
start time, no length shorter than the video, no `max_width` below the video's width, and `crf`
and `preset` at their defaults. Such outputs are the source's streams remuxed: much faster,
original quality, and usually a different size from 0.5's re-encode. Everything else is
re-encoded exactly as before.

| You want | 0.5 | 0.6 |
|---|---|---|
| An MP4 of an H.264 + AAC video | re-encoded at crf 23 | streams copied (route `"copy"`) |
| The same, with Opus or MP3 audio | re-encoded | video copied, audio to AAC (`"copy_video"`) |
| A smaller file from an H.264 video | `Mp4VideoConfig()` | set `crf` (e.g. 28) or `max_width`, which re-encode |
| HEVC, VP9, 10-bit, a cut, a resize | re-encoded | unchanged |

`video.convert` returns the route (`"copy"`, `"copy_video"` or `"encode"`) instead of `None`;
code that ignored the return value needs no change. `video.plan_mp4_route(info, config)` tells
you the route without running anything.

## Command line: exit codes, `info` and `--dry-run`

- A file you name that is skipped (not a video, already in the target format, too short, not
  animated) now exits 1. Scripts that treated "skipped" as success for named files should check
  the `--json` results instead. Searching a directory still exits 0 when it skips files.
- `sashimi info photo.png --json` prints the image's fields (`format`, `edit_target`, `width`,
  `height`, `frames`, `durations_ms`, `loop`, `animated`) instead of ffprobe's. GIFs are images
  here too. Videos and audio print what they did.
- Every command that writes files takes `-n/--dry-run`, and `--height` is `-H`.
- Every `--json` report and batch report has a top-level `dry_run` key.

## Contact sheets

| What | 0.5 | 0.6 |
|---|---|---|
| Animated sheet, `cols`/`rows` `"auto"` | the still sheets' table: 6x5 for 16:9, 7x2 for 9:16 | 3x3, or 4x2 for portrait videos (aspect ratio under 0.95) |
| Animated sheet time labels | 2 px padding, text near the bottom edge | 4 px padding, text centred, 4 px from the tile's corner |
| Still sheet of a 9:16 video | 7x2 | 8x2 (aspect ratios 0.55-0.6) |
| Animated sheet of a clip shorter than `sample_duration` | `MediaTooShortError` | one tile of the whole clip (still `MediaTooShortError` under 1 s) |

To keep 0.5's look for animated sheets, pass `cols` and `rows` explicitly, e.g. `cols=6, rows=5`
for a 16:9 video; the labels can't be switched back. Nothing else about sheets changed.

## Lossless trims and subtitles

A stream-copy trim (`CopyTrimConfig`) of a file with text subtitles the container can't hold,
for example an MP4 with `mov_text` into MKV, used to raise `InvalidOptionsError` ("This video
can't be trimmed to MKV without re-encoding. Try MP4 instead."). It now converts those subtitles
(to SRT for MKV, `mov_text` for MP4/MOV, WebVTT for WebM) and copies everything else. If you
showed that error as advice, there's nothing to show any more for these files.

## Batch API (`sashimi.api`)

**Output names.** Every batch function follows one policy (`sashimi.api` docstring, README):

| Function | 0.5 output | 0.6 output |
|---|---|---|
| `make_thumbs` (still) | `clip.mp4.jpeg` | `clip.sheet.jpg` |
| `make_thumbs` (animated) | `clip.mp4.webp` | `clip.animated-sheet.webp` |
| `resize_images` | `photo_resized.jpg` (`suffix=`) | `photo.resized.jpg` |
| `transform_videos` | `clip_rotated.mp4` (`suffix=`) | `clip.rotated.mp4` |
| `extract` | `clip_extracted.mkv` | `clip.extracted.mkv` |
| `convert_files` | `photo.webp` | unchanged; but `a.png` and `a.jpeg` together give `a.png.webp` and `a.jpeg.webp` |

The `suffix` argument is gone. Every batch function takes `output` (the path for a single input)
and `output_dir`.

**Results.** Generators yield 4-tuples `(path, status, message, output)` instead of 3-tuples:

```python
# 0.5
for item in convert_files_generator(paths, "webp"):
    if isinstance(item, tuple):
        path, status, message = item
# 0.6
for item in convert_files_generator(paths, "webp"):
    if isinstance(item, tuple):
        path, status, message, output = item
```

The non-generator functions (`convert_files`, `make_thumbs`, …) return the report the CLI prints
with `--json`. Its `summary` keys are the same as before; the report adds `schema`, `command`, and
`output` on each result.

**Errors.** `convert_files_generator` raises `InvalidOptionsError` instead of `ValueError` for an
unknown target, and `parse_time_to_seconds` raises `InvalidOptionsError` instead of `ValueError`.

**`extract`.**

```python
# 0.5
status, message, output = extract("clip.mkv", "1m", "2m", output_path="cut.mkv", force=True)
# 0.6
report = extract("clip.mkv", "1m", "2m", output="cut.mkv", force=True)
row = report["results"][0]      # {"file", "status", "message", "output"}
extract("clip.mkv", "1m")                      # from 1 minute to the end
extract("clip.mkv", "1m", duration="30s")      # 30 seconds from 1 minute
```

Invalid times raise `InvalidOptionsError` instead of returning an `"error"` tuple, and `start`
defaults to the beginning.

**`flatten`** returns the report (`results` with each move, `renamed`, `removed_dirs`,
`dry_run`) instead of `{"moved": n, "renamed": n, "removed_dirs": n}`; `report["summary"]["success"]`
is the number moved. It raises `InvalidOptionsError` instead of `FileNotFoundError` or
`NotADirectoryError`. `plan_flatten(path)` shows the plan without changing anything, and
`flatten(path, dry_run=True)` reports it.

## Command line

| 0.5 | 0.6 |
|---|---|
| `sashimi thumbs clip.mp4 -o png` | `sashimi thumbs clip.mp4 --format png` |
| `sashimi resize photos/ -w 800 -o webp` | `sashimi resize photos/ -w 800 --format webp` |
| `sashimi rotate clip.mp4 90 -o webm` | `sashimi rotate clip.mp4 90 --format webm` |
| `sashimi thumbs clip.mp4 -a --duration 3` | `sashimi thumbs clip.mp4 -a --clip-length 3` |
| `sashimi convert photo.png jpeg -h 600` | `sashimi convert photo.png jpeg -H 600` (`-h` is help) |
| `--suffix _small` (resize, rotate) | gone; use `-o` or `--output-dir` |
| `sashimi extract clip.mp4 10m 9999h` | `sashimi extract clip.mp4 10m` |

`-o` is the output file in every command (for one input file), and `--output-dir` collects
outputs in a folder. `--json` prints the same report from every command that processes files,
`extract` and `flatten` included; read `results[0]["message"]` where you read `extract`'s
`message`, and `summary.avg_time_seconds` where you read `avg_processing_time_seconds`. Delete the
`[global]` section from your `sashimi.toml` files by hand (sashimi warns about it now); it never
did anything.

## MP3 and M4A sample rates

`sample_rate` now accepts only the rates each encoder can write. Anything else raises
`InvalidOptionsError` from `from_params`, before any file is read, where 0.5 accepted any integer
from 8000 to 192000 and then failed in ffmpeg with `ProcessingError`.

| Output | Accepted `sample_rate` (Hz) | Constant |
|---|---|---|
| MP3 | 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000 | `sashimi.config.MP3_SAMPLE_RATES` |
| M4A (AAC) | the MP3 rates, plus 64000, 88200, 96000 | `sashimi.config.AAC_SAMPLE_RATES` |
| OGG (Opus) | 8000, 12000, 16000, 24000, 48000 (unchanged) | `sashimi.config.OPUS_SAMPLE_RATES` |
| WAV, FLAC | any integer from 8000 to 192000 (unchanged) | |

If a form offers sample rates, build its choices per format from these constants, for example
by dropping 96 kHz from MP3. Leaving `sample_rate` unset still keeps the source's rate when the
encoder supports it, and otherwise uses the nearest rate it does (a 96 kHz source becomes 48 kHz
MP3).

The MP3 and M4A fields are integer `Literal`s now: `model_json_schema()` gives an `enum`, and
`model_fields["sample_rate"].metadata` no longer has `Ge`/`Le` entries. Code that read the bounds
from there should read the choices instead:

```python
from sashimi.config import Mp3AudioConfig

schema = Mp3AudioConfig.model_json_schema()["properties"]["sample_rate"]
choices = schema["anyOf"][0]["enum"]          # [8000, 11025, ..., 48000]
```

## A missing or broken ffprobe

`sashimi.probe()` and everything that probes first (`video.convert`, `video.trim`,
`video.transform`, `audio.convert`, `sheets.render` and the batch API) raise
`BinaryNotFoundError` when ffprobe (or, for sheets, ffmpeg) is missing or can't be started. 0.5
raised `UnsupportedMediaError` ("This file doesn't look like media we can read.") when a path
given to `set_ffmpeg_paths()` didn't work, so a deployment problem looked like a bad upload. `BinaryNotFoundError` is a `ProcessingError`, so code
that shows `UserInputError.user_message` to users and logs everything else needs no change. The
batch API reports such files as `error` ("Failed: Cannot run …") rather than `skipped`.

# Migrating from 0.4 to 0.5

Configs moved to `sashimi.config` and split into one model per output format. The converters
(`sashimi.video.convert`, `image.convert`, …) take the same arguments as before, except for sheets.

## Building configs

Swap each config class for its family; the call shape is the same.

| 0.4 | 0.5 |
|---|---|
| `ImageConvertConfig.from_params(p, target=t)` | `IMAGE_CONVERT.from_params(p, target=t)` |
| `ImageResizeConfig.from_params(p, target=t)` | `IMAGE_RESIZE.from_params(p, target=t)` (target and a size required) |
| `IcoConfig.from_params(p)` | unchanged (`sashimi.config.IcoConfig`) |
| `VideoConvertConfig.from_params(p, target=t)` | `VIDEO_CONVERT.from_params(p, target=t)` |
| `TransformConfig.from_params(p, target=t)` | `VIDEO_TRANSFORM.from_params(p, target=t)` |
| `TrimConfig.from_params(p, container=c)` | `VIDEO_TRIM.from_params(p, container=c)` |
| `AudioConvertConfig.from_params(p, target=t)` | `AUDIO_CONVERT.from_params(p, target=t)` |
| `ContactSheetConfig.from_params(p, target=t)` | `CONTACT_SHEET.from_params(p, target=t)` |
| `AnimatedSheetConfig.from_params(p, target=t)` | `CONTACT_SHEET.from_params({**p, "animated": True}, target=t)` |

```python
# 0.4
from sashimi.video import VideoConvertConfig
config = VideoConvertConfig.from_params(params, target="mp4")

# 0.5
from sashimi.config import VIDEO_CONVERT
config = VIDEO_CONVERT.from_params(params, target="mp4")   # -> Mp4VideoConfig
```

To build a model directly, use its class: `Mp4VideoConfig(crf=20)`, `JpegImageConfig(quality=90)`,
`CopyTrimConfig(container="mkv", end_seconds=30)`. The `target` field defaults to the model's own
format. To read a model's fields, defaults and bounds without building one:
`VIDEO_CONVERT.model_for(target="mp4").model_fields`.

## Options that no longer apply

Each model only has the fields that change its output, so options for other formats are
rejected with "Unknown option: …" instead of being ignored. Drop them before validating:

- images: `quality` for PNG/GIF/BMP; `lossless` and `webp_method` outside WebP; `progressive`
  and `jpeg_subsampling` outside JPEG; `png_compress_level` outside PNG; `tiff_compression`
  outside TIFF; `preserve_transparency` for JPEG/BMP; `optimize` for BMP/TIFF; `keep_exif` and
  `keep_icc` for GIF/BMP;
- video: `fps`, `loop`, `dither`, `palette_mode`, `quality`, `lossless` for WebM/MP4; `crf`,
  `preset`, `speed`, `audio_bitrate_kbps`, `mute` for GIF/WebP; `preset` for WebM; `speed` for MP4;
- trims: every encoder option unless `accurate`; then `speed` for MP4/MOV/MKV and `preset` for WebM;
- audio: `bitrate_kbps` for WAV/FLAC; `wav_bit_depth` outside WAV; `flac_compression_level`
  outside FLAC;
- sheets: `sample_duration`, `fps`, `loop` for still sheets; `quality` for PNG still sheets and
  GIF animated sheets.

`None` still means "use the default" everywhere, and the defaults are now the field defaults:
`Mp4VideoConfig.crf` is `23`, not `None`. The `effective_*` properties and `encoder_target` are
gone; read the field itself.

## Contact sheets

```python
# 0.4
from sashimi.sheets import ContactSheetConfig, contact_sheet, animated_sheet
contact_sheet(src, dst, ContactSheetConfig(width=1920, header_font_path="/fonts/x.ttf"))

# 0.5
from sashimi import sheets
from sashimi.config import CONTACT_SHEET
config = CONTACT_SHEET.from_params({"width": 1920})                 # or {"animated": True, ...}
sheets.render(src, dst, config, header_font="/fonts/x.ttf")
```

`header_font_path`, `timestamp_font_path` and `emoji_dir` are now the `header_font`,
`timestamp_font` and `emoji_dir` arguments of `sheets.render` (and of `api.make_thumbs`), never
config fields. `api.make_animated_thumbs` is gone: pass an animated config to `api.make_thumbs`.

## Images

`image.resize` is gone: `image.convert` shrinks when the config has `max_width`/`max_height`.
`IMAGE_RESIZE` builds the same models with a size required. The batch `resize_images` takes one
resize config, or a function from format name (`"jpeg"`, `"png"`, …) to config to keep each
image's own format. `DEFAULT_MAX_PIXELS` moved to `sashimi.config`.

## Errors

- GIF/animated WebP over 30 s, explicit video lengths over 10 minutes and audio over 60 minutes
  now fail in `from_params` (`InvalidOptionsError`), before any file is read. Accurate trims over
  10 minutes are an `InvalidOptionsError` when the length is given, and `MediaTooLongError` as
  before when the rest of the video is longer.
- A copy trim into a container that can't hold the source's streams is an `InvalidOptionsError`
  (was a `ProcessingError` from the muxer).
- A video too wide for 16 px tiles is a `MediaTooLargeError`; sheet settings that make tiles
  narrower than 16 px are an `InvalidOptionsError`.

## CLI

Commands and flags are unchanged. `resize` now needs `--width` or `--height`, flags that don't
apply to the output are warned about and ignored, and `extract` refuses encoder flags without
`--accurate`. TOML keys are unchanged, including `header_font_path`, `timestamp_font_path` and
`emoji_dir`.

# Migrating from 0.2 to 0.4

## Install

```sh
pip uninstall sashimi                    # if you installed 0.2 from source
pip install 'sashimi-convert[cli]'       # CLI users
pip install sashimi-convert              # library users (add [heif] / [emoji] as needed)
```

The import name is unchanged: `import sashimi`. `ffmpeg` and `ffprobe` must be on `PATH`;
MediaInfo is no longer needed.

## CLI

All four commands keep working. Differences:

- `sashimi convert --width/--height` are now maximums and never upscale; with both, the image is
  fitted inside the box rather than stretched.
- `sashimi convert` also accepts `webp`, `gif`, `bmp`, `tiff` and `ico` for images, `gif`, `webm` and
  `mp4` for videos, and `mp3`, `m4a`, `wav`, `ogg` and `flac` for videos and audio.
- PNGs are no longer optimised by default; pass `--optimize` for the 0.2 behaviour.
- `sashimi thumbs --format/-o` now also chooses the static format (jpeg, png, webp).
- Your `~/.sashimi/sashimi.toml` and project `sashimi.toml` files keep working with the same keys.
  `mediainfo_template` and `header_height_ratio` are ignored.
- Headers no longer render emoji unless you set `emoji_dir` to a folder of Twemoji-style PNGs and
  install the `[emoji]` extra.

## Python: contact sheets

```python
# 0.2
from sashimi.api import make_thumbs
make_thumbs("videos/", recursive=True, static_width=1920, jpeg_quality=80, cols=4)

# 0.4: batch (outputs next to inputs); in 0.5, see above
from sashimi.api import make_thumbs
from sashimi.sheets import ContactSheetConfig
make_thumbs("videos/", ContactSheetConfig(width=1920, quality=80, cols=4), recursive=True)

# 0.4: single file, explicit paths
from sashimi.sheets import contact_sheet
contact_sheet("in.mkv", "out/sheet.jpg", ContactSheetConfig(width=1920), display_name="My clip.mkv")
```

| 0.2 keyword (`StaticThumbsConfig`) | 0.4 field (`ContactSheetConfig`) |
|---|---|
| `static_width` | `width` |
| `jpeg_quality` | `quality` |
| `video_extensions` | `make_thumbs(..., extensions=[...])` |
| `mediainfo_template`, `header_height_ratio` | removed |
| everything else (`cols`, `rows`, `gap`, offsets, colours, fonts) | same name |

| 0.2 keyword (`AnimatedThumbsConfig`) | 0.4 field (`AnimatedSheetConfig`) |
|---|---|
| `animated_width` | `width` |
| `output_format` | `target` |
| `output_fps` | `fps` |
| `sample_duration`, `loop`, `cols`, `rows`, `gap`, … | same name |

`make_animated_thumbs(paths, AnimatedSheetConfig(...), recursive=..., force=...)` follows the same pattern.

## Python: image conversion

```python
# 0.2
from sashimi.api.convert import to_jpeg
to_jpeg("photos/", width=800, quality="85%")

# 0.4
from sashimi.api import to_jpeg               # now exported from sashimi.api
to_jpeg("photos/", max_width=800, quality=85)  # options are ImageConvertConfig fields
```

`ToJpegConfig`/`ToPngConfig` are replaced by `sashimi.image.ImageConvertConfig(target=...)`.
Quality is an integer 1–100.

## Python: extract

`sashimi.api.extract(input_path, start, end, output_path=None, force=False)` still returns
`(status, message, output_path)`. The parameters `start_str`/`end_str` are now `start`/`end` and
also accept numbers of seconds. New keyword-only `accurate=True` re-encodes for an exact cut
(with `crf`, `preset`, `speed`, `audio_bitrate_kbps`, `mute` and `max_workers`). The single-file equivalent is
`sashimi.video.trim(input, output, TrimConfig(container="mkv", start_seconds=..., end_seconds=...))`.

## Python: config

`sashimi.config` is gone. The library no longer reads TOML files: build config models directly
(or with `Model.from_params(dict)`). TOML handling for the CLI lives in `sashimi.cli.settings`.

## Errors

Batch functions still report per-file statuses. Single-file functions raise:

- `sashimi.errors.UserInputError` subclasses: show `e.user_message` to your users;
- `sashimi.errors.ProcessingError`: log it.

Branch on the exception type, not on its message.
