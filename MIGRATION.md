# Migrating from 0.6 to 0.7

Nothing needs to change in code that calls sashimi, but these things behave differently.

## Lengths of late-starting Matroska and WebM files

`MediaInfo.duration` of a Matroska or WebM file whose timestamps start after zero is now measured
from its last packets. libavformat (ffmpeg, OBS, HandBrake, OvenMediaEngine) states the time such
a file's last frame ends, so a 5-second capture whose first frame is at 100 s said 105 s in 0.6;
mkvmerge states the span. If you stored durations, compared them with your own limits, or derived
sampling times from them, those files now get the real length (and checks you made against the
old one may now pass). Files that start at zero and other containers are unchanged. The new
`MediaInfo.start_time` is the timestamp the file starts at.

```python
from sashimi import probe

probe("capture.mkv").duration     # 0.6: 105.04   0.7: 5.0
probe("capture.mkv").start_time   # 100.0
```

## Recordings without a stated length, and `max_scan_packets`

0.6 refused a recording that states no length (a live dump written to a pipe) as "doesn't look
like a video we can read". 0.7 measures it by reading its packets, which costs about 20
microseconds of CPU time per packet (about 280,000 an hour for 30 fps video with AAC audio), and
refuses one with more than `Limits.max_scan_packets` (default 2,000,000) with
`MediaTooLargeError`, `limit="max_scan_packets"`.

The same limit bounds the video packets read to find where to start decoding: every contact
sheet, preview and start time reads the video's packet list up to the latest time it needs (about
0.6 CPU s per 300 MB here), so a long video can now be refused by a sheet or a cut under a low
limit. Catch the error where you catch the other limit errors, set a limit your machine and users
can afford, and pass the same `limits` to your own `probe()` calls:

```python
from sashimi import probe, sheets
from sashimi.config import Limits

limits = Limits(max_scan_packets=1_000_000)   # about 9 hours of 30 fps video to seek in
info = probe(upload, limits=limits)
sheets.render(upload, out, config, limits=limits)   # reuses what was read: the file isn't read again
```

On the command line, `sashimi config set limits.max_scan_packets 5000000` keeps a limit, and
`none` removes it.

## Seeking, and the commands that run

Every route that starts decoding at a time (sheet tiles, preview clips, start times, accurate
cuts) now reads the file's keyframe index. Where ffmpeg's own seek lands wrong (always in MPEG-TS,
MPEG-PS and Ogg; near open-GOP keyframes in MP4 and MOV; in the first second), it starts decoding
just before the keyframe shown at or before the time and drops the frames before it with
`trim`/`atrim`; elsewhere the command is 0.6's. The command builders give their 0.6 commands
unless you pass the new argument, `decode_from=` (from
`sashimi.core._seek.video_index(path, info, until, limits).start_for(time)`) or, for
`build_preview_commands`, `index=`. If you run the builders yourself and want the 0.7 behaviour,
pass it; if you compare the commands the core functions run, expect new ones for those files.
Outputs are byte-identical to 0.6 wherever 0.6's seek was right.

## Wording of length limits

`MediaTooLongError` now says how long the file is: "This video is 12 minutes 5 seconds long, and
the limit is 10 minutes. Set a shorter duration to convert part of it." (0.6: "Videos longer than
10 minutes aren't supported yet. …"). Branch on the type and `.limit`, never the text.

## More files in folder searches, and more formats

Folder searches now take `.mka`, `.m4b`, `.mp2`, `.aiff`, `.aif`, `.aifc`, `.ac3`, `.eac3`,
`.dts`, `.amr`, `.caf`, `.au`, `.wv`, `.wma` and `.m4r` as audio and `.mk3d`, `.m2ts`, `.mts`,
`.3g2`, `.vob`, `.ogv`, `.divx`, `.f4v` and `.y4m` as video. A command you rerun over a folder
holding such files processes them once. To keep the old set, pass `extensions=` to the batch
function, or list the extensions in `[thumbs] video_extensions` for `sashimi thumbs`. ffmpeg's
input allowlist adds the demuxers `aiff`, `ac3`, `eac3`, `dts`, `amr`, `amrnb`, `amrwb`, `caf`,
`au`, `wv` and `yuv4mpegpipe`; if you run sashimi's commands in a sandbox with its own allowlist,
add them there.

