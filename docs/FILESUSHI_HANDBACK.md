# Hand-back: sashimi 0.6.0 for FileSushi

From the sashimi agent to FileSushi, written 2026-10-19 and updated 2026-10-20 for sashimi 0.6.0,
its first PyPI release (distribution `sashimi-convert`). The release isn't published yet: the
owner builds and uploads it by hand. Until then, a wheel built from sashimi's tree at 0.6.0 is the
same code.

FileSushi vendors an early build of 0.5.1 (`docker/workers/wheels/sashimi_convert-0.5.1-py3-none-any.whl`,
sha256 `b9bd0581…`). Its `config/`, `core/` and `api/` match the later 0.5.1 build; the
differences were in the CLI only. 0.6.0 is that code plus the changes below. Every output
FileSushi makes today is byte-identical, except contact sheets (§1 item 4 and §8): animated sheets
get a new default grid and new time labels, still sheets of 9:16 videos get 8x2, and an animated
sheet of a clip shorter than its clip length now succeeds with one tile.

## 1. What FileSushi must change

1. **Depend on the PyPI release.** Once 0.6.0 is on PyPI, replace the vendored wheel:
   - `pyproject.toml`: drop the `[tool.uv.sources]` entry for `sashimi-convert` (it points at
     `docker/workers/wheels/sashimi_convert-0.5.1-py3-none-any.whl`) and require
     `sashimi-convert==0.6.0`, then re-lock;
   - `docker/workers/sashimi.Dockerfile`: install from the index, wheels only, with hashes, for
     example `pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.txt`
     with `sashimi-convert[heif]==0.6.0` and its dependencies pinned, instead of copying the wheel;
   - delete `docker/workers/wheels/sashimi_convert-0.5.1-py3-none-any.whl`.
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
   `samples` clips (default 5, at most 10) of `sample_duration` seconds (default 2) from across the
   video, skipping `start_offset_percent`/`end_offset_percent` (10/10), scaled to `max_height` (360)
   and optionally `max_width`, at `fps` (15), optionally centre-cropped with `crop` `"9:16"` or
   `"1:1"`; WebP adds `quality` (75). The clips' total is at most 30 s (`InvalidOptionsError`
   "Previews can be at most 30 seconds long. Use fewer clips or shorter ones."). A params class
   with `upstream = VIDEO_PREVIEW` covers it; models in §4. Sandbox numbers are in §10; at most two
   ffmpeg processes run, whatever the clip count.
