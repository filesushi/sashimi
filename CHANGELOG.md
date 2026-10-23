# Changelog

sashimi follows [Semantic Versioning](https://semver.org/). Before 1.0, minor versions may break
the Python API; every break is listed here. See [MIGRATION.md](MIGRATION.md) for upgrade steps.

## 0.6.0 — 2026-10-23

The first release on PyPI, as **`sashimi-convert`**. Builds labelled 0.5.1 were shared privately
but never published; 0.6.0 contains all of their changes, listed here too. It also brings over
what ftool still did better (a sampled preview, remuxing, the animated-sheet grid and labels, cuts
to the end), adds image editing (rotate, flip, crop, and brightness, contrast and saturation that
match CSS filters, for still images and every frame of animated GIFs and WebPs), frame selection
and speed for animations, and an MP4 conversion that copies the streams when it can, and settles
the batch and CLI behaviour before it goes public: one output-naming policy in which a name
depends only on its own input, with patterns you can set, one meaning per flag, one JSON report,
`--dry-run` everywhere and exit codes that report skipped files you named. Every limit is now the
caller's to set (`sashimi.config.Limits`, passed as `limits=`; on the command line, `--max-*`
flags and a `[limits]` section in `sashimi.toml`), with defaults that protect the machine and no
limit on lengths. Every output that can carry the source's metadata has one `metadata` setting
(`keep`, the default, `remove_location` or `remove_all`), `sashimi.metadata` reads what a file
holds and removes it without re-encoding, and the last known bugs are fixed.
[MIGRATION.md](MIGRATION.md) shows what to change.

Outputs are byte-identical to 0.5.1 except: animated contact sheets (a 3x3 grid by default, 4x2
for portrait videos, and padded time labels), still sheets of videos with an aspect ratio from
0.55 to 0.6 such as 9:16 (8x2 instead of 7x2), animated sheets of clips shorter than one tile's
clip (one tile instead of an error), MP4 conversions of 8-bit H.264 videos with default
encoder settings, which are now copied instead of re-encoded (see Breaking), and PNG and TIFF
conversions of images with a colour profile and `keep_icc=False`, and PNG conversions of CMYK
images, which now carry no profile (see Fixed; the pixels are the same); images whose source has
metadata, which now keep it by default (see Breaking; the pixels are the same); ICO files and
favicon packs of images with a colour profile, whose colours are now converted to sRGB; and
lossless cuts and remuxes of MOVs with chapters, and mono Opus audio above 256 kb/s, which used
to fail. Files the old limits refused may now succeed, since the limits are the caller's and
lengths have none by default.

### Breaking

