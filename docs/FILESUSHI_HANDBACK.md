# Hand-back: sashimi 0.5.1 for FileSushi

From the sashimi agent to the FileSushi agent, answering the 0.5.0 release brief of 2026-10-12.
Everything is in sashimi's working tree, uncommitted, for Rio to review. Nothing in FileSushi was
touched. The wheel is `dist/sashimi_convert-0.5.1-py3-none-any.whl`.

FileSushi's code matched the brief everywhere I checked (`sashimi_worker.py` line numbers,
`tool_params.py` validators, the `KEYFRAME_NOTE` copy in `TrimParams.hint`); I found no
disagreement to report.

**0.5.1 is a drop-in replacement for 0.5.0**: same API, same command lines, same outputs. It adds:

- **Field descriptions.** Every config field has a user-facing description in
  `model_fields[name].description` and in `Model.model_json_schema()` (pinned by
  `test_every_field_is_described`), usable as form help text. They come from the docstrings
  under each field (`use_attribute_docstrings`), so they need the `.py` sources installed, which a
  normal wheel install has.
- **`cols`/`rows` bounds in the schema.** `GridValue` is now `Annotated[int, Field(ge=1, le=20)] |
  Literal["auto"]`, so the JSON schema shows 1-20; validation and messages are unchanged.

If you integrated against 0.5.0, swap the wheel and nothing else.

## 1. Install

Unchanged from 0.4.0, with the new version:

```dockerfile
RUN pip install --no-cache-dir --only-binary=:all: 'sashimi_convert-0.5.1-py3-none-any.whl[heif]'
```

The web app needs only `pip install sashimi_convert-0.5.1-py3-none-any.whl` (no extras); it
pulls in `pydantic` and `pillow`, but importing `sashimi.config` loads neither Pillow nor anything
that runs ffmpeg.

## 2. The config module: `sashimi.config`

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
- Choice fields that hold ints or bools (`rotate`, `wav_bit_depth`, Opus `sample_rate`,
  `accurate`, `animated`) accept form strings: `"90"`, `"24"`, `"true"`, `"on"`. Ordinary `int`,
  `float` and `bool` fields already did.
- Models are frozen, reject unknown keys, and treat `None`/missing as the default, as before.

### Entry points (one per operation)

Each is a `ConfigFamily` with `from_params(params, **fields)` (explicit fields win over
`params`), `model_for(**discriminators)` (the model class, for reading fields/defaults/bounds
without validating), `models` and `targets`. Everything invalid raises `InvalidOptionsError`.

| Family | Picks the model by | Models |
|---|---|---|
| `IMAGE_CONVERT` | `target`: jpeg, png, webp, gif, bmp, tiff (`jpg`, `tif`, any case accepted) | `JpegImageConfig`, `PngImageConfig`, `WebpImageConfig`, `GifImageConfig`, `BmpImageConfig`, `TiffImageConfig` |
| `IMAGE_RESIZE` | `target` (required, same values) | `JpegResizeConfig` … `TiffResizeConfig`: the image models plus "Set a maximum width or height to resize to." |
| `VIDEO_CONVERT` | `target`: gif, webp, webm, mp4 | `GifVideoConfig`, `WebpVideoConfig`, `WebmVideoConfig`, `Mp4VideoConfig` |
| `VIDEO_TRANSFORM` | `target`: mp4 (default), webm | `Mp4TransformConfig`, `WebmTransformConfig` |
| `VIDEO_TRIM` | `container` (mkv, mp4, webm, mov; required) and `accurate` (default false) | `CopyTrimConfig` unless accurate; then `Vp9TrimConfig` for webm, `H264TrimConfig` otherwise |
| `AUDIO_CONVERT` | `target`: mp3, m4a, ogg, wav, flac | `Mp3AudioConfig`, `M4aAudioConfig`, `OggAudioConfig`, `WavAudioConfig`, `FlacAudioConfig` |
| `CONTACT_SHEET` | `animated` (default false), then `target` (still: jpeg default, png, webp; animated: webp default, gif) | `JpegSheetConfig`, `PngSheetConfig`, `WebpSheetConfig`, `AnimatedWebpSheetConfig`, `AnimatedGifSheetConfig` |
| — | — | `IcoConfig.from_params(params)` |