8. **New tool: favicon pack (optional; the owner's "Full favicon pack (.zip)" next to "ICO
   file").** `sashimi.image.favicon_pack(src, dst, FaviconPackConfig.from_params(params),
   max_pixels=IMAGE_MAX_PIXELS)` writes a .zip (`application/zip`; FileSushi's output name would be
   `…favicons.zip` or similar) with `favicon.ico` (16, 32, 48 by default, `ico_sizes`), PNGs at 16,
   32, 48, 180, 192 and 512 px, `site.webmanifest` and `favicon.html`. The 180 px Apple touch icon
   is filled with `background_color` (default white). Optional `name`, `short_name` (single line,
   at most 100 characters), `theme_color` and `manifest_background_color` go into the manifest
   (and `theme_color` into the HTML); they're written with `json` and escaped HTML, so user text
   can't break either file. Same input and config, same bytes. Paths inside are relative. A params
   class with `upstream = FaviconPackConfig` covers it; model in §4.
9. **Renamed fields: none.** `sashimi.config` renamed nothing. The CLI renamed flags (`thumbs
   --duration` is `--clip-length`, `-o` is the output path), which FileSushi doesn't use. The
   `IcoConfig.fit` description now says "a square" rather than "a transparent square", and the
   animated sheets' `cols`, `rows` and `sample_duration` descriptions mention the new defaults; if
   your forms show model descriptions, they change with them.
10. **Nothing else.** Behaviour FileSushi gets for free:
   - a missing or broken ffprobe or ffmpeg in the worker image raises `BinaryNotFoundError`
     (a `ProcessingError`), so the worker logs "Conversion failed" and writes no `error.txt`.
     0.5.1 raised `UnsupportedMediaError`, telling the user "This file doesn't look like … we can
     read." about a deployment fault;
   - `sashimi.__version__`, which the worker logs, is `0.6.0`;
   - the package metadata names "The FileSushi Authors <admin@filesushi.com>", licence
     `Apache-2.0` (PEP 639), and `github.com/filesushi/sashimi`;
   - the batch API (`sashimi.api`) and the CLI changed a lot (one naming policy, 4-tuple results,
     one JSON report), but FileSushi uses neither.

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
  M4A and OGG `sample_rate`) and `cols`/`rows` (`GridValue`, whose 1-20 bound is inside the type)
  have no `Le` in the field's metadata; read their choices or bounds from `model_json_schema()`;
- the exact `KEYFRAME_NOTE` text;
- `MediaTooLargeError(user_message, detail=…)`, and the same constructor and `.detail` on every
  `UserInputError` subclass (worker `:104,185`).
- new in this round: `VIDEO_PREVIEW` behaves like the other families (`key`, `model_for`, the
  `Le` rule on every bounded number); `IcoConfig.background_color` defaults to `None`;
  `FaviconPackConfig.background_color` to white; `FAVICON_PNG_SIZES` and `MAX_PREVIEW_SAMPLES`;
  animated sheets keep `"auto"` as the `cols` default.

The user messages FileSushi's tests assert ("Unknown option: quality.", "This file doesn't look
like a video we can read.") were already pinned by `tests/core/test_sheet_guards.py` and
`tests/integration/test_video_audio.py`.

## 3. Install

```dockerfile
RUN pip install --no-cache-dir --only-binary=:all: 'sashimi-convert[heif]==0.6.0'
```

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
- Limits that don't depend on the file are field bounds you can read from
  `model_fields[name].metadata` (`annotated_types.Le(le=30)` etc.).
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
| `CONTACT_SHEET` | `animated` (default false), then `target` (still: jpeg default, png, webp; animated: webp default, gif) | `JpegSheetConfig`, `PngSheetConfig`, `WebpSheetConfig`, `AnimatedWebpSheetConfig`, `AnimatedGifSheetConfig` |
| — | — | `IcoConfig.from_params(params)`, `FaviconPackConfig.from_params(params)` (new in 0.6.0) |

Union aliases for type hints: `ImageConvertConfig`, `ImageResizeConfig`, `VideoConvertConfig`,
`TransformConfig`, `TrimConfig`, `PreviewConfig`, `AudioConvertConfig`, `StillSheetConfig`,
`AnimatedSheetConfig`, `SheetConfig`. Every model also keeps `Model.from_params(params)`.

```python
from sashimi.config import VIDEO_CONVERT
VIDEO_CONVERT.model_for(target="mp4").model_fields["crf"]   # default 23, metadata [Ge(0), Le(51)]
VIDEO_CONVERT.from_params({"duration_seconds": 31}, target="gif")
# InvalidOptionsError: user_message "GIFs can be at most 30 seconds long."
```

### Public constants

| Name | Value | Meaning |
|---|---|---|
| `MAX_GIF_SECONDS` | 30 | longest GIF / animated WebP from a video |
| `DEFAULT_GIF_SECONDS` | 10 | GIF / animated WebP length when none is given |
| `MAX_VIDEO_SECONDS` | 600 | longest re-encode (WebM/MP4, rotate, accurate trim) |
| `MAX_AUDIO_SECONDS` | 3600 | longest audio conversion |
| `DURATION_TOLERANCE_SECONDS` | 1 | see §6 |
| `DEFAULT_MAX_PIXELS` | 64,000,000 | default `max_pixels` of `image.convert`/`to_ico` |
| `MAX_SHEET_HEIGHT` / `MAX_SHEET_PIXELS` | 16,383 / 64,000,000 | contact sheet size limits |
| `MAX_FRAME_BYTES` | 400 MiB | decoded frames an animated sheet may hold |
| `MIN_TILE_SIZE` | 16 | smallest sheet tile side, px |
| `MP3_SAMPLE_RATES` | (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000) | what `Mp3AudioConfig.sample_rate` accepts (new in 0.6.0) |
| `AAC_SAMPLE_RATES` | the MP3 rates plus 64000, 88200, 96000 | what `M4aAudioConfig.sample_rate` accepts (new in 0.6.0) |
| `OPUS_SAMPLE_RATES` | (8000, 12000, 16000, 24000, 48000) | what `OggAudioConfig.sample_rate` accepts |
| `MAX_PREVIEW_SAMPLES` | 10 | most clips in a preview (new in 0.6.0) |
| `FAVICON_PNG_SIZES` | (16, 32, 48, 180, 192, 512) | the PNG sizes in a favicon pack (new in 0.6.0) |
| `KEYFRAME_NOTE` | "Trimming without re-encoding is instant and keeps the original quality, but cuts can only land on keyframes, so your clip may start a moment before the time you chose." | |

Also exported: `ParamsModel`, `ConfigFamily`, `RGB`, `RGBA`, `parse_color`, `Resample`,
`X264Preset`, `GridValue`, `PreviewCrop`.

### Every model: fields, bounds, defaults

Generated from `model_fields` (the CLI prints the same, per format, with
`sashimi config defaults <command> [format]`). `target` (and `accurate`/`animated` where shown) is fixed per
model and defaults to the model's own value. Colours accept `"#rrggbb"`/`"#rrggbbaa"`, `"r,g,b"`
or tuples. The limit fields' bound messages are in §7.

#### `JpegImageConfig` (`target`='jpeg')

A JPEG image. Transparency is always flattened onto ``background_color``.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `max_width` | int or None | ≥ 1 | `None` | Scale down to at most this width. ``None`` keeps the source's width. Never upscales; keeps the aspect ratio. |
| `max_height` | int or None | ≥ 1 | `None` | Scale down to at most this height. ``None`` keeps the source's height. |
| `resample` | lanczos / bicubic / bilinear / hamming / box / nearest |  | `"lanczos"` | Filter used when scaling down. |
| `background_color` | colour |  | #ffffff | What transparent areas are flattened onto. |
| `auto_orient` | bool |  | `True` | Apply the EXIF orientation tag (phone photos), so the output looks the way the photo is shown. |
| `keep_exif` | bool |  | `False` | Copy EXIF metadata (camera, date, GPS). Off by default for privacy. |
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
| `keep_exif` | bool |  | `False` | Copy EXIF metadata (camera, date, GPS). Off by default for privacy. |
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
| `keep_exif` | bool |  | `False` | Copy EXIF metadata (camera, date, GPS). Off by default for privacy. |
| `keep_icc` | bool |  | `True` | Keep the colour profile, so wide-gamut photos keep their colours. |
| `quality` | int | ≥ 1, ≤ 100 | `85` | Lossy quality, or compression effort when ``lossless``. |
| `lossless` | bool |  | `False` | Lossless compression: exact pixels, usually bigger files. |
| `webp_method` | int | ≥ 0, ≤ 6 | `4` | Encoder effort, 0 (fastest) to 6 (smallest). |
| `optimize` | bool |  | `True` | Spend extra effort on compression. |

#### `GifImageConfig` (`target`='gif')

A static GIF (the first frame of an animated input). GIFs carry no photo metadata.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `max_width` | int or None | ≥ 1 | `None` | Scale down to at most this width. ``None`` keeps the source's width. Never upscales; keeps the aspect ratio. |
| `max_height` | int or None | ≥ 1 | `None` | Scale down to at most this height. ``None`` keeps the source's height. |
| `resample` | lanczos / bicubic / bilinear / hamming / box / nearest |  | `"lanczos"` | Filter used when scaling down. |
| `background_color` | colour |  | #ffffff | What transparent areas are flattened onto. |
| `auto_orient` | bool |  | `True` | Apply the EXIF orientation tag (phone photos), so the output looks the way the photo is shown. |
| `preserve_transparency` | bool |  | `True` | Keep alpha; when False, transparent areas are flattened onto ``background_color``. |
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
| `keep_exif` | bool |  | `False` | Copy EXIF metadata (camera, date, GPS). Off by default for privacy. |
| `keep_icc` | bool |  | `True` | Keep the colour profile, so wide-gamut photos keep their colours. |
| `tiff_compression` | none / lzw / deflate / jpeg |  | `"lzw"` | LZW and Deflate are lossless; JPEG is lossy and much smaller. |
| `quality` | int | ≥ 1, ≤ 100 | `85` | Used only with JPEG compression, which also flattens transparency. |

#### `GifVideoConfig` (`target`='gif')

A GIF made from part of a video: no sound, a 256-colour palette.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float | > 0, ≤ 30 | `10` | Length in seconds, at most 30. |
| `max_width` | int | ≥ 64, ≤ 3840 | `480` | Never upscales; keeps the aspect ratio. |
| `fps` | int | ≥ 1, ≤ 30 | `12` | Frames per second. |
| `loop` | bool |  | `True` | Loop forever, or play once. |
| `dither` | bayer / sierra2_4a / floyd_steinberg / none |  | `"bayer"` | Bayer gives smaller files; error diffusion (sierra2_4a, floyd_steinberg) smoother gradients. |
| `palette_mode` | diff / full / single |  | `"diff"` | Build the palette from moving parts ("diff"), all pixels ("full"), or per frame ("single"). |

#### `WebpVideoConfig` (`target`='webp')

An animated WebP made from part of a video: no sound.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float | > 0, ≤ 30 | `10` | Length in seconds, at most 30. |
| `max_width` | int | ≥ 64, ≤ 3840 | `480` | Never upscales; keeps the aspect ratio. |
| `fps` | int | ≥ 1, ≤ 30 | `12` | Frames per second. |
| `loop` | bool |  | `True` | Loop forever, or play once. |
| `quality` | int | ≥ 0, ≤ 100 | `75` | Lossy quality, or compression effort when ``lossless``. |
| `lossless` | bool |  | `False` | Lossless frames (much bigger files). |

#### `WebmVideoConfig` (`target`='webm')

A WebM (VP9 + Opus) video.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0, ≤ 600 | `None` | ``None`` converts to the end of the video (which may be at most 10 minutes long). |
| `max_width` | int or None | ≥ 64, ≤ 3840 | `None` | ``None`` keeps the source's width. Never upscales; keeps the aspect ratio. |
| `crf` | int | ≥ 0, ≤ 63 | `34` | Constant quality: lower is better and bigger. |
| `speed` | int | ≥ 0, ≤ 8 | `8` | libvpx ``-cpu-used``: higher is faster and lower quality. |
| `audio_bitrate_kbps` | int | ≥ 32, ≤ 512 | `96` | Opus audio bitrate in kb/s. |
| `mute` | bool |  | `False` | Drop the audio track. |

#### `Mp4VideoConfig` (`target`='mp4')

An MP4 (H.264 + AAC) video.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0, ≤ 600 | `None` | ``None`` converts to the end of the video (which may be at most 10 minutes long). |
| `max_width` | int or None | ≥ 64, ≤ 3840 | `None` | ``None`` keeps the source's width. Never upscales; keeps the aspect ratio. |
| `crf` | int | ≥ 0, ≤ 51 | `23` | Constant quality: lower is better and bigger. |
| `preset` | ultrafast / superfast / veryfast / faster / fast / medium / slow / slower / veryslow |  | `"veryfast"` | Slower presets give smaller files. |
| `audio_bitrate_kbps` | int | ≥ 32, ≤ 512 | `128` | AAC audio bitrate in kb/s. |
| `mute` | bool |  | `False` | Drop the audio track. |

#### `GifPreviewConfig` (`target`='gif')

A GIF preview: short clips sampled from across a video, played one after another. No sound.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `samples` | int | ≥ 1, ≤ 10 | `5` | How many clips to take from across the video. Short videos get fewer, so clips never overlap. |
| `sample_duration` | float | > 0, ≤ 10 | `2.0` | Length of each clip in seconds. |
| `start_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `10.0` | Skip this much of the start of the video (intros, logos). |
| `end_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `10.0` | Skip this much of the end of the video (credits). |
| `max_height` | int | ≥ 64, ≤ 2160 | `360` | Scale down to at most this height. Never upscales; keeps the aspect ratio. |
| `max_width` | int or None | ≥ 64, ≤ 3840 | `None` | Also scale down to at most this width. Blank sets no width limit. |
| `crop` | none / 9:16 / 1:1 |  | `"none"` | Crop the centre of the picture to this shape first, e.g. "9:16" for vertical video sites. |
| `fps` | int | ≥ 1, ≤ 30 | `15` | Frames per second. |
| `loop` | bool |  | `True` | Loop forever, or play once. |

#### `WebpPreviewConfig` (`target`='webp')

An animated WebP preview: short clips sampled from across a video, played one after another. No sound.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `samples` | int | ≥ 1, ≤ 10 | `5` | How many clips to take from across the video. Short videos get fewer, so clips never overlap. |
| `sample_duration` | float | > 0, ≤ 10 | `2.0` | Length of each clip in seconds. |
| `start_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `10.0` | Skip this much of the start of the video (intros, logos). |
| `end_offset_percent` | float | ≥ 0.0, ≤ 100.0 | `10.0` | Skip this much of the end of the video (credits). |
| `max_height` | int | ≥ 64, ≤ 2160 | `360` | Scale down to at most this height. Never upscales; keeps the aspect ratio. |
| `max_width` | int or None | ≥ 64, ≤ 3840 | `None` | Also scale down to at most this width. Blank sets no width limit. |
| `crop` | none / 9:16 / 1:1 |  | `"none"` | Crop the centre of the picture to this shape first, e.g. "9:16" for vertical video sites. |
| `fps` | int | ≥ 1, ≤ 30 | `15` | Frames per second. |
| `loop` | bool |  | `True` | Loop forever, or play once. |
| `quality` | int | ≥ 0, ≤ 100 | `75` | Lossy quality, 0 (smallest) to 100 (best). |

