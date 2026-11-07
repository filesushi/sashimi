# Changelog

sashimi follows [Semantic Versioning](https://semver.org/). Before 1.0, minor versions may break
the Python API; every break is listed here.

## 0.8.0 — 2026-10-31

A full metadata read: every field, by where it was found, with what isn't counted.

### Breaking

- `MetadataInfo.date_taken` is only a capture date: EXIF `DateTimeOriginal` (not `DateTime`),
  XMP `exif:DateTimeOriginal`/`photoshop:DateCreated` (not `xmp:CreateDate`), IPTC creation date,
  QuickTime `com.apple.quicktime.creationdate` or a recorded date; never `creation_time`.
- `MetadataInfo.creation_time` is the container's `creation_time` (else `date`); it no longer
  prefers `com.apple.quicktime.creationdate`.
- `MetadataInfo.carriers` leaves out XMP that only says how to show a gain map or motion photo
  (now in `not_counted`).
- `sashimi.core.video.hidden_tail` and `hidden_lead` return None when they can't read the packets
  (were 0).
- Dolby Vision IPT and ProRes RAW raise `UndecodableVideoError` (still an `UnsupportedMediaError`).
- `MIGRATION.md` is gone.
- A run over several files lists only what needs attention under its summary (`-v` lists every
  file), and "Exists (Skipped)" reads "Exists".

### Added

- `MetadataInfo.sections` (`MetadataSection`, `MetadataField`): every field read, by carrier,
  bounded (200 fields a section, 300 characters a value, 100 sections, 1 MiB of JSON a read), with
  `tags_omitted` and `not_counted_omitted` beside `sections_omitted`.
- `MetadataInfo.looked_for` and `not_counted` (`UncountedDetail`: orientation, colour profile,
  resolution, writing application, encoder, handler name, language, file type, display rotation).
- Highlights: `lens_make`, `lens_model`, `exposure_time`, `f_number`, `iso`, `focal_length`,
  `focal_length_35mm`, `image_direction`, `image_direction_ref`, `date_taken_offset`,
  `date_taken_subseconds`, `color_profile`, `thumbnail_size`, `maker_note`.
- `errors.UndecodableVideoError` (`codec`, `codec_tag`) for video FFmpeg can't decode, such as
  Blackmagic RAW, or whose codec the FFmpeg in use can't decode (`probe()` asks it once,
  `-codecs`); `MediaInfo.video_decodable` and `video_codec_tag`.
- `Fidelity.kept`: what a removal left because showing the file needs it.
- `errors.NothingToDoError` (an `UnsupportedMediaError`): a BMP to strip, a still image for `frames`.
- `errors.MissingReaderError` (an `UnsupportedMediaError`, with `install` and `advice`): a HEIC without the `heif` extra, an AVIF on a Pillow without AVIF.
- `sashimi metadata --all`.
- `.mpo` is read as an image, in folders and by name.
- `-v` also after the command.

### Fixed

- Video whose codec FFmpeg can't decode was reported as "may be damaged" or an FFmpeg error.
- `strip` of a QuickTime file without `ftyp` (Blackmagic RAW) wrote MP4 and failed; it writes MOV.
- `strip` said EXIF was removed when its orientation stayed.
- Hostile or damaged metadata can only raise a `SashimiError`, and a check that fails after writing
  keeps the output and says it couldn't check.
- A PNG with a large text chunk or colour profile no longer reads as "not an image": text is read up
  to 8 MiB a chunk, 64 MiB a file, and past that as not read.
