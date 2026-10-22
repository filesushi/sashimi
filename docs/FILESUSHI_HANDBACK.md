# Hand-back: sashimi 0.6.0 for FileSushi

From the sashimi agent to FileSushi, written 2026-10-19 and updated three times on 2026-10-20 for
sashimi 0.6.0, its first PyPI release (distribution `sashimi-convert`). The second update added
image editing, frame selection and the faster "to MP4" (§1 items 9-11, §13); the third made every
limit the caller's to set and fixed two bugs (§1 items 14-17, §14); the fourth replaced
`keep_exif` with one `metadata` setting on every output, added reading and removing metadata, and
fixed the last known bugs (§1 items 18-23, §15). The release isn't published yet: the owner builds
and uploads it by hand. Until then, a wheel built from sashimi's tree at 0.6.0 is the same code.

**Start with §1 items 14 and 18.** sashimi no longer enforces FileSushi's limits (10-minute
videos, 30-second GIFs, …) unless FileSushi passes them, and `tool_params.py` fails to import until
it stops importing the old limit constants. And images now **keep** the source's metadata by
default, GPS included, unless FileSushi passes `metadata`.

FileSushi vendors a 0.6.0 wheel built from sashimi `9bc8329`, before image editing
(`docker/workers/wheels/sashimi_convert-0.6.0-py3-none-any.whl`, sha256 `1c1703d9…`, checked
2026-10-20). The current 0.6.0 adds image editing, frame selection, the self-routing "to MP4" and
the caller's limits. Compared with the 0.5.1 FileSushi ran before, every output FileSushi makes is
byte-identical, except contact sheets (§1 item 4 and §8): animated sheets get a new default grid
and new time labels, still sheets of 9:16 videos get 8x2, and an animated sheet of a clip shorter
than its clip length now succeeds with one tile; "to MP4" of 8-bit H.264 videos, which is now a
stream copy (§1 item 9); PNG and TIFF conversions with `keep_icc` off, which now drop the
colour profile (§1 item 16); image outputs of sources with metadata, which now keep it unless
`metadata` says otherwise (§1 item 18; the pixels are identical); and icons and favicon packs of
images with a colour profile, now converted to sRGB (§1 item 21).

## 1. What FileSushi must change

1. **Depend on the PyPI release.** Once 0.6.0 is on PyPI, replace the vendored wheel:
   - `pyproject.toml`: drop the `[tool.uv.sources]` entry for `sashimi-convert` (it points at
     `docker/workers/wheels/sashimi_convert-0.6.0-py3-none-any.whl`) and require
     `sashimi-convert==0.6.0`, then re-lock;
   - `docker/workers/sashimi.Dockerfile`: install from the index, wheels only, with hashes, for
     example `pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.txt`
     with `sashimi-convert==0.6.0` and its dependencies pinned (FileSushi installs it without
     `[heif]`), instead of copying the wheel;
   - delete `docker/workers/wheels/sashimi_convert-0.6.0-py3-none-any.whl`.
   Before the upload, a wheel built from the sashimi tree is the same code, if you want to start early.