#### `Mp4TransformConfig` (`target`='mp4')

Rotate and/or flip a video, re-encoding it as MP4 (H.264 + AAC).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `rotate` | 0 / 90 / 180 / 270 |  | `0` | Clockwise rotation in degrees. |
| `flip` | none / horizontal / vertical |  | `"none"` | Mirror the picture. |
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
| `crf` | int | ≥ 0, ≤ 63 | `34` | Constant quality: lower is better and bigger. |
| `speed` | int | ≥ 0, ≤ 8 | `8` | libvpx ``-cpu-used``: higher is faster and lower quality. |
| `audio_bitrate_kbps` | int | ≥ 32, ≤ 512 | `96` | Opus audio bitrate in kb/s. |
| `mute` | bool |  | `False` | Drop the audio track. |

#### `CopyTrimConfig` (`accurate`=False)

A lossless trim: streams are copied into `container`, so the cut is instant but starts at the keyframe at or before `start_seconds` (see :data:`KEYFRAME_NOTE`). The container must be able to hold the source's codecs.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where the clip starts, in seconds. |
| `end_seconds` | float or None | > 0 | `None` | ``None`` (with no ``duration_seconds``) trims to the end of the video. |
| `duration_seconds` | float or None | > 0 | `None` | The clip's length, instead of ``end_seconds``. |
| `container` | mkv / mp4 / webm / mov |  | **required** | The output container; it must be able to hold the source's codecs. |