- A damaged file reads as damaged, whatever its decoder raises.
- A missing codec, a changed Pillow or a bug in sashimi reads as an internal error and exit 1, never as a damaged file.
- `strip` keeps every picture after a JPEG's main one that MPF or a container directory lists (depth maps, a stereo pair's other eye) without its metadata, and updates what locates it; a picture nothing lists goes.
- `read` counts metadata in pictures after a JPEG's main one, and says when false starts or too many pictures stopped it looking.
- Re-encoded images drop EXIF, Photoshop and XMP thumbnails under every mode; the result lists `embedded thumbnail` as lost.
- A HEIC or AVIF nothing installed can read names what it needs, on every image command, `read` and `info`, and `strip` no longer writes it as an MP4.
- A file's tag names and values take a bounded part of an FFmpeg command: when its location tags don't fit, `remove_location` removes every tag at that level and lists `tags` as lost; too many attachment names for the system are refused before FFmpeg runs.
- A command too long for the system is a `ProcessingError`, never reported as a bad FFmpeg.
- A packet scan where ffprobe exits with an error no longer gives a short length: a late-starting file keeps its stated length, one that states none has no length, and the failure is logged.
- A picture MPF or a directory lists past the end of the file is no longer reported as dropped.
- `strip` of a video claims nothing when its check of the end fails.
- `strip` keeps the container directory of a photo with depth or labelled items, without the labels, and names only what really went.
- A GIF frame that overflows its canvas is clipped as browsers clip it, on every target.
- A palette PNG with several transparent entries converts to GIF.
- A run of identical frames held for over 65 s converts to APNG on any Pillow, saying its timing changed if it must.
- `strip` of an image Pillow can't open reads as damaged, not as FFmpeg's error.
- A PNG's text and colour profile chunks share one budget for what a read inflates, and only the first profile counts; blank text costs nothing.
- A conversion no longer says it lost a location when it only couldn't look for one.
- Every XMP scan takes time in proportion to the packet; past 4 MiB a packet counts as holding a location.
- `Fidelity` text from a file is clean, on one line and capped.
- Display-only XMP allows no free text in namespaces, languages, toolkit or xpacket lines.
- Whatever a progress callback raises passes through unchanged.
- A line break in a file name or tag can't start a new line in the CLI.
- A two-page file's lost page gets a row in a folder run; so does a stereo pair's other picture, named as such.
- A folder of clips too short for the full grid ends with one line saying so.
- The CLI passes no control or text-reordering characters to the terminal; `--json` writes them as
  `\uXXXX`.
- `video.convert` copies video this FFmpeg can't decode into MP4; only routes that decode refuse it.
- A cut from mid-file no longer warns about frames hidden at the source's start.
- A failed read of a copy's first or last packets silently dropped the note about hidden frames.

## 0.7.0 — 2026-10-30

Unfinalized recordings, frame-accurate seeking on every container, HDR tone mapping, a fidelity
report on every result, lossless rotation, animations kept through conversions, AVIF and APNG
output, and per-task FFmpeg checks.

### Breaking

- `MediaInfo.duration` of a Matroska/WebM file whose timestamps start late is measured from its
  last packets (it was the stated end time). Files starting at zero are unchanged.
- `probe()`, sheets, previews and start times can raise `MediaTooLargeError`
  (`limit="max_scan_packets"`).
- `image.convert` keeps animations in GIF, WebP, AVIF, APNG and PNG (`animated=False` for the
  first frame), applies `max_animation_bytes`, and returns an `ImageConversion`; so does
  `image.edit`, which now keeps animations in PNG too.
- Every processing function returns its `fidelity`: `video.convert` a `VideoConversion` (was the
  route string), `metadata.strip` a `StripResult` (was the extension), `video.trim` a
  `TrimResult`/`CopyPlan` (was None), `video.transform` a `TransformResult`, and the rest an
  `Outcome` (was None). Batch messages, `api.FileResult.fidelity` and the `--json` report carry it.
- `video.transform` with a rotation alone into MP4 or MOV copies the streams and sets the display
  rotation (`reencode=True` for the old result).
- `rotate` writes MOV for MOV sources, unless `output_format` is given or the source is Dolby
  Vision with sound MP4 holds.
- HDR (PQ, HLG) is tone-mapped in every output that can't hold it, so those outputs change;
  without zscale this raises `MissingFiltersError`. An MP4 of HDR H.264 is re-encoded.
- MP4/MOV/M4A outputs of sources with QuickTime keys use QuickTime metadata keys; MP4, MOV,
  Matroska and WebM outputs record the source's creation time (except under `remove_all`).
- Copies leave out data tracks the container can't hold (or FFmpeg can't write: `mebx`, `fdsc`)
  and name them, instead of refusing; `check_copy_compatible` no longer raises for them.
- Command builders take `decode_from=`/`origin=` (`index=` for previews); their default output
  is unchanged.