# Migrating from 0.5 to 0.6

0.6.0 is the first release on PyPI (`pip install sashimi-convert`). If you used a private 0.5.1
build, replace it with 0.6.0. Conversion outputs are unchanged, except contact sheets, MP4s of
H.264 videos, PNG and TIFF outputs with `keep_icc=False`, images whose source has metadata (now
kept by default), and icons of images with a colour profile, as described in the next sections.
The single-file functions take the same arguments, except that limits are now yours to pass
(`limits=` instead of `max_pixels=`); the batch API and the CLI changed more, below. Image editing
and frame selection are new (`sashimi.image.edit`, `select_frames`, `frames_info`; see the
changelog).

## Metadata: `keep_exif` is now `metadata`, and keeping is the default

Every output that can carry the source's metadata has one `metadata` field: `"keep"` (the
default), `"remove_location"` or `"remove_all"`. It replaces `keep_exif`, which is gone: passing it
raises "Unknown option: keep_exif.". **Images now keep EXIF, XMP, IPTC, comments and PNG text by
default**, where 0.5 dropped EXIF unless `keep_exif=True`.

| You want | 0.5 | 0.6 |
|---|---|---|
| 0.5's default for images (no EXIF) | `JpegImageConfig()` | `JpegImageConfig(metadata="remove_all")` (it also drops XMP, IPTC and comments, which 0.5 dropped too, mostly) |
| EXIF kept | `keep_exif=True` | the default, `metadata="keep"` (XMP, IPTC and comments come too) |
| No GPS, but the camera and date | not possible | `metadata="remove_location"` |
| A video or song without its tags | not possible | `metadata="remove_all"` on any video, trim, rotate or audio config |
| The colour profile dropped | `keep_icc=False` | unchanged: `keep_icc` is separate from `metadata` |
| On the command line | `--keep-exif` | `--metadata keep`, the default; `--metadata remove_all` for 0.5's images |

```python
from sashimi.config import IMAGE_CONVERT, VIDEO_CONVERT

IMAGE_CONVERT.from_params({"metadata": "remove_location"}, target="jpeg")
VIDEO_CONVERT.from_params({"metadata": "remove_all"}, target="mp4")
```

When sashimi applies the orientation (`auto_orient`, and always in edits), the orientation tag is
now set to 1 in the output's EXIF and XMP; edits used to remove it. To show what a file holds, use
`sashimi.metadata.read(path)`; to remove metadata without re-encoding, `sashimi.metadata.strip(path,
out, "remove_all")` (`sashimi strip` on the command line).

## Icons are sRGB

`to_ico` and `favicon_pack` convert an image with a colour profile to sRGB, and no icon carries the
profile. 0.5 copied the profile into the icons (except the Apple touch icon) without converting the
colours. Images without a profile give the same bytes as before. Nothing to change.

## Opus bitrates above 256 kb/s for mono

An OGG `bitrate_kbps`, or a WebM `audio_bitrate_kbps`, above 256 for mono audio failed in ffmpeg
with `ProcessingError`. It is now lowered to 256 kb/s per channel
(`sashimi.config.OPUS_MAX_KBPS_PER_CHANNEL`), so stereo can still use up to 512. If you caught that
error, there is nothing to catch any more.

## Limits are yours to set

0.5 enforced fixed limits: 10-minute videos, 60-minute audio, 30-second GIFs and previews, 10
preview clips, and memory budgets for frames. 0.6.0 enforces only the limits you pass, in one
`sashimi.config.Limits`, as `limits=` on every converter, command builder and batch function.
The defaults protect the machine and leave lengths alone, so **with no `limits`, a two-hour video
converts**.