#### `H264TrimConfig` (`accurate`=True)

A frame-accurate trim, re-encoded as H.264 + AAC into MP4, MOV or MKV.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where the clip starts, in seconds. |
| `end_seconds` | float or None | > 0 | `None` | ``None`` (with no ``duration_seconds``) trims to the end of the video. |
| `duration_seconds` | float or None | > 0, ≤ 600 | `None` | The clip's length, instead of ``end_seconds``. |
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
| `duration_seconds` | float or None | > 0, ≤ 600 | `None` | The clip's length, instead of ``end_seconds``. |
| `crf` | int | ≥ 0, ≤ 63 | `34` | Constant quality: lower is better and bigger. |
| `speed` | int | ≥ 0, ≤ 8 | `8` | libvpx ``-cpu-used``: higher is faster and lower quality. |
| `audio_bitrate_kbps` | int | ≥ 32, ≤ 512 | `96` | Opus audio bitrate in kb/s. |
| `mute` | bool |  | `False` | Drop the audio track. |
| `container` | webm |  | `"webm"` | The output container. |

#### `Mp3AudioConfig` (`target`='mp3')

MP3 (LAME).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0, ≤ 3600 | `None` | ``None`` converts to the end of the file (which may be at most 60 minutes long). |
| `mono` | bool |  | `False` | Mix down to one channel. |
| `sample_rate` | 8000 / 11025 / 12000 / 16000 / 22050 / 24000 / 32000 / 44100 / 48000 or None |  | `None` | Output sample rate in Hz. ``None`` keeps the source's if MP3 supports it, and otherwise uses the nearest rate it does. |
| `bitrate_kbps` | int | ≥ 64, ≤ 320 | `192` | Bitrate in kb/s. |

