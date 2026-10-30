<h1 align="center">sashimi</h1>

<p align="center">
  <strong>File conversion and video contact sheets. Built with FFmpeg and Pillow.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/sashimi-convert/"><img src="https://img.shields.io/pypi/v/sashimi-convert?color=e8590c" alt="PyPI version"></a>
  <a href="https://pypi.org/project/sashimi-convert/"><img src="https://img.shields.io/pypi/pyversions/sashimi-convert?color=e8590c" alt="Python versions"></a>
  <a href="https://github.com/filesushi/sashimi/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache_2.0-e8590c" alt="License: Apache 2.0"></a>
  <br>
  <a href="https://github.com/filesushi/sashimi/actions/workflows/ci.yml"><img src="https://github.com/filesushi/sashimi/actions/workflows/ci.yml/badge.svg" alt="Tests"></a>
  <a href="https://codecov.io/gh/filesushi/sashimi"><img src="https://codecov.io/gh/filesushi/sashimi/graph/badge.svg" alt="Coverage"></a>
  <a href="https://mypy-lang.org/"><img src="https://img.shields.io/badge/mypy-checked-e8590c" alt="Checked with mypy"></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff"></a>
</p>

**sashimi** converts images, video and audio, makes contact sheets and previews of videos, edits
images and animations, and reads and removes metadata. It is a Python library and a `sashimi`
command, and the conversion engine behind FileSushi, so it is built to run inside locked-down
workers: explicit paths, atomic writes, typed errors, bounded threads, the limits you choose, and
no network.

### What it does

* **Images:** convert between JPEG, PNG, WebP, GIF, BMP, TIFF, AVIF and APNG (and read HEIC, ICO,
  TGA and PSD), keeping animated GIFs, WebPs, APNGs and AVIFs animated, resize, make ICO files and
  complete favicon packs; rotate, flip, crop, and adjust brightness, contrast and saturation to
  match CSS filters, on every frame of an animated GIF, WebP, APNG or AVIF too.
* **Video:** convert to GIF, animated WebP, WebM or MP4 (copying the streams when that gives the
  same result), cut losslessly or frame-accurately, remux, rotate and flip, and make short
  previews sampled from across a video. HDR video is tone-mapped to standard range, or kept as
  10-bit HDR in MP4 and WebM.
* **Audio:** extract from video, or convert, to MP3, M4A, WAV, OGG/Opus and FLAC.
* **Contact sheets:** still (JPEG, PNG, WebP) or animated (WebP, GIF) grids of a video, with a
  header and timestamps.
* **Metadata:** see what a file holds, and remove the metadata sashimi knows about (EXIF, XMP,
  IPTC, comments, tags, chapters, cover art) or just the location, without re-encoding; every
  output keeps or removes the source's metadata by one setting.
* **Says what it kept:** every result states whether the output is lossless, has the source's
  picture or sound with something else dropped (and what), or is lossy (and how), checked against
  the output.
* **Folders, safely:** the CLI names outputs predictably, never processes its own outputs, never
  overwrites without `--force`, shows the plan with `--dry-run` and reports in JSON.

## Contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [Python library](#python-library): [embedding](#designed-to-be-embedded), [configs](#configs),
  [limits](#limits), [metadata](#metadata), [what each output kept](#what-each-output-kept),
  [HDR video](#hdr-video), [editing images](#editing-images)
- [Command line](#command-line): [settings files](#settings-files),
  [limits](#limits-on-the-command-line), [where outputs go](#where-outputs-go),
  [mixed folders](#folders-of-mixed-files), [scripting](#scripting-exit-codes-and---json)
- [Development](#development)

## Installation

sashimi needs Python 3.11 or later and is on PyPI as **`sashimi-convert`** (the import name is
`sashimi`):

```sh
pip install sashimi-convert            # the library: pydantic and Pillow
pip install 'sashimi-convert[cli]'     # adds the `sashimi` command
uv tool install 'sashimi-convert[cli]' # or install just the command, with uv
```

| Extra | Adds |
|---|---|
| `[cli]` | the `sashimi` command (Typer, Rich, tomlkit) |
| `[heif]` | reading HEIC/HEIF photos (pillow-heif) |
| `[emoji]` | emoji in contact sheet headers, drawn from a local image folder (pilmoji; never downloads anything) |

Video and audio need **FFmpeg** (`ffmpeg` and `ffprobe`); images need only Pillow. sashimi is
tested with FFmpeg 8.1 and 9.0. It checks what the FFmpeg it runs can do, per task, and refuses
only the tasks that FFmpeg can't do right, before writing anything, saying what's missing and
which FFmpeg it used: an encoder or filter the build lacks (libx264 for MP4, libvpx-vp9 for WebM,
zscale for HDR video, …), or a task an older release gets wrong (FFmpeg 8.0 and older cut stream
copies with a start time or a length wrongly, and can't tell a start after the last frame from a
real result). A newer release than those tested is used, and reported. `sashimi info --ffmpeg`
shows the FFmpeg in use and what it can do. Distributions often ship an older release (Ubuntu
26.04 has 8.0.1), so install a current one from [ffmpeg.org](https://ffmpeg.org/download.html),
for example a static build, or `brew install ffmpeg` on macOS.

sashimi runs ffmpeg and ffprobe as a pair, from the first of: `sashimi.set_ffmpeg_paths(ffmpeg)`
(the ffprobe beside it), the `SASHIMI_FFMPEG` environment variable (an ffmpeg, or the folder
holding both), `ffmpeg.path` in `sashimi.toml` (`sashimi config set ffmpeg.path
/opt/ffmpeg/bin/ffmpeg`, or the folder), and the ffmpeg on `PATH` with the ffprobe beside it. When
the one chosen is missing, isn't executable, or isn't FFmpeg's ffmpeg or ffprobe, every command,
and `sashimi info --ffmpeg` (which then exits 1), says so once, naming the path, where it was
chosen and how to fix that (`FFmpegPathError` in Python); `sashimi config set ffmpeg.path` warns
about such a path when you set it.

sashimi is tested on Linux, macOS and Windows with Python 3.11 to 3.14. Before 1.0, minor
versions may change the Python API; the [changelog](https://github.com/filesushi/sashimi/blob/main/CHANGELOG.md)
lists every change and [MIGRATION.md](https://github.com/filesushi/sashimi/blob/main/MIGRATION.md)
how to upgrade.

## Quick start

### From the command line

```sh
sashimi convert photos/ webp -q 80          # every image in photos/ to WebP, next to the originals
sashimi resize *.jpg -w 1600                # shrink, keeping each format
sashimi convert clip.mkv gif -s 1m30s -d 5  # a 5-second GIF from 1:30
sashimi thumbs videos/ -r                   # a contact sheet next to each video
sashimi strip photos/ --remove remove_location   # drop GPS and place names, without re-encoding
sashimi convert photos/ webp -n             # --dry-run: see the plan first
```

Outputs are written next to their inputs and named after them (`photo.jpg` → `photo.jpg.webp`,
`clip.mp4` → `clip.mp4.sheet.jpg`), and nothing is overwritten without `-f`. See
[Command line](#command-line) for every command.

### From Python

```python
import sashimi
from sashimi.config import CONTACT_SHEET, IMAGE_RESIZE, VIDEO_CONVERT, Limits
from sashimi.errors import UserInputError

config = IMAGE_RESIZE.from_params({"max_width": 1600, "quality": 80}, target="webp")
sashimi.image.convert("photo.png", "photo.webp", config)

sheet = CONTACT_SHEET.from_params({"cols": 4}, target="jpeg")
sashimi.sheets.render("film.mkv", "film.jpg", sheet)

try:
    clip = VIDEO_CONVERT.from_params({"start_seconds": 12, "duration_seconds": 45}, target="gif")
    sashimi.video.convert("upload.blob", "clip.gif", clip, limits=Limits(max_gif_seconds=30))
except UserInputError as e:
    print(e.user_message)  # "GIFs can be at most 30 seconds long."
```

## Python library

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

### Designed to be embedded

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
  playlists are refused before anything parses them. The allowlist is single-file formats only:
  Matroska/WebM, MP4/MOV/3GP, AVI, ASF (WMV/WMA), MPEG-TS and MPEG-PS, FLV, Ogg, GIF, YUV4MPEG,
  MP3, WAV, FLAC, AAC, AIFF, AC-3, E-AC-3, DTS, AMR, CAF, Sun AU and WavPack.
- **Frames at the times asked for.** Contact sheet tiles, preview clips and start times show the
  frame at their time on every container, whatever the keyframe spacing: sashimi reads the video's
  keyframe index once, starts decoding just before the right keyframe and drops what comes before
  the time. Sheets and previews of videos with keyframes far apart are decoded in one pass instead
  of once per tile.

### Configs

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

### Limits

sashimi enforces only the limits you pass, as one `sashimi.config.Limits`:

| Field | Default | What it limits |
|---|---|---|
| `max_pixels` | 64,000,000 | images, by decoded pixels (conversions, icons, favicon packs, edits, frames) |
| `max_frame_bytes` | 1 GiB | video frames held in memory at once (width × height × 3 each): an animated sheet's clips |
| `max_animation_bytes` | 1 GiB | frames of an animation being edited or converted to an animated GIF or WebP (width × height × 4 each, at the output size) |
| `max_sheet_pixels` | 64,000,000 | contact sheets |
| `max_video_seconds` | none | WebM/MP4 conversions (MP4 copies included), rotate and flip, accurate cuts |
| `max_audio_seconds` | none | audio conversions |
| `max_gif_seconds` | none | GIFs and animated WebPs made from a video, and a preview's clips together |
| `max_preview_clips` | none | a preview's clips |
| `max_scan_packets` | 2,000,000 | packets read to measure a recording that states no length (a live capture that was never finalized) or the end of a Matroska file that starts late (about 20 µs of CPU each; 2,000,000 is about 7 hours of 30 fps video with audio), and video packets read to find the keyframe a sheet, preview or start time decodes from (2,000,000 is about 18 hours of 30 fps video) |

Every limit can be raised, and `None` turns it off; `Limits.unlimited()` turns them all off. A
refusal is a `UserInputError` whose `user_message` states the limit in force and whose `limit` is
the field's name. `limits.check(config)` raises for the limits a config breaks by itself (a GIF
longer than allowed), so a web app can refuse a job before it has the file. What stays fixed are
format limits and correctness rules: WebP's 16,383 px, the codecs' quality and bitrate ranges and
sample rates, a frame rate of at most 50 for GIF and animated WebP (browsers slow faster frames
down), and tiles of at least 16 px.

A GIF's palette is built from all its frames, so GIFs made from a video, GIF previews and animated
GIF sheets are made in two passes: the first builds the palette, the second encodes with it.
Neither holds more than a few frames, so a GIF's memory doesn't grow with its length, at the cost
of decoding the video twice; the output is the same as building the palette in one pass. The
palette passes between them in memory, never as a file. A palette per frame (`palette_mode =
"single"`) takes one pass.

### Metadata

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
and WebM all of them; MP4, MOV and M4A all of them too, QuickTime's `com.apple.quicktime.*` keys
and a camera's own tags included (written as QuickTime metadata keys when the source has any tag
FFmpeg's plain MP4 writer can't hold), with the source's creation time; MP3 ID3; OGG and FLAC
Vorbis comments; WAV its INFO list (no location). Matroska and WebM outputs record the source's
creation time too, in the file's own date field. Outputs are bit-exact, so the same input gives the same bytes. A video's display rotation
says how to show it, so every mode keeps it. GIFs and animated WebPs made from a video,
previews and contact sheets hold none.

```python
from sashimi import metadata

info = metadata.read("upload.blob")          # decodes nothing; works on outputs too
if info.has_location:
    print(info.latitude, info.longitude, info.carriers)   # 48.8584 2.2945 ['EXIF', 'GPS', 'XMP']
metadata.strip("upload.blob", "clean.bin", "remove_all")  # same format, nothing re-encoded
```

`strip` copies the compressed pixels byte for byte (JPEG, PNG, WebP, GIF, TIFF, HEIC, AVIF) and
stream-copies video and audio (MKV, WebM, MP4, MOV, M4A, 3GP, MPEG-TS and M2TS, VOB and MPG, FLV,
WMV, AVI, MP3, FLAC, OGG, WAV); an MPO keeps only its main picture. BMP and ICO hold no metadata,
and TGA and PSD can't be stripped without re-encoding, so `strip` says so and leaves them.

- **HEIC and AVIF** are rewritten in place: every picture item (the photo, its tiles, an iPhone's
  gain map, depth and portrait mattes, thumbnails) and the orientation viewers apply keep their
  bytes, and only the EXIF and XMP items change. `remove_location` removes the GPS directory and
  XMP location properties; `remove_all` empties the EXIF item (keeping an EXIF orientation other
  than 1) and the photo's XMP, and keeps the XMP that tells viewers how to show the gain map and
  other auxiliary pictures (without location). On iPhones up to the 15, how bright the gain map's
  HDR is lives in the EXIF maker notes, so after `remove_all` viewers use their default.
- **Motion photos and Ultra HDR JPEGs** (Google, Samsung) keep a video and a gain map after the
  picture, listed in the XMP. Both modes keep the gain map (stripping removes information about
  the person, place and device, not the picture), with the XMP that tells viewers how to show it,
  unless the gain map holds location or, for `remove_all`, metadata of its own. The video stays
  when it holds nothing the mode removes (location or data tracks for `remove_location`, any
  metadata for `remove_all`); otherwise sashimi strips it the same way, checks the result and puts
  it back, updating the lengths in the XMP. A Samsung trailer points into the old video, so a
  Samsung motion photo's video that needs stripping is removed instead, and the result says so
  (`fidelity.notes`). Conversions and edits never carry them, so their XMP drops what points at them.

### What each output kept

Every processing function returns a result with `fidelity` (`sashimi.fidelity.Fidelity`), and
every batch result ends its message with the statement, so a user can tell what a conversion cost:

| `level` | Meaning | Examples |
|---|---|---|
| `lossless` | the picture or sound is identical, and nothing else sashimi can see was dropped (`removed` lists what was removed as asked) | BMP → TIFF; GIF → lossless WebP or APNG; MKV → MKV remux; JPEG `strip` |
| `content_lossless` | the picture or sound is identical, but something else was dropped (`lost`), or the source can hold data sashimi can't read (`checked` is false) | TIFF → BMP ("picture identical; EXIF and colour profile dropped"); MP4 → MKV remux ("picture and sound identical"); GoPro cut ("…; GoPro telemetry track (GPS) dropped. FFmpeg can't copy … into MP4") |
| `lossy` | the picture or sound changed (`changed`) | "saved as JPEG at quality 85", "re-encoded as H.264 (CRF 23)", "tone-mapped from HDR (HLG)", "reduced to 256 colours", "scaled to 640x360" |

It comes from what happened, not from a table of formats: the encoder used, the source's and the
output's metadata, tracks and structure as sashimi reads them, and, for images saved without loss,
the output decoded and compared with the source pixel for pixel. HEIC, AVIF, PSD, TGA and ICO
sources, and video outside Matroska and WebM, can hold data no reader here sees (vendor boxes,
auxiliary images), so their outputs never claim `lossless`. A stream copy is identical stream for
stream; into MP4 or MOV it may also play the AAC encoder's priming frame that Matroska skips.

### HDR video

Video with an HDR transfer (PQ or HLG, Dolby Vision 8.1 and 8.4 included) is tone-mapped to
standard-range BT.709 wherever the output can't hold HDR: contact sheets, previews, GIFs, animated
WebPs, and re-encodes unless `keep_hdr` is set. The pictures go to linear light (zscale), to
BT.709 primaries, through FFmpeg's `tonemap=mobius` (it keeps mid-tones and colour and rolls off
the highlights; Hable left PQ landscapes dim, Reinhard blew out skies and skin), then to BT.709.
HLG uses BT.2408's 203 nits as reference white. Downscaled outputs are scaled first, which costs
nothing extra; full-size re-encodes of 4K roughly double their CPU time. The HDR mastering and
light-level metadata is removed from the tone-mapped output.

`keep_hdr=True` (`--keep-hdr`) keeps HDR in MP4 (10-bit HEVC, copied when the source already is
10-bit HEVC) and WebM (10-bit VP9 profile 2), with its colour tags and mastering metadata; formats
that can't hold HDR refuse it. sashimi never writes 8-bit video tagged PQ or HLG. Tone mapping needs
FFmpeg's `zscale` (libzimg) and `tonemap` filters: without them HDR routes raise
`MissingFiltersError` rather than write washed-out pictures. Dolby Vision in Dolby's own IPT colour
space (profiles 5 and 20) is still refused, since only Dolby's processing gives its colours.

### Editing images

`sashimi.image.edit` applies a fixed order: the photo's EXIF orientation, then `rotate` and
`flip`, then the crop box (in pixels of the rotated and flipped picture, as a viewer sees it),
then `brightness`, `contrast` and `saturation`. The three adjustments are the CSS filters of the
same names, as the Filter Effects spec defines them, so a web page can preview an edit live with
`filter: brightness(b) contrast(c) saturate(s)` (in that order) and get the same picture.
Animations (GIF, WebP, APNG, AVIF) have every frame edited and stay animated when saved as GIF,
WebP, AVIF, APNG or PNG (an animated PNG in a `.png` file), unless `animated=False` asks for the
first frame; `select_frames` keeps or drops frames and changes the speed, and `frames_info` lists
the frames without decoding them.

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
sashimi resize *.jpg -w 800                          # several files, as the shell lists them
sashimi convert *.png webp                           # the format comes last, as cp's destination
sashimi edit photo.jpg --rotate 90 --crop 1200x800+0+100  # crop is measured after rotating
sashimi edit photos/ --aspect 1:1 --saturation 1.2   # centred square crop, more vivid
sashimi edit clip.gif --brightness 1.3               # every frame of an animation
sashimi frames clip.gif --drop 1-5 --speed 1.5       # frames are numbered from 1
sashimi convert clip.mkv mp4                         # copies H.264/AAC when it can, else re-encodes
sashimi rotate phone.mp4 90                          # lossless: only the display rotation is set
sashimi rotate clip.mov 90                           # clip.mov.rotated.mov: a MOV keeps its PCM sound
sashimi rotate phone.mp4 90 --reencode               # turn the pixels themselves (re-encoded)
sashimi rotate photo.jpg 90 --output-format png      # photo.jpg.rotated.png, no JPEG re-save
sashimi rotate holiday/ 90 -y                        # photos, GIFs and videos; -y answers "go ahead?"
sashimi rotate *.jpg 180                             # the angle comes last (90 when left out)
sashimi extract clip.mkv 1m30s 2m                    # lossless cut (alias: trim)
sashimi extract clip.mkv 1h -d 90s --accurate        # exact 90 s cut from 1 h, re-encoded
sashimi extract clip.mkv 10m                         # from 10 minutes to the end
sashimi remux videos/ -r                             # MP4 and others to MKV, no re-encoding
sashimi thumbs videos/ -r                            # contact sheet next to each video
sashimi thumbs clip.mp4 -a --output-format gif       # animated sheet (3x3 clips)
sashimi preview clip.mp4                             # 5 x 2 s clips from across the video
sashimi preview clip.mp4 --output-format gif --crop 9:16  # vertical GIF preview
sashimi info clip.mkv                                # container, duration, codecs
sashimi info clip.gif                                # an image's size, frames and their durations
sashimi metadata photo.jpg                           # camera, dates, location: what the file holds
sashimi strip photos/ -r                             # remove metadata without re-encoding
sashimi strip clip.mov --remove remove_location      # remove only GPS and place names
sashimi convert photo.heic jpeg --metadata remove_all  # convert without the photo's metadata
sashimi convert photos/ webp -n                      # --dry-run: what would be written, change nothing
sashimi flatten downloads/ --dry-run                 # see what flatten would move
sashimi config set thumbs.cols 4                     # change your defaults, wherever you run sashimi
sashimi config set --project thumbs.cols 6           # ...or only in this folder (./sashimi.toml)
sashimi config path                                  # which settings files apply here
sashimi config set thumbs.output_name '{input_name}.jpeg'  # name sheets video.mp4.jpeg
sashimi config set thumbs.output_name 'sheets/{input_name}.jpeg'  # ...in a sheets folder beside each video
sashimi config set resize.output_dir ~/small          # collect resized photos in one folder
sashimi convert scan.tif jpeg --max-pixels none      # no pixel limit for this run
sashimi config defaults convert webp                 # every option's default for WebP output
sashimi --install-completion                         # tab completion (bash, zsh, fish, PowerShell)
```

Every command prints its help when run without arguments or with `-h`, and every library option
has a flag (`sashimi convert --help` lists them by group, with their defaults; `sashimi config
defaults <command> [format]` lists them per output format). The flags mean the same thing
everywhere: `-o/--output` is the output file, `--output-format` the output format (written
with the extension as you type it), `--output-dir` a folder for the outputs (`--no-output-dir`
for next to the inputs when `sashimi.toml` keeps one), `--output-name` the pattern outputs are
named by, `-f/--force` replaces whatever is in the way, `-y/--yes`
answers questions, `--duration` is how long the output is, and `--clip-length` how long each
sampled clip is.

Commands that take several paths take them as the shell lists them (`sashimi resize *.jpg -w
800`). `convert` takes its format last, the way `cp` takes its destination last (`sashimi
convert a.png b.png webp`), and `rotate` its angle (`sashimi rotate *.jpg 180`; 90 when there's
none). The last argument of `convert` is always the format: when a file or folder here has the
format's name (a `webp` folder of earlier outputs), sashimi says so, and `./webp` names the
folder. A last `0`, `90`, `180` or `270` given to `rotate` that is also a file or folder here is
refused, since both readings make sense; `--angle 90` (or `--output-format webp` for `convert`)
says it's the angle, and `./90` that it's the folder. A file you name that is already what the
command makes ("Already JPEG") isn't an error.

### Settings files

`sashimi config set` keeps a default in your own settings file, `~/.sashimi/sashimi.toml`, so
it applies wherever you run sashimi, and says which file it changed. `sashimi config set
--project` writes the project file instead: the `sashimi.toml` (or `.sashimi.toml`) in the
current folder or the nearest folder above it, created in the current folder if there is none.
A project file applies only when you run sashimi in its folder or a folder inside it, where it
wins over your own settings: a pattern kept with `--project` inside `New folder` isn't used by
`sashimi thumbs "New folder"` run from the folder above. When a project file here sets what you
just saved to your own settings, `config set` says so. `sashimi config unset` follows the same
rule (`--project` for the project file), `sashimi config path` shows both files, and `sashimi
config show` what they set. Only `sashimi config set` creates either file (a new one starts with
a comment saying what it is), and it never rewrites a file that isn't valid TOML; the library
never reads either.

### Limits on the command line

The CLI runs with the library's default limits. The ones you can meet with them have flags, each
taking a number with a unit, or `none` for no limit:

| Flag | Commands | Default | Example |
|---|---|---|---|
| `--max-pixels` | `convert`, `resize`, `rotate`, `favicon`, `edit`, `frames` | 64M | `sashimi convert scan.tif jpeg --max-pixels 300M` |
| `--max-frame-bytes` | `thumbs` (animated) | 1G | `sashimi thumbs film.mkv -a --cols 6 --rows 6 --max-frame-bytes 4G` |
| `--max-animation-bytes` | `convert`, `resize`, `rotate`, `edit`, `frames` | 1G | `sashimi edit big.gif --rotate 90 --max-animation-bytes none` |
| `--max-sheet-pixels` | `thumbs` | 64M | `sashimi thumbs film.mkv --cols 10 --rows 20 --max-sheet-pixels 200M` |

Every limit, the length limits included (off by default), can be set in the `[limits]` section of
`sashimi.toml`, with the same values; flags win over the files:

```sh
sashimi config set limits.max_pixels 300M             # in ~/.sashimi/sashimi.toml
sashimi config set --project limits.max_video_seconds 10m  # refuse videos over 10 minutes, here
sashimi config set limits.max_frame_bytes none        # no frame memory limit
sashimi config defaults limits                        # every limit, its flag, default and setting
```

Sizes take `K`, `M` and `G` (`512M`, `4G`, `4GiB`; a bare number is bytes), pixel counts `K`,
`M` and `G` (`200M` is 200 million), and lengths seconds or units (`600`, `10m`, `1h30m`).

### Where outputs go

An output's name depends only on its own input, the command and your settings, never on the
other files in the batch. Running a command again, on a bigger folder or on one subfolder, plans
the same name for a file, so an output made before is found and reported as "Exists".

Every command names its outputs `{input_name}.{role}.{output_ext}`: "this is `photo.jpg`,
rotated, saved as PNG" is `photo.jpg.rotated.png`. Conversions have no role, so theirs is
`{input_name}.{output_ext}`.

| Command | Role | Example |
|---|---|---|
| `convert` | (none) | `photo.png` → `photo.png.webp`; `convert . jpeg` → `photo.png.jpeg`, `convert . jpg` → `photo.png.jpg` |
| `remux` | (none) | `clip.mp4` → `clip.mp4.mkv` |
| `resize` | `resized` | `photo.jpg` → `photo.jpg.resized.jpg`; `photo.heic` → `photo.heic.resized.jpg` |
| `rotate` | `rotated` | `photo.jpg` → `photo.jpg.rotated.jpg` (`.png` with `--output-format png`); `clip.mkv` → `clip.mkv.rotated.mp4`; `clip.mov` → `clip.mov.rotated.mov` |
| `extract` | `extracted` | `clip.mkv` → `clip.mkv.extracted.mkv` |
| `edit` | `edited` | `photo.jpg` → `photo.jpg.edited.jpg` |
| `frames` | `frames` | `anim.gif` → `anim.gif.frames.gif` |
| `strip` | `stripped` | `clip.mov` → `clip.mov.stripped.mov` |
| `thumbs` | `sheet` | `clip.mp4` → `clip.mp4.sheet.jpg` |
| `thumbs -a` | `animated-sheet` | `clip.mp4` → `clip.mp4.animated-sheet.webp` |
| `preview` | `preview` | `clip.mp4` → `clip.mp4.preview.webp` |
| `favicon` | `favicons` | `logo.png` → `logo.png.favicons.zip` |

- **`{output_ext}`** is the format as you typed it (`--output-format jpeg` writes `.jpeg`,
  `jpg` writes `.jpg`, and so does `convert`'s format); else, when the output keeps the
  input's format, the input's own extension as written (`IMG_0001.JPG` → `IMG_0001.JPG.resized.JPG`);
  else sashimi's usual one (`.jpg` for JPEG, `.tif` for TIFF).
- **Patterns.** `--output-name PATTERN`, or `output_name` in the command's section of
  `sashimi.toml`, sets the pattern. Its fields are `{input_name}` (the input's file name,
  `clip.mp4`), `{input_stem}` (without the extension, `clip`), `{input_ext}` (the extension,
  `mp4`), `{role}` (`sheet`, `resized`, …) and `{output_ext}` (`jpg`); write `{{` and `}}` for
  literal braces. A pattern may spell out the extension instead of `{output_ext}`, but it must
  name the output's format (`.jpeg` is fine for a JPEG sheet, `.png` is refused before
  anything runs). Names must be valid on Windows as well as Linux and macOS. A pattern may
  start with folders, which are made inside each input's own folder (see below). For example:

  ```sh
  sashimi config set thumbs.output_name '{input_name}.jpeg'   # [thumbs] output_name = "{input_name}.jpeg"
  sashimi thumbs videos/ -r                                   # your existing video.mp4.jpeg sheets: "Exists"
  sashimi config set resize.output_name '{input_stem}.{role}.{output_ext}'   # short names: photo.resized.jpg
  sashimi convert photos/ webp --output-name '{input_stem}.{output_ext}'     # photo.webp, for this run only
  ```

  Flags win over `sashimi.toml`, and `sashimi config defaults <command>` shows each command's
  pattern and the one your files set. `[thumbs]` and `[animated_thumbs]` each name their own
  sheets. `flatten` keeps names as they are, so it has no pattern.
- **Outputs go next to their inputs,** wherever you run sashimi from.
- **A folder beside each input goes in the pattern.** `sashimi config set thumbs.output_name
  'sheets/{input_name}.jpeg'` writes `New folder/sheets/Bluebeard-5.mkv.jpeg` for `New
  folder/Bluebeard-5.mkv`, from any folder, and with `-r` each folder gets its own `sheets/`.
  Folders are made as needed, and a rerun finds the sheets there ("Exists"). `\` separates
  folders as `/` does, on every system. Patterns can't leave the input's folder: absolute paths,
  drive letters, `~`, `.` and `..` folders and empty folder names are refused; that is what
  `--output-dir` is for.
- **`--output-dir DIR` puts outputs in one folder** instead, with the usual meaning of a path: an
  absolute one as given, a relative one (`sheets` and `./sheets` alike) inside the folder you run
  sashimi from. The subfolders of a directory you passed are kept below it, and a pattern's
  folders below those: `sashimi thumbs Show -r --output-dir /srv/sheets` with the pattern above
  writes `/srv/sheets/S01/sheets/ep1.mkv.jpeg`. When you also name a folder or file inside one
  you passed (as `sashimi thumbs Show Show/* -r` does), the outermost folder counts, so the
  outputs go where they would without it. A file you name goes straight into `DIR`, and so do
  the files of each folder you name, so files from different folders can meet there: with
  `photos/x.jpg` and `photos/sub/x.jpg`, `sashimi resize photos/* -w 800 --output-dir out` (the
  shell names `photos/x.jpg` and the folder `photos/sub`) skips both `x.jpg`s as a clash, and a
  line at the end says how to keep each folder's layout: here, `sashimi resize photos -r -w 800
  --output-dir out`, which writes `out/x.jpg.resized.jpg` and `out/sub/x.jpg.resized.jpg`. It
  gives that command only when it would process the same files, and otherwise suggests giving
  each folder its own `--output-dir`. Files a search finds already inside `DIR` aren't
  processed (a line under the results says so), unless `DIR` holds the folder you searched.
- **Keep an output folder with `sashimi config set <command>.output_dir DIR`** (`~` is your home
  folder; `animated_thumbs.output_dir` for animated sheets). It means what the flag means, so a
  relative one is relative to wherever you run sashimi: with `output_dir = "sheets"`, `sashimi
  thumbs ~/Videos -r` run from your home folder writes `~/sheets/…`. `--output-dir` replaces it
  for one run, `--no-output-dir` writes next to the inputs, and `sashimi config unset
  <command>.output_dir` removes it. `sashimi config defaults <command>` and the command's
  `--help` show what your files keep, and `-n` shows where each file would go.
- Because the subfolders below `DIR` depend on the folder you pass, a folder given to
  `--output-dir` (or kept) finds earlier outputs only when you pass the same folder from the
  same place: `sashimi thumbs Show -r --output-dir sheets` writes `sheets/S01/ep1.mkv.sheet.jpg`,
  and a later `sashimi thumbs Show/S01 --output-dir sheets` writes `sheets/ep1.mkv.sheet.jpg`
  as well. A folder in the pattern doesn't have this problem.
- `-o FILE` names the output of a single input file, whatever the pattern and output folder; its
  extension must name the output's format (`-o out.png` is refused for a WebP).
- **A command never processes its own outputs.** When sashimi searches a folder, it leaves out
  each file that the same command, with the same settings, would make from another file it
  found: `photo.jpg.resized.jpg` for `resize`, `photo.png.webp` for `convert . webp`. That holds
  wherever in the search the two are, so `sashimi thumbs . -r --output-dir sheets` run twice
  writes nothing new, and a rerun reports the outputs as "Exists". The results end with a line
  naming what was left out. The same holds for files you name together, as a shell glob names
  them: a second `sashimi resize *.jpg -w 800` leaves out the `*.resized.jpg` files the first
  one made. A file you name on its own is always processed.
- **Everything else is processed, other commands' outputs included.** `sashimi convert . webp
  -r` in a folder of videos with their contact sheets in `sheets/` converts the videos and the
  sheets (and asks first, since that's videos and images), and `sashimi convert sheets webp`
  converts just the sheets. Running `convert . jpeg` after `convert . webp` converts the WebPs
  too (`photo.png.webp.jpeg`). Name files, or point at a subfolder, to process less.
- **Keep a pattern with `sashimi config set`.** A pattern given with `--output-name` is for that
  run only, and later runs don't know about it: after `sashimi thumbs . --output-name
  '{input_name}.jpeg'`, a plain `sashimi thumbs .` writes `video.mp4.sheet.jpg` beside your
  `video.mp4.jpeg`. `sashimi config set thumbs.output_name '{input_name}.jpeg'` keeps it, for
  every later run, wherever you run sashimi (`--project` for runs in that folder only).
- **Two outputs with one name are both skipped,** never renamed. This only happens with a
  pattern of your own (`{input_stem}.{output_ext}` gives `x.mp4` for both `x.mkv` and `x.mov`),
  with names that differ only in case, which are one file on Windows and macOS, or with
  `--output-dir` (or a kept `output_dir`) gathering files of one name from different folders
  (see `--output-dir` above). Each result says which other file it clashes with and the name
  they'd share ("Clashes with x.mov: both would be named x.mp4."), and one line at the end names
  the pattern, where you set it (the flag, or the `sashimi.toml` file and section) and how to
  tell the files apart, and, for files from different folders, how to keep each folder's layout.
  `--force` doesn't change this.
- **An output that already exists is left alone** and reported as "Exists", with what sashimi
  knows about it: "Output already exists" for a name sashimi gives outputs, "it's the input" for
  a pattern that writes in place (`{input_name}`), and "sashimi can't tell whether an earlier run
  made it" for a pattern that names outputs like originals (`{input_stem}.{output_ext}` beside
  your own `photo.jpg`). Names don't record the settings, so a rerun with other settings also
  reports "Exists"; use `--force` or another pattern.
- **`-f/--force` replaces whatever is there,** your own files and the input itself included, and
  says so in each result ("replaced the existing file", "replaced the input itself"). With
  `--output-name '{input_name}' -f`, `strip`, `rotate`, `edit` and the others change files in
  place. Outputs are written to a temporary file first and moved into place, so a failure leaves
  the original as it was. Without `--force` nothing is ever replaced, even a file that appears
  while sashimi works.
- **Problems come first:** the results table and the `--dry-run` listing show errors, then
  skipped files (name clashes marked), then existing outputs, then successes, and
  `--limit-output` cuts successes before problems.
- Messages name an output by its file name when it's beside its input, and otherwise by its path
  as built from the paths you gave, so relative to where you ran sashimi when those were relative
  ("Output already exists: sheets/Bluebeard-5.mkv.jpeg").
- `-n/--dry-run` shows each file's plan ("Would write a.png.webp", "Would replace …" with
  `--force`, "Output already exists", a skipped clash or a refused name, or why it's skipped)
  without reading or writing anything.
- Nothing deletes or changes an input unless you ask for it with an in-place pattern and
  `--force`, except `flatten`, which moves files (see `sashimi flatten --help`; `--dry-run` shows
  the plan first).

### Folders of mixed files

`rotate` handles photos and animated GIFs and WebPs (every frame) through the same code as `edit
--rotate`, MOV videos as MOV (Dolby Vision ones as MP4, the only format FFmpeg writes their
Dolby Vision settings into, unless MP4 would re-encode their sound) and other videos as MP4: a rotation alone of H.264, HEVC, AV1 or VP9
video (in a MOV: H.264, HEVC or ProRes) is lossless (every stream copied, only the display rotation
set, composed with any the video already has; a MOV keeps its PCM sound, while sound MP4 players
don't all play, such as PCM from a MOV with `--output-format mp4`, is re-encoded as AAC), and a
flip, `--reencode`, WebM or another codec re-encodes. FFmpeg, mpv, GStreamer, Chrome and Firefox
show MP4's and MOV's display rotation; `--reencode` is for players that don't. Apple's metadata
tracks (`mebx`) aren't copied into any format: FFmpeg can't write them. Images keep their format,
so a JPEG is re-saved (at quality 92); `--output-format png` keeps every pixel, and an image format
rotates only images (`mp4`, `webm` or `mov` only videos). `resize` works on images and animations; videos aren't supported
yet. `convert` and `resize` keep an animated GIF, WebP, APNG or AVIF animated when the output is
GIF, WebP, AVIF, APNG or PNG (an animated PNG in a `.png` file; `--no-animated` for a still, or
`sashimi config set convert.animated false` to keep that), and so do `edit` (`--no-animated`,
`edit.animated`) and `rotate`; to JPEG, BMP or TIFF an animation gives its first frame and a multi-page
TIFF its first page, and the result says so. Every command's message for a file it can't take says what it works on.

When a folder holds files that a command handles in different ways (photos and videos for
`rotate`; images and videos for `convert` to WebP or GIF; images and video or audio for
`strip`), sashimi asks first, on stderr:

```text
holiday contains 23 images and 44 videos; rotate all 67? [y/N]
```

(For two files it asks "rotate both?".)

The counts are of the files that would be processed. `-y/--yes` answers yes, and so does `-f`.
Without a terminal to ask on (in a script or a pipe), sashimi stops with exit code 2 and says to
pass `-y`. `--dry-run` shows the counts instead of asking, and `--json` reports them as `kinds`
and `routes`. Files you name one by one never ask, nor does a folder of one kind, nor
`flatten` (which takes `-y` as well as `-f`), `info` or `metadata`. Still and animated images
count as one kind: `rotate`, `edit`, `resize` and `convert` handle them with the same code.

### Scripting: exit codes and `--json`

Exit codes:

| Code | When |
|---|---|
| 0 | every file succeeded or already existed ("Exists"), including when a directory search skipped files it can't use |
| 1 | any file failed, or a file you named was skipped (not a video, already in that format, too short, not animated, its output name taken by another input's), or a path doesn't exist, or you answered "no" |
| 2 | the options were refused before anything ran, or a folder of mixed files needed `-y` and there was no terminal to ask on |

With `--json`, a command prints one report on stdout; logging and warnings go to stderr. Every
command that processes files (`convert`, `resize`, `rotate`, `edit`, `frames`, `favicon`,
`extract`, `remux`, `strip`, `thumbs`, `preview`, `flatten`) prints the same shape, which the batch
functions in `sashimi.api` also return:

```json
{"schema": 1, "command": "convert", "dry_run": false,
 "kinds": {"image": 2}, "routes": {"image": 2},
 "outputs_left_out": [], "clash_note": null,
 "summary": {"total_found": 2, "success": 2, "exists": 0, "skipped": 0, "errors": 0,
             "time_taken_seconds": 0.4, "avg_time_seconds": 0.2},
 "results": [{"file": "a.png", "status": "success", "output": "a.png.webp",
              "message": "Converted to a.png.webp (lossy: saved as WebP at quality 85)",
              "fidelity": {"level": "lossy", "content": "picture", "changed": ["saved as WebP at quality 85"],
                           "lost": [], "removed": [], "notes": [], "checked": true,
                           "text": "lossy: saved as WebP at quality 85"}}]}
```

`status` is `success`, `exists`, `skipped` or `error`. Every report also has `"dry_run": false`
(true with `--dry-run`, when the results are the plan); `kinds` and `routes` count the files the
command planned to process, `outputs_left_out` lists the command's own earlier outputs a folder
search left out (see "Where outputs go"), and `clash_note` names the pattern that gave inputs one
output name, or is null. `flatten` adds `renamed` and `removed_dirs` instead. Each result's `fidelity` says what a written output kept (see "What each output kept"), or is null. `info --json` prints the probe result, `metadata --json` the metadata summary, and `config ... --json` its own data.

## Development

sashimi uses [uv](https://docs.astral.sh/uv/):

```sh
uv sync                         # dev environment, including all extras
sh scripts/check.sh             # ruff format --check, ruff check, mypy, then pytest on Python 3.14
sh scripts/check.sh full        # the same, then pytest on Python 3.11 to 3.14
sh scripts/sandbox_check.sh     # every converter in a FileSushi-style locked-down container (needs Docker)
```

`scripts/check.sh` doesn't change the tree: it uses the locked dependencies as they are
(`uv run --frozen`) and runs each Python version in its own environment under `.venvs/`, so the
`.venv` that `uv sync` made is left alone. The integration tests make their own media with
FFmpeg and are skipped when it isn't installed.

`scripts/sandbox_check.sh` builds a `python:3.14-alpine` worker image from the wheel (wheels only,
`apk add ffmpeg`), then runs every converter under FileSushi's sandbox flags (`--network none
--read-only --pids-limit 128 --cpus 2 --memory 1g`) on an input named `original.blob`. Each job
reports its time, peak memory and peak process/thread count, and fails if the output is invalid,
anything is written outside `/vault/output` or left in `/tmp`, or a network call is attempted.
`--skip-long` skips the two cases on a 10-minute 1080p video: the 300 MB animated-sheet budget and
a 60-second, 1920 px GIF. The image is tagged `sashimi-sandbox-check`, or `$SASHIMI_SANDBOX_IMAGE`
if set; remove it with `docker rmi` when you're done.

## Contributing

Bug reports and ideas are welcome in the [issue tracker](https://github.com/filesushi/sashimi/issues).
For a pull request, run `sh scripts/check.sh full` first; every source file starts with the
project's two SPDX lines, and contributors may add themselves to
[AUTHORS](https://github.com/filesushi/sashimi/blob/main/AUTHORS).

## License

Copyright 2026 The FileSushi Authors, who are listed in
[AUTHORS](https://github.com/filesushi/sashimi/blob/main/AUTHORS). Licensed under the
[Apache License, Version 2.0](https://github.com/filesushi/sashimi/blob/main/LICENSE).
