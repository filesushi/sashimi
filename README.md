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
| `sashimi.config` | every config model, the `Limits`, and every user-facing constant; needs only pydantic |
| `sashimi.video` | `convert` (to GIF, animated WebP, WebM, MP4; MP4 copies H.264 and AAC instead of re-encoding when that gives the same result), `preview` (short clips from across a video, as GIF or animated WebP), `trim` (lossless or frame-accurate cut; with no end, a lossless remux), `transform` (rotate/flip) |
| `sashimi.audio` | `convert` (extract from video, or convert audio, to MP3, M4A, WAV, OGG/Opus, FLAC) |
| `sashimi.image` | `convert` (to JPEG, PNG, WebP, GIF, BMP, TIFF; also resizes), `to_ico`, `favicon_pack` (a .zip of ICO, PNG icons, web manifest and HTML tags), `edit` (rotate, flip, crop, brightness, contrast, saturation; every frame of animated GIF and WebP), `select_frames` (keep frames, change speed), `frames_info`, `frame_strip` |
| `sashimi.sheets` | `render` (still JPEG/PNG/WebP or animated WebP/GIF contact sheets) |
| `sashimi.metadata` | `read` (what metadata a file holds, for showing it), `strip` (remove it, or just location data, without re-encoding) |
| `sashimi` | `probe(path) -> MediaInfo` |
| `sashimi.api` | batch helpers: discover files, name outputs, progress, reports |

```python
import sashimi
from sashimi.config import VIDEO_CONVERT, Limits
from sashimi.errors import UserInputError

try:
    config = VIDEO_CONVERT.from_params({"start_seconds": 12, "duration_seconds": 45}, target="gif")
    sashimi.video.convert("upload.blob", "out/clip.gif", config, max_workers=2, limits=Limits(max_gif_seconds=30))
except UserInputError as e:
    print(e.user_message)  # "GIFs can be at most 30 seconds long."
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
- **Limits you choose.** Every function that can meet a limit takes `limits=`, a
  `sashimi.config.Limits` (see "Limits" below). The defaults protect the machine (64 MP images and
  sheets, 1 GiB of decoded frames) and leave lengths unlimited; a web app passes tighter ones, a
  desktop app looser ones or `Limits.unlimited()`.
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
`IMAGE_EDIT`, `IMAGE_FRAMES`, `VIDEO_CONVERT`, `VIDEO_PREVIEW`, `VIDEO_TRANSFORM`, `VIDEO_TRIM`
(by `container` and `accurate`), `AUDIO_CONVERT` and `CONTACT_SHEET` (by `animated` and
`target`); `IcoConfig`, `FaviconPackConfig` and `FrameStripConfig` stand alone. `FAMILY.from_params(params, target=...)` rejects
unknown keys, treats `None` or a missing key as "use the default", and raises
`InvalidOptionsError` with a readable `user_message` for anything invalid, such as an MP4 `crf`
over 51. Field bounds are only ever format and codec limits like that one; the limits you choose
are `Limits`.

## Limits

sashimi enforces only the limits you pass, as one `sashimi.config.Limits`:

| Field | Default | What it limits |
|---|---|---|
| `max_pixels` | 64,000,000 | images, by decoded pixels (conversions, icons, favicon packs, edits, frames) |
| `max_frame_bytes` | 1 GiB | video frames held in memory at once (width × height × 3 each): animated sheets, GIF previews, GIFs made from a video |
| `max_animation_bytes` | 1 GiB | frames of an animated GIF or WebP being edited (width × height × 4 each) |
| `max_sheet_pixels` | 64,000,000 | contact sheets |
| `max_video_seconds` | none | WebM/MP4 conversions (MP4 copies included), rotate and flip, accurate cuts |
| `max_audio_seconds` | none | audio conversions |
| `max_gif_seconds` | none | GIFs and animated WebPs made from a video, and a preview's clips together |
| `max_preview_clips` | none | a preview's clips |

Every limit can be raised, and `None` turns it off; `Limits.unlimited()` turns them all off. A
refusal is a `UserInputError` whose `user_message` states the limit in force and whose `limit` is
the field's name. `limits.check(config)` raises for the limits a config breaks by itself (a GIF
longer than allowed), so a web app can refuse a job before it has the file. What stays fixed are
format limits and correctness rules: WebP's 16,383 px, the codecs' quality and bitrate ranges and
sample rates, a frame rate of at most 50 for GIF and animated WebP (browsers slow faster frames
down), and tiles of at least 16 px.

## Metadata

Every output that can carry the source's metadata has one `metadata` field, with the same values
everywhere:

| Value | What the output keeps |
|---|---|
| `keep` (the default) | everything its format can hold (below) |
| `remove_location` | everything but location data: EXIF's GPS directory; XMP properties named GPS…, …Latitude, …Longitude, …Altitude, City, State, Country, CountryCode, ProvinceState, Sublocation, Location…, WorldRegion or …geo… in any namespace; IPTC's location code and name, city, sublocation, province or state, country code and name; PNG text whose keyword names a place, and ImageMagick's raw EXIF, XMP and IPTC profiles in PNG text; JPEG extended XMP; QuickTime, MP4, Matroska, ID3 and Vorbis tags named location, `com.apple.quicktime.location.*`, `com.android.location`, or containing gps, geo, latitude, longitude, coordinates or iso6709; and timed metadata tracks (GPS tracks) when streams are copied |
| `remove_all` | only what showing or playing it needs: the pixels and streams, the colour profile (as `keep_icc` says), the orientation if sashimi didn't apply it, PNG and WebP display and animation chunks, stream languages, dispositions and attachments' file names, and the display rotation when the video is copied |

Removal is best effort: it covers the carriers above, which are the ones sashimi knows of; a
device may store data where no tool looks. When sashimi applies the EXIF orientation it resets the
orientation to 1, so viewers don't turn the picture twice, and drops the EXIF thumbnail, which shows
the picture before the change; edits always do both. `keep_icc` decides the colour profile alone.
ICO files and favicon packs carry no metadata or profile: their colours are converted to sRGB.

What `keep` can carry into each output format:

| Output | EXIF | XMP | IPTC, Photoshop | Comments | PNG text |
|---|---|---|---|---|---|
| JPEG | yes (up to 64 KB) | yes (up to 64 KB; not extended XMP) | yes | yes | – |
| PNG | yes | yes | – | yes (`Comment`) | from a PNG |
| WebP | yes | yes | – | – | – |
| TIFF | yes (rewritten; maker notes may not survive) | yes | yes | – | – |
| GIF | – | – | – | yes | – |
| BMP | – | – | – | – | – |

Video and audio keep the global and stream tags and the chapters the container can hold: Matroska
and WebM all of them; MP4, MOV and M4A the standard ones (title, artist, album, comment, date, …)
and the location, but not QuickTime's `com.apple.quicktime.*` keys; MP3 ID3; OGG and FLAC Vorbis
comments; WAV its INFO list (no location). No output records a creation time (outputs are
bit-exact, so the same input gives the same bytes). GIFs and animated WebPs made from a video,
previews and contact sheets hold none.

```python
from sashimi import metadata