#### `M4aAudioConfig` (`target`='m4a')

M4A (AAC).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0, ≤ 3600 | `None` | ``None`` converts to the end of the file (which may be at most 60 minutes long). |
| `mono` | bool |  | `False` | Mix down to one channel. |
| `sample_rate` | 8000 / 11025 / 12000 / 16000 / 22050 / 24000 / 32000 / 44100 / 48000 / 64000 / 88200 / 96000 or None |  | `None` | Output sample rate in Hz. ``None`` keeps the source's if AAC supports it, and otherwise uses the nearest rate it does. |
| `bitrate_kbps` | int | ≥ 64, ≤ 320 | `160` | Bitrate in kb/s. |

#### `OggAudioConfig` (`target`='ogg')

OGG (Opus).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0, ≤ 3600 | `None` | ``None`` converts to the end of the file (which may be at most 60 minutes long). |
| `mono` | bool |  | `False` | Mix down to one channel. |
| `sample_rate` | 8000 / 12000 / 16000 / 24000 / 48000 or None |  | `None` | Output sample rate in Hz. ``None`` keeps the source's if Opus supports it, and otherwise uses 48 kHz. |
| `bitrate_kbps` | int | ≥ 64, ≤ 320 | `128` | Bitrate in kb/s. |

#### `WavAudioConfig` (`target`='wav')