- The "FFmpeg older than 8.1" warning and `MIN_FFMPEG`/`OLD_FFMPEG` are gone (see Added).
- A missing or wrong FFmpeg raises `FFmpegPathError` (a `BinaryNotFoundError`).
- Dolby Vision IPT (profiles 5, 20) and ProRes RAW raise `UnsupportedMediaError` before decoding;
  WebP over 16,383 px raises `MediaTooLargeError`.
- `frames_info`, `metadata.read`, `metadata.strip` and `strip_format` no longer apply
  `max_pixels`; `strip --max-pixels` is gone.
- `strip` keeps Ultra HDR gain maps in every mode, and a motion photo's video where it can.
- `frames_info(...).edit_target` is `avif` for AVIF and `apng` for animated PNG.
- A GIF frame stored as shorter than 20 ms counts as 100 ms, as browsers play it.
- Sheets of short clips use a smaller grid instead of repeating tiles; animated sheets never
  overlap clips; sheets and previews under a second are made (`MIN_PREVIEW_SECONDS` is 0).
- Length-limit and copy refusals are worded differently.
- Folder searches and the ffmpeg input allowlist take more formats (see Added).
- New fields: `MediaInfo.start_time`, `color_space`, `color_transfer`, `color_primaries`,
  `display_turn`, `display_mirrored`, `dolby_vision`; `StreamInfo.start_time`.

### Added

- Recordings that state no length are measured from their packets (`Limits.max_scan_packets`,
  2,000,000 by default); `probe(path, limits=)`.
- Inputs: AIFF, AC-3, E-AC-3, DTS, AMR, CAF, Sun AU, WavPack, WMA, `.mka`, `.m4b`, `.m2ts`/`.mts`,
  `.vob`, `.3g2`, `.mk3d`, Ogg Theora, DivX, F4V, Y4M; images: AVIF, ICO, TGA, PSD, APNG, `.jfif`.
- AVIF and APNG output (`--avif-speed`); `frames --output-format png`.
- `keep_hdr` / `--keep-hdr` for MP4 (10-bit HEVC) and WebM (10-bit VP9).
- `sashimi.fidelity` (`Fidelity`, `Outcome`).
- Lossless rotation: `video.plan_rotation`, `build_rotation_command`, `rotation_container`,
  `MovTransformConfig`, `--reencode/--lossless`.
- `video.plan_copy`.
- `strip` takes HEIC and AVIF (in place), 3GP, MPEG-TS, M2TS, VOB, MPG, FLV, WMV and AVI.
- Per-task FFmpeg checks: `UnsuitableFFmpegError` (`MissingEncodersError`, `MissingFiltersError`,
  `FFmpegTooOldError`), `sashimi.ffmpeg_info()`, `sashimi info --ffmpeg`. Tested with 8.1 and 9.0.
- ffmpeg and ffprobe are chosen as a pair: `set_ffmpeg_paths()`, `SASHIMI_FFMPEG`, `ffmpeg.path`.
- `errors.FFmpegPathError`; on Windows, a path without `.exe` means the `.exe`.
- `edit --animated/--no-animated`; `convert`/`resize --no-animated`, `--max-animation-bytes`.
- Limit refusals on the command line say how to raise the limit.
- `sashimi info` shows display size for non-square pixels and a GIF's played length.

### Changed

- AVIF defaults: quality 75, speed 9 (were 85, 6).
- Re-encodes that decode from a chosen keyframe prime the audio decoder, so the first samples
  differ slightly, and accurate cuts end exactly at their length.

### Fixed

- Seeks in MPEG-TS/PS and Ogg showed the next keyframe; open-GOP MP4/MOV lost frames before
  keyframes; AVI/FLV skipped to the second keyframe near the start.