| You want | 0.5 | 0.6 |
|---|---|---|
| 0.5's limits | (always on) | `Limits(max_video_seconds=600, max_audio_seconds=3600, max_gif_seconds=30, max_preview_clips=10, max_frame_bytes=400 * 1024**2, max_animation_bytes=256 * 1024**2)` |
| A pixel limit | `image.convert(..., max_pixels=24_000_000)` | `image.convert(..., limits=Limits(max_pixels=24_000_000))` |
| No limits at all | not possible | `limits=Limits.unlimited()`, or any field `None` |
| The 30 s GIF bound | `MAX_GIF_SECONDS`, `Le(30)` on `duration_seconds` | `Limits(max_gif_seconds=30)`; the field has no upper bound |
| A refusal before reading the file | `from_params` raised for a 31 s GIF | `from_params` accepts it; `limits.check(config)` raises the same `InvalidOptionsError` |
| Which limit refused a file | parse the message | `error.limit`, e.g. `"max_video_seconds"` |

```python
from sashimi import video
from sashimi.config import VIDEO_CONVERT, Limits
from sashimi.errors import UserInputError

LIMITS = Limits(max_video_seconds=600, max_gif_seconds=30)
config = VIDEO_CONVERT.from_params({"duration_seconds": 45}, target="gif")
try:
    LIMITS.check(config)
    video.convert("upload.blob", "out.gif", config, limits=LIMITS)
except UserInputError as e:
    print(e.user_message, e.limit)   # GIFs can be at most 30 seconds long. max_gif_seconds
```

The constants `MAX_GIF_SECONDS`, `MAX_VIDEO_SECONDS`, `MAX_AUDIO_SECONDS`, `MAX_PREVIEW_SAMPLES`,
`MAX_FRAME_BYTES`, `MAX_ANIMATION_BYTES`, `MAX_SHEET_PIXELS` and `MAX_SHEET_HEIGHT` are gone:
define your own, or read `Limits()`'s defaults. `max_pixels=` is now `limits=` on
`image.convert`, `to_ico`, `favicon_pack`, `edit`, `select_frames`, `frame_strip` and
`frames_info`. The config models keep only format limits as bounds, so code that read a length,
width, grid or speed bound from `model_fields` (`annotated_types.Le`) finds none; set your own in
your forms. `max_frame_bytes` now guards only an animated sheet's clips: GIFs made from a video
and GIF previews build their palette in a first pass and hold a few frames at a time, so a GIF
preview 0.5 refused over 400 MiB of frames now succeeds, whatever the limit. The refusals that state
a limit read differently (they now give the number in force); the length ones read as before
when the limit is the same.

On the command line, the limits a desktop user can meet have flags: `--max-pixels 200M`,
`--max-frame-bytes 4G` (animated sheets), `--max-animation-bytes 4G`, `--max-sheet-pixels 200M`, or `none` to turn
one off. Every limit, lengths included, can be set in `sashimi.toml`:

```sh
sashimi config set limits.max_pixels 200M            # ~/.sashimi/sashimi.toml; --project for ./sashimi.toml
sashimi config set limits.max_video_seconds 10m      # 0.5's 10-minute limit, back on
sashimi config defaults limits                       # every limit, its flag, default and setting
```

## WebP `optimize`

`WebpImageConfig.optimize` (and `WebpResizeConfig.optimize`) is gone: Pillow's WebP writer
ignores it, so it never changed a file. Drop it from WebP params; `from_params` now refuses it
("Unknown option: optimize."). For a smaller WebP, raise `webp_method` (0-6).

## Colour profiles with `keep_icc=False`