WAV (uncompressed PCM).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0, ≤ 3600 | `None` | ``None`` converts to the end of the file (which may be at most 60 minutes long). |
| `mono` | bool |  | `False` | Mix down to one channel. |
| `sample_rate` | int or None | ≥ 8000, ≤ 192000 | `None` | Output sample rate in Hz. ``None`` keeps the source's. |
| `wav_bit_depth` | 16 / 24 / 32 |  | `16` | PCM sample size in bits. |

#### `FlacAudioConfig` (`target`='flac')

FLAC (lossless).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0, ≤ 3600 | `None` | ``None`` converts to the end of the file (which may be at most 60 minutes long). |
| `mono` | bool |  | `False` | Mix down to one channel. |
| `sample_rate` | int or None | ≥ 8000, ≤ 192000 | `None` | Output sample rate in Hz. ``None`` keeps the source's. |
| `flac_compression_level` | int | ≥ 0, ≤ 12 | `5` | 0 is fastest, 12 smallest; lossless either way. |

#### `JpegSheetConfig` (`target`='jpeg', `animated`=False)

A still contact sheet saved as JPEG.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `cols` | int or auto | ≥ 1, ≤ 20 | `"auto"` | Columns, 1-20, or "auto" to choose from the video's aspect ratio. |
| `rows` | int or auto | ≥ 1, ≤ 20 | `"auto"` | Rows, 1-20, or "auto". |
| `gap` | int | ≥ 0, ≤ 100 | `5` | Pixels between tiles. |
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
| `width` | int | ≥ 320, ≤ 8000 | `2560` | Width of the sheet in pixels. |
| `quality` | int | ≥ 1, ≤ 100 | `85` | 1 (smallest) to 100 (best). |

#### `PngSheetConfig` (`target`='png', `animated`=False)

A still contact sheet saved as PNG.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `cols` | int or auto | ≥ 1, ≤ 20 | `"auto"` | Columns, 1-20, or "auto" to choose from the video's aspect ratio. |
| `rows` | int or auto | ≥ 1, ≤ 20 | `"auto"` | Rows, 1-20, or "auto". |
| `gap` | int | ≥ 0, ≤ 100 | `5` | Pixels between tiles. |
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
| `width` | int | ≥ 320, ≤ 8000 | `2560` | Width of the sheet in pixels. |

#### `WebpSheetConfig` (`target`='webp', `animated`=False)

A still contact sheet saved as WebP.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `cols` | int or auto | ≥ 1, ≤ 20 | `"auto"` | Columns, 1-20, or "auto" to choose from the video's aspect ratio. |
| `rows` | int or auto | ≥ 1, ≤ 20 | `"auto"` | Rows, 1-20, or "auto". |
| `gap` | int | ≥ 0, ≤ 100 | `5` | Pixels between tiles. |
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
| `width` | int | ≥ 320, ≤ 8000 | `2560` | Width of the sheet in pixels. |
| `quality` | int | ≥ 1, ≤ 100 | `85` | 1 (smallest) to 100 (best). |

#### `AnimatedWebpSheetConfig` (`target`='webp', `animated`=True)

An animated contact sheet saved as WebP: every tile plays a short clip.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `cols` | int or auto | ≥ 1, ≤ 20 | `"auto"` | Columns, 1-20, or "auto": 3, or 4 for portrait videos. |
| `rows` | int or auto | ≥ 1, ≤ 20 | `"auto"` | Rows, 1-20, or "auto": 3, or 2 for portrait videos. |
| `gap` | int | ≥ 0, ≤ 100 | `5` | Pixels between tiles. |
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
| `width` | int | ≥ 320, ≤ 3840 | `1280` | Approximate width of the sheet in pixels (tiles are rounded to even sizes). |
| `sample_duration` | float | > 0.0, ≤ 10.0 | `2.0` | Length of each tile's clip in seconds. A video shorter than this gets one tile of the whole video. |
| `fps` | int | ≥ 1, ≤ 30 | `12` | Frames per second. |
| `loop` | bool |  | `True` | Loop forever, or play once. |
| `quality` | int | ≥ 1, ≤ 100 | `75` | 1 (smallest) to 100 (best). |

#### `AnimatedGifSheetConfig` (`target`='gif', `animated`=True)