- **One `metadata` setting replaces `keep_exif`, and keeps the source's metadata by default.**
  Every model whose output can carry the source's metadata has `metadata: MetadataMode =
  "keep"`, one of `"keep"`, `"remove_location"` and `"remove_all"` (`sashimi.config.METADATA_MODES`):
  the image models for JPEG, PNG, WebP, TIFF and GIF (convert, resize, edit, frames), WebM and MP4
  conversions, rotate and flip, every trim (lossless and accurate, so remuxes too) and every
  audio format. `keep_exif` is removed (passing it raises "Unknown option: keep_exif."); the CLI's
  `--keep-exif` is `--metadata keep|remove_location|remove_all`. **Image outputs now keep EXIF,
  XMP, IPTC, comments and PNG text by default**, where they used to drop EXIF (`keep_exif=False`);
  pass `metadata="remove_all"` for the old behaviour. Video and audio kept their tags before and
  still do by default. `keep_icc` still decides the colour profile alone.
  - `keep` carries everything the output format holds: EXIF (camera, dates, GPS; byte for byte in
    JPEG, PNG and WebP, rewritten in TIFF), XMP, IPTC and Photoshop resources (JPEG and TIFF),
    comments (JPEG, PNG and GIF) and PNG text (PNG to PNG); for video and audio, the global and
    stream tags and chapters the container holds. The README's "Metadata" section lists what
    each format can't hold.
  - `remove_location` removes every location carrier sashimi knows of and keeps the rest: EXIF's
    GPS directory, XMP's GPS, latitude, longitude, altitude, city, state, country and location
    properties, IPTC's place fields, PNG text that names a place, the QuickTime, MP4, Matroska,
    ID3 and Vorbis location tags, and timed metadata tracks (GPS tracks) on copies.
  - `remove_all` keeps only what showing or playing the file needs: the pixels and streams, the
    colour profile (as `keep_icc` says), the orientation when sashimi doesn't apply it, stream
    languages and dispositions, and the display rotation on copies.
  - When sashimi applies the orientation (`auto_orient`, and always in edits), the EXIF and XMP
    orientation is reset to 1 rather than removed, and the EXIF thumbnail, which shows the picture
    before the change, is dropped. Edits and frame selection drop embedded thumbnails always.
- **ICO files and favicon packs are converted to sRGB.** An image with a colour profile has its
  colours converted from it to sRGB (browsers treat icons as sRGB), and no icon carries the
  profile or any metadata. Pillow's ICO writer used to copy the profile into the PNG frames, and
  the pack's PNGs carried it too, except the Apple touch icon. Images without a profile are
  unchanged, byte for byte.
- `MediaInfo` and `StreamInfo` gain fields (`MediaInfo.tags` and `audio_channels`;
  `StreamInfo.language`, `attached_pic`, `channels`, `tags` and `codec_tag`), so `sashimi info
  --json` for video and audio prints them too. Code that builds them with keywords is unaffected.
- The command builders take `source=` (the probed `MediaInfo`), which carries out `metadata` and
  the Opus ceiling below; without it they build the commands they did, plus the fixed list of
  location tags for `remove_location`.

- **Limits are configuration, set by the caller.** The fixed limits (10-minute videos,
  60-minute audio, 30-second GIFs and previews, 10 preview clips, 400 MiB of frames for animated
  sheets and GIF previews, 256 MiB for animated edits, 64 MP images and sheets) are replaced by a
  `sashimi.config.Limits` object that every converter, command builder and batch function takes
  as `limits=` (`None` means `Limits()`):

  | Field | Default | Limits | Was |
  |---|---|---|---|
  | `max_pixels` | 64,000,000 | images, by decoded pixels | `max_pixels=` argument, 64 MP |
  | `max_frame_bytes` | 1 GiB | video frames held at once: an animated sheet's clips (GIF previews no longer need it; see Changed) | `MAX_FRAME_BYTES`, 400 MiB |
  | `max_animation_bytes` | 1 GiB | frames of an animated GIF or WebP being edited | `MAX_ANIMATION_BYTES`, 256 MiB |
  | `max_sheet_pixels` | 64,000,000 | contact sheets | `MAX_SHEET_PIXELS` |
  | `max_video_seconds` | none | WebM/MP4 conversions (re-encodes and MP4 copies), rotate and flip, accurate cuts | `MAX_VIDEO_SECONDS`, 600 |
  | `max_audio_seconds` | none | audio conversions | `MAX_AUDIO_SECONDS`, 3600 |
  | `max_gif_seconds` | none | GIFs and animated WebPs made from a video, and a preview's clips together | `MAX_GIF_SECONDS`, 30 |
  | `max_preview_clips` | none | a preview's `samples` | `MAX_PREVIEW_SAMPLES`, 10 |

  Every limit can be raised, or turned off with `None` (`Limits.unlimited()` turns them all off).
  A refusal states the limit in force ("Video clips can be at most 2 hours long.", "This image
  is too large to process: it has 120 megapixels (12000x10000), and the limit is 64.") and names
  the field in the new `UserInputError.limit`. `limits.check(config)` applies the limits a config
  breaks without a file, so a web app can refuse a job when it is queued.
- **`max_pixels=` is now `limits=`** on `image.convert`, `to_ico`, `favicon_pack`, `edit`,
  `select_frames`, `frame_strip` and `frames_info`: pass `limits=Limits(max_pixels=…)`. A
  `max_pixels` above Pillow's own limit (about 179 MP), or `None`, now works: sashimi raises
  Pillow's limit for that call.
- **The limit constants are gone:** `MAX_GIF_SECONDS`, `MAX_VIDEO_SECONDS`, `MAX_AUDIO_SECONDS`,
  `MAX_PREVIEW_SAMPLES`, `MAX_FRAME_BYTES`, `MAX_ANIMATION_BYTES`, `MAX_SHEET_PIXELS` and
  `MAX_SHEET_HEIGHT`. A name sashimi no longer enforces would mislead; read `Limits()`'s fields
  for the defaults. `DEFAULT_MAX_PIXELS` stays, as `Limits.max_pixels`'s default.
- **Config fields keep only format limits as bounds.** Removed: the length bounds above (so
  `from_params` no longer refuses a 31-second GIF, a 601-second clip or a 3601-second audio
  clip; `Limits.check` and the converters do, when the caller sets the limit), the preview's
  `samples` (10), `sample_duration` (10 s), `max_height` (2160) and `max_width` (3840), the video
  `max_width` (3840), the sheets' `cols`/`rows` (20), `gap` (100), `width` (8000 still, 3840
  animated) and `sample_duration` (10 s), the edits' `brightness`, `contrast` and `saturation`
  (2; CSS has no upper bound), the frames' `speed` (0.25-4; now any speed above 0), the frame
  strip's `tile_height` (256) and `columns` (50), WAV and FLAC `sample_rate` (192000; FLAC now
  stops at its format's 1048575, `FLAC_MAX_SAMPLE_RATE`), and the favicon manifest names' 100
  characters. Widened to the codec's limit: M4A and OGG `bitrate_kbps` (320 → 512, as for the
  video models' audio). Animated outputs' `fps` (30 → 50, `MAX_ANIMATION_FPS`: browsers play
  frames under 20 ms at 100 ms). Lower bounds are unchanged, and every number field now refuses
  infinity and NaN. Code that read these bounds from `model_fields` (`annotated_types.Le`) gets
  none, or 50 for `fps`.
- **`WebpImageConfig.optimize` (and `WebpResizeConfig.optimize`) is removed.** Pillow's WebP
  writer ignores it, so it never changed an output; `webp_method` is WebP's effort setting.
  Passing `optimize` for WebP now raises "Unknown option: optimize.", and the CLI warns that
  `--optimize` doesn't apply to WebP.
- **Refusal messages state the limit**, so these read differently: "This image is too large to
  process." (now "…: it has 30 megapixels (6000x5000), and the limit is 24."), "This animation
  is too large to edit. …", and the preview, animated sheet and contact sheet size refusals. The
  length refusals read as before when the limit is the old one ("Videos longer than 10 minutes
  aren't supported yet. …").
- **Still JPEG and PNG contact sheets may be taller than 16,383 px**, WebP's limit, which used to
  apply to every format: JPEG now stops at 65,500 px (`JPEG_MAX_SIDE`) and PNG only at
  `max_sheet_pixels`. A sheet too big for its format raises "These settings would make the
  contact sheet too large for WebP, which can be at most 16383 pixels on a side (…)".
  `MAX_SHEET_HEIGHT` is replaced by `WEBP_MAX_SIDE`, with `JPEG_MAX_SIDE` and `GIF_MAX_SIDE`.
- `ParamsModel.bound_messages` is removed: no field bound is a product limit any more.

- **"To MP4" copies the streams when it can.** `video.convert` with an `Mp4VideoConfig` now picks
  the fastest route that gives what a re-encode would, as far as anyone watching can tell
  (`video.plan_mp4_route`, pure):
  - `copy`: the video is 8-bit 4:2:0 H.264 (`yuv420p`/`yuvj420p`) with even dimensions and the
    audio is AAC, absent, or dropped by `mute`: both are copied, in a fraction of a second;
  - `copy_video`: the same video with other audio (Opus, MP3, …), or an `audio_bitrate_kbps`
    other than its default: the video is copied and the audio re-encoded to AAC;
  - `encode`: everything else, as before, byte for byte. A start time, a length shorter than
    the rest of the video, a `max_width` below the video's width, or a `crf` or `preset` other
    than the defaults (23, `veryfast`) asks for a re-encode; HEVC, VP9, AV1, 10-bit and 4:4:4
    video are always re-encoded, since many browsers can't play them in MP4.
  The copy routes keep the re-encode's streams (the first video and audio track; no subtitles
  or data), its length limit (`-t`, `Limits.max_video_seconds`) and `+faststart`, and keep the
  source's display rotation as metadata. If ffmpeg can't copy a file, it is re-encoded instead.
  `video.convert` returns the route taken (`VideoRoute`: `"copy"`, `"copy_video"` or
  `"encode"`; always `"encode"` for other targets), where it returned `None`. There is no
  option for the route.
- **CLI exit codes:** a file named on the command line that is skipped (unsupported, too short,
  not animated) now exits 1, like a failure; it exited 0. A directory search that skips files
  it can't use still exits 0, and so does an output that already exists, and a named file that
  is already what the command makes ("Already JPEG", "Already MKV"), since nothing went wrong
  and there is nothing to do (`BatchPlan.already_done` and the report's `already_done` list
  them).
- **`sashimi info` on an image** prints the image's format, size and frames (with each frame's
  duration), and `--json` prints `sashimi.image.frames_info`'s fields, instead of ffprobe's view
  (which refused most images). Videos and audio are unchanged.
- **Animated contact sheets default to a 3x3 grid** (4x2 for portrait videos, aspect ratio under
  0.95), where `cols`/`rows` `"auto"` used the still sheets' table (6x5 tiles of about 205 px for
  16:9 at 1280 px). 3x3 renders about 2.4 times faster, with 426 px tiles. Explicit `cols`/`rows`
  are unchanged. Their time labels have 4 px of padding and centred text (still sheets keep theirs).
- **Still sheets of 9:16 videos use 8x2** instead of 7x2: the automatic grid's first step moved
  from an aspect ratio of 0.55 to 0.6.
- **Animated sheets of short clips:** a video shorter than `sample_duration`, but at least a
  second long, gets one tile of the whole video instead of `MediaTooShortError`.
- **One output-naming policy for the batch API and CLI, in which a name depends only on its own
  input** (see the README's "Where outputs go"). A name never depends on the other files in the
  batch, so a rerun, a bigger batch or a subfolder plans the same name, and "exists" means the
  same thing every time. **Every command's default is `{input_name}.{role}.{output_ext}`**, and
  conversions, which have no role, are `{input_name}.{output_ext}` (see Added for patterns):
  - contact sheets `<video name>.<format>` (`clip.mp4.jpeg`) become `clip.mp4.sheet.jpg`,
    animated ones `clip.mp4.animated-sheet.webp`; previews are `clip.mp4.preview.webp` and
    favicon packs `logo.png.favicons.zip`;
  - conversions keep the input's name: `photo.png` to WebP is `photo.png.webp` (it was
    `photo.webp`), and a remux of `clip.mp4` is `clip.mp4.mkv`;
  - resized images `photo_resized.jpg` become `photo.jpg.resized.jpg`, rotated videos
    `clip_rotated.mp4` become `clip.mp4.rotated.mp4`, cuts `clip_extracted.mkv` become
    `clip.mkv.extracted.mkv`; edits are `photo.jpg.edited.jpg`, frame selections
    `anim.gif.frames.gif` and copies without metadata `clip.mov.stripped.mov`. Rotating
    `photo.jpg` to PNG gives `photo.jpg.rotated.png`: "photo.jpg, rotated, saved as PNG";
  - `{output_ext}` is the extension the caller chose for the format (`convert . jpeg` writes
    `.jpeg`, `convert . jpg` writes `.jpg`, and so do `--output-format` and `output_ext=`), else
    the input's own as written when the output keeps its format (`IMG_0001.JPG` keeps `.JPG`),
    else sashimi's standard one (`.jpg`, `.tif`). In 0.5 `jpeg` and `jpg` both wrote `.jpg`;
  - the `suffix` argument of `resize_images(_generator)` and `transform_videos(_generator)`, and
    the CLI's `--suffix`, are removed;
  - **two inputs that would be written to the same file are both skipped**, never renamed, with
    or without `force`, each with a `skipped` result naming the other and the shared name
    ("Clashes with x.mp4: both would be named x.jpg."); when a pattern caused it,
    `BatchPlan.clash_note` (the report's `clash_note`, printed once under the CLI's results) names
    the pattern and where it was set, and how to tell the files apart ("2 files were skipped
    because the pattern {input_stem}.jpg (--output-name) gives them the same name as another
    input's output; keep {input_name} or {input_ext} in it to tell them apart."). The rest of
    the batch carries on. In 0.5 the second was reported as existing, or with `force` overwrote
    the first;
  - **a run never processes its own outputs:** a directory search leaves out each file that
    this command, with these settings (its pattern and output format), would make from another
    file found in the same search, wherever in the search the two are, so outputs written with
    `output_dir` are recognised too. Reruns report "exists" instead of making
    `photo.jpg.resized.jpg.resized.jpg`, and `thumbs . -r --output-dir sheets` run twice writes
    nothing new. A name that is only another file's stem with a new extension (`photo.webp`
    under `{input_stem}.{output_ext}`) counts only in that file's folder. **Everything else is
    processed, other commands' outputs included** (owner, 2026-10-21): `convert . webp` after
    `thumbs .` converts the sheets too, and `convert . jpeg` after `convert . webp` writes
    `photo.png.webp.jpeg`. The same goes for files named together, as a shell glob names them:
    a second `resize *.jpg` leaves out the `*.resized.jpg` files the first made, where it made
    `photo.jpg.resized.jpg.resized.jpg`. A file named on its own is always processed. A file
    reached twice (named and found in a folder) is processed once;
  - **an existing output is reported as it is:** "Output already exists: …" for a name sashimi
    gives outputs, "… already exists (it's the input; …)" when a pattern writes in place, "(it's
    also one of the inputs)", and "(sashimi can't tell whether an earlier run made it: …)" when
    a pattern names outputs like originals;
  - **`force` replaces whatever is in the way**, the input itself included (`{input_name}`
    edits in place), and every replacement is reported ("… (replaced the existing file)",
    "(replaced the input itself)"). Outputs are always written to a staged file and moved into
    place, so a failure leaves the original intact; an input that another input's output will
    replace is processed first. Without `force`, the output is moved into place only if its name
    is still free, so a file created meanwhile is never replaced.
- **Every `*_generator` returns a `sashimi.api.Batch`** (an iterator yielding the same protocol)
  whose `plan()` returns a `BatchPlan`: each file's `PlannedFile` (output, action, kind and
  route), counts by kind and route, `describe()` ("44 videos and 23 images"), `mixed`, the
  command's own earlier outputs left out, and `clash_note`. Code that only iterates the generators is unaffected. Options a
  batch function can tell are wrong, such as a pattern whose literal extension isn't the
  output's format, still raise `InvalidOptionsError` when it's called.
- **`--format` is `--output-format`** on every command that has one (`resize`, `rotate`, `edit`,
  `frames`, `extract`, `remux`, `thumbs`, `preview`), and is written with the extension as typed:
  `--output-format jpeg` writes `.jpeg`, `jpg` writes `.jpg` (`jpg` and `tif` are accepted
  wherever `jpeg` and `tiff` are). `convert` keeps the format as its second argument.
- **`sashimi rotate` takes photos, animated GIFs and WebPs, and videos** (it took only videos,
  and a photo was "Unsupported file type"). Images go through `sashimi.image.edit`, the same code
  as `edit --rotate`, keeping their format (`--output-format png` for no JPEG re-save); videos are
  re-encoded as before. An image format in `--output-format` rotates only images, a video format
  only videos. `rotate` also takes `-q` (images) and the image limits.
- **Folders of mixed files ask first.** A run over a directory whose files the command handles
  with different code (photos and videos for `rotate`; images and videos for `convert` to WebP or
  GIF; images and video or audio for `strip`) asks on stderr ("holiday contains 23 images and 44
  videos; rotate all 67? [y/N]"; "… rotate both?" for two files). `-y/--yes` answers yes, and `-f` implies it; without a terminal
  the command stops with exit code 2. `--dry-run` shows the counts instead. The batch API never
  asks; `Batch.plan()` gives callers the counts.
- **Unsupported files say what the command takes:** "thumbs works on videos, not .jpg files.",
  "resize works on images and animations; videos aren't supported yet.", instead of
  "Unsupported file type".
- **Problems come first** in the results table and the `--dry-run` listing (errors, skipped with
  name clashes marked, existing, successes), and `--limit-output` hides successes before problems
  and says how many of each it hid.
- **The batch protocol's results have four items:** `(path, status, message, output)`, where
  `output` is the file written (or `None`). Every non-generator batch function returns the same
  report as the CLI's `--json`: `schema` (1), `command`, `summary` (with `avg_time_seconds`) and
  `results` (each with `output`). The CLI's `avg_processing_time_seconds` is now `avg_time_seconds`.
- **`sashimi.api.extract` is a batch operation:** `extract(paths, start=None, end=None, *,
  duration=None, container=None, accurate=False, …, output=None, output_dir=None)` returns the
  report, and `extract_generator` yields the protocol. Invalid times and options raise
  `InvalidOptionsError` before anything runs, instead of returning `("error", "Time Parsing
  Failure: …", path)`; per-file failures are results classified by type. `output_path` is now
  `output`. `parse_time_to_seconds` raises `InvalidOptionsError` instead of `ValueError`.
- **`sashimi.api.flatten`** returns the report (with `dry_run`, `renamed` and `removed_dirs`)
  instead of `{"moved", "renamed", "removed_dirs"}`, raises `InvalidOptionsError` for a missing
  folder or a file instead of `FileNotFoundError`/`NotADirectoryError`, and leaves symbolic links
  and the files and folders they point to where they are (see Fixed).
- `convert_files_generator` raises `InvalidOptionsError` instead of `ValueError` for an unknown
  target or a config for another format.
- **Lossless trims convert text subtitles the container can't hold** (MP4 `mov_text` to SRT for
  MKV, SRT/ASS/WebVTT to `mov_text` for MP4 and MOV, to WebVTT for WebM), where
  `check_copy_compatible` refused them with `InvalidOptionsError`. Bitmap subtitles are still
  refused where they were. Commands for files without such subtitles are unchanged.