info = metadata.read("upload.blob")          # decodes nothing; works on outputs too
if info.has_location:
    print(info.latitude, info.longitude, info.carriers)   # 48.8584 2.2945 ['EXIF', 'GPS', 'XMP']
metadata.strip("upload.blob", "clean.bin", "remove_all")  # same format, nothing re-encoded
```

`strip` copies the compressed pixels byte for byte (JPEG, PNG, WebP, GIF, TIFF) and stream-copies
video and audio (MKV, WebM, MP4, MOV, M4A, MP3, FLAC, OGG, WAV); an MPO keeps only its main picture.

## Editing images

`sashimi.image.edit` applies a fixed order: the photo's EXIF orientation, then `rotate` and
`flip`, then the crop box (in pixels of the rotated and flipped picture, as a viewer sees it),
then `brightness`, `contrast` and `saturation`. The three adjustments are the CSS filters of the
same names, as the Filter Effects spec defines them, so a web page can preview an edit live with
`filter: brightness(b) contrast(c) saturate(s)` (in that order) and get the same picture.
Animated GIFs and WebPs have every frame edited; `select_frames` keeps or drops frames and
changes the speed, and `frames_info` lists the frames without decoding them.

```python
from sashimi import image
from sashimi.config import IMAGE_EDIT

info = image.frames_info("upload.blob")                     # format, size, frames, durations
x, y, w, h = image.aspect_crop(info.width, info.height, "16:9")
config = IMAGE_EDIT.from_params(
    {"crop_left": x, "crop_top": y, "crop_width": w, "crop_height": h, "contrast": 1.2},
    target=info.edit_target,                                 # keep the source's format
)
image.edit("upload.blob", "out/edited.bin", config)
```

## Command line

```sh
sashimi --version
sashimi convert photos/ webp --width 1600 -q 80     # images: jpeg png webp gif bmp tiff ico
sashimi convert clip.mkv gif -s 1m30s -d 5 --fps 15  # videos: gif webp webm mp4
sashimi convert talk.mp4 mp3 --bitrate 128 --mono    # video or audio to mp3 m4a wav ogg flac
sashimi convert logo.png ico --background '#ffffff'  # favicon.ico with an opaque background
sashimi favicon logo.png --name "My site"            # favicon pack: ICO, PNGs, manifest, <head> tags
sashimi resize photos/ --width 800 -H 800           # shrink, keeping each format
sashimi edit photo.jpg --rotate 90 --crop 1200x800+0+100  # crop is measured after rotating
sashimi edit photos/ --aspect 1:1 --saturation 1.2   # centred square crop, more vivid
sashimi edit clip.gif --brightness 1.3               # every frame of an animation
sashimi frames clip.gif --drop 1-5 --speed 1.5       # frames are numbered from 1
sashimi convert clip.mkv mp4                         # copies H.264/AAC when it can, else re-encodes
sashimi rotate phone.mp4 90                          # rotate/flip and re-encode
sashimi extract clip.mkv 1m30s 2m                    # lossless cut (alias: trim)
sashimi extract clip.mkv 1h -d 90s --accurate        # exact 90 s cut from 1 h, re-encoded
sashimi extract clip.mkv 10m                         # from 10 minutes to the end
sashimi remux videos/ -r                             # MP4 and others to MKV, no re-encoding
sashimi thumbs videos/ -r                            # contact sheet next to each video
sashimi thumbs clip.mp4 -a --format gif              # animated sheet (3x3 clips)
sashimi preview clip.mp4                             # 5 x 2 s clips from across the video
sashimi preview clip.mp4 --format gif --crop 9:16    # vertical GIF preview
sashimi info clip.mkv                                # container, duration, codecs
sashimi info clip.gif                                # an image's size, frames and their durations
sashimi metadata photo.jpg                           # camera, dates, location: what the file holds
sashimi strip photos/ -r                             # remove metadata without re-encoding
sashimi strip clip.mov --remove remove_location      # remove only GPS and place names
sashimi convert photo.heic jpeg --metadata remove_all  # convert without the photo's metadata
sashimi convert photos/ webp -n                      # --dry-run: what would be written, change nothing
sashimi flatten downloads/ --dry-run                 # see what flatten would move
sashimi config set thumbs.cols 4                     # change CLI defaults
sashimi convert scan.tif jpeg --max-pixels none      # no pixel limit for this run
sashimi config defaults convert webp                 # every option's default for WebP output
sashimi --install-completion                         # tab completion (bash, zsh, fish, PowerShell)
```

Every command prints its help when run without arguments or with `-h`, and every library option
has a flag (`sashimi convert --help` lists them by group, with their defaults; `sashimi config
defaults <command> [format]` lists them per output format). The flags mean the same thing
everywhere: `-o/--output` is the output file, `--format` the output format, `--output-dir` a
folder for the outputs, `-f/--force` replaces existing outputs, `--duration` is how long the
output is, and `--clip-length` how long each sampled clip is. The CLI reads defaults from
`~/.sashimi/sashimi.toml` and a project `sashimi.toml`, which only `sashimi config set` creates
(`-g` for the global one); the library never reads either.

### Limits on the command line

The CLI runs with the library's default limits. The ones you can meet with them have flags, each
taking a number with a unit, or `none` for no limit:

| Flag | Commands | Default | Example |
|---|---|---|---|
| `--max-pixels` | `convert`, `resize`, `favicon`, `edit`, `frames` | 64M | `sashimi convert scan.tif jpeg --max-pixels 300M` |
| `--max-frame-bytes` | `convert` (GIFs), `thumbs` (animated), `preview` (GIF) | 1G | `sashimi convert talk.mp4 gif -d 60 --max-frame-bytes 4G` |
| `--max-animation-bytes` | `edit`, `frames` | 1G | `sashimi edit big.gif --rotate 90 --max-animation-bytes none` |
| `--max-sheet-pixels` | `thumbs` | 64M | `sashimi thumbs film.mkv --cols 10 --rows 20 --max-sheet-pixels 200M` |

Every limit, the length limits included (off by default), can be set in the `[limits]` section of
`sashimi.toml`, with the same values; flags win over the files:

```sh
sashimi config set limits.max_pixels 300M -g          # in ~/.sashimi/sashimi.toml
sashimi config set limits.max_video_seconds 10m       # refuse videos over 10 minutes, here
sashimi config set limits.max_frame_bytes none        # no frame memory limit
sashimi config defaults limits                        # every limit, its flag, default and setting
```

Sizes take `K`, `M` and `G` (`512M`, `4G`, `4GiB`; a bare number is bytes), pixel counts `K`,
`M` and `G` (`200M` is 200 million), and lengths seconds or units (`600`, `10m`, `1h30m`).

### Where outputs go

Every command, and every `sashimi.api` batch function, names its outputs the same way:

| Output | Name | Example |
|---|---|---|
| A conversion (`convert`, `remux`) | `<stem>.<ext>` | `photo.png` → `photo.webp`; `clip.mp4` → `clip.mkv` |
| A contact sheet | `<stem>.sheet.<ext>` | `clip.mp4` → `clip.sheet.jpg` |
| An animated contact sheet | `<stem>.animated-sheet.<ext>` | `clip.mp4` → `clip.animated-sheet.webp` |
| A preview | `<stem>.preview.<ext>` | `clip.mp4` → `clip.preview.webp` |
| A resized image, rotated video, cut | `<stem>.resized.<ext>`, `<stem>.rotated.<ext>`, `<stem>.extracted.<ext>` | `photo.jpg` → `photo.resized.jpg` |
| A favicon pack | `<stem>.favicons.zip` | `logo.png` → `logo.favicons.zip` |
| An edited image or animation (`edit`, `frames`) | `<stem>.edited.<ext>`, in the image's own format | `photo.jpg` → `photo.edited.jpg` |
| A copy without metadata (`strip`) | `<stem>.stripped.<ext>`, in the file's own format | `clip.mov` → `clip.stripped.mov` |

- Outputs go next to their inputs. `--output-dir DIR` puts them in `DIR` instead, keeping the
  subfolders of a directory you passed, and `-o FILE` names the output of a single input file.
  JPEG is written as `.jpg` and TIFF as `.tif`.
- If two inputs would get the same name (`a.png` and `a.jpeg` → `a.webp`), both keep their
  extension in it instead: `a.png.webp` and `a.jpeg.webp`. An output that would replace one of
  the inputs isn't written, even with `--force`.
- An output that already exists is left alone and reported as "Exists". `--force` replaces it
  and says so in the result ("replaced the existing file"). Without `--force` nothing is ever
  replaced, even a file that appears while sashimi works.
- `-n/--dry-run` shows each file's plan ("Would write a.webp", "Would replace a.webp" with
  `--force`, "Output already exists", or why it's skipped) without reading or writing anything.
- Nothing ever deletes or changes an input, except `flatten`, which moves files (see `sashimi
  flatten --help`; `--dry-run` shows the plan first).

### Scripting: exit codes and `--json`

Exit codes:

| Code | When |
|---|---|
| 0 | every file succeeded or already existed ("Exists"), including when a directory search skipped files it can't use |
| 1 | any file failed, or a file you named was skipped (not a video, already in that format, too short, not animated), or a path doesn't exist |
| 2 | the options were refused before anything ran |

With `--json`, a command prints one report on stdout; logging and warnings go to stderr. Every
command that processes files (`convert`, `resize`, `rotate`, `edit`, `frames`, `favicon`,
`extract`, `remux`, `strip`, `thumbs`, `preview`, `flatten`) prints the same shape, which the batch
functions in `sashimi.api` also return:

```json
{"schema": 1, "command": "convert", "dry_run": false,
 "summary": {"total_found": 2, "success": 2, "exists": 0, "skipped": 0, "errors": 0,
             "time_taken_seconds": 0.4, "avg_time_seconds": 0.2},
 "results": [{"file": "a.png", "status": "success", "message": "Converted to a.webp", "output": "a.webp"}]}
```

`status` is `success`, `exists`, `skipped` or `error`. Every report also has `"dry_run": false`
(true with `--dry-run`, when the results are the plan), and `flatten` adds `renamed` and
`removed_dirs`. MP4 conversions say in the message when the streams were copied. `info --json` prints the probe result, `metadata --json` the metadata summary, and `config ... --json` its own data.

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
the 10-minute, 300 MB animated-sheet budget case. The image is tagged `sashimi-sandbox-check`, or
`$SASHIMI_SANDBOX_IMAGE` if set; remove it with `docker rmi` when you're done.

## License

Copyright 2026 The FileSushi Authors, who are listed in `AUTHORS`. Licensed under the Apache
License, Version 2.0; see `LICENSE`.