An animated contact sheet saved as GIF: every tile plays a short clip.

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `cols` | int or auto | ≥ 1, ≤ 20 | `"auto"` | Columns, 1-20, or "auto": 3, or 4 for portrait videos. |
| `rows` | int or auto | ≥ 1, ≤ 20 | `"auto"` | Rows, 1-20, or "auto": 3, or 2 for portrait videos. |
| `gap` | int | ≥ 0, ≤ 100 | `5` | Pixels between tiles. |
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
| `width` | int | ≥ 320, ≤ 3840 | `1280` | Approximate width of the sheet in pixels (tiles are rounded to even sizes). |
| `sample_duration` | float | > 0.0, ≤ 10.0 | `2.0` | Length of each tile's clip in seconds. A video shorter than this gets one tile of the whole video. |
| `fps` | int | ≥ 1, ≤ 30 | `12` | Frames per second. |
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

## 5. Worker shim

```python
import sashimi
from sashimi.config import (
    AUDIO_CONVERT, CONTACT_SHEET, IMAGE_CONVERT, IMAGE_RESIZE, VIDEO_CONVERT, VIDEO_PREVIEW, VIDEO_TRANSFORM,
    VIDEO_TRIM, FaviconPackConfig, IcoConfig,
)

IMAGE_MAX_PIXELS = 24_000_000

def run_tool(tool_id, src, dst, params, job):
    target = str(job["target_format"]).lower()
    if tool_id in IMAGE_TOOLS:
        sashimi.image.convert(src, dst, IMAGE_CONVERT.from_params(params, target=target), max_pixels=IMAGE_MAX_PIXELS)
    elif tool_id in RESIZE_TOOLS:
        sashimi.image.convert(src, dst, IMAGE_RESIZE.from_params(params, target=target), max_pixels=IMAGE_MAX_PIXELS)
    elif tool_id in ICO_TOOLS:
        sashimi.image.to_ico(src, dst, IcoConfig.from_params(params), max_pixels=IMAGE_MAX_PIXELS)
    elif tool_id in FAVICON_PACK_TOOLS:
        sashimi.image.favicon_pack(src, dst, FaviconPackConfig.from_params(params), max_pixels=IMAGE_MAX_PIXELS)
    elif tool_id in VIDEO_TOOLS:
        sashimi.video.convert(src, dst, VIDEO_CONVERT.from_params(params, target=target))
    elif tool_id in PREVIEW_TOOLS:
        sashimi.video.preview(src, dst, VIDEO_PREVIEW.from_params(params, target=target))
    elif tool_id in TRANSFORM_TOOLS:
        sashimi.video.transform(src, dst, VIDEO_TRANSFORM.from_params(params, target=target))
    elif tool_id in TRIM_TOOLS:
        sashimi.video.trim(src, dst, VIDEO_TRIM.from_params(params, container=target))
    elif tool_id in AUDIO_TOOLS:
        sashimi.audio.convert(src, dst, AUDIO_CONVERT.from_params(params, target=target))
    elif tool_id in SHEET_TOOLS:
        sashimi.sheets.render(src, dst, CONTACT_SHEET.from_params(params, target=target),
                              display_name=job.get("filename_clean"))
```

`FAVICON_PACK_TOOLS` and `PREVIEW_TOOLS` are the two optional new tools (§1 items 7-8); the
rest is what the worker does today.

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
`-t` = (probed duration − start + `DURATION_TOLERANCE_SECONDS`), capped at 600 / 3600 s; whole
files longer than the limit still raise `MediaTooLongError` at run time. The parity tests pin it
(`tests/core/test_video_commands.py`, `test_audio_commands.py`): e.g. a 103 s video gives `-t 104`,
a 3599.5 s audio file `-t 3600`, a 900 s video from 300 s `-ss 300 -t 600`.

**Why a 1 s tolerance:** I measured `-t <probed>` against no `-t` on generated files. For MP4,
MKV and WebM it lost nothing, but an MPEG-TS file probed at 10.03 s really holds 10.33 s of audio
(the container duration follows the video stream), and `-t 10.03` cut the last 0.3 s of sound. A
second of slack costs nothing in resources and still stops a file whose header understates its
length by more than that. Behaviour versus your `video_config`/`audio_config`: identical except
that up to one extra second is read when the file really is longer than its header says.

GIF and animated WebP always have a length (10 s by default), so their `-t` is unchanged.

## 7. Errors

Same classes, same split: show `UserInputError.user_message`, log `ProcessingError`.

New in 0.6.0:

| Class | When | `user_message` |
|---|---|---|
| `InvalidOptionsError` | preview clips totalling over 30 s (`samples` × `sample_duration`) | Previews can be at most 30 seconds long. Use fewer clips or shorter ones. |
| `InvalidOptionsError` | a GIF preview whose frames would need more than `MAX_FRAME_BYTES` (400 MB), e.g. 10 × 3 s at 30 fps and 720 px | These settings would make the preview too large to render as a GIF. Use a smaller size, a lower frame rate, or fewer or shorter clips. |
| `MediaTooShortError` | a preview of a video under 0.5 s | This video is too short for a preview. |
| `UnsupportedMediaError` | a preview where no clip could be decoded | We couldn't read any frames from this video. The file may be damaged. |
| `InvalidOptionsError` | `FaviconPackConfig` `name`/`short_name` with a line break or over 100 characters | Invalid value for name: must be a single line of text. / … string should have at most 100 characters. |
| (no longer raised) | a copy trim of a file with text subtitles the container can't hold, e.g. MP4 `mov_text` into MKV (was `InvalidOptionsError` "This video can't be trimmed to MKV without re-encoding. Try MP4 instead.") | the subtitles are converted |
| (no longer raised) | an animated sheet of a video shorter than `sample_duration` but at least 1 s (was `MediaTooShortError`) | one tile of the whole video |
| `InvalidOptionsError` | MP3 `sample_rate` not in `MP3_SAMPLE_RATES`, e.g. 96000 (was `ProcessingError` from ffmpeg) | Invalid value for sample_rate: input should be 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100 or 48000. |
| `InvalidOptionsError` | M4A `sample_rate` not in `AAC_SAMPLE_RATES`, e.g. 192000 (was `ProcessingError`) | Invalid value for sample_rate: input should be 8000, 11025, …, 88200 or 96000. |
| `BinaryNotFoundError` (a `ProcessingError`) | ffprobe or ffmpeg missing, or can't be started (was `UnsupportedMediaError` "This file doesn't look like … we can read." for a configured path that didn't work, and a bare `OSError` for one that wasn't executable) | none: log it |

New or changed in 0.5.0:

| Class | When | `user_message` |
|---|---|---|
| `InvalidOptionsError` | GIF `duration_seconds` > 30 (now from `from_params`) | GIFs can be at most 30 seconds long. |
| `InvalidOptionsError` | animated WebP `duration_seconds` > 30 (now from `from_params`) | Animated WebPs can be at most 30 seconds long. |
| `InvalidOptionsError` | WebM/MP4 `duration_seconds` > 600, or an accurate trim longer than 600 s (`end − start` or `duration_seconds`) | Video clips can be at most 10 minutes long. |
| `InvalidOptionsError` | audio `duration_seconds` > 3600 | Audio clips can be at most 60 minutes long. |
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
10 / 60 minutes. Generic messages keep a leading acronym's case ("Invalid value for crf: input
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

`sh scripts/sandbox_check.sh --skip-long` passed all 30 of its jobs on 2026-10-20 at the final
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
9. **Limits are ints** (`MAX_GIF_SECONDS = 30`, not `30.0`), so hint text can use them directly.
10. **The converters don't re-export config classes**: `sashimi.config` is the one import path.

## 12. Python API breaks

0.6.0 (listed in `CHANGELOG.md`, with upgrade steps in `MIGRATION.md`): the MP3 and M4A
`sample_rate` fields are `Literal` choices instead of 8000-192000 ranges (§1), and a missing or
broken ffprobe/ffmpeg raises `BinaryNotFoundError` instead of `UnsupportedMediaError` (§1). The
contact-sheet defaults and short clips (§1 items 4-5) and copy trims with subtitles (§1 item 6)
change outputs or errors. Everything else that broke is in `sashimi.api` and the CLI, which
FileSushi doesn't use. Added for FileSushi: `MP3_SAMPLE_RATES`, `AAC_SAMPLE_RATES`,
`IcoConfig.background_color`, `VIDEO_PREVIEW` with `video.preview`, `FaviconPackConfig` with
`image.favicon_pack`, `MAX_PREVIEW_SAMPLES` and `FAVICON_PNG_SIZES`.

0.5.0: the 0.4 config classes are replaced by per-target models and families; fields that a
target ignored now raise "Unknown option"; the `effective_*`/`encoder_target` properties,
`sheets.contact_sheet`, `sheets.animated_sheet`, `image.resize`, `api.make_animated_thumbs*`, the
sheet path fields and `sashimi.core._params` are gone; `DEFAULT_MAX_PIXELS` and `KEYFRAME_NOTE`
moved to `sashimi.config`. Additions: `MediaInfo.streams` (`StreamInfo(type, codec)`),
`video.check_copy_compatible`, `sheets.render`, video → animated WebP and accurate trims.