- **CLI flags mean one thing each:**
  - `-o` is the output file everywhere (`-o/--output`); `thumbs`, `resize` and `rotate` used it
    for the format, which is `--output-format` (no short flag) in every command;
  - `thumbs --duration` (seconds per animated tile) is `--clip-length`, so `--duration` always
    means the output's length;
  - `-h` is `--help` everywhere; `--height` is `-H` (it was `-h` in `convert` and `resize`), in
    `preview` too;
  - `extract` needs a start, an end or a `--duration`, but no longer both a start and an end;
  - `--json` prints the shared report for `extract` and `flatten` too (`extract` printed a flat
    `{status, message, output, seconds}`).
- **`sashimi.toml` takes current key names only.** `static_width`, `jpeg_quality`,
  `animated_width`, `output_fps` and `output_format` were read as `width`, `quality`, `fps` and
  `target`; they now get the unknown-settings warning and are ignored. Write `width`, `quality`,
  `fps` and `target` (in `[animated_thumbs]` for animated sheets). `target` and `output_name`
  in `[thumbs]` no longer apply to animated sheets.
- **`sashimi config set` and `config unset` change your own settings by default**,
  `~/.sashimi/sashimi.toml`, so a setting applies wherever you run sashimi; they changed the
  project `sashimi.toml` (found from the current folder upward, or created there), and a
  pattern set inside a folder was ignored when sashimi ran from the folder above. The project
  file is written only with the new **`--project`/`-p`**; `-g/--global` is gone. Both print the
  file they changed, as a full path, and when the project file that applies here sets what you
  just saved to your own settings, `config set` says it wins there. `config path` names the files
  "User" and "Project" and says when each applies; `config show` names both files and its
  `--json` has `files` (`user`, `project`).