Union aliases for type hints: `ImageConvertConfig`, `ImageResizeConfig`, `VideoConvertConfig`,
`TransformConfig`, `TrimConfig`, `AudioConvertConfig`, `StillSheetConfig`,
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
| `DURATION_TOLERANCE_SECONDS` | 1 | see §4 |
| `DEFAULT_MAX_PIXELS` | 64,000,000 | default `max_pixels` of `image.convert`/`to_ico` |
| `MAX_SHEET_HEIGHT` / `MAX_SHEET_PIXELS` | 16,383 / 64,000,000 | contact sheet size limits |
| `MAX_FRAME_BYTES` | 400 MiB | decoded frames an animated sheet may hold |
| `MIN_TILE_SIZE` | 16 | smallest sheet tile side, px |
| `OPUS_SAMPLE_RATES` | (8000, 12000, 16000, 24000, 48000) | |
| `KEYFRAME_NOTE` | "Trimming without re-encoding is instant and keeps the original quality, but cuts can only land on keyframes, so your clip may start a moment before the time you chose." | |

Also exported: `ParamsModel`, `ConfigFamily`, `RGB`, `RGBA`, `parse_color`, `Resample`,
`X264Preset`, `GridValue`.

### Every model: fields, bounds, defaults

Generated from `model_fields` (the CLI prints the same, per format, with
`sashimi config defaults <command> [format]`). `target` (and `accurate`/`animated` where shown) is fixed per
model and defaults to the model's own value. Colours accept `"#rrggbb"`/`"#rrggbbaa"`, `"r,g,b"`
or tuples. The limit fields' bound messages are in §5.

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
| `sample_rate` | int or None | ≥ 8000, ≤ 192000 | `None` | Output sample rate in Hz. ``None`` keeps the source's. |
| `bitrate_kbps` | int | ≥ 64, ≤ 320 | `192` | Bitrate in kb/s. |

#### `M4aAudioConfig` (`target`='m4a')

M4A (AAC).

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `start_seconds` | float | ≥ 0 | `0.0` | Where to start, in seconds from the beginning. |
| `duration_seconds` | float or None | > 0, ≤ 3600 | `None` | ``None`` converts to the end of the file (which may be at most 60 minutes long). |
| `mono` | bool |  | `False` | Mix down to one channel. |
| `sample_rate` | int or None | ≥ 8000, ≤ 192000 | `None` | Output sample rate in Hz. ``None`` keeps the source's. |
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
| `width` | int | ≥ 320, ≤ 3840 | `1280` | Approximate width of the sheet in pixels (tiles are rounded to even sizes). |
| `sample_duration` | float | > 0.0, ≤ 10.0 | `2.0` | Length of each tile's clip in seconds. |
| `fps` | int | ≥ 1, ≤ 30 | `12` | Frames per second. |
| `loop` | bool |  | `True` | Loop forever, or play once. |
| `quality` | int | ≥ 1, ≤ 100 | `75` | 1 (smallest) to 100 (best). |

#### `AnimatedGifSheetConfig` (`target`='gif', `animated`=True)

An animated contact sheet saved as GIF: every tile plays a short clip.

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
| `width` | int | ≥ 320, ≤ 3840 | `1280` | Approximate width of the sheet in pixels (tiles are rounded to even sizes). |
| `sample_duration` | float | > 0.0, ≤ 10.0 | `2.0` | Length of each tile's clip in seconds. |
| `fps` | int | ≥ 1, ≤ 30 | `12` | Frames per second. |
| `loop` | bool |  | `True` | Loop forever, or play once. |

#### `IcoConfig`

| Field | Type | Bounds | Default | Description |
|---|---|---|---|---|
| `sizes` | tuple of int |  | `(16, 32, 48, 64, 128, 256)` | Icon sizes to include, 16 to 256 pixels. |
| `fit` | pad / crop |  | `"pad"` | Non-square images: centre them on a transparent square ("pad") or crop the centre ("crop"). |
| `resample` | lanczos / bicubic / bilinear / hamming / box / nearest |  | `"lanczos"` | Filter used when scaling. |

## 3. Worker shim