- Sheets and previews of sparse-keyframe video decoded once per tile; they now decode in one pass.
- Tiles and clips inside a held frame (variable frame rate) showed a later frame or were dropped.
- Tiles past the end of the picture were empty.
- Animations became stills, reported as success.
- 16-bit and float greyscale images came out nearly white.
- Non-square-pixel video made squashed GIFs and animated WebPs.
- Lossless copies showed frames an edit list hides at the start.
- `metadata=keep` dropped QuickTime keys; `remove_location` dropped non-location keys.
- Conversions dropped an image's DPI.
- Tracks a container can't hold were "removed as asked" under `remove_location`.
- Rotating a MOV re-encoded PCM sound to AAC in an MP4.
- `convert hdr.mp4 mp4` and broken `.mp4` files said "Already MP4".
- `convert anim.gif png` dropped the animation.
- Animated WebP to PNG/APNG/GIF said "timing changed".
- Animated sheet notes disagreed with what was drawn.
- Motion photos and Ultra HDR kept XMP pointing at removed data.
- Dolby Vision profiles 5 and 20 failed with raw ffmpeg errors; ProRes RAW came out colourless.
- Surround layouts failed to encode to Opus.
- Remux to MP4 of codecs MP4 can't hold failed with raw ffmpeg errors.
- GoPro, iPhone and ProRes files couldn't be cut or remuxed because of their data tracks.
- MPEG-PS copies failed with "unknown timestamp".
- Lossless cuts left without a video frame failed.
- A missing or misconfigured FFmpeg gave misleading messages.
- The CLI hard-wrapped long paths and commands, cut names with "…", and coloured by guesswork.
- `sashimi convert a.mka mp3` was refused.
- Favicon packs of panoramas padded to a huge square first.
- Copies of HEVC with Dolby Vision into MP4 lost `dvcC`.

## 0.6.0 — 2026-10-23

First release on PyPI, as `sashimi-convert` (includes the unpublished 0.5.1). Adds previews,
remuxing, image editing, frame selection, favicon packs, metadata reading and stripping, and a
stream-copying MP4 route; settles output naming, limits and the CLI.

### Breaking

- `metadata` (`keep`, `remove_location`, `remove_all`) replaces `keep_exif` on every output that
  can carry metadata, and image outputs now keep metadata by default (`--metadata` in the CLI).
- Limits are the caller's: `sashimi.config.Limits`, passed as `limits=` (`max_pixels=` is gone).
  The `MAX_*` limit constants are gone; lengths have no default limit.
- Config fields keep only format and codec bounds; animated `fps` goes up to 50; numbers refuse
  infinity and NaN. `ParamsModel.bound_messages` is gone.
- `WebpImageConfig.optimize` is removed.
- MP3 and M4A `sample_rate` accept only the encoders' rates.
- Refusal messages state the limit in force.
- Still JPEG and PNG sheets may exceed 16,383 px (`WEBP_MAX_SIDE`, `JPEG_MAX_SIDE`,
  `GIF_MAX_SIDE` replace `MAX_SHEET_HEIGHT`).
- ICO files and favicon packs are converted to sRGB and carry no profile or metadata.
- New `MediaInfo`/`StreamInfo` fields; builders take `source=`.
- `video.convert` to MP4 copies streams when it can and returns the `VideoRoute`.
- Lossless trims convert text subtitles the container can't hold instead of refusing.
- A missing or broken ffprobe raises `BinaryNotFoundError`, not `UnsupportedMediaError`.
- Output naming: every name depends only on its own input. Defaults are
  `{input_name}.{role}.{output_ext}` (conversions `{input_name}.{output_ext}`); `suffix` and
  `--suffix` are gone. Clashing inputs are skipped; a run never processes its own outputs;
  `force` replaces whatever is in the way.
- Batch generators return a `Batch` with `plan()`; results are 4-tuples; every batch function
  returns the `--json` report (`avg_time_seconds`, was `avg_processing_time_seconds`).
- `api.extract` is a batch operation; `api.flatten` returns the report; both raise
  `InvalidOptionsError` where they returned errors or raised `FileNotFoundError`/`ValueError`.
- CLI: `-o` is always the output file, `--output-format` the format, `--clip-length` replaces
  `thumbs --duration`, `-h` is help and `-H` height; a skipped file you named exits 1.
- `rotate` takes images too; mixed folders ask first (`-y`; `-f` implies it).
- `sashimi info` on an image shows the image's own details.
- Animated sheets default to 3x3 (4x2 portrait); 9:16 still sheets use 8x2.
- `config set`/`unset` write your own `~/.sashimi/sashimi.toml` (`-p` for the project file;
  `-g` is gone); old 0.2 key names and `[global]` are no longer read.
- `frames` with nothing to change is refused.

### Added

- `sashimi.metadata`: `read` and `strip` (no re-encoding); `sashimi strip`, `sashimi metadata`.
- `image.edit` (rotate, flip, crop, CSS-matching brightness, contrast, saturation), for stills and
  animations; `sashimi edit`.