- **`sashimi frames` with nothing to change is refused** (no `--keep`, `--drop`, `--speed` or
  output format; `--speed 1` changes nothing too), saying what to pass, instead of writing an
  unchanged copy (exit 2).
- **The `[global]` section of `sashimi.toml` is gone.** `verbose` and `priority` were accepted but
  never read; files that still set them now get the unknown-settings warning, and `sashimi config
  set global.…` is refused.

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

- **`sashimi.metadata`:** `read(path, *, limits)` returns a `MetadataInfo` for showing a file's
  metadata to people: the carriers found (`EXIF`, `GPS`, `XMP`, `IPTC`, `QuickTime/MP4 tags`,
  `ID3`, `Vorbis comments`, `chapters`, …), whether it has a location and its coordinates or
  place, the camera, date taken, software, title, artist, album, creation time and comment,
  whether it has a colour profile, and other named tags. It decodes no pixels or samples and
  works on outputs too; it reports what it found, not a promise that nothing else is there.
  `strip(input, output, mode="remove_all", *, keep_icc=True, …)` writes a copy without the
  metadata, re-encoding nothing: JPEG segments, PNG chunks, WebP chunks, GIF extensions and TIFF
  tags are dropped or filtered with the compressed pixels copied byte for byte, and video and
  audio streams are copied (MKV, WebM, MP4, MOV, M4A, MP3, FLAC, OGG, WAV). It returns the
  format's extension, which `strip_format(path)` gives beforehand; `build_strip_command` is the
  pure ffmpeg command. An MPO keeps only its main picture.
- **CLI:** `sashimi strip` (`--remove remove_location|remove_all`, `--strip-icc`, `-n`, writing
  `<stem>.stripped.<ext>`), `sashimi metadata <file>` (`--json`), and `--metadata` on `convert`,
  `resize`, `rotate`, `edit`, `frames`, `extract` and `remux`. Batch: `sashimi.api.strip_files(_generator)`
  and `STRIP_EXTENSIONS`, and `metadata=` on `extract(_generator)` and `remux_files(_generator)`.
- `video.build_palette_command`, `video.build_preview_palette_command` and `video.two_pass`, and
  `palette=` on `video.build_convert_command` and `video.build_preview_commands`, for callers
  that run the two passes themselves; without `palette=`, the builders give the one-pass command,
  which makes the same GIF.
- `sashimi.config.MetadataMode`, `METADATA_MODES` and `OPUS_MAX_KBPS_PER_CHANNEL`;
  `sashimi.image.to_srgb`; `sashimi.audio.output_channels`.
- `frames_info` reads animated PNGs' frame count, frame durations and loop count from their
  `acTL` and `fcTL` chunks (they were `[0, 0, …]`); `animated` stays False, as PNG animations
  can't be edited.
- **`sashimi.config.Limits`** (see Breaking): `Limits(...)`, `Limits.from_params(params)`
  (numbers or strings; `None`, `""` or `"none"` turns a limit off), `Limits.unlimited()` and
  `limits.check(config)`. Importable without Pillow or ffmpeg, like the rest of `sashimi.config`.
  Also `UserInputError.limit`, `WEBP_MAX_SIDE`, `JPEG_MAX_SIDE`,
  `GIF_MAX_SIDE`, `MAX_ANIMATION_FPS` and `FLAC_MAX_SAMPLE_RATE`.
- **Limits on the command line:** `--max-pixels` (`convert`, `resize`, `favicon`, `edit`,
  `frames`), `--max-frame-bytes` (`thumbs`, for animated sheets), `--max-animation-bytes`
  (`edit`, `frames`) and `--max-sheet-pixels` (`thumbs`), taking `200M` (pixels), `4G` or `512M`
  (bytes), or `none`; and a `[limits]` section in `sashimi.toml` for every limit, including the
  lengths (`sashimi config set limits.max_video_seconds 2h`). Flags win over the files.
  `sashimi config defaults limits` lists each limit, its flag, default and `sashimi.toml` value.
  `sashimi info` reads images of any size (it decodes nothing).