2. **Add the ICO background to `IcoParams` (required: a FileSushi test fails without it).**
   `IcoConfig` has a new field, `background_color` (`RGB | None`, default `None` = transparent,
   accepts `"#rrggbb"`). `tests/test_tool_params.py::test_every_sashimi_option_is_offered_or_deliberately_left_out`
   fails for `image-to-ico` until `IcoParams` offers it (an optional colour, blank meaning
   transparent; "Background" with help such as "Fills transparent areas; leave blank to keep
   them") or lists it in `not_offered`. With it unset, `.ico` output is byte-identical to 0.5.1.
3. **Don't offer 96 kHz for MP3.** `Mp3AudioConfig.sample_rate` now takes only LAME's rates
   (`sashimi.config.MP3_SAMPLE_RATES`: 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100,
   48000). `tool_params.py` gave every audio tool except OGG the same choices, including "96 kHz";
   with 0.6.0, MP3 at 96000 is refused by `from_params` with `InvalidOptionsError` ("Invalid value
   for sample_rate: input should be 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100 or
   48000."), where 0.5.1 accepted it and the worker then failed with `ProcessingError`. M4A, WAV
   and FLAC still take 96000. FileSushi's working tree (checked 2026-10-19) already gives
   `Mp3Params` its own choices without 96 kHz (`tool_params.py:1042-1047,1099`); keep that. If you
   build the choices from sashimi's constants instead, note that `MP3_SAMPLE_RATES` is also the
   name of the local `Choices` tuple there. M4A takes AAC's rates only (`AAC_SAMPLE_RATES`, the
   MP3 rates plus 64000, 88200, 96000); FileSushi's 22050, 44100, 48000 and 96000 are all accepted.
4. **Animated contact sheets look different (accepted by the owner, 2026-10-19).** With
   `cols`/`rows` unset or `"auto"`, which is what `SheetParams` sends when the user leaves
   "Columns"/"Rows" on "Auto", an animated sheet is now 3x3 (4x2 for portrait videos, aspect
   ratio under 0.95) instead of the still sheets' table (6x5 for 16:9). It renders about 2.4
   times faster with 426 px tiles at the default 1280 px. Time labels on animated sheets have
   4 px of padding and centred text. Still sheets of 9:16 videos are 8x2 instead of 7x2. Nothing
   to change in code; the "Auto" placeholder is still right. If the form explains the automatic
   grid, say "3x3 for animated sheets". FileSushi's tests use explicit grids (`TINY_ANIMATED`) and
   pin no hashes, so they're unaffected.
5. **Short clips:** an animated sheet of a video shorter than its clip length (2 s by default)
   but at least 1 s long now succeeds with one tile of the whole video, where it raised
   `MediaTooShortError` ("This video is too short for an animated contact sheet."). Under 1 s
   still raises it.
6. **Lossless trims with subtitles now succeed.** `video.trim` with a `CopyTrimConfig` converts
   text subtitles the container can't hold (MP4 `mov_text` to SRT for MKV; SRT, ASS or WebVTT to
   `mov_text` for MP4 and MOV; to WebVTT for WebM) instead of raising `InvalidOptionsError` ("This
   video can't be trimmed to MKV without re-encoding. Try MP4 instead."). No FileSushi code or test
   depends on the old error (checked 2026-10-20). `TrimParams` needs nothing new; "MP4 → MKV" with
   no end is now a lossless remux that keeps every stream, if you want to offer one.
7. **New tool: preview GIF / WebP (optional, when you want it).** `sashimi.video.preview(src, dst,
   VIDEO_PREVIEW.from_params(params, target=target))`, `target` `gif` or `webp`. It joins
   `samples` clips (default 5) of `sample_duration` seconds (default 2) from across the
   video, skipping `start_offset_percent`/`end_offset_percent` (10/10), scaled to `max_height` (360)
   and optionally `max_width`, at `fps` (15), optionally centre-cropped with `crop` `"9:16"` or
   `"1:1"`; WebP adds `quality` (75). With FileSushi's limits (item 14) the clips' total is at most
   30 s (`InvalidOptionsError` "Previews can be at most 30 seconds long. Use fewer clips or shorter
   ones.") and their number at most 10. A params class
   with `upstream = VIDEO_PREVIEW` covers it; models in §4. Sandbox numbers are in §10; at most two
   ffmpeg processes run, whatever the clip count.
8. **New tool: favicon pack (optional; the owner's "Full favicon pack (.zip)" next to "ICO
   file").** `sashimi.image.favicon_pack(src, dst, FaviconPackConfig.from_params(params),
   limits=LIMITS)` writes a .zip (`application/zip`; FileSushi's output name would be
   `…favicons.zip` or similar) with `favicon.ico` (16, 32, 48 by default, `ico_sizes`), PNGs at 16,
   32, 48, 180, 192 and 512 px, `site.webmanifest` and `favicon.html`. The 180 px Apple touch icon
   is filled with `background_color` (default white). Optional `name`, `short_name` (single line;
   sashimi sets no length limit, and FileSushi's form keeps its own 100), `theme_color` and
   `manifest_background_color` go into the manifest
   (and `theme_color` into the HTML); they're written with `json` and escaped HTML, so user text
   can't break either file. Same input and config, same bytes. Paths inside are relative. A params
   class with `upstream = FaviconPackConfig` covers it; model in §4.
9. **"To MP4" is faster, for free (nothing to change).** `sashimi.video.convert` with an MP4
   config now picks its route itself (`sashimi.video.plan_mp4_route`, pure; owner, 2026-10-20:
   "If 'to MP4' happens to, in the backend, be three possible routes, it's fine for that to be
   config-driven as far as the user knows"):
   - **`copy`**: 8-bit 4:2:0 H.264 (`pix_fmt` `yuv420p` or `yuvj420p`) with even dimensions, and
     AAC audio (or none, or `mute`): both streams are copied. A 103 s 720p MKV took 0.4 s in the
     sandbox instead of about 20 s;
   - **`copy_video`**: the same video with other audio (Opus, MP3, …), or `audio_bitrate_kbps`
     other than 128: the video is copied and only the audio is re-encoded to AAC (0.5 s for 20 s);
   - **`encode`**: everything else, byte for byte as before. HEVC, VP9, AV1, 10-bit and 4:4:4
     always re-encode, so "Make an MP4 to watch it here" still gets an MP4 that plays in every
     browser. So do a start time, a length shorter than the rest of the video, a `max_width`
     below the video's width, and a `crf` or `preset` other than the defaults (23, `veryfast`).
   The route isn't an option and doesn't appear in any model. `Mp4Params` keeps working: its
   `crf` is blank by default and its preset and audio-quality selects start on the model defaults
   (`tool_params.py:892-913`, checked 2026-10-20), so a form left alone copies. A user who sets a
   quality or a preset gets a re-encode. The copy routes keep the re-encode's streams (first video
   and audio only, no subtitles or data), its `-t` limit and the refusal of videos longer than
   `limits.max_video_seconds` (`MediaTooLongError`; FileSushi passes 600, item 14), `+faststart`, and the source's display rotation (as MP4 metadata, which
   browsers apply). If ffmpeg can't copy a file, `convert` re-encodes it instead. `convert`
   returns the route (`"copy"`, `"copy_video"`, `"encode"`); the worker can log it
   (`sashimi_worker.py` ignores the return value today, which is fine). `MediaInfo.pixel_format`
   is new. To give users "make it smaller", offer `crf` or `max_width`, which re-encode.
10. **New tool: "Edit image" (crop, rotate, flip, adjust).** In the worker:
   `sashimi.image.edit(src, dst, IMAGE_EDIT.from_params(params, target=target), limits=LIMITS)`,
   with `target` the source's format: `sashimi.image.frames_info(src).edit_target` gives it from
   the content (`jpeg` for JPEG, MPO and HEIC; `png`, `webp`, `gif`, `bmp`, `tiff`). The output's
   extension is that format's. Fields (models in §4, `JpegEditConfig` … `TiffEditConfig`):
   - `rotate` 0/90/180/270 clockwise and `flip` none/horizontal/vertical, applied after the EXIF
     orientation, which is always applied first (browsers show photos already oriented);
   - the crop box `crop_left`, `crop_top` (default 0) and `crop_width`, `crop_height` (default
     blank = to the right/bottom edge), **in pixels of the rotated and flipped picture**, i.e.
     what the user sees after the rotate and flip. Outside the picture: `InvalidOptionsError`
     "The crop area must fit inside the picture, which is 400x300 pixels.";
   - `brightness`, `contrast`, `saturation`: multipliers of at least 0, default 1. sashimi sets no
     upper bound any more (CSS has none), so give the sliders your own 0-2;
   - the format's save fields, from the image models: JPEG `quality` (default **92**,
     `EDIT_QUALITY`), `optimize`, `progressive`, `jpeg_subsampling`; WebP `quality` (92),
     `lossless`, `webp_method`; PNG `png_compress_level`, `optimize`; GIF `optimize`; TIFF
     `tiff_compression`, `quality`; and `metadata`/`keep_icc` for JPEG, PNG, WebP and TIFF (`metadata` for GIF too). The
     owner's rule ("forms with 8 settings or fewer show everything") suggests leaving the save
     fields out of the form (`not_offered`) or under "More options".
   **The live preview:** draw the picture as the browser shows it (EXIF applied, the default
   `image-orientation: from-image`), apply the rotation and flip, crop, then set
   `filter: brightness(b) contrast(c) saturate(s)` **in that order**. sashimi implements those
   three as the Filter Effects spec defines them (sRGB, colours not premultiplied, clamped after
   each function), not Pillow's `ImageEnhance`, so the output matches: brightness and contrast
   exactly, saturation within one 8-bit level, all three together within two. For crop presets,
   `sashimi.config.CROP_ASPECTS` is ("free", "1:1", "4:3", "16:9", "9:16"), and
   `sashimi.image.aspect_crop(width, height, "16:9")` gives the largest centred box (so the web
   app and the desktop app agree on the starting box). A no-op edit still writes a valid image.
   **Animated GIF and WebP** are edited frame by frame by the same call: every frame gets the
   same rotate, flip, crop and adjustment, and keeps its duration; the loop count is kept; GIF
   colours are kept exactly and transparency survives. Animations whose decoded frames (width ×
   height × 4 × frames) exceed `limits.max_animation_bytes` (FileSushi passes 256 MiB, item 14;
   sashimi's default is now 1 GiB) raise `MediaTooLargeError` ("This animation is too large to
   edit: its frames would take 300 MB of memory, and the limit is 256 MB. Try one with fewer frames
   or smaller pictures.") before decoding; §10 has the time and memory at that limit. Animated PNGs raise
   `UnsupportedMediaError` ("Animated PNGs can't be edited yet."). SEO pages (`/crop-image`,
   `/rotate-image`, `/flip-image`, `/image-editor`) can all post to this one tool; the page only
   decides which control the editor opens on.
11. **New tool: GIF frame editor (keep or drop frames, speed).** In the worker:
   `sashimi.image.select_frames(src, dst, IMAGE_FRAMES.from_params(params, target=target), limits=LIMITS)`
   with `target` `gif` or `webp` (the source's; `frames_info(src).edit_target`). Fields
   (`GifFramesConfig`, `WebpFramesConfig`, §4): `keep`, the frames to keep **numbered from 0**,
   as a list or a string such as `"0-9,12"` (blank keeps all; duplicates are dropped and frames
   play in their original order, since reordering comes later), and `speed`, above 0 (default 1;
   sashimi sets no upper bound any more, so give the form your own 0.25-4;
   each frame's duration is divided by it; frames under 20 ms, which browsers play at 100 ms,
   count as 100 ms, none gets shorter than `MIN_FRAME_MS`, 20 ms, and none longer than the
   format holds; GIFs store durations in 10 ms steps, so in a GIF each new duration is rounded to
   the nearest step, and a page that previews the total length should round each frame the same
   way); WebP adds `quality` (92),
   `lossless`, `webp_method`, `metadata`, `keep_icc`; GIF adds `optimize` and `metadata`. Errors: a frame the
   animation doesn't have, `InvalidOptionsError` "Some of the frames to keep aren't in this
   animation, which has 120 frames."; an image that isn't animated, `UnsupportedMediaError`
   "This image isn't animated."; over the budget, as item 10. Every kept frame is the full
   picture as shown at that point, so dropping frames never leaves holes. With every frame kept
   and speed 1 the animation is unchanged.
   **To show the frames:** `sashimi.image.frames_info(src)` reads the frame count, each frame's
   duration (`durations_ms`), the loop count and the size from the file's blocks, without
   decoding (2-40 ms for 300 frames). It reads the upload, so like `metadata.read` (item 20) it
   belongs **in the worker, never the web app**: run it as part of the job that opens the
   editor, and hand its fields to the page.
   `sashimi.image.frame_strip(src, dst, FrameStripConfig(...))` draws every frame as a tile in
   one still WebP (default tiles 96 px tall, 10 per row, quality 80) and returns the layout:
   frame `i` is at `x = (i % columns) * tile_width`, `y = (i // columns) * tile_height`, so the
   page can show frame `i` as a CSS sprite (`background-position: -x -y`). A 120-frame
   480x270 GIF took 1.4 s and 52 MB in the sandbox and gave a 175 KB strip. The strip is a second
   output of the upload (one file, one request), reachable only by the uploading session like any
   other output. I chose a strip from sashimi over decoding in the browser because browsers have
   no portable way to get the frames of an animated WebP (WebCodecs' `ImageDecoder` isn't
   everywhere), and the desktop app needs the same frames.
12. **Renamed fields: none.** `sashimi.config` renamed nothing. The CLI renamed flags (`thumbs
   --duration` is `--clip-length`, `-o` is the output path), which FileSushi doesn't use. The
   `IcoConfig.fit` description now says "a square" rather than "a transparent square", and the
   animated sheets' `cols`, `rows` and `sample_duration` descriptions mention the new defaults; if
   your forms show model descriptions, they change with them.
13. **Nothing else.** Behaviour FileSushi gets for free:
   - a missing or broken ffprobe or ffmpeg in the worker image raises `BinaryNotFoundError`
     (a `ProcessingError`), so the worker logs "Conversion failed" and writes no `error.txt`.
     0.5.1 raised `UnsupportedMediaError`, telling the user "This file doesn't look like … we can
     read." about a deployment fault;
   - `sashimi.__version__`, which the worker logs, is `0.6.0`;
   - the package metadata names "The FileSushi Authors <admin@filesushi.com>", licence
     `Apache-2.0` (PEP 639), and `github.com/filesushi/sashimi`;
   - the batch API (`sashimi.api`) and the CLI changed a lot (one naming policy, 4-tuple results,
     one JSON report with `dry_run`, `--dry-run` everywhere, exit 1 for a named file that was
     skipped, `edit` and `frames`, `--max-*` flags and a `[limits]` settings section), but
     FileSushi uses neither.
   - output names in the batch API and the CLI (2026-10-21: a name depends only on its own
     input, sheets are `clip.mp4.sheet.jpg`, conversions `photo.png.webp`, `--output-name`
     patterns, clashes refused) changed nothing FileSushi uses: it calls core functions with
     explicit output paths, and core's code and `sashimi.config` are unchanged (only the
     `select_frames` docstring gained the GIF rounding note in item 11).
   - the second naming round (2026-10-21: `{input_name}.{role}.{output_ext}` names, `rotate` for
     images, `--output-format`, the mixed-folder question, `-y`, `-f` replacing anything) is also
     batch API and CLI only, and changes nothing FileSushi uses: core, `sashimi.config` and every
     output's bytes are unchanged.
   - the output-folder round (2026-10-21: name patterns may start with folders inside each
     input's folder, `sashimi.toml` keeps an `output_dir` per command, messages show where an
     output is, `output` must end in an extension of its format, and `BatchPlan.in_output_dir`,
     `accepts` and `recursive` plus the report's `in_output_dir` are new) is batch API and CLI
     only too: core, `sashimi.config` and every output's bytes are unchanged.
   - the settings and several-paths round (2026-10-21: `config set` writes the user's own
     settings by default, `resize`/`convert`/`rotate` take several paths, files named together
     leave out their own earlier outputs, `BatchPlan.already_done` and the report's
     `already_done`) is CLI and batch API only: core, `sashimi.config` and every output's bytes
     are unchanged.
   - the clash hint (2026-10-22: `BatchPlan.folder_clash`, the report's `folder_clash`, and one
     line under the CLI's results when `--output-dir` gathers files of one name from different
     folders) is CLI and batch API only: core, `sashimi.config` and every output's bytes are
     unchanged.
14. **Limits are now FileSushi's to set (required; owner, 2026-10-20: "all limits should be config
   driven … The sashimi defaults don't need to be aligned with filesushi at all, sashimi just needs
   to be configurable").** sashimi enforces only the limits its caller passes, in one
   `sashimi.config.Limits` object (pydantic only, so the web app can import it). Its defaults
   protect the machine and leave lengths unlimited, so **without changes FileSushi would convert
   two-hour videos, 10-minute GIFs and 40-clip previews**, and `tool_params.py` would fail to import.
   The model is in §4 (`Limits`); §14 explains the design.

   **a. The limits to pass, so that FileSushi accepts exactly what it accepted before:**

   | `Limits` field | FileSushi's value | What it limits | sashimi's default | Replaces |
   |---|---|---|---|---|
   | `max_pixels` | `24_000_000` | decoded pixels of an image (convert, resize, ICO, favicon pack, edit, frames, `frames_info`, frame strip) | 64,000,000 | `IMAGE_MAX_PIXELS`, passed as `max_pixels=` |
   | `max_video_seconds` | `600` | a WebM/MP4 conversion (re-encode **and** MP4 copy), rotate/flip, accurate trim: a longer clip is `InvalidOptionsError`, a longer whole video `MediaTooLongError` | none | `MAX_VIDEO_SECONDS` |
   | `max_audio_seconds` | `3600` | audio conversion and extraction (clip or whole file) | none | `MAX_AUDIO_SECONDS` |
   | `max_gif_seconds` | `30` | a GIF or animated WebP from a video, and a preview's clips together | none | `MAX_GIF_SECONDS` |
   | `max_preview_clips` | `10` | a preview's `samples` | none | `MAX_PREVIEW_SAMPLES` |
   | `max_frame_bytes` | `400 * 1024 * 1024` | decoded frames held at once (width × height × 3 × frames): animated sheets and GIF previews, **and now GIFs made from a video** | 1 GiB | `MAX_FRAME_BYTES` |
   | `max_animation_bytes` | `256 * 1024 * 1024` | decoded frames of an animated GIF/WebP being edited (width × height × 4 × frames): edit, frames, frame strip | 1 GiB | `MAX_ANIMATION_BYTES` |
   | `max_sheet_pixels` | `64_000_000` | a contact sheet's pixels | 64,000,000 | `MAX_SHEET_PIXELS` (pass it anyway, so it doesn't follow sashimi's default) |

   As code: `LIMITS = Limits(max_pixels=24_000_000, max_video_seconds=600, max_audio_seconds=3600,
   max_gif_seconds=30, max_preview_clips=10, max_frame_bytes=400 * 1024 * 1024,
   max_animation_bytes=256 * 1024 * 1024, max_sheet_pixels=64_000_000)`.

   **GIFs made from a video are the one exception.** They had no frame budget before (only the
   30 s cap), but ffmpeg holds every frame of a GIF until it has built the palette (measured: 10 s
   → 162 MB, 40 s → 365 MB of RSS at 480 px and 12 fps), so 0.6.0 checks them against
   `max_frame_bytes` too. To keep accepting every GIF the form allows (30 s at 30 fps and 800 px,
   the widest choice, holds about 1.3 GB of frames), pass
   `LIMITS.model_copy(update={"max_frame_bytes": None})` for `video-to-gif` jobs. With `LIMITS`
   itself, GIFs over 400 MiB of frames (e.g. 30 s at 30 fps and 640 px: 593 MB) are refused with
   `InvalidOptionsError` "These settings would make the GIF too large to render: its frames would
   take 593 MB of memory, and the limit is 400 MB. Use a smaller width, a lower frame rate or a
   shorter length." instead of risking the worker's memory limit. Which to use is the owner's
   call (inbox).

   **b. The worker** (`docker/workers/scripts/sashimi_worker.py`, checked 2026-10-20): `max_pixels=`
   is gone from every function, so the three calls that pass it (`:118`, `:143`, `:150`) raise
   `TypeError` until they pass `limits=LIMITS` instead. Pass `limits=LIMITS` (or the GIF variant
   above) to **every** sashimi call: `image.convert`, `image.to_ico`, `image.favicon_pack`,
   `video.convert`, `video.transform`, `video.trim`, `audio.convert`, `sheets.render`,
   `video.preview`, and the editor's `image.frames_info`, `image.edit`, `image.select_frames` and
   `image.frame_strip`. `IMAGE_MAX_PIXELS` (`:78`) becomes `LIMITS.max_pixels`. The worker image
   has no `filesushi` package, so the worker defines `LIMITS` itself; keep the web app's copy in
   step with a test (the FileSushi tests already load the worker by path).

   **c. The web app** (`src/filesushi/models/tool_params.py`, checked 2026-10-20):
   - the imports `MAX_AUDIO_SECONDS`, `MAX_GIF_SECONDS`, `MAX_PREVIEW_SAMPLES` and
     `MAX_VIDEO_SECONDS` (`:20-56`) no longer exist, so the module fails to import. Define them
     in FileSushi (or read `LIMITS.max_gif_seconds` …); they set the form bounds and hint text at
     `:922`, `:975`, `:993`, `:1006`, `:1014`, `:1102`, `:1176`, `:1230`, `:1238`, `:1539`,
     `:1543` and `:1629`. Keep the values (30, 600, 3600, 10) and the help reads as before.
     `DEFAULT_GIF_SECONDS`, `KEYFRAME_NOTE` and `OPUS_SAMPLE_RATES` are unchanged;
   - `_upper(AnimatedGifSheetConfig, "sample_duration")` (`:1484`) and
     `_upper(GifPreviewConfig, "sample_duration")` (`:1549`) raise `StopIteration` at import: those
     fields have no upper bound any more. Use your own 10 (the old bound) in both;
   - `_upper(GifPreviewConfig, "fps")` (`:1579`, `:1583`) is now 50 (`MAX_ANIMATION_FPS`, a
     browser limit, not a policy), which would offer 1-50 fps. Use your own 30 to keep the form;
   - `_lower(GifPreviewConfig, "max_height")` (`:1559`, 64) and every `_upper(..., "crf")` (51, 63)
     are unchanged;
   - **queue-time checks:** `ToolParams.upstream_parameters` (`:139-157`) validates with
     `from_params`, which no longer refuses a preview over 30 s in all or an accurate trim over 10
     minutes (`tests/test_tool_params.py:388` and `:416` expect those messages). Keep the config
     that `from_params` returns and call `LIMITS.check(config)` on it: it raises the same
     `InvalidOptionsError`s with the same messages ("Previews can be at most 30 seconds long. Use
     fewer clips or shorter ones.", "Video clips can be at most 10 minutes long.", and the GIF,
     clip and audio length ones), before the file is read. Every other form field already has its
     own bound (widths, fps, grids, gaps, font sizes, bitrates, sample rates; checked 2026-10-20).

   **d. What FileSushi users see differently** with the limits above: only the wording of some
   refusals, which now state the limit in force:
   - "This image is too large to process." → "This image is too large to process: it has 30
     megapixels (6000x5000), and the limit is 24.";
   - "This animation is too large to edit. Try …" → "This animation is too large to edit: its
     frames would take 300 MB of memory, and the limit is 256 MB. Try one with fewer frames or
     smaller pictures.";
   - the preview, animated-sheet and still-sheet size refusals gain the same kind of clause (§7);
   - every limit's refusal carries `.limit`, the `Limits` field's name (`"max_video_seconds"`);
     FileSushi can ignore it.

   **e. One widening FileSushi can't switch off:** still JPEG and PNG contact sheets may be taller
   than 16,383 px (JPEG up to 65,500, PNG without a format limit), within `max_sheet_pixels`; only
   WebP keeps 16,383. At FileSushi's widest sheet (3840 px) that admits sheets from 16,384 to
   16,666 px tall that were refused before.
15. **`WebpImageConfig.optimize` is gone (required).** It never did anything: Pillow's WebP writer
   ignores `optimize`, so WebP outputs are byte-identical without it. `WebpResizeConfig` loses it
   too. In FileSushi:
   - remove `optimize` from `WebpParams` (`tool_params.py:432`):
     `test_every_sashimi_option_is_offered_or_deliberately_left_out` fails for the WebP tools
     until you do ("offered options sashimi doesn't have"). Don't add it to `not_offered`;
   - `ResizeParams.optimize` (`:737-746`) can stay (JPEG and GIF still have it, and
     `upstream_parameters` already drops it for WebP), but its help "JPEG, WebP and GIF. On for
     JPEG and WebP unless set." should read "JPEG and GIF. On for JPEG unless set.".
16. **`keep_icc` off now strips the profile from PNG and TIFF (nothing to change).** Pillow's PNG
   and TIFF writers copied the source's profile even when sashimi left it out, so
   `keep_icc=False` only worked for JPEG and WebP. It now works for every format. The same fix
   stops a CMYK source's profile landing on an RGB PNG (it is still kept on a CMYK TIFF, which
   needs it). Those outputs differ from before in their metadata only; the pixels are identical.
17. **Limits that stay fixed** (format and codec limits and correctness rules, which a form can
   rely on): `crf` 0-51 (H.264) and 0-63 (VP9), `speed` 0-8, every `quality` 0/1-100, PNG level
   0-9, `webp_method` 0-6, FLAC level 0-12, the MP3/AAC/Opus sample-rate choices and FLAC's
   1,048,575 Hz (`FLAC_MAX_SAMPLE_RATE`), MP3 bitrates up to 320 and AAC/Opus up to 512 kb/s
   (M4A and OGG were 320), `fps` up to 50 for GIF and animated WebP (`MAX_ANIMATION_FPS`; faster
   frames play at 100 ms in browsers), header and time-label text up to 200 px, offsets 0-100 %,
   icon sizes 16-256, WebP's 16,383 px side (`WEBP_MAX_SIDE`), tiles of at least 16 px
   (`MIN_TILE_SIZE`) and frames of at least 20 ms (`MIN_FRAME_MS`). Every other upper bound in
   the models is gone: GIF/clip/audio lengths, widths and heights, preview clips and their
   length, sheet grids, gaps, widths and clip lengths, the adjustments, the speed, the frame
   strip's tile height and columns, WAV and FLAC sample rates (8000-192000), and the manifest
   names' 100 characters. Lower bounds are unchanged. Infinity and NaN are now refused in every
   number field.

18. **`keep_exif` is replaced by `metadata`, which keeps everything by default (required; owner,
   2026-10-20: "In sashimi? keep, of course, but it must be *capable* of full and partial
   removal").** Every model whose output can carry the source's metadata has `metadata`:
   `"keep"` (the default), `"remove_location"` or `"remove_all"` (`MetadataMode`,
   `METADATA_MODES`; description "The source's metadata (camera, dates, GPS, titles): "keep",
   "remove_location" or "remove_all"."). The models: `JpegImageConfig`, `PngImageConfig`,
   `WebpImageConfig`, `TiffImageConfig`, **`GifImageConfig`** (comments), their resize and edit
   twins, `GifFramesConfig`, `WebpFramesConfig`, `WebmVideoConfig`, `Mp4VideoConfig`, both
   transform models, **all three trim models** (so remuxes too), and **every audio model**. Not
   on GIF or animated WebP from video, previews, sheets, ICO, favicon packs or frame strips,
   whose outputs hold no source metadata. In FileSushi (`tool_params.py`, checked 2026-10-20):
   - remove `keep_exif` from `JpegParams`, `WebpParams`, `PngParams`, `TiffParams` and
     `ResizeParams` (`:411`, `:435`, `:455`, `:501`, `:810`) and the `KeepExif` alias (`:375`);
     `from_params` refuses it ("Unknown option: keep_exif.");
   - offer `metadata` on every tool whose model has it, or list it in `not_offered`:
     `test_every_sashimi_option_is_offered_or_deliberately_left_out` fails for each of those tools
     (images, GIF, every video, trim, rotate and audio tool) until you do. The owner chose
     FileSushi's default (2026-10-20): **"Remove location metadata"**, with "Preserve all
     metadata" and "Remove all metadata" either side, shown only when the file has something to
     remove (item 20). sashimi's own default stays `keep`, so FileSushi must **pass** its default;
   - **until FileSushi passes `metadata`, image outputs keep EXIF (with GPS), XMP, IPTC and
     comments**, where they dropped EXIF before. To keep exactly today's image behaviour instead,
     pass `metadata="remove_all"`. Video and audio kept their tags before and still do with
     `keep`; `remove_location` and `remove_all` are new for them;
   - the worker needs nothing new: the field travels in the params.
   What each mode does, and what each format can hold, is in item 23.
19. **The "Remove metadata" tool (new, when you want it).** In the worker:
   `ext = sashimi.metadata.strip(src, dst, mode, keep_icc=True, limits=LIMITS)` with `mode`
   `"remove_location"` or `"remove_all"` (the default). It writes the file in its own format
   **without re-encoding**: JPEG segments, PNG chunks, WebP chunks, GIF extensions and TIFF tags
   are dropped or filtered and the compressed pixels copied byte for byte; video and audio are
   stream copies. It returns the extension (`jpg`, `png`, `webp`, `gif`, `tif`, `mkv`, `webm`,
   `mp4`, `mov`, `m4a`, `mp3`, `flac`, `ogg`, `wav`); `sashimi.metadata.strip_format(src)` gives it
   before running, for naming the output. Refusals (`UnsupportedMediaError`, plain sentences):
   HEIC, BMP (which holds no metadata), BigTIFF, DNG, and other containers ("Removing metadata
   from this kind of file isn't supported."). `keep` raises `InvalidOptionsError`. An MPO keeps
   only its main picture. A params class with `mode` (two choices) and, if you want it, `keep_icc`
   covers it; there is no config model, since the only options are those two.
20. **The info box (new): `sashimi.metadata.read(path, limits=LIMITS)`** returns a
   `MetadataInfo` (pydantic, frozen): `kind` (`image`, `video`, `audio`), `carriers` (where
   metadata was found: `EXIF`, `GPS`, `XMP`, `IPTC`, `Photoshop`, `comment`, `PNG text`,
   `extended XMP`, `QuickTime/MP4 tags`, `Matroska tags`, `ID3`, `Vorbis comments`, `RIFF INFO`,
   `tags`, `stream tags`, `chapters`, `cover art`, `timed metadata track`), `has_location`,
   `latitude`, `longitude`, `altitude`, `place`, `camera_make`, `camera_model`, `date_taken`,
   `software`, `title`, `artist`, `album`, `creation_time`, `comment`, `has_color_profile` and
   `tags` (other named fields), plus the property `has_metadata` (`carriers` isn't empty). It
   decodes no pixels or samples (measured on 3.14: 1-2 ms for an image; one ffprobe run, about
   0.15 s, for video and audio), and
   it runs ffprobe, so it belongs **in the worker, never the web app**. Show the three-way
   selector when `has_metadata` is true. It works on outputs, so the page can show "before" and
   "after"; after `remove_all`, `carriers` is empty for every format sashimi writes, and after
   `remove_location`, `has_location` is false. Its docstring says it reports what it found, not
   a promise that nothing else is there, which matches FileSushi's "best effort" policy.
21. **Icons are sRGB (nothing to change).** `to_ico` and `favicon_pack` convert an image with a
   colour profile to sRGB, and no icon carries the profile or any metadata. Pillow used to copy
   the profile into the ICO's frames and the pack's PNGs (not the Apple touch icon) without
   converting the colours, so browsers, which treat icons as sRGB, showed wide-gamut sources'
   colours wrong. Icons of images without a profile are byte-identical; sRGB-tagged sources gave
   identical icons in the hash check.
22. **Mono Opus above 256 kb/s works now (nothing to change).** Opus allows 256 kb/s per channel,
   so a mono source at FileSushi's 320 kb/s choice failed in ffmpeg (OGG: `ProcessingError`, so
   "Conversion failed"; FileSushi's own sandbox fixture `ref.mkv` has mono audio). sashimi now
   lowers the bitrate to 256 per output channel (`OPUS_MAX_KBPS_PER_CHANNEL`) and logs it, for
   OGG audio and the Opus audio of WebM conversions, rotations and accurate cuts. A mono source
   at 320 gives exactly the file 256 gives (hash-checked). The descriptions of `bitrate_kbps`
   (OGG) and `audio_bitrate_kbps` (WebM, VP9) say so; if the form explains bitrates, say "up to
   256 for mono". MP3 (LAME) and AAC accept every allowed bitrate for mono (they clamp
   themselves). Nothing else to change: the 320 choice can stay.
23. **What the modes do, and the carriers sashimi handles (for FileSushi's "best effort"
   policy).**
   - `keep` carries everything the output format can hold. Images: EXIF (camera, dates, GPS;
     byte for byte in JPEG, PNG and WebP, rewritten in TIFF), XMP, IPTC and Photoshop resources
     (JPEG and TIFF), comments (JPEG, PNG, GIF), PNG text (PNG to PNG). Not carried: EXIF or XMP
     over 64 KB into JPEG, extended XMP, vendor JPEG segments (APP3-APP15 other than IPTC, MPF),
     PNG `tIME`, anything into BMP. Video and audio: the tags and chapters the container holds —
     all of them in MKV and WebM; the standard ones and the location in MP4, MOV and M4A (not
     `com.apple.quicktime.*` keys); ID3 in MP3; Vorbis comments in OGG and FLAC; RIFF INFO in WAV
     (no location). No output records a creation time (outputs are bit-exact). GIF and animated
     WebP from video, previews and sheets hold none.
   - `remove_location` removes: EXIF's GPS directory (also inside Photoshop resources); XMP
     properties named `GPS…`, `…Latitude`, `…Longitude`, `…Altitude`, `City`, `State`,
     `Country`, `CountryCode`, `CountryName`, `ProvinceState`, `ProvinceName`, `Sublocation`,
     `Location…`, `WorldRegion` or containing `geo`, in any namespace (`exif:GPSLatitude`,
     `photoshop:City`, `Iptc4xmpCore:Location`, `Iptc4xmpExt:LocationCreated`,
     `drone-dji:GpsLatitude`, …); IPTC 2:26, 2:27, 2:90, 2:92, 2:95, 2:100, 2:101 (location code
     and name, city, sublocation, province or state, country code and name); PNG text whose
     keyword contains gps, location, latitude, longitude, geo, city or country, and ImageMagick's
     raw EXIF, XMP and IPTC profiles in PNG text; JPEG extended XMP and anything after a JPEG's
     end (removal only); the video and audio tags `location`, `location-eng`,
     `com.apple.quicktime.location.ISO6709`, `.name`, `.body`, `.note`, `.role`, `.date`,
     `.accuracy.horizontal`, `com.android.location`, and any global or stream tag whose name
     contains location, gps, geo, latitude, longitude, coordinates or iso6709 (QuickTime and MP4
     `©xyz` and `loci`, Matroska `LOCATION`, ID3 `TXXX:location`, Vorbis `LOCATION`); and timed
     metadata tracks (data streams such as GoPro GPMF or Apple's `mebx`) when streams are copied
     (re-encodes never copy them). Everything else stays.
   - `remove_all` keeps only what showing or playing the file needs: the pixels and streams; the
     colour profile as `keep_icc` says; the EXIF orientation alone when sashimi doesn't apply it
     (`auto_orient` off, and `strip`); PNG display and animation chunks; JPEG JFIF and Adobe
     segments; GIF loop extensions; stream languages, dispositions and attachments' file names
     and types; and the display rotation on copies. Chapters, cover art (on copies) and timed
     metadata tracks go.
   - When sashimi applies the orientation it resets it to 1 (EXIF and XMP) and drops the EXIF
     thumbnail; edits and frame selection always do, and drop Photoshop thumbnails too.
   - Byte-level tests pin all of this: `tests/core/test_metadata_images.py` (every source format ×
     target × mode × function, with and without `auto_orient`, and the lossless removal with its
     pixels compared) and `tests/integration/test_metadata_media.py` (every video and audio route
     × mode, and the stream-copy removal with its decoded streams compared).

## 2. What sashimi now pins for FileSushi

`tests/core/test_filesushi_contract.py` (2026-10-19) pins the behaviour FileSushi relies on
beyond field names, defaults and bounds:

- `ConfigFamily.key` is public: `"target"`, or `"container"` for `VIDEO_TRIM`
  (`tool_params.py:107,127`);
- `ConfigFamily.model_for(**kwargs)` ignores keyword arguments that don't pick the model, so
  passing every form field works (`tool_params.py:107`);
- every `int`/`float` field with an upper bound has exactly one `annotated_types.Le` in
  `model_fields[name].metadata`, equal to the schema's `maximum`, and none uses an exclusive
  maximum (`_upper`, `tool_params.py:692`). Fields that are choices (`Literal`s, such as the MP3,
  M4A and OGG `sample_rate`) and `cols`/`rows` (`GridValue`, whose lower bound of 1 is inside the
  type) have no `Le` in the field's metadata; read their choices from `model_json_schema()`;
- the exact `KEYFRAME_NOTE` text;
- `MediaTooLargeError(user_message, detail=…)`, and the same constructor and `.detail` on every
  `UserInputError` subclass (worker `:104,185`).
- new in the editing round (2026-10-20): `IMAGE_EDIT` and `IMAGE_FRAMES` behave like the other
  families (`key`, `model_for`, the `Le` rule, including `FrameStripConfig`); the edit defaults
  (rotate 0, no crop, adjustments 1.0, JPEG quality 92), `MIN_FRAME_MS`,
  `CROP_ASPECTS`, the `keep` string form, the `VideoRoute` names, and no route field on
  `Mp4VideoConfig`;
- new in the ftool round: `VIDEO_PREVIEW` behaves like the other families (`key`, `model_for`, the
  `Le` rule on every bounded number); `IcoConfig.background_color` defaults to `None`;
  `FaviconPackConfig.background_color` to white; `FAVICON_PNG_SIZES`; animated sheets keep
  `"auto"` as the `cols` default;
- new in the limits round (2026-10-20, last): the limits of §1 item 14 build a `Limits`, from
  numbers or strings (`from_params`), and `Limits.unlimited()` turns every one off; `Limits`
  follows the `Le` rule; sashimi's defaults; `.limit` on refusals; the bounds FileSushi reads
  (`crf`, the preview's `max_height` lower bound and `fps`) and that `sample_duration` has none;
  and that the old `MAX_*` names are gone. `tests/core/test_config_module.py` also pins that every
  upper bound left on a config field is a format limit (`FORMAT_UPPER_BOUNDS`).

The user messages FileSushi's tests assert ("Unknown option: quality.", "This file doesn't look
like a video we can read.") were already pinned by `tests/core/test_sheet_guards.py` and
`tests/integration/test_video_audio.py`.

## 3. Install

```dockerfile
RUN pip install --no-cache-dir --only-binary=:all: 'sashimi-convert==0.6.0'
```

(Add `[heif]` for HEIC input; FileSushi installs without it.)

The web app needs only `sashimi-convert==0.6.0` (no extras); it pulls in `pydantic` and
`pillow`, but importing `sashimi.config` loads neither Pillow nor anything that runs ffmpeg.

## 4. The config module: `sashimi.config`

- Import path: `sashimi.config` (a package; everything public is re-exported from it, so import
  only from `sashimi.config`).
- Importing it loads pydantic and `sashimi.errors` only. `tests/core/test_config_module.py`
  runs `import sashimi.config` in a fresh interpreter and asserts that no `PIL*`, `subprocess`,
  `sashimi.core*` or `sashimi.cli*` module is loaded.
- The converters (`sashimi.video`, `.audio`, `.image`, `.sheets`) no longer re-export configs;
  there is one place to import them from.
- One model per operation and output format. Each holds only fields that change its output (a
  test changes every field of every ffmpeg and image model in turn and checks the command or the
  rendered image changes), and `model_fields[name].default` is the value used. `None` is a default
  only for `max_width`/`max_height` (keep the source's size), `sample_rate` (keep the source's),
  `duration_seconds` on WebM/MP4/audio (to the end) and `end_seconds`/`duration_seconds` on trims.
- Field bounds are format and codec limits only (`annotated_types.Le(le=51)` for an H.264 `crf`),
  readable from `model_fields[name].metadata`. The limits a caller chooses are `Limits` (below),
  passed to the converters as `limits=`; `Limits.check(config)` applies the ones that need no file.
- Choice fields that hold ints or bools (`rotate`, `wav_bit_depth`, the MP3, M4A and OGG
  `sample_rate`, `accurate`, `animated`) accept form strings: `"90"`, `"24"`, `"true"`, `"on"`. Ordinary `int`,
  `float` and `bool` fields already did.
- Models are frozen, reject unknown keys, and treat `None`/missing as the default, as before.

### Entry points (one per operation)

Each is a `ConfigFamily` with `from_params(params, **fields)` (explicit fields win over
`params`), `model_for(**discriminators)` (the model class, for reading fields/defaults/bounds
without validating; other keyword arguments are ignored), `key` (the discriminating option's
name), `models` and `targets`. Everything invalid raises `InvalidOptionsError`.

| Family | Picks the model by | Models |
|---|---|---|
| `IMAGE_CONVERT` | `target`: jpeg, png, webp, gif, bmp, tiff (`jpg`, `tif`, any case accepted) | `JpegImageConfig`, `PngImageConfig`, `WebpImageConfig`, `GifImageConfig`, `BmpImageConfig`, `TiffImageConfig` |
| `IMAGE_RESIZE` | `target` (required, same values) | `JpegResizeConfig` … `TiffResizeConfig`: the image models plus "Set a maximum width or height to resize to." |
| `VIDEO_CONVERT` | `target`: gif, webp, webm, mp4 | `GifVideoConfig`, `WebpVideoConfig`, `WebmVideoConfig`, `Mp4VideoConfig` |
| `VIDEO_TRANSFORM` | `target`: mp4 (default), webm | `Mp4TransformConfig`, `WebmTransformConfig` |
| `VIDEO_TRIM` | `container` (mkv, mp4, webm, mov; required) and `accurate` (default false) | `CopyTrimConfig` unless accurate; then `Vp9TrimConfig` for webm, `H264TrimConfig` otherwise |
| `AUDIO_CONVERT` | `target`: mp3, m4a, ogg, wav, flac | `Mp3AudioConfig`, `M4aAudioConfig`, `OggAudioConfig`, `WavAudioConfig`, `FlacAudioConfig` |
| `VIDEO_PREVIEW` | `target`: gif, webp (new in 0.6.0) | `GifPreviewConfig`, `WebpPreviewConfig` |
| `IMAGE_EDIT` | `target`: jpeg, png, webp, gif, bmp, tiff (`jpg`, `tif` accepted; new in 0.6.0) | `JpegEditConfig`, `PngEditConfig`, `WebpEditConfig`, `GifEditConfig`, `BmpEditConfig`, `TiffEditConfig` |
| `IMAGE_FRAMES` | `target`: gif, webp (new in 0.6.0) | `GifFramesConfig`, `WebpFramesConfig` |
| `CONTACT_SHEET` | `animated` (default false), then `target` (still: jpeg default, png, webp; animated: webp default, gif) | `JpegSheetConfig`, `PngSheetConfig`, `WebpSheetConfig`, `AnimatedWebpSheetConfig`, `AnimatedGifSheetConfig` |
| — | — | `IcoConfig.from_params(params)`, `FaviconPackConfig.from_params(params)`, `FrameStripConfig.from_params(params)` (new in 0.6.0) |

Union aliases for type hints: `ImageConvertConfig`, `ImageResizeConfig`, `VideoConvertConfig`,
`TransformConfig`, `TrimConfig`, `PreviewConfig`, `AudioConvertConfig`, `StillSheetConfig`,
`AnimatedSheetConfig`, `SheetConfig`, `ImageEditConfig`, `FramesConfig`. Every model also keeps `Model.from_params(params)`.

```python
from sashimi.config import VIDEO_CONVERT
VIDEO_CONVERT.model_for(target="mp4").model_fields["crf"]   # default 23, metadata [Ge(0), Le(51)]
gif = VIDEO_CONVERT.from_params({"duration_seconds": 31}, target="gif")   # valid: no length bound
Limits(max_gif_seconds=30).check(gif)
# InvalidOptionsError: user_message "GIFs can be at most 30 seconds long.", limit "max_gif_seconds"
```

### Public constants

| Name | Value | Meaning |
|---|---|---|
| `DEFAULT_GIF_SECONDS` | 10 | GIF / animated WebP length when none is given |
| `DURATION_TOLERANCE_SECONDS` | 1 | see §6 |
| `DEFAULT_MAX_PIXELS` | 64,000,000 | the default of `Limits.max_pixels` |
| `WEBP_MAX_SIDE` | 16,383 | WebP's widest and tallest image: WebP contact sheets and frame strips (new; replaces `MAX_SHEET_HEIGHT`) |
| `JPEG_MAX_SIDE` / `GIF_MAX_SIDE` | 65,500 / 65,535 | the widest and tallest JPEG and GIF sheets (new) |
| `MAX_ANIMATION_FPS` | 50 | the highest `fps` of a GIF or animated WebP made from a video (new) |
| `FLAC_MAX_SAMPLE_RATE` | 1,048,575 | the highest FLAC `sample_rate` (new) |
| `MIN_TILE_SIZE` | 16 | smallest sheet tile side, px |
| `MP3_SAMPLE_RATES` | (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000) | what `Mp3AudioConfig.sample_rate` accepts (new in 0.6.0) |
| `AAC_SAMPLE_RATES` | the MP3 rates plus 64000, 88200, 96000 | what `M4aAudioConfig.sample_rate` accepts (new in 0.6.0) |
| `OPUS_SAMPLE_RATES` | (8000, 12000, 16000, 24000, 48000) | what `OggAudioConfig.sample_rate` accepts |
| `OPUS_MAX_KBPS_PER_CHANNEL` | 256 | Opus's highest bitrate per channel; higher OGG and WebM audio bitrates are lowered to it (new) |
| `METADATA_MODES` | ("keep", "remove_location", "remove_all") | the values of every `metadata` field (new; the type is `MetadataMode`) |
| `FAVICON_PNG_SIZES` | (16, 32, 48, 180, 192, 512) | the PNG sizes in a favicon pack (new in 0.6.0) |
| `MIN_FRAME_MS` | 20 | shortest frame a speed change writes, ms (new) |
| `EDIT_QUALITY` | 92 | the default `quality` of JPEG, WebP and JPEG-TIFF edits and WebP frame selection (new) |
| `CROP_ASPECTS` | ("free", "1:1", "4:3", "16:9", "9:16") | the crop presets to offer (new) |
| `KEYFRAME_NOTE` | "Trimming without re-encoding is instant and keeps the original quality, but cuts can only land on keyframes, so your clip may start a moment before the time you chose." | |

Also exported: `ParamsModel`, `ConfigFamily`, `RGB`, `RGBA`, `parse_color`, `Resample`,
`X264Preset`, `GridValue`, `PreviewCrop`, `Rotation`, `Flip`, and `Limits` (next).

Gone in the limits round: `MAX_GIF_SECONDS`, `MAX_VIDEO_SECONDS`, `MAX_AUDIO_SECONDS`,
`MAX_PREVIEW_SAMPLES`, `MAX_FRAME_BYTES`, `MAX_ANIMATION_BYTES`, `MAX_SHEET_PIXELS` and
`MAX_SHEET_HEIGHT` (§1 item 14 says what replaces each), and `ParamsModel.bound_messages`.

### `Limits`

`Limits(**fields)`, `Limits.from_params(params)` (numbers, or strings such as `"600"`; `None`,
`""` or `"none"` turns a limit off; `InvalidOptionsError` for anything else), `Limits.unlimited()`
(every limit off) and `limits.check(config)`. Frozen; unknown fields are refused. Every converter
and batch function takes `limits=`, and `None` means `Limits()`.

#### `Limits`

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `max_pixels` | int or None | > 0 | `64000000` | Images with more pixels than this are refused before they are decoded (conversions, icons, edits, frames). |
| `max_frame_bytes` | int or None | > 0 | `1073741824` | Video frames held in memory at once, at width x height x 3 bytes each: an animated contact sheet's clips, and every frame of a GIF made from a video or of a GIF preview (a GIF's palette is built from all of them). |
| `max_animation_bytes` | int or None | > 0 | `1073741824` | The frames of an animated GIF or WebP being edited, at width x height x 4 bytes each (edits, frame selection, frame strips). |
| `max_sheet_pixels` | int or None | > 0 | `64000000` | The largest contact sheet, in pixels. |
| `max_video_seconds` | float or None | > 0 | `None` | The longest video that is re-encoded or copied into an MP4 (to WebM or MP4, rotate and flip, accurate cuts). Lossless cuts and remuxes have no limit. |
| `max_audio_seconds` | float or None | > 0 | `None` | The longest audio that is converted or extracted. |
| `max_gif_seconds` | float or None | > 0 | `None` | The longest GIF or animated WebP made from a video, and the longest preview (all its clips together). |
| `max_preview_clips` | int or None | > 0 | `None` | The most clips a preview takes. |

### Every model: fields, bounds, defaults

Generated from `model_fields` (the CLI prints the same, per format, with
`sashimi config defaults <command> [format]`). `target` (and `accurate`/`animated` where shown) is fixed per
model and defaults to the model's own value. Colours accept `"#rrggbb"`/`"#rrggbbaa"`, `"r,g,b"`
or tuples.


#### `JpegImageConfig` (`target`='jpeg')

A JPEG image. Transparency is always flattened onto ``background_color``.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `max_width` | int or None | ≥ 1 | `None` | Scale down to at most this width. ``None`` keeps the source's width. Never upscales; keeps the aspect ratio. |
| `max_height` | int or None | ≥ 1 | `None` | Scale down to at most this height. ``None`` keeps the source's height. |
| `resample` | lanczos / bicubic / bilinear / hamming / box / nearest |  | `"lanczos"` | Filter used when scaling down. |
| `background_color` | colour |  | #ffffff | What transparent areas are flattened onto. |
| `auto_orient` | bool |  | `True` | Apply the EXIF orientation tag (phone photos), so the output looks the way the photo is shown. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `keep_icc` | bool |  | `True` | Keep the colour profile, so wide-gamut photos keep their colours. |
| `quality` | int | ≥ 1, ≤ 100 | `85` | 1 (smallest) to 100 (best). |
| `optimize` | bool |  | `True` | Extra Huffman-table pass: slightly smaller files, slightly slower. |
| `progressive` | bool |  | `False` | Progressive (interlaced) encoding. |
| `jpeg_subsampling` | 4:4:4 / 4:2:2 / 4:2:0 |  | `"4:2:0"` | Chroma subsampling. 4:4:4 keeps colour edges sharp; 4:2:0 is smallest. |

#### `PngImageConfig` (`target`='png')

A PNG image (always lossless).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `max_width` | int or None | ≥ 1 | `None` | Scale down to at most this width. ``None`` keeps the source's width. Never upscales; keeps the aspect ratio. |
| `max_height` | int or None | ≥ 1 | `None` | Scale down to at most this height. ``None`` keeps the source's height. |
| `resample` | lanczos / bicubic / bilinear / hamming / box / nearest |  | `"lanczos"` | Filter used when scaling down. |
| `background_color` | colour |  | #ffffff | What transparent areas are flattened onto. |
| `auto_orient` | bool |  | `True` | Apply the EXIF orientation tag (phone photos), so the output looks the way the photo is shown. |
| `preserve_transparency` | bool |  | `True` | Keep alpha; when False, transparent areas are flattened onto ``background_color``. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `keep_icc` | bool |  | `True` | Keep the colour profile, so wide-gamut photos keep their colours. |
| `png_compress_level` | int | ≥ 0, ≤ 9 | `6` | zlib level, 0 (none) to 9 (smallest). Lossless either way. |
| `optimize` | bool |  | `False` | Search for the smallest encoding. Can take tens of seconds on large images. |

#### `WebpImageConfig` (`target`='webp')

A WebP image.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `max_width` | int or None | ≥ 1 | `None` | Scale down to at most this width. ``None`` keeps the source's width. Never upscales; keeps the aspect ratio. |
| `max_height` | int or None | ≥ 1 | `None` | Scale down to at most this height. ``None`` keeps the source's height. |
| `resample` | lanczos / bicubic / bilinear / hamming / box / nearest |  | `"lanczos"` | Filter used when scaling down. |
| `background_color` | colour |  | #ffffff | What transparent areas are flattened onto. |
| `auto_orient` | bool |  | `True` | Apply the EXIF orientation tag (phone photos), so the output looks the way the photo is shown. |
| `preserve_transparency` | bool |  | `True` | Keep alpha; when False, transparent areas are flattened onto ``background_color``. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `keep_icc` | bool |  | `True` | Keep the colour profile, so wide-gamut photos keep their colours. |
| `quality` | int | ≥ 1, ≤ 100 | `85` | Lossy quality, or compression effort when ``lossless``. |
| `lossless` | bool |  | `False` | Lossless compression: exact pixels, usually bigger files. |
| `webp_method` | int | ≥ 0, ≤ 6 | `4` | Encoder effort, 0 (fastest) to 6 (smallest). |

#### `GifImageConfig` (`target`='gif')

A static GIF (the first frame of an animated input). Of the source's metadata, GIFs hold only comments.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `max_width` | int or None | ≥ 1 | `None` | Scale down to at most this width. ``None`` keeps the source's width. Never upscales; keeps the aspect ratio. |
| `max_height` | int or None | ≥ 1 | `None` | Scale down to at most this height. ``None`` keeps the source's height. |
| `resample` | lanczos / bicubic / bilinear / hamming / box / nearest |  | `"lanczos"` | Filter used when scaling down. |
| `background_color` | colour |  | #ffffff | What transparent areas are flattened onto. |
| `auto_orient` | bool |  | `True` | Apply the EXIF orientation tag (phone photos), so the output looks the way the photo is shown. |
| `preserve_transparency` | bool |  | `True` | Keep alpha; when False, transparent areas are flattened onto ``background_color``. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `optimize` | bool |  | `False` | Drop unused palette entries. |

#### `BmpImageConfig` (`target`='bmp')

An uncompressed BMP. Transparency is always flattened onto ``background_color``.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `max_width` | int or None | ≥ 1 | `None` | Scale down to at most this width. ``None`` keeps the source's width. Never upscales; keeps the aspect ratio. |
| `max_height` | int or None | ≥ 1 | `None` | Scale down to at most this height. ``None`` keeps the source's height. |
| `resample` | lanczos / bicubic / bilinear / hamming / box / nearest |  | `"lanczos"` | Filter used when scaling down. |
| `background_color` | colour |  | #ffffff | What transparent areas are flattened onto. |
| `auto_orient` | bool |  | `True` | Apply the EXIF orientation tag (phone photos), so the output looks the way the photo is shown. |

#### `TiffImageConfig` (`target`='tiff')

A TIFF image.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `max_width` | int or None | ≥ 1 | `None` | Scale down to at most this width. ``None`` keeps the source's width. Never upscales; keeps the aspect ratio. |
| `max_height` | int or None | ≥ 1 | `None` | Scale down to at most this height. ``None`` keeps the source's height. |
| `resample` | lanczos / bicubic / bilinear / hamming / box / nearest |  | `"lanczos"` | Filter used when scaling down. |
| `background_color` | colour |  | #ffffff | What transparent areas are flattened onto. |
| `auto_orient` | bool |  | `True` | Apply the EXIF orientation tag (phone photos), so the output looks the way the photo is shown. |
| `preserve_transparency` | bool |  | `True` | Keep alpha; when False, transparent areas are flattened onto ``background_color``. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `keep_icc` | bool |  | `True` | Keep the colour profile, so wide-gamut photos keep their colours. |
| `tiff_compression` | none / lzw / deflate / jpeg |  | `"lzw"` | LZW and Deflate are lossless; JPEG is lossy and much smaller. |
| `quality` | int | ≥ 1, ≤ 100 | `85` | Used only with JPEG compression, which also flattens transparency. |

#### `GifVideoConfig` (`target`='gif')

A GIF made from part of a video: no sound, a 256-colour palette.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float | > 0 | `10` | Length in seconds. |
| `max_width` | int | ≥ 64 | `480` | Never upscales; keeps the aspect ratio. |
| `fps` | int | ≥ 1, ≤ 50 | `12` | Frames per second. |
| `loop` | bool |  | `True` | Loop forever, or play once. |
| `dither` | bayer / sierra2_4a / floyd_steinberg / none |  | `"bayer"` | Bayer gives smaller files; error diffusion (sierra2_4a, floyd_steinberg) smoother gradients. |
| `palette_mode` | diff / full / single |  | `"diff"` | Build the palette from moving parts ("diff"), all pixels ("full"), or per frame ("single"). |

#### `WebpVideoConfig` (`target`='webp')

An animated WebP made from part of a video: no sound.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float | > 0 | `10` | Length in seconds. |
| `max_width` | int | ≥ 64 | `480` | Never upscales; keeps the aspect ratio. |
| `fps` | int | ≥ 1, ≤ 50 | `12` | Frames per second. |
| `loop` | bool |  | `True` | Loop forever, or play once. |
| `quality` | int | ≥ 0, ≤ 100 | `75` | Lossy quality, or compression effort when ``lossless``. |
| `lossless` | bool |  | `False` | Lossless frames (much bigger files). |

#### `WebmVideoConfig` (`target`='webm')

A WebM (VP9 + Opus) video.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0 | `None` | ``None`` converts to the end of the video. |
| `max_width` | int or None | ≥ 64 | `None` | ``None`` keeps the source's width. Never upscales; keeps the aspect ratio. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `crf` | int | ≥ 0, ≤ 63 | `34` | Constant quality: lower is better and bigger. |
| `speed` | int | ≥ 0, ≤ 8 | `8` | libvpx ``-cpu-used``: higher is faster and lower quality. |
| `audio_bitrate_kbps` | int | ≥ 32, ≤ 512 | `96` | Opus audio bitrate in kb/s. Opus allows at most 256 per channel, so mono audio gets at most 256. |
| `mute` | bool |  | `False` | Drop the audio track. |

#### `Mp4VideoConfig` (`target`='mp4')

An MP4 (H.264 + AAC) video.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0 | `None` | ``None`` converts to the end of the video. |
| `max_width` | int or None | ≥ 64 | `None` | ``None`` keeps the source's width. Never upscales; keeps the aspect ratio. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `crf` | int | ≥ 0, ≤ 51 | `23` | Constant quality: lower is better and bigger. |
| `preset` | ultrafast / superfast / veryfast / faster / fast / medium / slow / slower / veryslow |  | `"veryfast"` | Slower presets give smaller files. |
| `audio_bitrate_kbps` | int | ≥ 32, ≤ 512 | `128` | AAC audio bitrate in kb/s. |
| `mute` | bool |  | `False` | Drop the audio track. |

#### `GifPreviewConfig` (`target`='gif')

A GIF preview: short clips sampled from across a video, played one after another. No sound.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `samples` | int | ≥ 1 | `5` | How many clips to take from across the video. Short videos get fewer, so clips never overlap. |
| `sample_duration` | float | > 0 | `2.0` | Length of each clip in seconds. |
| `start_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `10.0` | Skip this much of the start of the video (intros, logos). |
| `end_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `10.0` | Skip this much of the end of the video (credits). |
| `max_height` | int | ≥ 64 | `360` | Scale down to at most this height. Never upscales; keeps the aspect ratio. |
| `max_width` | int or None | ≥ 64 | `None` | Also scale down to at most this width. Blank sets no width limit. |
| `crop` | none / 9:16 / 1:1 |  | `"none"` | Crop the centre of the picture to this shape first, e.g. "9:16" for vertical video sites. |
| `fps` | int | ≥ 1, ≤ 50 | `15` | Frames per second. |
| `loop` | bool |  | `True` | Loop forever, or play once. |

#### `WebpPreviewConfig` (`target`='webp')

An animated WebP preview: short clips sampled from across a video, played one after another. No sound.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `samples` | int | ≥ 1 | `5` | How many clips to take from across the video. Short videos get fewer, so clips never overlap. |
| `sample_duration` | float | > 0 | `2.0` | Length of each clip in seconds. |
| `start_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `10.0` | Skip this much of the start of the video (intros, logos). |
| `end_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `10.0` | Skip this much of the end of the video (credits). |
| `max_height` | int | ≥ 64 | `360` | Scale down to at most this height. Never upscales; keeps the aspect ratio. |
| `max_width` | int or None | ≥ 64 | `None` | Also scale down to at most this width. Blank sets no width limit. |
| `crop` | none / 9:16 / 1:1 |  | `"none"` | Crop the centre of the picture to this shape first, e.g. "9:16" for vertical video sites. |
| `fps` | int | ≥ 1, ≤ 50 | `15` | Frames per second. |
| `loop` | bool |  | `True` | Loop forever, or play once. |
| `quality` | int | ≥ 0, ≤ 100 | `75` | Lossy quality, 0 (smallest) to 100 (best). |

#### `Mp4TransformConfig` (`target`='mp4')

Rotate and/or flip a video, re-encoding it as MP4 (H.264 + AAC).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `rotate` | 0 / 90 / 180 / 270 |  | `0` | Clockwise rotation in degrees. |
| `flip` | none / horizontal / vertical |  | `"none"` | Mirror the picture. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `crf` | int | ≥ 0, ≤ 51 | `23` | Constant quality: lower is better and bigger. |
| `preset` | ultrafast / superfast / veryfast / faster / fast / medium / slow / slower / veryslow |  | `"veryfast"` | Slower presets give smaller files. |
| `audio_bitrate_kbps` | int | ≥ 32, ≤ 512 | `128` | AAC audio bitrate in kb/s. |
| `mute` | bool |  | `False` | Drop the audio track. |

#### `WebmTransformConfig` (`target`='webm')

Rotate and/or flip a video, re-encoding it as WebM (VP9 + Opus).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `rotate` | 0 / 90 / 180 / 270 |  | `0` | Clockwise rotation in degrees. |
| `flip` | none / horizontal / vertical |  | `"none"` | Mirror the picture. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `crf` | int | ≥ 0, ≤ 63 | `34` | Constant quality: lower is better and bigger. |
| `speed` | int | ≥ 0, ≤ 8 | `8` | libvpx ``-cpu-used``: higher is faster and lower quality. |
| `audio_bitrate_kbps` | int | ≥ 32, ≤ 512 | `96` | Opus audio bitrate in kb/s. Opus allows at most 256 per channel, so mono audio gets at most 256. |
| `mute` | bool |  | `False` | Drop the audio track. |

#### `CopyTrimConfig` (`accurate`=False)

A lossless trim: streams are copied into `container`, so the cut is instant but starts at the keyframe at or before `start_seconds` (see :data:`KEYFRAME_NOTE`). The container must be able to hold the source's codecs.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where the clip starts, in seconds. |
| `end_seconds` | float or None | > 0 | `None` | ``None`` (with no ``duration_seconds``) trims to the end of the video. |
| `duration_seconds` | float or None | > 0 | `None` | The clip's length, instead of ``end_seconds``. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `container` | mkv / mp4 / webm / mov |  | **required** | The output container; it must be able to hold the source's codecs. |

#### `H264TrimConfig` (`accurate`=True)

A frame-accurate trim, re-encoded as H.264 + AAC into MP4, MOV or MKV.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where the clip starts, in seconds. |
| `end_seconds` | float or None | > 0 | `None` | ``None`` (with no ``duration_seconds``) trims to the end of the video. |
| `duration_seconds` | float or None | > 0 | `None` | The clip's length, instead of ``end_seconds``. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `crf` | int | ≥ 0, ≤ 51 | `23` | Constant quality: lower is better and bigger. |
| `preset` | ultrafast / superfast / veryfast / faster / fast / medium / slow / slower / veryslow |  | `"veryfast"` | Slower presets give smaller files. |
| `audio_bitrate_kbps` | int | ≥ 32, ≤ 512 | `128` | AAC audio bitrate in kb/s. |
| `mute` | bool |  | `False` | Drop the audio track. |
| `container` | mp4 / mov / mkv |  | **required** | The output container. |

#### `Vp9TrimConfig` (`accurate`=True)

A frame-accurate trim, re-encoded as VP9 + Opus into WebM.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where the clip starts, in seconds. |
| `end_seconds` | float or None | > 0 | `None` | ``None`` (with no ``duration_seconds``) trims to the end of the video. |
| `duration_seconds` | float or None | > 0 | `None` | The clip's length, instead of ``end_seconds``. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `crf` | int | ≥ 0, ≤ 63 | `34` | Constant quality: lower is better and bigger. |
| `speed` | int | ≥ 0, ≤ 8 | `8` | libvpx ``-cpu-used``: higher is faster and lower quality. |
| `audio_bitrate_kbps` | int | ≥ 32, ≤ 512 | `96` | Opus audio bitrate in kb/s. Opus allows at most 256 per channel, so mono audio gets at most 256. |
| `mute` | bool |  | `False` | Drop the audio track. |
| `container` | webm |  | `"webm"` | The output container. |

#### `Mp3AudioConfig` (`target`='mp3')

MP3 (LAME).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0 | `None` | ``None`` converts to the end of the file. |
| `mono` | bool |  | `False` | Mix down to one channel. |
| `sample_rate` | 8000 / 11025 / 12000 / 16000 / 22050 / 24000 / 32000 / 44100 / 48000 or None |  | `None` | Output sample rate in Hz. ``None`` keeps the source's if MP3 supports it, and otherwise uses the nearest rate it does. |
| `bitrate_kbps` | int | ≥ 64, ≤ 320 | `192` | Bitrate in kb/s. |

#### `M4aAudioConfig` (`target`='m4a')

M4A (AAC).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0 | `None` | ``None`` converts to the end of the file. |
| `mono` | bool |  | `False` | Mix down to one channel. |
| `sample_rate` | 8000 / 11025 / 12000 / 16000 / 22050 / 24000 / 32000 / 44100 / 48000 / 64000 / 88200 / 96000 or None |  | `None` | Output sample rate in Hz. ``None`` keeps the source's if AAC supports it, and otherwise uses the nearest rate it does. |
| `bitrate_kbps` | int | ≥ 64, ≤ 512 | `160` | Bitrate in kb/s. |

#### `OggAudioConfig` (`target`='ogg')

OGG (Opus).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0 | `None` | ``None`` converts to the end of the file. |
| `mono` | bool |  | `False` | Mix down to one channel. |
| `sample_rate` | 8000 / 12000 / 16000 / 24000 / 48000 or None |  | `None` | Output sample rate in Hz. ``None`` keeps the source's if Opus supports it, and otherwise uses 48 kHz. |
| `bitrate_kbps` | int | ≥ 64, ≤ 512 | `128` | Bitrate in kb/s. Opus allows at most 256 per channel, so mono audio gets at most 256. |

#### `WavAudioConfig` (`target`='wav')

WAV (uncompressed PCM).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0 | `None` | ``None`` converts to the end of the file. |
| `mono` | bool |  | `False` | Mix down to one channel. |
| `sample_rate` | int or None | ≥ 8000 | `None` | Output sample rate in Hz. ``None`` keeps the source's. |
| `wav_bit_depth` | 16 / 24 / 32 |  | `16` | PCM sample size in bits. |

#### `FlacAudioConfig` (`target`='flac')

FLAC (lossless).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0 | `None` | ``None`` converts to the end of the file. |
| `mono` | bool |  | `False` | Mix down to one channel. |
| `sample_rate` | int or None | ≥ 8000, ≤ 1048575 | `None` | Output sample rate in Hz. ``None`` keeps the source's. |
| `flac_compression_level` | int | ≥ 0, ≤ 12 | `5` | 0 is fastest, 12 smallest; lossless either way. |

#### `JpegSheetConfig` (`target`='jpeg', `animated`=False)

A still contact sheet saved as JPEG.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `cols` | int or auto | ≥ 1 | `"auto"` | Columns, or "auto" to choose from the video's aspect ratio. |
| `rows` | int or auto | ≥ 1 | `"auto"` | Rows, or "auto". |
| `gap` | int | ≥ 0 | `5` | Pixels between tiles. |
| `start_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `7.0` | Skip this much of the start of the video (credits, logos). |
| `end_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `7.0` | Skip this much of the end of the video (credits). |
| `grid_bg_color` | colour |  | #141414 | Background behind the tiles. |
| `show_header` | bool |  | `True` | The block above the grid with the file name, size, duration and dimensions. |
| `header_font_size` | int | ≥ 6, ≤ 200 | `18` | Header text size in pixels. |
| `header_bg_color` | colour |  | #000000 | Header background. |
| `header_text_color` | colour |  | #ffffff | Header text colour. |
| `show_timestamps` | bool |  | `True` | The time label in each tile's corner. |
| `timestamp_font_size` | int | ≥ 6, ≤ 200 | `14` | Time label text size in pixels. |
| `timestamp_bg_color` | colour |  | #000000b4 | Time label background, with alpha. |
| `timestamp_text_color` | colour |  | #ffffff | Time label text colour. |
| `width` | int | ≥ 320 | `2560` | Width of the sheet in pixels. |
| `quality` | int | ≥ 1, ≤ 100 | `85` | 1 (smallest) to 100 (best). |

#### `PngSheetConfig` (`target`='png', `animated`=False)

A still contact sheet saved as PNG.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `cols` | int or auto | ≥ 1 | `"auto"` | Columns, or "auto" to choose from the video's aspect ratio. |
| `rows` | int or auto | ≥ 1 | `"auto"` | Rows, or "auto". |
| `gap` | int | ≥ 0 | `5` | Pixels between tiles. |
| `start_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `7.0` | Skip this much of the start of the video (credits, logos). |
| `end_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `7.0` | Skip this much of the end of the video (credits). |
| `grid_bg_color` | colour |  | #141414 | Background behind the tiles. |
| `show_header` | bool |  | `True` | The block above the grid with the file name, size, duration and dimensions. |
| `header_font_size` | int | ≥ 6, ≤ 200 | `18` | Header text size in pixels. |
| `header_bg_color` | colour |  | #000000 | Header background. |
| `header_text_color` | colour |  | #ffffff | Header text colour. |
| `show_timestamps` | bool |  | `True` | The time label in each tile's corner. |
| `timestamp_font_size` | int | ≥ 6, ≤ 200 | `14` | Time label text size in pixels. |
| `timestamp_bg_color` | colour |  | #000000b4 | Time label background, with alpha. |
| `timestamp_text_color` | colour |  | #ffffff | Time label text colour. |
| `width` | int | ≥ 320 | `2560` | Width of the sheet in pixels. |

#### `WebpSheetConfig` (`target`='webp', `animated`=False)

A still contact sheet saved as WebP.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `cols` | int or auto | ≥ 1 | `"auto"` | Columns, or "auto" to choose from the video's aspect ratio. |
| `rows` | int or auto | ≥ 1 | `"auto"` | Rows, or "auto". |
| `gap` | int | ≥ 0 | `5` | Pixels between tiles. |
| `start_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `7.0` | Skip this much of the start of the video (credits, logos). |
| `end_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `7.0` | Skip this much of the end of the video (credits). |
| `grid_bg_color` | colour |  | #141414 | Background behind the tiles. |
| `show_header` | bool |  | `True` | The block above the grid with the file name, size, duration and dimensions. |
| `header_font_size` | int | ≥ 6, ≤ 200 | `18` | Header text size in pixels. |
| `header_bg_color` | colour |  | #000000 | Header background. |
| `header_text_color` | colour |  | #ffffff | Header text colour. |
| `show_timestamps` | bool |  | `True` | The time label in each tile's corner. |
| `timestamp_font_size` | int | ≥ 6, ≤ 200 | `14` | Time label text size in pixels. |
| `timestamp_bg_color` | colour |  | #000000b4 | Time label background, with alpha. |
| `timestamp_text_color` | colour |  | #ffffff | Time label text colour. |
| `width` | int | ≥ 320 | `2560` | Width of the sheet in pixels. |
| `quality` | int | ≥ 1, ≤ 100 | `85` | 1 (smallest) to 100 (best). |

#### `AnimatedWebpSheetConfig` (`target`='webp', `animated`=True)

An animated contact sheet saved as WebP: every tile plays a short clip.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `cols` | int or auto | ≥ 1 | `"auto"` | Columns, or "auto": 3, or 4 for portrait videos. |
| `rows` | int or auto | ≥ 1 | `"auto"` | Rows, or "auto": 3, or 2 for portrait videos. |
| `gap` | int | ≥ 0 | `5` | Pixels between tiles. |
| `start_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `7.0` | Skip this much of the start of the video (credits, logos). |
| `end_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `7.0` | Skip this much of the end of the video (credits). |
| `grid_bg_color` | colour |  | #141414 | Background behind the tiles. |
| `show_header` | bool |  | `True` | The block above the grid with the file name, size, duration and dimensions. |
| `header_font_size` | int | ≥ 6, ≤ 200 | `18` | Header text size in pixels. |
| `header_bg_color` | colour |  | #000000 | Header background. |
| `header_text_color` | colour |  | #ffffff | Header text colour. |
| `show_timestamps` | bool |  | `True` | The time label in each tile's corner. |
| `timestamp_font_size` | int | ≥ 6, ≤ 200 | `14` | Time label text size in pixels. |
| `timestamp_bg_color` | colour |  | #000000b4 | Time label background, with alpha. |
| `timestamp_text_color` | colour |  | #ffffff | Time label text colour. |
| `width` | int | ≥ 320 | `1280` | Approximate width of the sheet in pixels (tiles are rounded to even sizes). |
| `sample_duration` | float | > 0.0 | `2.0` | Length of each tile's clip in seconds. A video shorter than this gets one tile of the whole video. |
| `fps` | int | ≥ 1, ≤ 50 | `12` | Frames per second. |
| `loop` | bool |  | `True` | Loop forever, or play once. |
| `quality` | int | ≥ 1, ≤ 100 | `75` | 1 (smallest) to 100 (best). |

#### `AnimatedGifSheetConfig` (`target`='gif', `animated`=True)

An animated contact sheet saved as GIF: every tile plays a short clip.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `cols` | int or auto | ≥ 1 | `"auto"` | Columns, or "auto": 3, or 4 for portrait videos. |
| `rows` | int or auto | ≥ 1 | `"auto"` | Rows, or "auto": 3, or 2 for portrait videos. |
| `gap` | int | ≥ 0 | `5` | Pixels between tiles. |
| `start_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `7.0` | Skip this much of the start of the video (credits, logos). |
| `end_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `7.0` | Skip this much of the end of the video (credits). |
| `grid_bg_color` | colour |  | #141414 | Background behind the tiles. |
| `show_header` | bool |  | `True` | The block above the grid with the file name, size, duration and dimensions. |
| `header_font_size` | int | ≥ 6, ≤ 200 | `18` | Header text size in pixels. |
| `header_bg_color` | colour |  | #000000 | Header background. |
| `header_text_color` | colour |  | #ffffff | Header text colour. |
| `show_timestamps` | bool |  | `True` | The time label in each tile's corner. |
| `timestamp_font_size` | int | ≥ 6, ≤ 200 | `14` | Time label text size in pixels. |
| `timestamp_bg_color` | colour |  | #000000b4 | Time label background, with alpha. |
| `timestamp_text_color` | colour |  | #ffffff | Time label text colour. |
| `width` | int | ≥ 320 | `1280` | Approximate width of the sheet in pixels (tiles are rounded to even sizes). |
| `sample_duration` | float | > 0.0 | `2.0` | Length of each tile's clip in seconds. A video shorter than this gets one tile of the whole video. |
| `fps` | int | ≥ 1, ≤ 50 | `12` | Frames per second. |
| `loop` | bool |  | `True` | Loop forever, or play once. |

#### `IcoConfig`

A multi-size .ico (favicon). Sizes are square icon edges in pixels.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `sizes` | tuple of int |  | `(16, 32, 48, 64, 128, 256)` | Icon sizes to include, 16 to 256 pixels. |
| `fit` | pad / crop |  | `"pad"` | Non-square images: centre them on a square ("pad") or crop the centre ("crop"). |
| `resample` | lanczos / bicubic / bilinear / hamming / box / nearest |  | `"lanczos"` | Filter used when scaling. |
| `background_color` | colour or None |  | `None` | Fill transparent areas (and the padding) with this colour. Blank keeps them transparent. |

#### `FaviconPackConfig`

A favicon pack (.zip): ``favicon.ico``, PNG icons at 16, 32, 48, 180, 192 and 512 pixels, a ``site.webmanifest`` and ``favicon.html``, the ``<head>`` tags that use them.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `ico_sizes` | tuple of int |  | `(16, 32, 48)` | Sizes inside favicon.ico, 16 to 256 pixels. |
| `fit` | pad / crop |  | `"pad"` | Non-square images: centre them on a square ("pad") or crop the centre ("crop"). |
| `resample` | lanczos / bicubic / bilinear / hamming / box / nearest |  | `"lanczos"` | Filter used when scaling. |
| `background_color` | colour |  | #ffffff | Fills the Apple touch icon (180 px), which must be opaque because iOS shows transparency as black. |
| `name` | text |  | `""` | The web app's name in site.webmanifest. Blank leaves it out. |
| `short_name` | text |  | `""` | A short name for home screens (about 12 characters). Blank leaves it out. |
| `theme_color` | colour or None |  | `None` | Browser UI colour, in the manifest and a theme-color tag. Blank leaves it out. |
| `manifest_background_color` | colour or None |  | `None` | Splash screen colour in the manifest. Blank leaves it out. |

#### `JpegEditConfig` (`target`='jpeg')

An edited JPEG: EXIF orientation, then rotate and flip, then crop, then brightness, contrast and saturation. Re-saved at high quality; transparency (from a HEIC source) is flattened onto white.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `rotate` | 0 / 90 / 180 / 270 |  | `0` | Clockwise rotation in degrees, after the photo's own orientation (EXIF) is applied. |
| `flip` | none / horizontal / vertical |  | `"none"` | Mirror the picture, after rotating. |
| `crop_left` | int | ≥ 0 | `0` | Left edge of the crop, in pixels of the rotated and flipped picture. |
| `crop_top` | int | ≥ 0 | `0` | Top edge of the crop, in pixels of the rotated and flipped picture. |
| `crop_width` | int or None | ≥ 1 | `None` | Width of the crop in pixels. Blank keeps everything right of the left edge. |
| `crop_height` | int or None | ≥ 1 | `None` | Height of the crop in pixels. Blank keeps everything below the top edge. |
| `brightness` | float | ≥ 0.0 | `1.0` | 0 is black, 1 unchanged, 2 twice as bright. The same as CSS ``brightness()``. |
| `contrast` | float | ≥ 0.0 | `1.0` | 0 is flat grey, 1 unchanged, 2 twice the contrast. The same as CSS ``contrast()``. |
| `saturation` | float | ≥ 0.0 | `1.0` | 0 is greyscale, 1 unchanged, 2 twice as vivid. The same as CSS ``saturate()``. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `keep_icc` | bool |  | `True` | Keep the colour profile, so wide-gamut photos keep their colours. |
| `quality` | int | ≥ 1, ≤ 100 | `92` | 1 (smallest) to 100 (best). Edits are re-saved at high quality by default. |
| `optimize` | bool |  | `True` | Extra Huffman-table pass: slightly smaller files, slightly slower. |
| `progressive` | bool |  | `False` | Progressive (interlaced) encoding. |
| `jpeg_subsampling` | 4:4:4 / 4:2:2 / 4:2:0 |  | `"4:2:0"` | Chroma subsampling. 4:4:4 keeps colour edges sharp; 4:2:0 is smallest. |

#### `PngEditConfig` (`target`='png')

An edited PNG (lossless). Transparency is kept.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `rotate` | 0 / 90 / 180 / 270 |  | `0` | Clockwise rotation in degrees, after the photo's own orientation (EXIF) is applied. |
| `flip` | none / horizontal / vertical |  | `"none"` | Mirror the picture, after rotating. |
| `crop_left` | int | ≥ 0 | `0` | Left edge of the crop, in pixels of the rotated and flipped picture. |
| `crop_top` | int | ≥ 0 | `0` | Top edge of the crop, in pixels of the rotated and flipped picture. |
| `crop_width` | int or None | ≥ 1 | `None` | Width of the crop in pixels. Blank keeps everything right of the left edge. |
| `crop_height` | int or None | ≥ 1 | `None` | Height of the crop in pixels. Blank keeps everything below the top edge. |
| `brightness` | float | ≥ 0.0 | `1.0` | 0 is black, 1 unchanged, 2 twice as bright. The same as CSS ``brightness()``. |
| `contrast` | float | ≥ 0.0 | `1.0` | 0 is flat grey, 1 unchanged, 2 twice the contrast. The same as CSS ``contrast()``. |
| `saturation` | float | ≥ 0.0 | `1.0` | 0 is greyscale, 1 unchanged, 2 twice as vivid. The same as CSS ``saturate()``. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `keep_icc` | bool |  | `True` | Keep the colour profile, so wide-gamut photos keep their colours. |
| `png_compress_level` | int | ≥ 0, ≤ 9 | `6` | zlib level, 0 (none) to 9 (smallest). Lossless either way. |
| `optimize` | bool |  | `False` | Search for the smallest encoding. Can take tens of seconds on large images. |

#### `WebpEditConfig` (`target`='webp')

An edited WebP. Transparency and animation are kept; every frame of an animation is edited.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `rotate` | 0 / 90 / 180 / 270 |  | `0` | Clockwise rotation in degrees, after the photo's own orientation (EXIF) is applied. |
| `flip` | none / horizontal / vertical |  | `"none"` | Mirror the picture, after rotating. |
| `crop_left` | int | ≥ 0 | `0` | Left edge of the crop, in pixels of the rotated and flipped picture. |
| `crop_top` | int | ≥ 0 | `0` | Top edge of the crop, in pixels of the rotated and flipped picture. |
| `crop_width` | int or None | ≥ 1 | `None` | Width of the crop in pixels. Blank keeps everything right of the left edge. |
| `crop_height` | int or None | ≥ 1 | `None` | Height of the crop in pixels. Blank keeps everything below the top edge. |
| `brightness` | float | ≥ 0.0 | `1.0` | 0 is black, 1 unchanged, 2 twice as bright. The same as CSS ``brightness()``. |
| `contrast` | float | ≥ 0.0 | `1.0` | 0 is flat grey, 1 unchanged, 2 twice the contrast. The same as CSS ``contrast()``. |
| `saturation` | float | ≥ 0.0 | `1.0` | 0 is greyscale, 1 unchanged, 2 twice as vivid. The same as CSS ``saturate()``. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `keep_icc` | bool |  | `True` | Keep the colour profile, so wide-gamut photos keep their colours. |
| `quality` | int | ≥ 1, ≤ 100 | `92` | Lossy quality, or compression effort when ``lossless``. Edits are re-saved at high quality by default. |
| `lossless` | bool |  | `False` | Lossless compression: exact pixels, usually bigger files. |
| `webp_method` | int | ≥ 0, ≤ 6 | `4` | Encoder effort, 0 (fastest) to 6 (smallest). |

#### `GifEditConfig` (`target`='gif')

An edited GIF. Transparency and animation are kept; every frame of an animation is edited.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `rotate` | 0 / 90 / 180 / 270 |  | `0` | Clockwise rotation in degrees, after the photo's own orientation (EXIF) is applied. |
| `flip` | none / horizontal / vertical |  | `"none"` | Mirror the picture, after rotating. |
| `crop_left` | int | ≥ 0 | `0` | Left edge of the crop, in pixels of the rotated and flipped picture. |
| `crop_top` | int | ≥ 0 | `0` | Top edge of the crop, in pixels of the rotated and flipped picture. |
| `crop_width` | int or None | ≥ 1 | `None` | Width of the crop in pixels. Blank keeps everything right of the left edge. |
| `crop_height` | int or None | ≥ 1 | `None` | Height of the crop in pixels. Blank keeps everything below the top edge. |
| `brightness` | float | ≥ 0.0 | `1.0` | 0 is black, 1 unchanged, 2 twice as bright. The same as CSS ``brightness()``. |
| `contrast` | float | ≥ 0.0 | `1.0` | 0 is flat grey, 1 unchanged, 2 twice the contrast. The same as CSS ``contrast()``. |
| `saturation` | float | ≥ 0.0 | `1.0` | 0 is greyscale, 1 unchanged, 2 twice as vivid. The same as CSS ``saturate()``. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `optimize` | bool |  | `False` | Drop unused palette entries. |

#### `BmpEditConfig` (`target`='bmp')

An edited BMP. Transparency is flattened onto white.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `rotate` | 0 / 90 / 180 / 270 |  | `0` | Clockwise rotation in degrees, after the photo's own orientation (EXIF) is applied. |
| `flip` | none / horizontal / vertical |  | `"none"` | Mirror the picture, after rotating. |
| `crop_left` | int | ≥ 0 | `0` | Left edge of the crop, in pixels of the rotated and flipped picture. |
| `crop_top` | int | ≥ 0 | `0` | Top edge of the crop, in pixels of the rotated and flipped picture. |
| `crop_width` | int or None | ≥ 1 | `None` | Width of the crop in pixels. Blank keeps everything right of the left edge. |
| `crop_height` | int or None | ≥ 1 | `None` | Height of the crop in pixels. Blank keeps everything below the top edge. |
| `brightness` | float | ≥ 0.0 | `1.0` | 0 is black, 1 unchanged, 2 twice as bright. The same as CSS ``brightness()``. |
| `contrast` | float | ≥ 0.0 | `1.0` | 0 is flat grey, 1 unchanged, 2 twice the contrast. The same as CSS ``contrast()``. |
| `saturation` | float | ≥ 0.0 | `1.0` | 0 is greyscale, 1 unchanged, 2 twice as vivid. The same as CSS ``saturate()``. |

#### `TiffEditConfig` (`target`='tiff')

An edited TIFF (the first page).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `rotate` | 0 / 90 / 180 / 270 |  | `0` | Clockwise rotation in degrees, after the photo's own orientation (EXIF) is applied. |
| `flip` | none / horizontal / vertical |  | `"none"` | Mirror the picture, after rotating. |
| `crop_left` | int | ≥ 0 | `0` | Left edge of the crop, in pixels of the rotated and flipped picture. |
| `crop_top` | int | ≥ 0 | `0` | Top edge of the crop, in pixels of the rotated and flipped picture. |
| `crop_width` | int or None | ≥ 1 | `None` | Width of the crop in pixels. Blank keeps everything right of the left edge. |
| `crop_height` | int or None | ≥ 1 | `None` | Height of the crop in pixels. Blank keeps everything below the top edge. |
| `brightness` | float | ≥ 0.0 | `1.0` | 0 is black, 1 unchanged, 2 twice as bright. The same as CSS ``brightness()``. |
| `contrast` | float | ≥ 0.0 | `1.0` | 0 is flat grey, 1 unchanged, 2 twice the contrast. The same as CSS ``contrast()``. |
| `saturation` | float | ≥ 0.0 | `1.0` | 0 is greyscale, 1 unchanged, 2 twice as vivid. The same as CSS ``saturate()``. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `keep_icc` | bool |  | `True` | Keep the colour profile, so wide-gamut photos keep their colours. |
| `tiff_compression` | none / lzw / deflate / jpeg |  | `"lzw"` | LZW and Deflate are lossless; JPEG is lossy and much smaller. |
| `quality` | int | ≥ 1, ≤ 100 | `92` | Used only with JPEG compression, which also flattens transparency. |

#### `GifFramesConfig` (`target`='gif')

An animated GIF with some of its frames, at a new speed.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `keep` | frame numbers (from 0) or None |  | `None` | Frames to keep, numbered from 0, e.g. "0-9,12". Blank keeps every frame. Kept frames play in their original order. |
| `speed` | float | > 0.0 | `1.0` | Playback speed: 2 plays twice as fast, 0.5 half as fast. Every frame's duration is divided by it. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `optimize` | bool |  | `False` | Drop unused palette entries. |

#### `WebpFramesConfig` (`target`='webp')

An animated WebP with some of its frames, at a new speed.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `keep` | frame numbers (from 0) or None |  | `None` | Frames to keep, numbered from 0, e.g. "0-9,12". Blank keeps every frame. Kept frames play in their original order. |
| `speed` | float | > 0.0 | `1.0` | Playback speed: 2 plays twice as fast, 0.5 half as fast. Every frame's duration is divided by it. |
| `metadata` | keep / remove_location / remove_all |  | `"keep"` | The source's metadata (camera, dates, GPS, titles): "keep", "remove_location" or "remove_all". |
| `keep_icc` | bool |  | `True` | Keep the colour profile, so wide-gamut photos keep their colours. |
| `quality` | int | ≥ 1, ≤ 100 | `92` | Lossy quality, or compression effort when ``lossless``. Frames are re-saved at high quality by default. |
| `lossless` | bool |  | `False` | Lossless compression: exact pixels, usually bigger files. |
| `webp_method` | int | ≥ 0, ≤ 6 | `4` | Encoder effort, 0 (fastest) to 6 (smallest). |

#### `FrameStripConfig` (standalone)

Every frame of an animated image as small tiles in one still WebP (a sprite sheet), left to right and top to bottom, for UIs that let people pick frames.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `tile_height` | int | ≥ 16 | `96` | Height of each frame's tile in pixels; widths follow the animation's shape. |
| `columns` | int | ≥ 1 | `10` | Tiles per row. More are used if the strip would otherwise be too tall for WebP. |
| `quality` | int | ≥ 1, ≤ 100 | `80` | 1 (smallest) to 100 (best). |

## 5. Worker shim

```python
import sashimi
from sashimi.config import (
    AUDIO_CONVERT, CONTACT_SHEET, IMAGE_CONVERT, IMAGE_EDIT, IMAGE_FRAMES, IMAGE_RESIZE, VIDEO_CONVERT,
    VIDEO_PREVIEW, VIDEO_TRANSFORM, VIDEO_TRIM, FaviconPackConfig, FrameStripConfig, IcoConfig, Limits,
)

LIMITS = Limits(
    max_pixels=24_000_000, max_video_seconds=600, max_audio_seconds=3600, max_gif_seconds=30,
    max_preview_clips=10, max_frame_bytes=400 * 1024 * 1024, max_animation_bytes=256 * 1024 * 1024,
    max_sheet_pixels=64_000_000,
)
GIF_LIMITS = LIMITS.model_copy(update={"max_frame_bytes": None})

def run_tool(tool_id, src, dst, params, job):
    target = str(job["target_format"]).lower()
    if tool_id in IMAGE_TOOLS:
        sashimi.image.convert(src, dst, IMAGE_CONVERT.from_params(params, target=target), limits=LIMITS)
    elif tool_id in RESIZE_TOOLS:
        sashimi.image.convert(src, dst, IMAGE_RESIZE.from_params(params, target=target), limits=LIMITS)
    elif tool_id in ICO_TOOLS:
        sashimi.image.to_ico(src, dst, IcoConfig.from_params(params), limits=LIMITS)
    elif tool_id in FAVICON_PACK_TOOLS:
        sashimi.image.favicon_pack(src, dst, FaviconPackConfig.from_params(params), limits=LIMITS)
    elif tool_id in EDIT_TOOLS:
        target = sashimi.image.frames_info(src, limits=LIMITS).edit_target
        sashimi.image.edit(src, dst, IMAGE_EDIT.from_params(params, target=target), limits=LIMITS)
    elif tool_id in FRAMES_TOOLS:
        target = sashimi.image.frames_info(src, limits=LIMITS).edit_target
        sashimi.image.select_frames(src, dst, IMAGE_FRAMES.from_params(params, target=target), limits=LIMITS)
    elif tool_id in FRAME_STRIP_TOOLS:
        layout = sashimi.image.frame_strip(src, dst, FrameStripConfig.from_params(params), limits=LIMITS)
        job_result = {"frame_strip": layout.model_dump(), "frames": sashimi.image.frames_info(src, limits=LIMITS).model_dump()}
    elif tool_id in VIDEO_TOOLS:
        limits = GIF_LIMITS if target == "gif" else LIMITS
        route = sashimi.video.convert(src, dst, VIDEO_CONVERT.from_params(params, target=target), limits=limits)
    elif tool_id in PREVIEW_TOOLS:
        sashimi.video.preview(src, dst, VIDEO_PREVIEW.from_params(params, target=target), limits=LIMITS)
    elif tool_id in TRANSFORM_TOOLS:
        sashimi.video.transform(src, dst, VIDEO_TRANSFORM.from_params(params, target=target), limits=LIMITS)
    elif tool_id in TRIM_TOOLS:
        sashimi.video.trim(src, dst, VIDEO_TRIM.from_params(params, container=target), limits=LIMITS)
    elif tool_id in AUDIO_TOOLS:
        sashimi.audio.convert(src, dst, AUDIO_CONVERT.from_params(params, target=target), limits=LIMITS)
    elif tool_id in SHEET_TOOLS:
        sashimi.sheets.render(src, dst, CONTACT_SHEET.from_params(params, target=target),
                              display_name=job.get("filename_clean"), limits=LIMITS)
```

`LIMITS` and `GIF_LIMITS` are §1 item 14's (`GIF_LIMITS` keeps today's GIFs; use `LIMITS` for
them too if the owner prefers a frame budget for GIFs).

`FAVICON_PACK_TOOLS` and `PREVIEW_TOOLS` are the two optional new tools (§1 items 7-8), and
`EDIT_TOOLS`, `FRAMES_TOOLS` and `FRAME_STRIP_TOOLS` the editors (§1 items 10-11); the rest is
what the worker does today. `frames_info` takes the target from the file's content, so an
upload's edits keep its format whatever its name. For a frames job, `frames_info(src).animated`
tells you before queueing whether the file is an animated GIF or WebP; `select_frames` refuses
anything else with `UnsupportedMediaError` ("This image isn't animated."), and `IMAGE_FRAMES`
refuses targets other than gif and webp with `InvalidOptionsError`. `job_result` stands for
however the worker hands data back to the web app.

What this lets you delete: `video_config`, `audio_config` (§6), `make_sheet` (the sheet family
takes `animated` itself; `sheets.render` takes either config), `check_sheet_shape` (§8) and
`PATH_OPTIONS` (no config has a path field, so `header_font_path` etc. are already "Unknown
option"). The worker makes no probe of its own any more.

Notes for the web app:

- `IMAGE_RESIZE` needs a target. For "keep the input's format", map the sniffed source format
  onto jpeg/png/webp/gif/bmp/tiff yourself; HEIC/HEIF has no output model, so send `jpeg`
  (that's what sashimi's CLI does).
- Options for other formats are rejected, not ignored. Your per-target settings classes already
  avoid that, with two exceptions to handle in `SheetParams`: drop `quality` for PNG still sheets
  and GIF animated sheets, as you already drop `sample_duration`/`fps`/`loop` from still sheets.
- `TrimParams` doesn't offer `accurate`; if you add it, the encoder fields come from
  `VIDEO_TRIM.model_for(container=..., accurate=True)`.
- `VIDEO_TRANSFORM.from_params(params, target=...)` lets the explicit `target` win over the
  form's, as `from_params` always does.

## 6. Never encoding past the probed length

Without `duration_seconds`, WebM/MP4 convert, transform, accurate trims and audio convert now pass
`-t` = (probed duration − start + `DURATION_TOLERANCE_SECONDS`), capped at
`limits.max_video_seconds` / `limits.max_audio_seconds` (600 / 3600 s with FileSushi's limits, no
cap by default); whole files longer than the limit still raise `MediaTooLongError` at run time.
The parity tests pin it (`tests/core/test_video_commands.py`, `test_audio_commands.py`), with
FileSushi's limits: e.g. a 103 s video gives `-t 104`, a 3599.5 s audio file `-t 3600`, a 900 s
video from 300 s `-ss 300 -t 600`; without limits the last two give `-t 3600.5` and `-t 601`.

**Why a 1 s tolerance:** I measured `-t <probed>` against no `-t` on generated files. For MP4,
MKV and WebM it lost nothing, but an MPEG-TS file probed at 10.03 s really holds 10.33 s of audio
(the container duration follows the video stream), and `-t 10.03` cut the last 0.3 s of sound. A
second of slack costs nothing in resources and still stops a file whose header understates its
length by more than that. Behaviour versus your `video_config`/`audio_config`: identical except
that up to one extra second is read when the file really is longer than its header says.

GIF and animated WebP always have a length (10 s by default), so their `-t` is unchanged.

## 7. Errors

Same classes, same split: show `UserInputError.user_message`, log `ProcessingError`.

New in the limits round (2026-10-20, last). Every refusal by a limit has `.limit`, the `Limits`
field that refused it; the length refusals read exactly as before at FileSushi's limits.

| Class | When | `user_message` |
|---|---|---|
| `MediaTooLargeError` | an image over `max_pixels` (was "This image is too large to process.") | This image is too large to process: it has 30 megapixels (6000x5000), and the limit is 24. |
| `MediaTooLargeError` | an image over Pillow's own limit (about 179 MP) while `max_pixels` is lower | This image is too large to process: it has more than 179 megapixels, and the limit is 24. |
| `MediaTooLargeError` | an animation over `max_animation_bytes` (was "This animation is too large to edit. Try …") | This animation is too large to edit: its frames would take 300 MB of memory, and the limit is 256 MB. Try one with fewer frames or smaller pictures. |
| `InvalidOptionsError` | a GIF made from a video whose frames exceed `max_frame_bytes` (new; only when FileSushi passes a frame budget for GIFs) | These settings would make the GIF too large to render: its frames would take 593 MB of memory, and the limit is 400 MB. Use a smaller width, a lower frame rate or a shorter length. |
| `InvalidOptionsError` | a GIF preview over `max_frame_bytes` | These settings would make the preview too large to render as a GIF: its frames would take 593 MB of memory, and the limit is 400 MB. Use a smaller size, a lower frame rate, or fewer or shorter clips. |
| `InvalidOptionsError` | an animated sheet over `max_frame_bytes` | These settings would make the animated sheet too large to render: its frames would take 500 MB of memory, and the limit is 400 MB. Use a smaller width, a lower frame rate or shorter clips. |
| `MediaTooLargeError` | a sheet over `max_sheet_pixels` (was "These settings would make the contact sheet too large. Use fewer rows or a smaller width.") | These settings would make the contact sheet too large: it would have 72 megapixels (8000x9000), and the limit is 64. Use fewer rows or a smaller width. (animated sheets: "… the animated sheet too large to render: it would have …") |
| `MediaTooLargeError` | a WebP sheet wider or taller than 16,383 px, a JPEG one than 65,500 (no `.limit`: a format limit) | These settings would make the contact sheet too large for WebP, which can be at most 16383 pixels on a side (2560x20000). Use fewer rows or a smaller width. |
| `InvalidOptionsError` | a preview with more clips than `max_preview_clips` | Previews can have at most 10 clips. |
| `InvalidOptionsError` | any number field given infinity or NaN | Invalid value for duration_seconds: input should be a finite number. |
| (no longer raised) | `FaviconPackConfig` `name`/`short_name` over 100 characters | the name is used |

New earlier in 0.6.0:

| Class | When | `user_message` |
|---|---|---|
| `InvalidOptionsError` | an edit's crop box outside the rotated picture | The crop area must fit inside the picture, which is 400x300 pixels. |
| `InvalidOptionsError` | `crop_width`/`crop_height` 0, or a negative adjustment | Invalid value for crop_width: input should be greater than or equal to 1. / Invalid value for brightness: input should be greater than or equal to 0. |
| `MediaTooLargeError` | an animation over `max_animation_bytes` (edit, frame selection, frame strip) | (see the limits round above) |
| `UnsupportedMediaError` | editing an animated PNG | Animated PNGs can't be edited yet. |
| `UnsupportedMediaError` | `select_frames` on a still image | This image isn't animated. |
| `InvalidOptionsError` | `keep` names a frame the animation doesn't have | Some of the frames to keep aren't in this animation, which has 120 frames. |
| `InvalidOptionsError` | `keep` empty, or a range like "5-2" | Invalid value for keep: keep at least one frame. / Invalid value for keep: 5-2 counts down; write ranges low-high. |
| `InvalidOptionsError` | preview clips totalling over `max_gif_seconds` (`samples` × `sample_duration`), from `Limits.check` and the converter | Previews can be at most 30 seconds long. Use fewer clips or shorter ones. |
| `MediaTooShortError` | a preview of a video under 0.5 s | This video is too short for a preview. |
| `UnsupportedMediaError` | a preview where no clip could be decoded | We couldn't read any frames from this video. The file may be damaged. |
| `InvalidOptionsError` | `FaviconPackConfig` `name`/`short_name` with a line break | Invalid value for name: must be a single line of text. |
| (no longer raised) | a copy trim of a file with text subtitles the container can't hold, e.g. MP4 `mov_text` into MKV (was `InvalidOptionsError` "This video can't be trimmed to MKV without re-encoding. Try MP4 instead.") | the subtitles are converted |
| (no longer raised) | an animated sheet of a video shorter than `sample_duration` but at least 1 s (was `MediaTooShortError`) | one tile of the whole video |
| `InvalidOptionsError` | MP3 `sample_rate` not in `MP3_SAMPLE_RATES`, e.g. 96000 (was `ProcessingError` from ffmpeg) | Invalid value for sample_rate: input should be 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100 or 48000. |
| `InvalidOptionsError` | M4A `sample_rate` not in `AAC_SAMPLE_RATES`, e.g. 192000 (was `ProcessingError`) | Invalid value for sample_rate: input should be 8000, 11025, …, 88200 or 96000. |
| `BinaryNotFoundError` (a `ProcessingError`) | ffprobe or ffmpeg missing, or can't be started (was `UnsupportedMediaError` "This file doesn't look like … we can read." for a configured path that didn't work, and a bare `OSError` for one that wasn't executable) | none: log it |

New or changed in 0.5.0:

| Class | When | `user_message` |
|---|---|---|
| `InvalidOptionsError` | GIF `duration_seconds` over `max_gif_seconds` (from `Limits.check` and the converter since the limits round; `from_params` in 0.5.0) | GIFs can be at most 30 seconds long. |
| `InvalidOptionsError` | animated WebP `duration_seconds` over `max_gif_seconds` (likewise) | Animated WebPs can be at most 30 seconds long. |
| `InvalidOptionsError` | WebM/MP4 `duration_seconds`, or an accurate trim (`end − start` or `duration_seconds`), over `max_video_seconds` (likewise) | Video clips can be at most 10 minutes long. |
| `InvalidOptionsError` | audio `duration_seconds` over `max_audio_seconds` (likewise) | Audio clips can be at most 60 minutes long. |
| `InvalidOptionsError` | rotate 0 and flip none | Choose a rotation or a flip. |
| `InvalidOptionsError` | trim with both end and length | Give an end time or a length, not both. |
| `InvalidOptionsError` | trim end ≤ start | The end time must be after the start time. |
| `InvalidOptionsError` | resize without a size | Set a maximum width or height to resize to. |
| `InvalidOptionsError` | copy trim into a container that can't hold the source's streams (checked before ffmpeg runs; was a `ProcessingError`) | This video can't be trimmed to WEBM without re-encoding. Try MKV instead. (the hint names the first of MKV, MP4, MOV, WEBM that would work, or is left out) |
| `InvalidOptionsError` | sheet settings giving tiles under 16 px wide (checked by the model when `cols` is a number, else at run time) | These settings would make the contact sheet's tiles too small. Use fewer columns or a wider sheet. |
| `InvalidOptionsError` | `animated` sheet with jpeg/png | Animated contact sheets can be WebP or GIF. |
| `InvalidOptionsError` | still sheet with gif | Still contact sheets can be JPEG, PNG or WebP. GIF sheets must be animated. |
| `InvalidOptionsError` | an option the target doesn't use | Unknown option: {field}. |
| `InvalidOptionsError` | missing / unknown format | Missing option: target. / Invalid value for target: must be jpeg, png, webp, gif, bmp or tiff. |
| `MediaTooLargeError` | a video too wide for 16 px-high tiles | This video is too tall or too wide to make a contact sheet. |

Unchanged: everything else in the 0.4.0 table, including `MediaTooLongError` for whole files over
`max_video_seconds` / `max_audio_seconds` ("Videos longer than 10 minutes aren't supported yet. Set
a shorter duration to convert part of it.", with the limit in force). Generic messages keep a leading acronym's case ("Invalid value for crf: input
should be less than or equal to 51.").

Copy-trim rules (from a stream-copy matrix run against ffmpeg 8.1.2): MKV takes any video, audio,
subtitle and attachment except `mov_text`, and no data tracks; MP4 takes any video except VP8 and
ProRes, any audio, `mov_text` subtitles only, no data or attachments; MOV refuses VP8/VP9/AV1,
Opus and FLAC, takes `mov_text` and data tracks (e.g. iPhone timecode); WebM takes only
VP8/VP9/AV1, Opus/Vorbis, WebVTT and attachments. Unknown codecs pass for MKV/MP4/MOV (ffmpeg
decides), so the check never refuses a file that would have worked.

## 8. Contact sheets

- **New in 0.6.0:** with `cols`/`rows` `"auto"`, animated sheets use their own grid, 3x3, or 4x2
  for portrait videos (display aspect ratio under 0.95); explicit numbers win per dimension as
  before. Still sheets keep their table, except that its first step moved from 0.55 to 0.6, so
  9:16 (0.5625) is 8x2 instead of 7x2. Animated time labels are padded by 4 px with the text
  centred on a fixed height, 4 px in from the tile's corner. A video shorter than
  `sample_duration` (and at least 1 s) gets a single tile. Measured on 2026-10-19 for the brief:
  3x3 took 10.4 s against 25.1 s for the old 6x5, with 426 px tiles against about 205 px.

- `sheets.render(src, dst, config, *, display_name=None, header_font=None, timestamp_font=None,
  emoji_dir=None, max_workers=None, scratch_dir=None, on_progress=None)` renders either kind.
  `contact_sheet` and `animated_sheet` are removed.
- Fonts and the emoji folder are arguments, never config fields. The CLI flags and TOML keys
  (`header_font_path`, `timestamp_font_path`, `emoji_dir`) are unchanged and passed through.
- Degenerate shapes: after laying out the grid, tiles under `MIN_TILE_SIZE` (16 px) on either
  side are refused before any frame is decoded: too narrow is the settings' fault
  (`InvalidOptionsError`), too flat is the video's (`MediaTooLargeError`, your wording). With
  default settings and the auto grid, that refuses display aspect ratios above about 27 for still
  sheets and about 13.5 for animated ones (your check refused above 10, for both).
- Very tall videos aren't refused for their shape, since their tiles are full width. They meet
  the existing limits instead: a default still sheet is over 16,383 px tall below an aspect ratio
  of about 0.037 ("These settings would make the contact sheet too large. Use fewer rows or a
  smaller width.", `MediaTooLargeError`), and a default animated sheet passes its 400 MB frame
  budget below about 0.065 ("These settings would make the animated sheet too large to render.
  …", `InvalidOptionsError`). Still sheets between your 0.1 floor and 0.037 now render.

## 9. Image memory

Peak RSS of one `image.convert` in a fresh process inside the worker image (`python:3.14-alpine`,
Pillow wheels, `--cpus 2`), measured with `wait4`. Input: an RGBA PNG with EXIF orientation 6 (so
auto-orient transposes) and a photo-like mix of fractal, noise and gradients. RGBA input is the
worst case: it's the largest decode, and JPEG/BMP outputs (and `preserve_transparency=false`)
flatten it into extra copies. For each target, every option that could cost memory was tried; the
table shows the worst variant. Interpreter plus imports: 35 MB.

| Target (worst variant) | 6 MP | 12 MP | 24 MP | ≈ per MP | Time at 24 MP |
|---|---|---|---|---|---|
| JPEG (any options) | 139 MB | 237 MB | 431 MB | 16 MB | 2–6 s |
| PNG, alpha kept | 89 | 134 | 226 | 8 | 16 s (`optimize`: 156 s) |
| PNG, flattened | 140 | 237 | 431 | 16 | 15 s |
| WebP lossy, default (method 4) | 217 | 368 | 690 | 26 | 8 s |
| WebP lossy, method 6, q100 | 234 | 424 | 800 | 31 | 29 s |
| WebP lossless, method 4 | 277 | 468 | 890 | 34 | 50 s |
| **WebP lossless, method 6** | 376 | 706 | **1,362** | **55** | **167 s** |
| GIF (flattened) | 193 | 299 | 496 | 17 | 10 s |
| BMP | 139 | 236 | 431 | 16 | 2 s |
| TIFF, JPEG compression | 139 | 236 | 430 | 16 | 2 s |
| TIFF, lzw/deflate/none | 89 | 134 | 225 | 8 | 2–8 s |

Rule of thumb: **peak ≈ 40 MB + k × megapixels**, with k = 17 for every non-WebP target, 27 for
lossy WebP (32 at `webp_method` 6) and 35 for lossless WebP (55 at `webp_method` 6). Your 1.5 GB
estimate for lossy WebP with alpha at 24 MP was high: it's 690–800 MB. The real worst case is
lossless WebP at method 6, 1.36 GB, which fits your 1536m limit with ~170 MB to spare (RSS
excludes the output's page cache, which is reclaimable).

Two things beat the timeouts before memory does: lossless WebP at method 6 takes 167 s and PNG
`optimize` 156 s at 24 MP (2 CPUs), both over your 60/120 s image timeouts. If you keep offering
those, cap the pixels for them lower, or the timeout higher. HEIC input wasn't measured
(out of scope for the worker).

## 10. Measured results (sandbox)

**Limits round (2026-10-20, last).** `sh scripts/sandbox_check.sh --skip-long` passed all 45 of
its jobs, every one called with FileSushi's limits from §1 item 14 (`video-to-gif` with the GIF
variant, no frame budget), as the worker will call them. Image 261 MB (Python 3.14, Pillow 13.0.0,
ffmpeg 8.1.2). The three new jobs show a limit refusing and a raised limit passing:

| Job | Time | Peak memory | Peak pids |
|---|---|---|---|
| A 300-frame 640x360 GIF at FileSushi's 256 MiB animation budget (`error.txt`: "…its frames would take 264 MB of memory, and the limit is 256 MB. …") | 0.05 s | 29 MB | 6 |
| The same GIF with `max_animation_bytes` raised to 512 MiB | 13.7 s | 174 MB | 6 |
| A 40 s GIF at FileSushi's 30 s (`error.txt`: "GIFs can be at most 30 seconds long.") | 0.0 s | 26 MB | 5 |
| A 40 s GIF with `max_gif_seconds` raised to 60 (480 px, 12 fps) | 14.7 s | 376 MB | 12 |

The other 41 jobs gave the same outputs as in the editing round, in comparable times (the host
was busier; ±30 % as below). Outside the sandbox, 56 outputs were hashed against the editing
round's commit (`ffdf73d`), calling the converters without limits: 52 are byte-identical, and the
4 that this round changes on purpose differ in their colour profile only (§1 item 16; pixels
identical): PNG and TIFF with `keep_icc=False` from a source with a profile, and a CMYK JPEG to
PNG.

**Editing round (2026-10-20, later).** `sh scripts/sandbox_check.sh --skip-long` passed all 42
of its jobs at the final tree (the image had Python 3.14.8, Pillow 13.0.0 and ffmpeg 8.1.2;
261 MB). The 12 new jobs:

| Job | Time | Peak memory | Peak pids |
|---|---|---|---|
| Edit a 12 MP JPEG (rotate, flip, crop, all three adjustments) | 0.4 s | 212 MB | 6 |
| Edit a 120-frame 480x270 GIF (same edit) | 3.7 s | 47 MB | 6 |
| Edit a 120-frame 480x270 animated WebP (same edit) | 2.9 s | 64 MB | 6 |
| **Adjust a 280-frame 640x360 GIF, at the frame budget (258 MB of frames)** | 12.8 s | 168 MB | 6 |
| **Adjust a 280-frame 640x360 animated WebP, at the frame budget** | 15.1 s | 302 MB | 5 |
| A 300-frame 640x360 GIF, over the budget (`error.txt`) | 0.05 s | 33 MB | 6 |
| Keep 60 of 120 frames at speed 2, GIF / WebP | 3.9 / 1.9 s | 46 / 67 MB | 6 |
| Frame strip of the 120-frame GIF (1710x1152 WebP, 175 KB) | 1.4 s | 52 MB | 6 |
| To MP4, `copy` (103 s 720p H.264 + AAC MKV) | 0.4 s | 76 MB | 6 |
| To MP4, `copy_video` (20 s H.264 + Opus) | 0.5 s | 54 MB | 9 |
| To MP4, `encode` (20 s VP9 + Opus) | 8.7 s | 165 MB | 18 |

For comparison, the existing re-encode of the same 103 s MKV at 640 px took 21.5 s. **The frame
budget:** peak memory is about 40 MB plus 1.1 times the decoded frames for WebP (Pillow's WebP
encoder takes every frame at once), and about 40 MB plus half of them for GIF (frames are held as
palette images, one byte a pixel). At 256 MiB that is about 300 MB for WebP, well inside the
1 GB sandbox and a 1536m worker, and 13-15 s on two CPUs. Re-quantising GIF frames is the slow
step (about 15 ms per 640x360 frame, single-threaded; Pillow's median cut, which keeps every
colour of a frame with up to 256). Outside the sandbox (python3.15 with Pillow 13.0.0), editing a
300-frame 480x270 GIF took 6.1 s and 123 MB, the same WebP 6.4 s and 211 MB; `frames_info` read
them in 36 ms and 2 ms. Raising the budget to 512 MiB would allow about 600 MB and 30 s; FileSushi
can refuse lower with its own check on `frames_info` (width × height × 4 × frames).

Outside the sandbox, 43 outputs were hashed against the baseline (`9bc8329`): the 37 that this
round doesn't change are byte-identical (every image conversion, ICO, favicon pack, GIF, WebP,
WebM, the MP4 re-encodes with a width, crf, start, length, 4:4:4, HEVC, VP9 or GIF source, trims,
remux, rotate, previews, sheets and audio), and the 6 MP4s of H.264 sources with default
settings that now take a copy route differ, as expected (§1 item 9).

**Earlier in the round.** `sh scripts/sandbox_check.sh --skip-long` passed all 30 of its jobs on 2026-10-20 at the then
0.6.0 tree, under `--network none --read-only --pids-limit 128 --cpus 2 --memory 1g`: outputs
valid, nothing written outside `/vault/output`, `/tmp` empty, no network attempts. Image size
260 MB. The script gained five jobs this round: two previews, the favicon pack, ICO with a
background and a whole-file remux. The full script's 31st job, the 10-minute animated-sheet
budget case (`sheet-animated-long`), which `--skip-long` leaves out, last passed at 0.5.1; with
the 3x3 default it now decodes 9 clips instead of 30. Outside the sandbox, 34 conversions were
hashed before and after this round's changes: the 28 that the brief doesn't change were
byte-identical, and the 6 contact sheets it does change differ as expected (§1 items 4-5).

| Job (0.6.0, 2026-10-20) | Time | Peak memory | Peak pids |
|---|---|---|---|
| Video → GIF (defaults) | 4.4 s | 151 MB | 12 |
| GIF → WebM | 0.7 s | 90 MB | 11 |
| WebM, whole 103 s video | 27.8 s | 251 MB | 16 |
| MP4 at 640 px, whole | 17.0 s | 95 MB | 19 |
| Video → animated WebP | 2.0 s | 63 MB | 12 |
| **Preview GIF (defaults: 5 × 2 s, 360 px, 15 fps)** | 4.6 s | 225 MB | 30 |
| **Preview WebP, 10 clips, 9:16 crop** | 8.5 s | 74 MB | 30 |
| **Remux 103 s MKV → MOV** | 0.5 s | 73 MB | 6 |
| Trim 10 s, copy / accurate | 0.3 / 2.7 s | 46 / 157 MB | 6 / 18 |
| Trim into the wrong container (`error.txt`) | 0.1 s | 41 MB | 6 |
| Rotate 90° (103 s) | 30.1 s | 189 MB | 18 |
| Still sheet / animated sheet (ref clip; animated is now 3x3) | 8.1 / 7.5 s | 101 / 189 MB | 28 / 41 |
| Video → MP3; audio → M4A / WAV / OGG / FLAC | 1.4; 2.9 / 1.1 / 3.6 / 0.5 s | ≤ 72 MB | 9 |
| Images (12 MP JPEG → PNG, HEIC → JPEG, …) | ≤ 0.6 s | ≤ 156 MB | ≤ 7 |
| **ICO with background / favicon pack (12 MP photo)** | 0.1 / 3.4 s | 30 / 206 MB | 7 / 6 |

A first version of the preview opened the video once per clip in a single ffmpeg run; the
sandbox measured 80 pids for 5 clips and hit the 128 limit at 10. The shipped version decodes
one clip at a time in a single-threaded ffmpeg and streams the frames into one encoder, so it
peaks at 30 pids whatever the clip count. Outside the sandbox (python3.15, `max_workers=2`, a
20 s 720p clip) the default animated sheet took 4.7-5.4 s against 8.7-9.1 s for the old 6x5,
with half the ffmpeg CPU.

Timings vary by about ±30% between runs on this host; compare jobs with each other rather than
with earlier hand-backs. The audio fixtures are unseeded noise, so their output sizes differ by
a few KB between sandbox runs.

## 11. Design decisions from 0.5.0

These answered FileSushi's 0.5.0 release brief (2026-10-12) and still hold.

1. **Entry points are `ConfigFamily` objects, not a pydantic discriminated union.** Trims and
   sheets are picked by two options (`container` + `accurate`, `animated` + `target`), `jpg`/`tif`
   have to be accepted, and pydantic's tagged unions put the tag in every error location
   ("gif.duration_seconds"). The families give the same `from_params(params, target=...)` call,
   clean messages, and `model_for(...)` for introspection. Plain union aliases cover typing.
2. **Contact sheets are split by format too** (five models), not just still/animated, because
   the brief's "no model has a field it ignores" also covers `quality` on PNG and GIF sheets.
   That's the one extra field to drop in `SheetParams` (§5).
3. **`image.resize` is gone.** With a required target it was `image.convert`; `IMAGE_RESIZE`
   builds the same models with a size required, replacing your `_needs_a_size` validator.
4. **A 1 s tolerance on the probed length** (§6).
5. **Shapes (§8):** no aspect-ratio rule. Tiles under 16 px are refused; very tall videos fall
   to the existing size and frame-memory limits, with their existing messages rather than yours.
6. **Accurate trims over 10 minutes with a given length are `InvalidOptionsError`** (checked by
   the model) instead of `MediaTooLongError`. Copy trims have no length limit, as before.
7. **The trim container check is a rule table** built from an ffmpeg 8.1.2 stream-copy matrix
   (§7), not a dry-run of the muxer: it runs before ffmpeg, as asked, and only refuses
   combinations ffmpeg refused.
8. **Encoder defaults are passed explicitly** (FLAC `-compression_level 5`, JPEG 4:2:0, WebP
   method 4) so the model defaults are the values used; outputs are byte-identical (checked).
9. **Limits were ints** (`MAX_GIF_SECONDS = 30`, not `30.0`), so hint text could use them directly.
   Superseded in the limits round: the limits are `Limits` fields (seconds are floats), and the
   `MAX_*` constants are gone.
10. **The converters don't re-export config classes**: `sashimi.config` is the one import path.

## 12. Python API breaks

0.6.0 (listed in `CHANGELOG.md`, with upgrade steps in `MIGRATION.md`): `video.convert` returns the
MP4 route instead of `None` and copies H.264 when it can (§1 item 9); the MP3 and M4A
`sample_rate` fields are `Literal` choices instead of 8000-192000 ranges (§1), and a missing or
broken ffprobe/ffmpeg raises `BinaryNotFoundError` instead of `UnsupportedMediaError` (§1). The
contact-sheet defaults and short clips (§1 items 4-5) and copy trims with subtitles (§1 item 6)
change outputs or errors. Everything else that broke is in `sashimi.api` and the CLI, which
FileSushi doesn't use. Added for FileSushi: `MP3_SAMPLE_RATES`, `AAC_SAMPLE_RATES`,
`IcoConfig.background_color`, `VIDEO_PREVIEW` with `video.preview`, `FaviconPackConfig` with
`image.favicon_pack`, `MAX_PREVIEW_SAMPLES` (removed again in the limits round) and
`FAVICON_PNG_SIZES`; and in the editing round
`IMAGE_EDIT` with `image.edit`, `IMAGE_FRAMES` with `image.select_frames`, `image.frames_info`
(`FramesInfo`), `FrameStripConfig` with `image.frame_strip` and `plan_frame_strip`
(`FrameStrip`), `image.adjust_colors`, `image.aspect_crop`, `image.crop_box`,
`video.plan_mp4_route`, `video.VideoRoute`, `MediaInfo.pixel_format`, `EDIT_QUALITY`,
`MIN_FRAME_MS`, `CROP_ASPECTS`, `Rotation` and `Flip`.

The limits round (2026-10-20, last; §1 items 14-17): every converter takes `limits=` instead of
enforcing fixed limits, and `image.convert`, `to_ico`, `favicon_pack`, `edit`, `select_frames`,
`frame_strip` and `frames_info` take it instead of `max_pixels=`; the command builders
(`build_convert_command`, `build_trim_command`, `build_transform_command`,
`build_preview_commands`, the audio `build_convert_command`) take `limits=` too, and without it
apply the defaults (no length cap). Removed: `MAX_GIF_SECONDS`, `MAX_VIDEO_SECONDS`,
`MAX_AUDIO_SECONDS`, `MAX_PREVIEW_SAMPLES`, `MAX_FRAME_BYTES`, `MAX_ANIMATION_BYTES`,
`MAX_SHEET_PIXELS`, `MAX_SHEET_HEIGHT`, `ParamsModel.bound_messages`, `WebpImageConfig.optimize`
and the policy bounds of §1 item 17. Added: `Limits`, `video.gif_frame_bytes`,
`UserInputError.limit` (and its keyword-only `limit=`), `WEBP_MAX_SIDE`, `JPEG_MAX_SIDE`,
`GIF_MAX_SIDE`, `MAX_ANIMATION_FPS` and `FLAC_MAX_SAMPLE_RATE`.

0.5.0: the 0.4 config classes are replaced by per-target models and families; fields that a
target ignored now raise "Unknown option"; the `effective_*`/`encoder_target` properties,
`sheets.contact_sheet`, `sheets.animated_sheet`, `image.resize`, `api.make_animated_thumbs*`, the
sheet path fields and `sashimi.core._params` are gone; `DEFAULT_MAX_PIXELS` and `KEYFRAME_NOTE`
moved to `sashimi.config`. Additions: `MediaInfo.streams` (`StreamInfo(type, codec)`),
`video.check_copy_compatible`, `sheets.render`, video → animated WebP and accurate trims.

## 13. Decisions from the editing round (2026-10-20)

1. **One model per output format for edits and frames** (`IMAGE_EDIT`, `IMAGE_FRAMES`), like every
   other family, because each format has its own save settings (JPEG quality and subsampling,
   PNG compression, WebP lossless, …) and "no model has a field its output ignores" still holds.
   The save fields are the image models' own (shared mixins), not copies. The output format is
   `target`, as everywhere in sashimi (formats come from the config, never from the input);
   "edits keep the source's format" is the caller's choice of `target`, which
   `frames_info(src).edit_target` makes from the content, and which the batch API and CLI make
   from the extension, as `resize` does.
2. **The crop aspect is not a config field.** The config holds the box in pixels; an aspect is a
   UI helper that needs the picture's size. `CROP_ASPECTS` and `aspect_crop()` give FileSushi and
   the desktop app the same presets and the same centred box, and the CLI's `--aspect` uses them
   per file. A field would have been one more form option for FileSushi to hide, with rules for
   how it combines with an explicit box.
3. **JPEG and WebP edits default to quality 92** (`EDIT_QUALITY`), JPEG keeping 4:2:0: high enough
   that one edit isn't visibly worse than its source, without the size of 95-100. Pillow can't
   reuse a JPEG's own quantisation tables once the pixels have changed.
4. **Adjustments are CSS's formulas, in sRGB, in the order brightness → contrast → saturate**,
   each clamped, computed with Pillow lookup tables and a colour matrix (no new dependency).
   Combining all three rounds to 8 bits between contrast and saturate, so a pixel can be up to two
   levels from the exact result; doing it exactly needs float images, about six times the memory.
5. **GIF frames keep their colours exactly** when they have at most 256 (every edit of a GIF
   does, since rotate, flip, crop and the adjustments map colours to colours): Pillow's median-cut
   quantiser is exact in that case, and its palette lookup isn't. Frames with transparency get
   disposal 2, so a pixel that turns transparent shows the background, not the previous frame.
6. **Animated PNG is refused, not flattened.** Pillow reads APNG, but the owner's scope was GIF
   and WebP, and silently keeping one frame would lose the animation.
7. **A frame strip from sashimi**, not frames decoded in the browser (§1 item 11).
8. **Frame selection takes `keep` only; the CLI also has `--drop`**, which it turns into `keep`
   per file. One field keeps forms simple; the CLI numbers frames from 1, the config from 0.
9. **What makes "to MP4" re-encode is a value other than the default**, not "a value was sent":
   FileSushi's preset and audio-quality selects always post their defaults, and a separate
   `allow_stream_copy` field would be a route in the form, which the owner ruled out. The cost:
   asking for exactly crf 23 and `veryfast` can't force a re-encode, which would only lose
   quality.
10. **The copy routes keep the re-encode's streams** (first video and audio, no subtitles), rather
    than the lossless trim's (every stream), so the output doesn't depend on the route taken;
    `remux` keeps everything. They also keep the video length limit (`max_video_seconds`), so which videos are
    refused doesn't depend on the codec (owner question in the inbox). Since the limits round
    that limit is FileSushi's `max_video_seconds` (§1 item 14).

## 14. Decisions from the limits round (2026-10-20)

The owner, 2026-10-20: "all limits should be config driven, so there's no reason to add upper
limits to any of sashimi's commands unless there are seriously good reasons to do so. The sashimi
defaults don't need to be aligned with filesushi at all, sashimi just needs to be configurable."
On memory budgets: "this is a *filesushi* concern. Sashimi should have sensible defaults but it
should be possible to run it at higher settings."

1. **One `Limits` object, passed as `limits=`**, rather than a keyword per limit like the old
   `max_pixels`. Eight limits across thirteen functions would have meant a different set of
   keywords on each; one object is set once (FileSushi's `LIMITS`, the desktop app's settings,
   the CLI's flags and `[limits]`) and passed everywhere, and a new limit doesn't change any
   signature. It lives in `sashimi.config`, so the web app can import it.
2. **The limits are checked where the file is known**, with the config-only ones also in
   `Limits.check(config)` for queue time. The models keep only format limits as field bounds, so a
   model describes what sashimi *can* make, and `Limits` what the caller *allows*. The rule is
   pinned by `tests/core/test_config_module.py::FORMAT_UPPER_BOUNDS`.
3. **Defaults protect the machine and leave lengths alone:** 64 MP images and sheets, 1 GiB of
   decoded frames for GIFs, previews and animated sheets, 1 GiB for animated edits, and no limit
   on lengths or clips, because a longer video takes longer but needs no more memory (measured:
   ffmpeg's RSS is flat with length for MP4, WebM and animated WebP). The frame budgets rose from
   400 and 256 MiB because those were set for FileSushi's 1.5 GB workers; at 1 GiB a WebP edit
   peaks near 1.2 GB.
4. **GIFs made from a video got a frame budget.** The brief expected GIF length to protect
   nothing, but ffmpeg keeps every frame of a GIF until its palette is built (§1 item 14), so with
   no length cap a long GIF could exhaust memory. They now count against `max_frame_bytes`, like
   GIF previews already did. This is the one place a default can refuse something the old limits
   allowed: GIFs whose frames exceed 1 GiB (for example 30 s at 1280 px and 15 fps), which needed
   about 1.4 GB.
5. **The old `MAX_*` names are removed, not kept.** A constant called `MAX_VIDEO_SECONDS` that
   sashimi no longer enforces would mislead, and removing it makes FileSushi's upgrade fail at
   import, where the hand-back is read, instead of silently dropping its limits.
6. **Pillow's own size limit no longer caps `max_pixels`.** Pillow refuses images over about
   179 MP by itself; when the caller allows more (or `None`), sashimi raises Pillow's limit for
   the duration of `Image.open`, under a lock, and restores it.
7. **Format limits are per format.** Still sheets were limited to 16,383 px tall in every format
   (WebP's limit); JPEG's is 65,500 and PNG has none, so only WebP keeps 16,383.
8. **Fixed, with reasons:** `fps` ≤ 50 (`MAX_ANIMATION_FPS`: frames under 20 ms play at 100 ms in
   browsers, the rule `MIN_FRAME_MS` already followed); header and label text ≤ 200 px (text
   bitmaps grow with the square of the size, and a 200 px label is already wider than a default
   tile); tiles ≥ 16 px; the codecs' and formats' ranges (§1 item 17). Very slow speeds now stop at
   the longest frame each format stores (655.35 s for GIF), where they crashed.
9. **`WebpImageConfig.optimize` is removed** rather than mapped onto something: libwebp has no
   such switch, and `webp_method` (0-6) already is the effort setting.
10. **`keep_icc=False` passes "no profile" to every writer explicitly,** since Pillow's PNG and
    TIFF writers fall back to the source's profile when none is given. The same fallback had put a
    CMYK profile on RGB PNGs; a CMYK profile now stays only on CMYK output (TIFF).

## 15. Decisions from the metadata round (2026-10-20)

The owner's design (2026-10-20): "In sashimi? keep, of course, but it must be *capable* of full and
partial removal", "capability with permissive defaults", and all three choices ("Both. All.").

1. **One field named `metadata`, values `keep`, `remove_location`, `remove_all`.** The same
   name and values on every model, so a form, the CLI (`--metadata`) and the desktop app use one
   control. `keep_icc` stays separate: a colour profile changes how colours display and isn't
   personal data.
2. **Models whose outputs can't hold source metadata get no field** (GIF and animated WebP from
   video, previews, sheets, ICO, favicon packs, frame strips, BMP): ffmpeg's GIF and WebP muxers
   write none, and a field that changes nothing would break the rule that every field changes the
   output. This deviates from the brief, which listed GIF and WebP from video.
3. **Raw carriers, not Pillow's `info`.** sashimi reads EXIF, XMP, IPTC, comments and PNG text
   from the file's own structure and writes them explicitly; Pillow's writers otherwise copy
   whatever the source left in `info` (a JPEG's comment into a GIF) or in a TIFF's tags. Outputs
   are saved with only the drawing keys left in `info`, and a TIFF opened from a file is copied
   first, so nothing reaches a writer by accident.
4. **EXIF is edited in place** (`core/_tiffmeta.py`): the GPS directory is removed by writing a
   new IFD0 and zeroing the old one and the GPS values, the orientation is patched, the thumbnail
   zeroed. Every other byte keeps its offset, so maker notes survive `keep` and
   `remove_location` in JPEG, PNG and WebP. The same code rewrites TIFF files' tags for `strip`.
5. **Thumbnails go when the picture changes:** after any edit or frame selection, and when the
   orientation is applied. A plain conversion or resize keeps the EXIF thumbnail, which still
   shows the same picture.
6. **`remove_all` keeps the orientation when sashimi doesn't apply it**, as a one-tag EXIF block:
   without it the picture would show sideways.
7. **Icons are converted to sRGB** rather than given the profile or stripped of it: browsers treat
   favicons as sRGB, so a wide-gamut icon is only right if its colours are converted. No field:
   there is nothing an icon should keep.
8. **Opus is capped, not refused** ("one operation per thing the user wants; sashimi picks the
   route"): the user asked for a high-quality OGG, and 256 kb/s is the best mono Opus can do, so
   refusing would only make them pick a lower number. The cap follows the output's channels
   (`mono` gives 1).
9. **Timed metadata tracks are dropped by the removal modes on copies,** since they can hold GPS
   (GoPro, Apple). QuickTime chapter tracks are never copied as streams (the muxer writes the
   chapters from the chapter list), which fixed MOVs with chapters failing to remux.
10. **Bit-exact outputs record no creation time**, even with `keep`: ffmpeg's `+bitexact`, which
    makes outputs repeatable, also stops MP4, MOV and Matroska writing it. Dates in tags (e.g. MP4
    `date`) are kept.