- `image.select_frames`, `frames_info`, `frame_strip`; `sashimi frames`.
- `video.preview`; `sashimi preview`.
- `image.favicon_pack`; `sashimi favicon`; `IcoConfig.background_color`.
- `sashimi remux`; `extract` to the end or by `--duration`.
- Output name patterns (`--output-name`, `output_name` in `sashimi.toml`), folders in patterns,
  `--output-dir` and a kept `output_dir` per command, `-o` everywhere.
- `--dry-run` and `-y` on every processing command; `resize`, `convert` and `rotate` take
  several paths.
- `--max-*` flags and a `[limits]` section in `sashimi.toml`; `sashimi config defaults`.
- Two-pass palette builders: `build_palette_command`, `build_preview_palette_command`, `two_pass`.
- Shell completion; field descriptions in every config model's schema.

### Changed

- GIFs are made in two passes, so memory no longer grows with length (byte-identical output).
- `max_frame_bytes` guards only animated sheets.
- Quieter CLI output; all logging on stderr; problems listed first.
- The CLI no longer writes a settings file on first use.
- PEP 639 licence metadata; copyright "The FileSushi Authors".

### Fixed

- Start times after the last frame or the end of the sound "succeeded" with empty output.
- `keep_exif=False` and `keep_icc=False` left metadata and profiles in some outputs.
- Mono Opus above 256 kb/s failed.
- MOVs with chapters couldn't be cut or remuxed; compressed TIFFs with EXIF failed.
- `flatten` broke symlinks, depended on order and could race.
- Reruns on a shell glob processed their own outputs.
- `config set` replaced an invalid `sashimi.toml`.
- Long names passed the dry run, then failed.
- CLI help lost its defaults and bracketed text.
- `--output-dir` placement depended on what else was named.

## 0.5.0 — 2026-10-12

Configs a web app can import on their own: one model per operation and output format, in
`sashimi.config`, which needs only pydantic.

### Added

- `sashimi.config`: per-target models (`JpegImageConfig`, `Mp4VideoConfig`, `CopyTrimConfig`,
  `JpegSheetConfig`, …) and `ConfigFamily` entry points (`IMAGE_CONVERT`, `VIDEO_TRIM`,
  `CONTACT_SHEET`, …).
- Video to animated WebP; frame-accurate trims (`extract --accurate`).
- `sheets.render`; `video.check_copy_compatible`; `MediaInfo.streams`.

### Changed

- Configs check limits that don't depend on the file; re-encodes stop at the probed length.
- Copy trims check the container first; sheets refuse tiles under 16 px.
- Validation messages are sentences; choice fields accept form strings.

### Removed (breaking)

- The old config classes and their re-exports; fields a target ignores; `None` as "default" for
  most fields; the `effective_*` properties.
- Font and emoji config fields (now `sheets.render` arguments).
- `sheets.contact_sheet`, `sheets.animated_sheet`, `image.resize`, `api.make_animated_thumbs`.

## 0.4.0 — 2026-10-09

A three-layer library (core, batch API, CLI), distributed as `sashimi-convert`. Not published.

### Added

- `sashimi.video`, `.audio`, `.image`, `.sheets`, `sashimi.probe` and `sashimi.errors`, with pure
  command builders and `from_params` configs.
- `on_progress`, bounded `max_workers`, `set_ffmpeg_paths`.
- Extras: `[cli]`, `[emoji]`, `[heif]`.
- The batch API and the `convert`, `resize`, `rotate`, `info` and `config` commands.

### Changed

- No moviepy or MediaInfo; emoji off by default and never downloaded.
- Bounded threads, local files only, allowlisted demuxers, bit-exact output.
- The library never reads config files or touches `HOME`.
- Images are never upscaled; quality is 1–100; EXIF orientation is applied by default.
- Core dependencies are `pydantic` and `pillow`.

### Removed (breaking)

- `sashimi.file`, the old `sashimi.config`, and the old config and thumbs helpers.

### Fixed

- Animated sheets exceeded container pid limits.
- Outputs were created with mode 0600.

## 0.2.0

Initial structure: contact sheets, image conversion, `extract`, `flatten`, TOML settings and the CLI.