- **Image editing:** `sashimi.image.edit(input, output, config, *, scratch_dir, limits,
  on_progress)` applies, in this order: the EXIF orientation (so the picture is as it is shown),
  `rotate` (0, 90, 180, 270, clockwise) and `flip` (`horizontal`, `vertical`), the crop box
  (`crop_left`, `crop_top`, `crop_width`, `crop_height`, in pixels of the rotated and flipped
  picture; unset means no crop), then `brightness`, `contrast` and `saturation` (multipliers of
  at least 0, 1 unchanged). The adjustments are CSS `filter: brightness() contrast() saturate()` as the
  Filter Effects spec defines them (in sRGB, unpremultiplied, clamped after each step), not
  Pillow's `ImageEnhance`, so a browser preview with the same CSS matches: brightness and
  contrast exactly, saturation within one 8-bit level, all three together within two
  (`image.adjust_colors`, pinned by tests against the spec's formulas). The output format is
  `config.target`; pass the source's own (`frames_info(path).edit_target`) to keep it.
  Transparency is kept by PNG, WebP, GIF and TIFF; JPEG and BMP flatten it onto white. JPEG,
  WebP and JPEG-compressed TIFF are re-saved at quality 92 (`EDIT_QUALITY`). A crop box outside
  the picture raises `InvalidOptionsError` ("The crop area must fit inside the picture, which is
  400x300 pixels."); a no-op edit still writes a valid copy. Config: `IMAGE_EDIT` by `target`
  (`JpegEditConfig`, `PngEditConfig`, `WebpEditConfig`, `GifEditConfig`, `BmpEditConfig`,
  `TiffEditConfig`), each with the edit fields plus that format's save fields from the image
  models (`quality`, `keep_exif`, `keep_icc`, …). Also `image.crop_box` and `image.aspect_crop`
  (the largest centred box of a shape such as "16:9", for UIs and the CLI's `--aspect`), and
  `CROP_ASPECTS` (free, 1:1, 4:3, 16:9, 9:16).
- **Animated GIF and WebP edits:** saved as GIF or WebP, every frame of an animation gets the same
  edit, keeping each frame's duration and the loop count. GIF frames keep their colours exactly
  when they have at most 256 (any edit of a GIF source does), with transparency and disposal 2
  when a frame has transparent pixels. Animations whose decoded frames would take more than
  `Limits.max_animation_bytes` (1 GiB by default: width × height × 4 × frames) raise
  `MediaTooLargeError` before any frame is decoded. Animated PNGs raise `UnsupportedMediaError`; saved as a still format, an
  animation gives its first frame.
- **Frame selection:** `sashimi.image.select_frames(input, output, config, …)` keeps some frames
  of an animated GIF or WebP (`keep`: frame numbers from 0, "0-9,12" or a list; unset keeps all;
  kept frames play in their original order) and changes its `speed` (above 0, dividing every
  frame's duration; frames browsers would play at 100 ms count as 100 ms, none gets shorter
  than `MIN_FRAME_MS`, 20 ms, and none longer than the format stores). Frames are as shown in the animation, so dropping some never leaves
  holes. Config: `IMAGE_FRAMES` by `target` (`GifFramesConfig`, `WebpFramesConfig`).
- **Frame information:** `sashimi.image.frames_info(path) -> FramesInfo` gives the format,
  `edit_target`, size as shown, frame count, each frame's duration and the loop count, read from
  the file's blocks without decoding pixels (300 frames in about 2-40 ms).
  `sashimi.image.frame_strip(input, output, FrameStripConfig)` draws every frame as a small tile
  in one still WebP (a sprite sheet for frame pickers) and returns the `FrameStrip` layout;
  `plan_frame_strip` computes it without the image.
- **Batch editing:** `sashimi.api.edit_images(_generator)(paths, config_or_function, *,
  crop_aspect=…)` and `edit_frames(_generator)(paths, config_or_function, *, drop=…)`, writing
  `<stem>.edited.<ext>` and `<stem>.frames.<ext>` in each image's own format.
- **CLI:** `sashimi edit` (`--rotate`, `--flip`, `--crop WxH+X+Y`, `--aspect 16:9`,
  `--brightness`, `--contrast`, `--saturation` and the save options) and `sashimi frames`
  (`--keep 1-10,15`, `--drop 3`, `--speed 2`; frames numbered from 1, as `sashimi info` lists
  them). `sashimi config defaults edit|frames [format]` describes their options.
- **`--dry-run` (`-n`) on every command that processes files** (`convert`, `resize`, `rotate`,
  `edit`, `frames`, `favicon`, `extract`, `remux`, `thumbs`, `preview`, as well as `flatten`):
  outputs are planned under the naming policy and each file is reported as "Would write …",
  "Would replace …" (with `-f`), existing, refused (an unusable name), or skipped (a name clash),
  without opening or writing anything. Every
  batch function takes `dry_run`.
- `MediaInfo.pixel_format` (ffprobe's `pix_fmt` of the video, e.g. `yuv420p`).
- `sashimi.config` exports `Rotation` and `Flip`, the types of `rotate` and `flip`.
- `scripts/sandbox_check.sh` runs edits (photo, GIF, WebP, two animations at the frame budget
  and one over it), frame selection (GIF, WebP), a frame strip and the three MP4 routes too.
- **Previews:** `sashimi.video.preview(input, output, config, *, max_workers, scratch_dir,
  on_progress)` joins `samples` clips of `sample_duration` seconds, spread across the video
  between `start_offset_percent` and `end_offset_percent`, into a GIF or animated WebP scaled to
  fit `max_height` (and `max_width`), optionally centre-cropped to `crop` ("9:16" or "1:1").
  Defaults, as ftool's: 5 × 2 s from the middle 80%, 360 px tall, 15 fps. Clips never overlap, so
  short videos get fewer; a video shorter than one clip becomes one clip; under half a second is
  `MediaTooShortError`. One ffmpeg run, ffmpeg only (no moviepy or numpy), with the pure
  `build_preview_command` and `plan_preview`. Config: `VIDEO_PREVIEW` (`GifPreviewConfig`,
  `WebpPreviewConfig`, `PreviewConfig`, `PreviewCrop`), the clips' total limited by
  `Limits.max_gif_seconds` and their number by `Limits.max_preview_clips`. Batch: `make_previews` and
  `make_previews_generator`. CLI: `sashimi preview`.
- **Favicon packs:** `sashimi.image.favicon_pack(input, output_zip, config, *, scratch_dir,
  limits, on_progress)` writes a .zip with `favicon.ico`, PNGs at 16, 32, 48, 180 (Apple
  touch icon, filled with `background_color` because iOS shows transparency as black), 192 and
  512 px (`FAVICON_PNG_SIZES`), `site.webmanifest` and `favicon.html` with the `<head>` tags.
  Paths inside are relative; the manifest is written with `json` and the HTML escaped, so names
  can't break either. The zip's entries, order and timestamps are fixed, so the bytes are the same
  every run. Config: `FaviconPackConfig` (`ico_sizes`, `fit`, `resample`, `background_color`,
  `name`, `short_name`, `theme_color`, `manifest_background_color`). Also `favicon_manifest` and
  `favicon_html`. Batch: `make_favicon_packs(_generator)`, writing `<name>.favicons.zip` (`logo.png.favicons.zip`). CLI:
  `sashimi favicon`.
- **`IcoConfig.background_color`:** fills transparent areas and the padding of `.ico` files; unset
  (the default) keeps them transparent, byte for byte as before. CLI: `convert … ico --background`.
- **Remuxing:** `sashimi remux` and `sashimi.api.remux_files(_generator)` copy every stream of
  each video into MKV (or MP4, WebM, MOV) without re-encoding, writing `<name>.mkv` (`clip.mp4.mkv`). It's a
  `video.trim` with a `CopyTrimConfig` and no end, so the container check runs before anything is
  written, and outputs are atomic. Sources are never changed or removed (ftool's `--cleanup` is
  not ported). Also `video.subtitle_conversions` and `build_trim_command(…, subtitles=…)`.
- **Cuts to the end and by length:** `sashimi extract clip.mp4 10m` cuts from 10 minutes to the
  end, and `-d/--duration` gives a length instead of an end time. `extract` also takes a
  directory, `-r`, `--output-format` (the container) and `--output-dir`.
- **`-o/--output` and `--output-dir`** on every batch command, and `output`/`output_dir` on every
  batch function. Outputs under `output_dir` keep the subfolders of a searched directory; files
  already in `output_dir` aren't picked up as inputs.
- **Output name patterns:** every batch function but `flatten` takes `output_name`, a pattern of
  text and the fields `{input_name}` (`clip.mp4`), `{input_stem}` (`clip`), `{input_ext}`
  (`mp4`), `{role}` (`sheet`) and `{output_ext}` (`jpg`), with `{{`/`}}` for literal braces;
  `convert_files`, `resize_images`, `edit_images` and `make_thumbs` take
  `output_ext` to spell the format's extension. A pattern is checked when the function is
  called (`InvalidOptionsError` for an unknown field, with a hint for the names an unreleased
  build used, a format spec, unbalanced braces, `/`, `\`, `<>:"|?*` or control characters,
  `{role}` for a conversion, or a literal extension of another format when every output has one
  format), and each planned name before anything runs: it must end in an extension of the
  output's format (`{input_name}.jpeg` for a JPEG sheet, not `{input_name}.png`), be valid on
  Windows and POSIX, and be at most 255 bytes in UTF-8 (the limit on Linux and macOS, and
  stricter than Windows'); a name that fails is that file's `error` result, in a dry run too. `NameTemplate(text, origin)` carries where a pattern was set, for messages.
  `sashimi.api` exports `NameTemplate`, `default_output_name(command)`, `Batch`, `BatchPlan` and
  `PlannedFile`, as well as `build_report`, `YieldType`, `plan_flatten` and `FlattenPlan`.
- **`sashimi.api.rotate_files(_generator)(paths, rotate=90, flip="none", *, output_format=None,
  image_config=None, video_config=None, …)`** rotates and flips images (through
  `sashimi.image.edit`) and videos (through `sashimi.video.transform`), routed by kind; the
  plan's routes are `edit` and `transform`. `transform_videos(_generator)` remain, for videos only.
- **On the command line:** `--output-name PATTERN` on every command that writes files (but
  `flatten`), and `output_name` in each command's section of `sashimi.toml` (`[convert]`,
  `[remux]`, `[resize]`, `[rotate]`, `[extract]`, `[edit]`, `[frames]`, `[strip]`, `[preview]`,
  `[favicon]`, and `[thumbs]` and `[animated_thumbs]` for their own sheets): `sashimi config set
  thumbs.output_name '{input_name}.jpeg'` names sheets `video.mp4.jpeg`, so sheets made that way
  before count as existing, and `{input_stem}.{role}.{output_ext}` gives short names
  (`photo.resized.jpg`). A pattern given with `--output-name` is for that run only; `sashimi
  config set` keeps one, and `--output-name`'s help says so with the command's own example. Each
  command reads only its own section; a broken pattern there is refused (exit 2) with the file
  and section named, and doesn't stop other commands. Flags win over the files; `-o` with `--output-name` is refused. `sashimi config
  defaults <command>` starts with an "Output names" table (`--json`: a section with `"model":
  null`) giving the pattern, an example and the `sashimi.toml` value, `thumbs --help` shows a
  pattern from `sashimi.toml`, and `sashimi config defaults` covers `remux` and `strip`.
- **`-y/--yes`** on every processing command and on `flatten`.
- The JSON report (and every batch function's report) gains `kinds`, `routes`,
  `outputs_left_out` (`[{"file", "made_from"}]`) and `clash_note`.
- **A hint when `--output-dir` makes files from different folders clash.** A named file goes
  straight into the output folder, and so do the files of each named folder, so `sashimi resize
  photos/* -w 800 --output-dir out` (a shell glob names `photos/x.jpg` and the folder
  `photos/sub`, which holds another `x.jpg`) skips both `x.jpg`s. The results now end with one
  line saying why and, when searching the folder that holds them all would take exactly the same
  files, the command that keeps each folder's layout: "2 files from different folders were
  skipped because they'd meet in out. To keep each folder's layout there, name the folder that
  holds them, with -r: sashimi resize photos -r -w 800 --output-dir out". Otherwise (`sashimi
  resize a b` in a folder that holds more than `a` and `b`) it says to give each folder its own
  `--output-dir`, and that naming the folder holding both wouldn't process the same files. It
  works with `-n` and with a kept `output_dir`, and isn't shown for clashes a pattern made on its
  own or for names that differ only in case. A clash with both causes (`a/x.jpg`, `a/x.png` and
  `b/x.jpg` under `{input_stem}.{output_ext}`) ends with both lines: the pattern's line now
  counts every clash in which the pattern gave different names one output, where before it
  counted only clashes in which every name differed, so such a clash had no closing line at
  all. The suggested command is printed on a line of its own and never wrapped, so it can be
  copied whole. The batch API has it as `BatchPlan.folder_clash` (a
  `FolderClash`: the files, the output folder, the folder that holds them all, and `same_files`),
  and the report as `folder_clash` (`{"files", "output_dir", "holder", "same_files"}` or `null`).
- `sashimi flatten --dry-run` (`-n`) and `--json`.
- **Shell completion:** `sashimi --install-completion` sets it up for the shell you run it from
  (bash, zsh, fish or PowerShell), and `--show-completion` prints the script instead.
- `sashimi config defaults` covers `favicon` and `preview`.
- `scripts/sandbox_check.sh` runs the preview, favicon pack, ICO background and remux too, and
  takes its image tag from `SASHIMI_SANDBOX_IMAGE`.
- `MP3_SAMPLE_RATES` and `AAC_SAMPLE_RATES` in `sashimi.config`, next to `OPUS_SAMPLE_RATES`.
- Tests that pin what FileSushi relies on (`tests/core/test_filesushi_contract.py`): `ConfigFamily.key`,
  `model_for` ignoring non-discriminating options, an `annotated_types.Le` in `model_fields` for
  every number with an upper bound, the `KEYFRAME_NOTE` text, and the `UserInputError(user_message,
  detail=…)` constructor of every user error. A real-ffmpeg test encodes every accepted MP3, AAC and
  Opus sample rate.
- Every config field has a description, available as `model_fields[name].description` and in
  `model_json_schema()` (the docstrings under each field, via pydantic's
  `use_attribute_docstrings`).
- `GridValue` (`cols`, `rows`) carries its lower bound of 1 in the type, so it appears in the schema.
- `sashimi config defaults <command> [format]` lists each option's flag, default, accepted values
  and description, per output format (`--json` for scripts); for `thumbs` it also shows values
  set in `sashimi.toml`.
- The CLI warns about `sashimi.toml` keys it doesn't use (0.2-era names such as `static_width`,
  `jpeg_quality`, `animated_width`, `output_format`, `output_fps`, `output_width` or
  `start_offset`, typos, unknown sections), naming the file and the `sashimi config unset`
  command that removes them. Such keys were silently ignored before.
- `sashimi thumbs --help` shows the `sashimi.toml` values that replace built-in defaults, e.g.
  `[default: 85, animated 75; sashimi.toml: 80]`.

- **Folders in name patterns:** a pattern may start with folders, made inside each input's own
  folder: `sashimi config set thumbs.output_name 'sheets/{input_name}.jpeg'` writes
  `New folder/sheets/Bluebeard-5.mkv.jpeg` for `New folder/Bluebeard-5.mkv`, from any folder,
  and with `-r` each folder gets its own `sheets/`. Reruns find the outputs there ("Exists"),
  and a run never processes them. `\` separates folders as `/` does, on every system. Patterns
  that would leave the input's folder are refused (absolute paths, drive letters, `~`, `.` and
  `..` folders, empty folder names), pointing at `output_dir`. With `output_dir`, the pattern's
  folders go below it.
- **A kept output folder per command:** `sashimi config set <command>.output_dir <folder>` (and
  `animated_thumbs.output_dir`), shown by `config show`, `config defaults <command>` and the
  command's `--help`, removed by `config unset`. It means what `--output-dir` means: an absolute
  path as given, a relative one inside the folder sashimi runs from (`~` is the home folder).
  `--output-dir` replaces it for one run, and the new **`--no-output-dir`** writes next to the
  inputs. A value that isn't a folder stops only its own command (exit 2).
- **`resize`, `convert` and `rotate` take several paths,** as `edit`, `thumbs` and the others
  do: `sashimi resize *.jpg -w 800`. `convert` takes the format last, the way `cp` takes its
  destination (`sashimi convert a.png b.png webp`), and `rotate` the angle, which stays optional
  (`sashimi rotate *.jpg 180`, `sashimi rotate *.mp4` for 90). `convert . webp` and `rotate
  x.mp4` work as before. The last argument of `convert` is always the format; when a file or
  folder here has the format's name, a note says how to name it (`./webp`). A last `0`, `90`,
  `180` or `270` given to `rotate` that is also a file or folder here is refused, since both
  readings make sense; the new `convert --output-format` and `rotate --angle` give the format or
  angle as an option, so every argument is a path. Other mistakes say what to write: "End with
  the format to convert to, e.g. sashimi convert a.png webp", "45 isn't a file here, nor an angle
  rotate takes (0, 90, 180 or 270)".
- `thumbs` and `preview` take `-w` for `--width`, as `resize` and `convert` do.
- A new settings file starts with a comment saying what it is and which file wins.
- `BatchPlan.in_output_dir` (and the report's `in_output_dir`) lists the files a search found
  inside `output_dir` and left out; the CLI says so under the results. `BatchPlan.accepts` and
  `BatchPlan.recursive` let a caller explain an empty run: "Found no files thumbs works on
  (videos); -r searches subfolders too."

### Changed

- **GIFs are made in two passes, so their memory no longer grows with their length.** A GIF's
  palette is built from all its frames, and the one-pass graph (`split`, `palettegen`,
  `paletteuse`) held every decoded frame until the end: 2.6 GB for 10 s of 1080p at 30 fps. GIFs
  made from a video (`palette_mode` `diff` or `full`), GIF previews and animated GIF sheets now
  build the palette in a first pass and encode with it in a second; the palette passes between
  them in memory, as a `data:` input, so no palette file is ever written. Outputs are
  byte-identical to before. A 60 s, 1920 px, 30 fps GIF of a 1080p video peaks under 200 MB
  (about 100 s on two CPUs); GIFs at 480 px take about the same time as before, and a default GIF
  preview takes about 60% longer (its clips are decoded twice). `palette_mode="single"` (a palette
  per frame) never held the frames and stays one pass. Progress shows the first pass as the first
  third, "analysing".
- **GIFs made from a video and GIF previews no longer count against `max_frame_bytes`**, which
  now guards only an animated sheet's clips. 0.5.1 refused GIF previews over 400 MiB of frames;
  they now succeed. `--max-frame-bytes` is now only on `thumbs` (it was also on `convert` and
  `preview`).
- Every batch report (and the CLI's `--json`) has a top-level `dry_run` (false unless it was a
  dry run), after `command`; it was only in `flatten`'s.
- `convert` says in its message when an MP4 was copied: "Converted to clip.mp4 (video and audio
  copied, not re-encoded)" or "(video copied, audio re-encoded)".
- The image models share per-format save fields with the edit models (internal; every field,
  default, description and order is unchanged).
- **Quieter CLI output:** one file gets one line ("Success: clip.mp4: Preview created:
  clip.mp4.preview.webp (1.2s)") instead of a panel and a table; several files get a heading, the
  progress bar, the summary and the table as before. Status lines have no emoji.
- All logging goes to stderr, so `--json` output on stdout stays clean without the CLI checking
  its own arguments; warnings now also show in `--json` runs, on stderr.
- `--verbose` shows no `<int>` placeholder in the help.
- `flatten` asks for confirmation on stderr, saying how many files will move and be renamed.
- The help panel "Contact sheets" is "Sheets & previews".
- `IcoConfig.fit` says "a square" rather than "a transparent square", since the padding takes
  `background_color` when one is set.
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
  `.venvs/`, leaving `.venv` and `uv.lock` alone. It also lints `scripts/`. By default it runs the
  tests on Python 3.14 only and prints just the failures and the summary; `sh scripts/check.sh
  full` runs them on every supported version (3.11-3.14), verbosely. Any other argument is
  refused.
- The CLI no longer writes `~/.sashimi/sashimi.toml` on first use. Only `sashimi config set`
  creates a settings file, holding just the values you set. The old full template pinned
  that version's defaults, so later changes to built-in defaults never reached you. Existing
  files still work; delete one to go back to the built-in defaults.
- `sashimi config show` lists only what your files set; built-in defaults are in
  `sashimi config defaults`.

- **Messages say where an output is:** an output beside its input is named by its file name, any
  other by its path as built from the paths you gave ("Output already exists:
  sheets/Bluebeard-5.mkv.jpeg", "Would write sheets/S01/ep1.mkv.sheet.jpg", "Contact sheet
  created: …"). The "left out" line under the results shows paths too.
- **`output`/`-o` must end in an extension of the output's format:** `convert photo.jpg webp -o
  out.png` used to write WebP data into `out.png`; it's now that file's error ("out.png must end
  in .webp, since the output is WEBP."), as a pattern's literal extension already was.
- `-o` with `--output-dir` is refused (exit 2), as `-o` with `--output-name` is; `--output-dir`
  used to be ignored silently.
- `rotate` says what it did: "Rotated to …", "Flipped to …" or "Rotated and flipped to …"
  instead of "Saved …".
- Help reads whole at 80 columns: options with long lists of values show a short placeholder
  (`FORMAT`, `MODE`, `PRESET`, …) and list the values in their help, and number ranges are in the
  help instead of the type column, so no option name, default or pattern is cut off with "…".
  Command descriptions wrap to the terminal instead of breaking mid-sentence (`sashimi config
  --help`, `sashimi convert --help`). `sashimi --help` describes `rotate` as rotating photos,
  GIFs, WebPs and videos (it said videos), `favicon` as working on images, `extract` as lossless
  or exact with `--accurate`, and `thumbs` sheets as still or animated.
- `config defaults <command>`'s first table is "Output names and folder", with an `output_dir`
  row ("next to each input", or your setting).
- `config defaults` reads whole at 80 columns: when its columns don't fit the terminal without
  cutting a value, each option is shown as a block (name, default, accepted values, description),
  so default patterns such as `{input_name}.{role}.{output_ext}` are never cut off with "…".
- `convert --help` and `remux --help` say that `convert clip.mkv mp4` and `remux clip.mkv
  --output-format mp4` both write `clip.mkv.mp4`, and how they differ (the first video and audio
  track, re-encoded when they don't fit, against every stream copied).
- `info` and `metadata` given a folder say so ("videos is a folder; info shows one file at a
  time.") instead of "This file doesn't look like media we can read."

### Fixed

- **A start time after the last frame, or after the sound ends, is refused on every route,**
  and nothing is written. A start inside a file's probed length but after its last video frame
  (4.97 s into a 5 s, 30 fps video, whose last frame starts at 4.967 s, or anywhere the sound
  runs on past the picture) gave an MP4, WebM or accurate cut that "succeeded" with no picture
  (a 0.05 s audio-only MP4), a lossless cut holding only the frames before the start (the whole
  video, for one with a single keyframe), and a GIF or animated WebP that failed with a
  `ProcessingError` ("Error writing trailer"). Each now raises `InvalidOptionsError`: "The start
  time (4.97s) is after the video's last frame, so there's nothing to convert." (`video.convert`)
  or "… nothing to cut." (`video.trim`, `extract`). Audio from a start after the sound ends (a
  video whose picture runs on past its sound) "succeeded" with an empty MP3, M4A, OGG, WAV or
  FLAC; it now raises `InvalidOptionsError`: "The start time (4s) is after the end of the audio,
  so there's nothing to convert." A video target still works where only the sound has ended
  (an MP4 without audio), and audio where only the picture has. Re-encodes check their own
  output (one `ffprobe` read up to its first packet) and look at the source only when it is
  empty; lossless cuts check the source first, reading packets without decoding. Starts before
  the last frame are unaffected, variable frame rates and stream copies included.
- **A GIF or animated WebP starting inside the video's last frame** (from 4.96 s of that video)
  failed with a `ProcessingError`: ffmpeg keeps the frame, but at 12 fps it leaves none. It now
  raises `InvalidOptionsError`: "The start time (4.96s) is too close to the end of the video: it
  leaves less than one frame at 12 fps, so there's nothing to convert."
- **A start time ffmpeg can't decode from** (in an MPEG-TS with B-frames, a seek can land where
  no frame can be decoded until the end, though frames follow) made an empty MP4 or WebM, or
  failed. It now raises `InvalidOptionsError`: "No frame of the video could be read from the
  start time (5s), so there's nothing to convert. Try an earlier start."
- **"The start time (4s) is past the end of the video (4s)."** The refusal of a start at or
  after the probed end rounded the length to whole seconds, so a 3.52 s file read as 4 s. It now
  shows the length to the millisecond: "The start time (4s) is at or past the end of the video
  (3.523s)." (video and audio).
- **`keep_exif=False` didn't remove everything.** Before it was replaced by `metadata`, it left
  the source's XMP, IPTC, Photoshop and description tags in TIFF to TIFF conversions without
  `auto_orient` (Pillow copies a TIFF's tags when nothing transforms the picture), and with
  compression every other tag of the source (camera, software, artist, GPS pointer); a JPEG's,
  GIF's or PNG's comment in JPEG and GIF outputs; and the source's colour profile in ICO files and
  favicon packs. Outputs now carry only what `metadata` and `keep_icc` say, whatever Pillow's
  writers would copy, and byte-level tests pin every carrier for every function.
- **Mono Opus above 256 kb/s failed** in ffmpeg (Opus allows 256 kb/s per channel): OGG audio
  conversions and the Opus audio of WebM conversions, WebM rotations and accurate WebM cuts now
  lower the bitrate to 256 kb/s per output channel (`OPUS_MAX_KBPS_PER_CHANNEL`), and log it, so
  a mono source at 320 gives the file 256 gives. Stereo is unchanged up to 512. MP3 (LAME) and
  AAC accept any allowed bitrate for mono and clamp it themselves, so they were never affected.
- **`frames_info` decoded PNGs** (Pillow's `getexif` loads a PNG to look for an `eXIf` chunk after
  the image data), although it promised to decode nothing. It now reads the chunks itself, so no
  format is decoded.
- **Lossless cuts and remuxes of MOVs with chapters failed:** the QuickTime chapter track made
  MKV and MP4 refuse the file ("can't be trimmed … without re-encoding. Try MOV instead.") and MOV
  fail in the muxer. The chapter track is no longer copied as a stream; the chapters are written
  from the chapter list, as for every other container.
- **TIFF outputs with EXIF directories failed** with compression (`RuntimeError: Error setting
  from dictionary` from Pillow's libtiff writer, with `keep_exif=True` and the default LZW): the
  EXIF and GPS directories are now added after Pillow writes the file.
- `sashimi.api.convert.__all__` no longer lists `Target` and `TARGET_SUFFIX`, which aren't public.
- The test suite's `audio_blob` is the 4 s it says (it was 0.026 s), and the sandbox's audio
  fixture is seeded noise, so its outputs' sizes repeat.

- A file named on the command line that was skipped no longer exits 0 (see Breaking).
- **`keep_icc=False` strips the colour profile from PNG and TIFF outputs too.** Pillow's PNG and
  TIFF writers copied the source's profile when sashimi passed none, so only JPEG and WebP
  outputs lost it. The same fallback put a CMYK source's profile on RGB PNGs; a CMYK profile is
  now kept only on CMYK output (TIFF). Pixels are unchanged.
- `WebpImageConfig.optimize` did nothing; it is removed (see Breaking).
- Frame selection at a very slow speed no longer crashes on frames longer than the format
  stores (655.35 s for GIF, about 4.7 hours for WebP); they stop at that length.
- `max_pixels` above Pillow's own limit (about 179 MP) is honoured; Pillow refused such images
  whatever `max_pixels` said.

- **`flatten` is safe** (it was ftool's code, with ftool's bugs):
  - symbolic links are left where they are, and so are the files and folders a link in the tree
    points to, so no link ends up dangling (relative links used to be moved and broken);
  - every destination is planned before anything moves, plain names before renamed ones, so
    names don't depend on the order files are found in (`c/a_x.txt` could take `a_x.txt` and push
    `a/x.txt` to `a_x_1.txt` depending on the walk);
  - names clash case-insensitively, so a plan works the same on every filesystem;
  - files are moved by hard-linking then unlinking, which fails rather than replace a file that
    appeared after planning, so there's no check-then-rename race (where hard links aren't
    supported, Windows renames without replacing, and elsewhere it checks just before renaming);
  - it has tests.
- `convert photo.png jpeg -f` no longer silently replaces an unrelated `photo.jpg`: the result
  says it was replaced, and without `-f` it's never touched.
- The batch API's error classification maps `OSError` (for example a full disk while creating an
  output folder) to an `error` result with its reason.
- CLI help shows option defaults again. Rich read `[default: …]` in help text as a markup tag and
  removed it, leaving lines like "Sheet width in pixels ." Help defaults are now pinned to the
  config models by `tests/cli/test_help_defaults.py`, and the options that lacked one
  (`--subsampling`, `--start`, `--duration`, `--height`, the thumbs colours and fonts) have one.
- `resize --help` no longer mentions animated WebP or video widths.
- A contact sheet whose ffmpeg can't be run raises `BinaryNotFoundError`, instead of
  `UnsupportedMediaError` ("We couldn't read any frames from this video.").
- **An `--output-dir` that holds the searched folder left every file out:** `sashimi thumbs . -r
  --output-dir .` (or `..`) said "No matching files found", because every file was inside the
  output folder. Files are now left out only when the output folder is inside the search.
- A file named on the command line that sits inside `--output-dir` is processed, like any file you
  name; it used to be left out.
- **`sashimi config set` replaced a `sashimi.toml` that wasn't valid TOML** with a file holding
  only the new setting, losing everything else in it; `config unset` said the setting "wasn't
  set there". Both now refuse, naming the file and the parse error, and leave it as it is.
- **Rerunning a command on a shell glob processed its own outputs** (`sashimi edit *.jpg
  --rotate 90` twice wrote `a.jpg.edited.jpg.edited.jpg`); see the naming policy under Breaking.
- `config defaults convert` and `remux` suggested `{input_stem}.{role}.{output_ext}` for short
  names, which conversions refuse (they have no `{role}`); they now suggest
  `{input_stem}.{output_ext}`, and don't list `{role}` among their fields.
- **`flatten` removed empty subfolders without asking** when there was nothing to move; it now
  asks whenever it would change anything, and its help says that folders that were empty already
  are removed too.
- **Long names passed the dry run, then failed:** outputs were staged as `.{name}.{random}.staged`
  and written through `.{that}.{random}.partial`, so a name over about 212 bytes (a 230-character
  name, or 75 Chinese characters) was planned as "Would write" and then failed with "File name too
  long". Staged and temporary files now have short names of their own (`.sashimi-{random}.staged`,
  `.sashimi-{random}.partial`), so every name the plan accepts is written, and the limit is
  counted in UTF-8 bytes, as Linux and macOS count it: a name over 255 bytes is refused in the
  plan, naming the limit. `-o` names are checked the same way.
- **Where an output went under `--output-dir` depended on what else was named:** with
  `photos/x.jpg` and `photos/sub/x.jpg`, `sashimi resize photos -r --output-dir out` wrote
  `out/x.jpg…` and `out/sub/x.jpg…`, but `sashimi resize photos photos/* -r --output-dir out`
  (a shell glob names the subfolder too) planned both as `out/x.jpg…` and skipped both as a
  clash. Each file now keeps its folders below the outermost folder given that holds it.
- **`strip` ignored the `[limits]` in `sashimi.toml`**, refusing images over the default 64 MP
  whatever was set; it now uses them, and has `--max-pixels` like the other image commands.
- **`rotate`, `extract` and `thumbs` lost bracketed text in errors** (`--output-name '[draft]
  {input_name}.jpeg'` was shown as ` {input_name}.jpeg`): every CLI error is now printed as plain
  text, so no message can lose text to Rich markup.
- **`sashimi config set resize.output_dir 2024` was refused**, as were `2024-10-21` and `true`,
  read as a number, a date and a yes/no value. Folder, pattern, font and emoji settings now keep
  the text as typed (quotes around it are removed).
- **`sashimi config defaults rotate png` said rotate has no PNG output,** and `config defaults
  rotate` showed only the video options. It now shows the image options rotate takes
  (`--quality`, `--metadata`) for each image format. `config defaults remux mkv` now shows the
  `--metadata` option for each container instead of refusing.
- **A dot in a pattern's folder was read as the extension:** `--output-name
  '{input_stem}.d/sheet'` for a JPEG was refused as ending in `.d/sheet`; the extension is now
  taken from the file name only, so the message says that `sheet` must end in `.jpeg` or `.jpg`.
- Planning a large search with `--output-dir` inside it is faster (4,000 files: 2.0 s to 0.3 s).

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