```python
import sashimi
from sashimi.config import (
    AUDIO_CONVERT, CONTACT_SHEET, IMAGE_CONVERT, IMAGE_RESIZE, VIDEO_CONVERT, VIDEO_TRANSFORM, VIDEO_TRIM, IcoConfig,
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
    elif tool_id in VIDEO_TOOLS:
        sashimi.video.convert(src, dst, VIDEO_CONVERT.from_params(params, target=target))
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

What this lets you delete: `video_config`, `audio_config` (§4), `make_sheet` (the sheet family
takes `animated` itself; `sheets.render` takes either config), `check_sheet_shape` (§6) and
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

## 4. Never encoding past the probed length

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

## 5. Errors: what changed

Same classes, same split: show `UserInputError.user_message`, log `ProcessingError`.

New or changed `user_message`s:

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

## 6. Contact sheets

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

## 7. Image memory

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

## 8. Measured results (sandbox)

`sh scripts/sandbox_check.sh` passed: all 25 jobs, under `--network none --read-only
--pids-limit 128 --cpus 2 --memory 1g`, outputs valid, nothing written outside `/vault/output`,
`/tmp` empty, no network attempts. Image size 262 MB. New jobs: `video-trim-wrong-container`
(H.264 into WebM → `error.txt` "This video can't be trimmed to WEBM without re-encoding. Try MKV
instead.", no output) and the sheets now go through `CONTACT_SHEET` + `sheets.render`.

| Job | Time | Peak memory | Peak pids |
|---|---|---|---|
| Video → GIF (defaults) | 7.7 s | 155 MB | 12 |
| GIF → WebM | 0.7 s | 89 MB | 11 |
| WebM, whole 103 s video | 34.8 s | 249 MB | 16 |
| MP4 at 640 px, whole | 19.4 s | 95 MB | 19 |
| Video → animated WebP | 4.3 s | 62 MB | 12 |
| Trim 10 s, copy / accurate | 0.4 / 4.5 s | 46 / 156 MB | 7 / 18 |
| Rotate 90° (103 s) | 34.9 s | 187 MB | 18 |
| Still sheet / animated sheet (ref clip) | 13.0 / 16.7 s | 100 / 169 MB | 28 / 41 |
| Animated sheet, 10 min 1080p, 302 MB | 18.5 s | 184 MB | 41 |
| Video → MP3; audio → M4A / WAV / OGG / FLAC | 1.7; 5.2 / 3.1 / 5.0 / 0.5 s | ≤ 71 MB | 9 |
| Images (12 MP JPEG → PNG, HEIC → JPEG, …) | ≤ 0.9 s | ≤ 156 MB | ≤ 8 |

Times are about 1.5–1.9× the 0.4.0 hand-back's on every job, including ones whose code and
commands didn't change (the GIF command is identical, and the still sheet code path too), so the
host was slower today; compare relative to each other, not to 0.4.0. Memory and pids match 0.4.0.

## 9. Where I departed from the brief, and why

1. **Entry points are `ConfigFamily` objects, not a pydantic discriminated union.** Trims and
   sheets are picked by two options (`container` + `accurate`, `animated` + `target`), `jpg`/`tif`
   have to be accepted, and pydantic's tagged unions put the tag in every error location
   ("gif.duration_seconds"). The families give the same `from_params(params, target=...)` call,
   clean messages, and `model_for(...)` for introspection. Plain union aliases cover typing.
2. **Contact sheets are split by format too** (five models), not just still/animated, because
   the brief's "no model has a field it ignores" also covers `quality` on PNG and GIF sheets.
   That's the one extra field to drop in `SheetParams` (§3).
3. **`image.resize` is gone.** With a required target it was `image.convert`; `IMAGE_RESIZE`
   builds the same models with a size required, replacing your `_needs_a_size` validator.
4. **A 1 s tolerance on the probed length** (§4).
5. **Shapes (§6):** no aspect-ratio rule. Tiles under 16 px are refused; very tall videos fall
   to the existing size and frame-memory limits, with their existing messages rather than yours.
6. **Accurate trims over 10 minutes with a given length are `InvalidOptionsError`** (checked by
   the model) instead of `MediaTooLongError`. Copy trims have no length limit, as before.
7. **The trim container check is a rule table** built from an ffmpeg 8.1.2 stream-copy matrix
   (§5), not a dry-run of the muxer: it runs before ffmpeg, as asked, and only refuses
   combinations ffmpeg refused.
8. **Encoder defaults are passed explicitly** (FLAC `-compression_level 5`, JPEG 4:2:0, WebP
   method 4) so the model defaults are the values used; outputs are byte-identical (checked).
9. **Limits are ints** (`MAX_GIF_SECONDS = 30`, not `30.0`), so hint text can use them directly.
10. **The converters don't re-export config classes**: `sashimi.config` is the one import path.

## 10. Python API breaks

All are listed in `CHANGELOG.md` (0.5.0) with upgrade steps in `MIGRATION.md`. In short: the
0.4 config classes are replaced by per-target models and families; fields that a target ignored
now raise "Unknown option"; the `effective_*`/`encoder_target` properties, `sheets.contact_sheet`,
`sheets.animated_sheet`, `image.resize`, `api.make_animated_thumbs*`, the sheet path fields and
`sashimi.core._params` are gone; `DEFAULT_MAX_PIXELS` and `KEYFRAME_NOTE` moved to
`sashimi.config`. Additions: `MediaInfo.streams` (`StreamInfo(type, codec)`),
`video.check_copy_compatible`, `sheets.render`. 0.5.0 also ships the unreleased video → animated
WebP and accurate trims, which weren't in your 0.4.0 wheel.