PNG and TIFF outputs now lose the profile when `keep_icc=False`, as JPEG and WebP already did
(Pillow's PNG and TIFF writers used to copy it anyway). A CMYK source's profile no longer lands on
an RGB PNG. Nothing to change unless you relied on the profile surviving `keep_icc=False`.

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
`{input_name}.{role}.{output_ext}`, or `{input_name}.{output_ext}` for conversions.

| Function | 0.5 output | 0.6 output |
|---|---|---|
| `make_thumbs` (still) | `clip.mp4.jpeg` | `clip.mp4.sheet.jpg` |
| `make_thumbs` (animated) | `clip.mp4.webp` | `clip.mp4.animated-sheet.webp` |
| `resize_images` | `photo_resized.jpg` (`suffix=`) | `photo.jpg.resized.jpg` |
| `transform_videos` | `clip_rotated.mp4` (`suffix=`) | `clip.mp4.rotated.mp4` (`clip.mov.rotated.mp4` from MOV) |
| `extract` | `clip_extracted.mkv` | `clip.mkv.extracted.mkv` |
| `convert_files` | `photo.webp` | `photo.png.webp`; `convert_files(paths, "jpeg")` writes `.jpeg`, `"jpg"` writes `.jpg` |

A name now depends only on its own input, never on the other files in the batch. Two inputs
that would share an output are both skipped, never renamed. To keep 0.5's names, pass a pattern:
`make_thumbs(paths, output_name="{input_name}.jpeg")` for `clip.mp4.jpeg`, so sheets 0.5 made
count as existing, `convert_files(paths, "webp", output_name="{input_stem}.{output_ext}")` for
`photo.webp`, or `output_name="{input_stem}_resized.{output_ext}"` for 0.5's resized names; the
`suffix` argument is gone. Every batch function takes `output` (the path for a single input),
`output_dir` and `output_name`. A pattern may start with folders, made inside each input's folder
(`output_name="sheets/{input_name}.jpeg"`); `output_dir` is one folder, relative to the current
folder unless absolute; `output` must end in an extension of the output's format, or that file
gets an `error`. A directory search leaves out only the function's own earlier
outputs (what it would make, with the same settings, from another file found); other commands'
outputs are processed like any file.

**Generators return a `Batch`.** Iterating it works as before. Call `batch.plan()` first to see
what it will do (each file's output, action, kind and route, and `plan.mixed` when a folder holds
files the command handles differently), for example to ask the user before rotating a folder of
photos and videos. `rotate_files(paths, 90)` rotates images and videos; `transform_videos` still
rotates videos only.

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
| `sashimi thumbs clip.mp4 -o png` | `sashimi thumbs clip.mp4 --output-format png` |
| `sashimi resize photos/ -w 800 -o webp` | `sashimi resize photos/ -w 800 --output-format webp` |
| `sashimi rotate clip.mp4 90 -o webm` | `sashimi rotate clip.mp4 90 --output-format webm` |
| `sashimi rotate holiday/ 90` (videos only) | the same, now photos and GIFs too; add `-y` in scripts if the folder has both |
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

`sashimi config set` and `config unset` change your own settings (`~/.sashimi/sashimi.toml`)
unless you pass `--project`; they used to change the project `sashimi.toml` unless you passed
`-g`, which is gone. A pattern or limit you set before in a project file still applies when you
run sashimi in that folder; move it with `sashimi config set <key> <value>` and `sashimi config
unset --project <key>` if you want it everywhere. `sashimi frames` now needs something to change
(`--keep`, `--drop`, `--speed` or `--output-format`). `resize`, `convert` and `rotate` take
several paths, with `convert`'s format and `rotate`'s angle last, so commands written for 0.5
work unchanged.

Output names: see the batch API above. On the command line, `--output-name`, or `output_name` in
the command's section of `sashimi.toml`, sets the pattern:

```sh
sashimi config set thumbs.output_name '{input_name}.jpeg'      # 0.5's contact sheet names
sashimi config set animated_thumbs.output_name '{input_name}.webp'
sashimi config set convert.output_name '{input_stem}.{output_ext}'   # photo.webp, as in 0.5
```

If you set a pattern with an unreleased 0.6 build, rename its fields: `{name}` is `{input_name}`,
`{stem}` `{input_stem}`, `{ext}` `{input_ext}` and `{format}` `{output_ext}`; sashimi refuses the
old ones and suggests the new. `--keep-extension` is gone: `--output-name
'{input_stem}.{output_ext}'` replaces `--no-keep-extension`.

Running a command on a folder that holds files it handles differently (photos and videos for
`rotate`) asks first; pass `-y` (or `-f`) in scripts, which otherwise stop with exit code 2.

`sashimi.toml` takes current key names only: rename `static_width` and `animated_width` to
`width` (in `[animated_thumbs]` for animated sheets), `jpeg_quality` to `quality`, `output_fps`
to `fps`, and `output_format` to `target` in `[animated_thumbs]`. The old names get the
unknown-settings warning.

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
