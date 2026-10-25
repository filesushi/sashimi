# sashimi Benchmark Report — `wsl2-desktop-ryzen3600`

## Environment

| Field | Value |
|---|---|
| **Machine ID** | `wsl2-desktop-ryzen3600` |
| **Timestamp (UTC)** | `2026-10-25T17:46:28+00:00` |
| **CPU** | AMD Ryzen 5 3600 6-Core Processor (12 logical threads) |
| **RAM** | 47.0 GiB |
| **Platform** | `Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.39` (WSL2: `True`) |
| **sashimi** | `0.6.1` (git `6f2874c08b`, dirty=`True`) |
| **FFmpeg** | `ffmpeg version 8.1.2 Copyright (c) 2000-2026 the FFmpeg developers` |
| **Python / Pillow** | `3.14.6` / `11.3.0` |
| **Default `max_workers`** | `2` |
| **Host load avg (start → end)** | `[0.6, 0.45, 0.43]` → `[0.6, 0.45, 0.43]` |
| **Total cases** | 256/256 passed in 5355.2s wall (9236.2s CPU) |

## Suite: `probe`

| Task | Input Clip | Codec | Workers | Wall (s) | CPU (s) | CPU/Wall | Peak RSS (MB) | Output Size | Output Details |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `probe` | `FourPeople` | `h264` | 2 | 0.13 | 0.13 | 1.01x | 65.6 | 645 B | probe:mov 1280x720 |
| `metadata-read` | `FourPeople` | `h264` | 2 | 0.21 | 0.17 | 0.82x | 65.6 | 298 B | metadata:none |
| `probe` | `FourPeople` | `hevc` | 2 | 0.11 | 0.11 | 1.01x | 63.7 | 641 B | probe:mov 1280x720 |
| `metadata-read` | `FourPeople` | `hevc` | 2 | 0.15 | 0.15 | 1.01x | 63.5 | 298 B | metadata:none |
| `probe` | `crowd_run` | `h264` | 2 | 0.16 | 0.17 | 1.01x | 67.5 | 643 B | probe:mov 1920x1080 |
| `metadata-read` | `crowd_run` | `h264` | 2 | 0.19 | 0.19 | 1.01x | 68.0 | 298 B | metadata:none |
| `probe` | `crowd_run` | `hevc` | 2 | 0.10 | 0.10 | 1.01x | 63.6 | 639 B | probe:mov 1920x1080 |
| `metadata-read` | `crowd_run` | `hevc` | 2 | 0.14 | 0.14 | 1.01x | 63.4 | 298 B | metadata:none |
| `probe` | `ducks_take_off` | `h264` | 2 | 0.15 | 0.15 | 1.01x | 65.9 | 645 B | probe:mov 1920x1080 |
| `metadata-read` | `ducks_take_off` | `h264` | 2 | 0.20 | 0.19 | 0.98x | 65.8 | 298 B | metadata:none |
| `probe` | `ducks_take_off` | `hevc` | 2 | 0.11 | 0.11 | 0.98x | 63.2 | 640 B | probe:mov 1920x1080 |
| `metadata-read` | `ducks_take_off` | `hevc` | 2 | 0.14 | 0.14 | 0.99x | 63.3 | 298 B | metadata:none |
| `probe` | `in_to_tree` | `h264` | 2 | 0.16 | 0.16 | 0.99x | 67.8 | 643 B | probe:mov 1920x1080 |
| `metadata-read` | `in_to_tree` | `h264` | 2 | 0.20 | 0.20 | 0.99x | 67.6 | 298 B | metadata:none |
| `probe` | `in_to_tree` | `hevc` | 2 | 0.10 | 0.10 | 0.99x | 63.2 | 639 B | probe:mov 1920x1080 |
| `metadata-read` | `in_to_tree` | `hevc` | 2 | 0.14 | 0.13 | 0.99x | 63.5 | 298 B | metadata:none |
| `probe` | `park_joy` | `h264` | 2 | 0.17 | 0.16 | 0.98x | 65.9 | 644 B | probe:mov 1920x1080 |
| `metadata-read` | `park_joy` | `h264` | 2 | 0.18 | 0.18 | 1.00x | 66.0 | 298 B | metadata:none |
| `probe` | `park_joy` | `hevc` | 2 | 0.10 | 0.10 | 1.00x | 63.5 | 639 B | probe:mov 1920x1080 |
| `metadata-read` | `park_joy` | `hevc` | 2 | 0.14 | 0.14 | 1.00x | 63.4 | 298 B | metadata:none |
| `probe` | `red_kayak` | `h264` | 2 | 0.14 | 0.14 | 1.00x | 65.4 | 658 B | probe:mov 1920x1080 |
| `metadata-read` | `red_kayak` | `h264` | 2 | 0.17 | 0.17 | 1.00x | 65.5 | 298 B | metadata:none |
| `probe` | `red_kayak` | `hevc` | 2 | 0.10 | 0.10 | 1.00x | 63.3 | 654 B | probe:mov 1920x1080 |
| `metadata-read` | `red_kayak` | `hevc` | 2 | 0.14 | 0.14 | 1.00x | 63.2 | 298 B | metadata:none |
| `probe` | `rush_hour` | `h264` | 2 | 0.13 | 0.13 | 1.00x | 65.2 | 643 B | probe:mov 1920x1080 |
| `metadata-read` | `rush_hour` | `h264` | 2 | 0.16 | 0.16 | 1.00x | 65.5 | 298 B | metadata:none |
| `probe` | `rush_hour` | `hevc` | 2 | 0.14 | 0.13 | 0.98x | 63.5 | 639 B | probe:mov 1920x1080 |
| `metadata-read` | `rush_hour` | `hevc` | 2 | 0.13 | 0.13 | 1.01x | 63.5 | 298 B | metadata:none |
| `probe` | `noise_1280x720_200MB` | `h264` | 2 | 0.17 | 0.17 | 1.00x | 66.2 | 648 B | probe:mov 1280x720 |
| `metadata-read` | `noise_1280x720_200MB` | `h264` | 2 | 0.21 | 0.21 | 1.00x | 66.0 | 298 B | metadata:none |
| `probe` | `noise_1280x720_200MB_30s` | `h264` | 2 | 0.29 | 0.29 | 1.00x | 67.2 | 643 B | probe:mov 1280x720 |
| `metadata-read` | `noise_1280x720_200MB_30s` | `h264` | 2 | 0.32 | 0.32 | 1.00x | 67.2 | 298 B | metadata:none |
| `probe` | `noise_1280x720_200MB_30s` | `hevc` | 2 | 0.11 | 0.11 | 1.00x | 63.4 | 639 B | probe:mov 1280x720 |
| `metadata-read` | `noise_1280x720_200MB_30s` | `hevc` | 2 | 0.13 | 0.14 | 1.01x | 63.2 | 298 B | metadata:none |
| `probe` | `noise_1920x1080_300MB` | `h264` | 2 | 0.25 | 0.25 | 1.01x | 67.7 | 649 B | probe:mov 1920x1080 |
| `metadata-read` | `noise_1920x1080_300MB` | `h264` | 2 | 0.31 | 0.31 | 1.00x | 67.3 | 298 B | metadata:none |
| `probe` | `noise_1920x1080_300MB_30s` | `h264` | 2 | 0.52 | 0.53 | 1.01x | 71.6 | 644 B | probe:mov 1920x1080 |
| `metadata-read` | `noise_1920x1080_300MB_30s` | `h264` | 2 | 0.58 | 0.58 | 1.00x | 71.8 | 298 B | metadata:none |
| `probe` | `noise_1920x1080_300MB_30s` | `hevc` | 2 | 0.10 | 0.10 | 1.01x | 63.0 | 640 B | probe:mov 1920x1080 |
| `metadata-read` | `noise_1920x1080_300MB_30s` | `hevc` | 2 | 0.13 | 0.13 | 1.01x | 63.2 | 298 B | metadata:none |
| `probe-unfinalized-mkv` | `FourPeople` | `h264` | 2 | 0.29 | 0.29 | 1.01x | 66.2 | 475 B | probe:matroska 1280x720 |
| `probe-unfinalized-mkv` | `ducks_take_off` | `h264` | 2 | 0.34 | 0.34 | 1.02x | 67.9 | 476 B | probe:matroska 1920x1080 |
| `probe-unfinalized-mkv` | `noise_1280x720_200MB_30s` | `h264` | 2 | 0.64 | 0.65 | 1.02x | 77.8 | 475 B | probe:matroska 1280x720 |
| `probe-unfinalized-mkv` | `noise_1920x1080_300MB` | `h264` | 2 | 0.56 | 0.57 | 1.01x | 89.8 | 477 B | probe:matroska 1920x1080 |

## Suite: `sheets`

| Task | Input Clip | Codec | Workers | Wall (s) | CPU (s) | CPU/Wall | Peak RSS (MB) | Output Size | Output Details |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `sheet-static-jpeg` | `FourPeople` | `h264` | 2 | 10.15 | 20.37 | 2.01x | 92.5 | 736.6 KB | JPEG 2560x1335 |
| `sheet-animated-webp` | `FourPeople` | `h264` | 2 | 8.82 | 15.16 | 1.72x | 143.5 | 1.83 MB | WEBP 1280x853 24f |
| `sheet-static-jpeg` | `red_kayak` | `h264` | 2 | 42.24 | 84.53 | 2.00x | 92.5 | 674.4 KB | JPEG 2560x1335 |
| `sheet-animated-webp` | `red_kayak` | `h264` | 2 | 23.89 | 43.12 | 1.80x | 143.7 | 2.34 MB | WEBP 1280x853 24f |
| `sheet-static-jpeg` | `park_joy` | `h264` | 2 | 67.78 | 132.65 | 1.96x | 93.9 | 1.10 MB | JPEG 2560x1335 |
| `sheet-animated-webp` | `park_joy` | `h264` | 2 | 43.62 | 82.67 | 1.90x | 145.2 | 5.76 MB | WEBP 1280x853 24f |
| `sheet-static-jpeg` | `ducks_take_off` | `h264` | 2 | 82.74 | 162.03 | 1.96x | 94.3 | 1.05 MB | JPEG 2560x1335 |
| `sheet-animated-webp` | `ducks_take_off` | `h264` | 2 | 44.09 | 84.59 | 1.92x | 141.6 | 5.37 MB | WEBP 1280x853 24f |
| `sheet-static-jpeg` | `noise_1280x720_200MB_30s` | `h264` | 2 | 53.79 | 107.35 | 2.00x | 93.6 | 1.46 MB | JPEG 2560x1335 |
| `sheet-animated-webp` | `noise_1280x720_200MB_30s` | `h264` | 2 | 26.68 | 50.21 | 1.88x | 146.6 | 8.37 MB | WEBP 1280x853 24f |
| `sheet-static-jpeg` | `noise_1920x1080_300MB` | `h264` | 2 | 229.23 | 451.98 | 1.97x | 115.7 | 1.35 MB | JPEG 2560x1335 |
| `sheet-animated-webp` | `noise_1920x1080_300MB` | `h264` | 2 | 99.63 | 179.24 | 1.80x | 146.0 | 7.91 MB | WEBP 1280x853 24f |
| `sheet-static-webp` | `FourPeople` | `h264` | 2 | 10.37 | 20.56 | 1.98x | 114.1 | 562.8 KB | WEBP 2560x1335 |
| `sheet-static-png` | `FourPeople` | `h264` | 2 | 10.90 | 21.16 | 1.94x | 82.6 | 4.56 MB | PNG 2560x1335 |
| `sheet-animated-gif` | `FourPeople` | `h264` | 2 | 7.61 | 14.43 | 1.90x | 131.5 | 9.34 MB | GIF 1280x853 24f |
| `sheet-static-webp` | `ducks_take_off` | `h264` | 2 | 80.11 | 156.37 | 1.95x | 124.0 | 1.03 MB | WEBP 2560x1335 |
| `sheet-static-png` | `ducks_take_off` | `h264` | 2 | 79.55 | 154.73 | 1.95x | 94.4 | 6.13 MB | PNG 2560x1335 |
| `sheet-animated-gif` | `ducks_take_off` | `h264` | 2 | 40.33 | 79.96 | 1.98x | 131.3 | 20.67 MB | GIF 1280x853 24f |
| `sheet-static-webp` | `noise_1280x720_200MB_30s` | `h264` | 2 | 52.13 | 103.86 | 1.99x | 135.9 | 1.56 MB | WEBP 2560x1335 |
| `sheet-static-png` | `noise_1280x720_200MB_30s` | `h264` | 2 | 52.55 | 104.62 | 1.99x | 89.7 | 8.22 MB | PNG 2560x1335 |
| `sheet-animated-gif` | `noise_1280x720_200MB_30s` | `h264` | 2 | 33.61 | 56.73 | 1.69x | 179.4 | 23.20 MB | GIF 1280x853 24f |

## Suite: `preview`

| Task | Input Clip | Codec | Workers | Wall (s) | CPU (s) | CPU/Wall | Peak RSS (MB) | Output Size | Output Details |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `preview-gif` | `FourPeople` | `h264` | 2 | 8.52 | 12.26 | 1.44x | 87.2 | 11.47 MB | GIF 640x360 120f |
| `preview-webp` | `FourPeople` | `h264` | 2 | 4.67 | 8.17 | 1.75x | 87.0 | 1.81 MB | WEBP 640x360 120f |
| `preview-webp-9x16` | `FourPeople` | `h264` | 2 | 4.33 | 5.94 | 1.37x | 82.1 | 632.7 KB | WEBP 202x360 120f |
| `preview-gif` | `rush_hour` | `h264` | 2 | 33.42 | 38.87 | 1.16x | 100.0 | 23.50 MB | GIF 640x360 150f |
| `preview-webp` | `rush_hour` | `h264` | 2 | 16.84 | 22.69 | 1.35x | 100.1 | 2.44 MB | WEBP 640x360 150f |
| `preview-webp-9x16` | `rush_hour` | `h264` | 2 | 16.77 | 19.32 | 1.15x | 94.5 | 921.8 KB | WEBP 202x360 150f |
| `preview-gif` | `ducks_take_off` | `h264` | 2 | 52.78 | 57.46 | 1.09x | 104.9 | 27.02 MB | GIF 640x360 120f |
| `preview-webp` | `ducks_take_off` | `h264` | 2 | 26.40 | 31.76 | 1.20x | 104.4 | 6.71 MB | WEBP 640x360 120f |
| `preview-webp-9x16` | `ducks_take_off` | `h264` | 2 | 26.26 | 28.76 | 1.10x | 99.2 | 2.23 MB | WEBP 202x360 120f |
| `preview-gif` | `noise_1280x720_200MB_30s` | `h264` | 2 | 41.48 | 58.75 | 1.42x | 198.0 | 37.52 MB | GIF 640x360 150f |
| `preview-webp` | `noise_1280x720_200MB_30s` | `h264` | 2 | 17.29 | 24.53 | 1.42x | 105.6 | 14.48 MB | WEBP 640x360 150f |
| `preview-webp-9x16` | `noise_1280x720_200MB_30s` | `h264` | 2 | 16.85 | 19.64 | 1.17x | 88.7 | 4.94 MB | WEBP 202x360 150f |

## Suite: `gif-methods`

| Task | Input Clip | Codec | Workers | Wall (s) | CPU (s) | CPU/Wall | Peak RSS (MB) | Output Size | Output Details |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `gif-diff-bayer` | `FourPeople` | `h264` | 2 | 2.63 | 5.12 | 1.95x | 105.2 | 3.50 MB | GIF 480x270 60f [encode] |
| `gif-full-bayer` | `FourPeople` | `h264` | 2 | 2.62 | 5.08 | 1.94x | 105.1 | 3.50 MB | GIF 480x270 60f [encode] |
| `gif-single-bayer` | `FourPeople` | `h264` | 2 | 4.22 | 5.52 | 1.31x | 89.2 | 5.44 MB | GIF 480x270 60f [encode] |
| `gif-diff-sierra2_4a` | `FourPeople` | `h264` | 2 | 3.44 | 5.87 | 1.71x | 105.5 | 4.63 MB | GIF 480x270 60f [encode] |
| `gif-diff-floyd` | `FourPeople` | `h264` | 2 | 3.30 | 5.74 | 1.74x | 105.5 | 4.23 MB | GIF 480x270 60f [encode] |
| `gif-diff-none` | `FourPeople` | `h264` | 2 | 2.64 | 5.04 | 1.91x | 105.3 | 3.41 MB | GIF 480x270 60f [encode] |
| `gif-diff-bayer` | `park_joy` | `h264` | 2 | 10.79 | 19.94 | 1.85x | 155.3 | 7.01 MB | GIF 480x270 60f [encode] |
| `gif-full-bayer` | `park_joy` | `h264` | 2 | 11.03 | 20.28 | 1.84x | 156.0 | 7.04 MB | GIF 480x270 60f [encode] |
| `gif-single-bayer` | `park_joy` | `h264` | 2 | 6.56 | 14.39 | 2.19x | 117.3 | 7.09 MB | GIF 480x270 60f [encode] |
| `gif-diff-sierra2_4a` | `park_joy` | `h264` | 2 | 10.86 | 20.74 | 1.91x | 154.7 | 7.21 MB | GIF 480x270 60f [encode] |
| `gif-diff-floyd` | `park_joy` | `h264` | 2 | 10.92 | 20.65 | 1.89x | 152.5 | 7.16 MB | GIF 480x270 60f [encode] |
| `gif-diff-none` | `park_joy` | `h264` | 2 | 10.93 | 19.93 | 1.82x | 154.9 | 6.94 MB | GIF 480x270 60f [encode] |
| `gif-diff-bayer` | `ducks_take_off` | `h264` | 2 | 10.91 | 21.86 | 2.00x | 123.3 | 7.35 MB | GIF 480x270 60f [encode] |
| `gif-full-bayer` | `ducks_take_off` | `h264` | 2 | 11.15 | 22.06 | 1.98x | 122.7 | 7.34 MB | GIF 480x270 60f [encode] |
| `gif-single-bayer` | `ducks_take_off` | `h264` | 2 | 5.63 | 14.25 | 2.53x | 117.5 | 7.48 MB | GIF 480x270 60f [encode] |
| `gif-diff-sierra2_4a` | `ducks_take_off` | `h264` | 2 | 11.02 | 22.75 | 2.06x | 132.4 | 7.46 MB | GIF 480x270 60f [encode] |
| `gif-diff-floyd` | `ducks_take_off` | `h264` | 2 | 11.00 | 22.26 | 2.02x | 126.9 | 7.39 MB | GIF 480x270 60f [encode] |
| `gif-diff-none` | `ducks_take_off` | `h264` | 2 | 10.95 | 21.75 | 1.99x | 123.4 | 7.27 MB | GIF 480x270 60f [encode] |
| `gif-diff-bayer` | `noise_1280x720_200MB` | `h264` | 2 | 22.86 | 39.56 | 1.73x | 325.6 | 9.07 MB | GIF 480x270 60f [encode] |
| `gif-full-bayer` | `noise_1280x720_200MB` | `h264` | 2 | 22.90 | 39.37 | 1.72x | 330.1 | 9.07 MB | GIF 480x270 60f [encode] |
| `gif-single-bayer` | `noise_1280x720_200MB` | `h264` | 2 | 11.97 | 21.88 | 1.83x | 106.2 | 9.08 MB | GIF 480x270 60f [encode] |
| `gif-diff-sierra2_4a` | `noise_1280x720_200MB` | `h264` | 2 | 22.94 | 40.31 | 1.76x | 324.9 | 9.78 MB | GIF 480x270 60f [encode] |
| `gif-diff-floyd` | `noise_1280x720_200MB` | `h264` | 2 | 22.42 | 39.40 | 1.76x | 325.5 | 9.71 MB | GIF 480x270 60f [encode] |
| `gif-diff-none` | `noise_1280x720_200MB` | `h264` | 2 | 22.30 | 38.42 | 1.72x | 326.1 | 9.07 MB | GIF 480x270 60f [encode] |

## Suite: `animations`

| Task | Input Clip | Codec | Workers | Wall (s) | CPU (s) | CPU/Wall | Peak RSS (MB) | Output Size | Output Details |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `gif-diff-bayer` | `FourPeople` | `h264` | 2 | 2.63 | 5.12 | 1.95x | 105.2 | 3.50 MB | GIF 480x270 60f [encode] |
| `webp-480p-5s-12fps` | `FourPeople` | `h264` | 2 | 1.15 | 2.76 | 2.39x | 84.9 | 586.2 KB | WEBP 480x270 60f [encode] |
| `gif-720p-6s-30fps` | `FourPeople` | `h264` | 2 | 6.76 | 12.86 | 1.90x | 105.1 | 54.04 MB | GIF 1280x720 180f [encode] |
| `webp-720p-6s-30fps` | `FourPeople` | `h264` | 2 | 14.72 | 16.69 | 1.13x | 117.4 | 5.38 MB | WEBP 1280x720 180f [encode] |
| `gif-1080p-5s-30fps` | `FourPeople` | `h264` | 2 | 5.78 | 10.96 | 1.90x | 104.7 | 45.98 MB | GIF 1280x720 150f [encode] |
| `webp-1080p-5s-30fps` | `FourPeople` | `h264` | 2 | 12.29 | 13.84 | 1.13x | 115.1 | 4.52 MB | WEBP 1280x720 150f [encode] |
| `gif-diff-bayer` | `park_joy` | `h264` | 2 | 10.79 | 19.94 | 1.85x | 155.3 | 7.01 MB | GIF 480x270 60f [encode] |
| `webp-480p-5s-12fps` | `park_joy` | `h264` | 2 | 4.87 | 9.52 | 1.96x | 111.8 | 1.97 MB | WEBP 480x270 60f [encode] |
| `gif-720p-6s-30fps` | `park_joy` | `h264` | 2 | 49.35 | 73.99 | 1.50x | 262.8 | 143.72 MB | GIF 1280x720 180f [encode] |
| `webp-720p-6s-30fps` | `park_joy` | `h264` | 2 | 24.87 | 35.51 | 1.43x | 232.6 | 38.62 MB | WEBP 1280x720 180f [encode] |
| `gif-1080p-5s-30fps` | `park_joy` | `h264` | 2 | 33.91 | 56.25 | 1.66x | 164.4 | 255.70 MB | GIF 1920x1080 150f [encode] |
| `webp-1080p-5s-30fps` | `park_joy` | `h264` | 2 | 45.95 | 54.69 | 1.19x | 325.0 | 58.65 MB | WEBP 1920x1080 150f [encode] |
| `gif-diff-bayer` | `ducks_take_off` | `h264` | 2 | 10.91 | 21.86 | 2.00x | 123.3 | 7.35 MB | GIF 480x270 60f [encode] |
| `webp-480p-5s-12fps` | `ducks_take_off` | `h264` | 2 | 5.70 | 12.01 | 2.11x | 113.2 | 1.84 MB | WEBP 480x270 60f [encode] |
| `gif-720p-6s-30fps` | `ducks_take_off` | `h264` | 2 | 28.85 | 58.63 | 2.03x | 165.1 | 155.15 MB | GIF 1280x720 180f [encode] |
| `webp-720p-6s-30fps` | `ducks_take_off` | `h264` | 2 | 24.43 | 37.73 | 1.54x | 214.4 | 32.19 MB | WEBP 1280x720 180f [encode] |
| `gif-1080p-5s-30fps` | `ducks_take_off` | `h264` | 2 | 19.96 | 46.85 | 2.35x | 159.1 | 280.84 MB | GIF 1920x1080 150f [encode] |
| `webp-1080p-5s-30fps` | `ducks_take_off` | `h264` | 2 | 43.56 | 54.13 | 1.24x | 304.9 | 51.27 MB | WEBP 1920x1080 150f [encode] |
| `gif-diff-bayer` | `noise_1280x720_200MB_30s` | `h264` | 2 | 22.25 | 32.19 | 1.45x | 289.0 | 8.63 MB | GIF 480x270 60f [encode] |
| `webp-480p-5s-12fps` | `noise_1280x720_200MB_30s` | `h264` | 2 | 4.30 | 8.76 | 2.04x | 104.1 | 3.28 MB | WEBP 480x270 60f [encode] |
| `gif-720p-6s-30fps` | `noise_1280x720_200MB_30s` | `h264` | 2 | 72.97 | 88.50 | 1.21x | 256.4 | 180.43 MB | GIF 1280x720 180f [encode] |
| `webp-720p-6s-30fps` | `noise_1280x720_200MB_30s` | `h264` | 2 | 31.91 | 38.75 | 1.21x | 376.7 | 87.83 MB | WEBP 1280x720 180f [encode] |
| `gif-1080p-5s-30fps` | `noise_1280x720_200MB_30s` | `h264` | 2 | 58.62 | 72.96 | 1.24x | 256.5 | 157.18 MB | GIF 1280x720 150f [encode] |
| `webp-1080p-5s-30fps` | `noise_1280x720_200MB_30s` | `h264` | 2 | 27.98 | 34.36 | 1.23x | 354.7 | 80.52 MB | WEBP 1280x720 150f [encode] |
| `gif-720p-20s-30fps` | `rush_hour` | `h264` | 2 | 54.02 | 92.43 | 1.71x | 165.4 | 290.13 MB | GIF 1280x720 600f [encode] |
| `gif-720p-20s-30fps` | `noise_1280x720_200MB_30s` | `h264` | 2 | 215.35 | 254.01 | 1.18x | 256.5 | 609.02 MB | GIF 1280x720 600f [encode] |
| `gif-1080p-8.8s-30fps` | `park_joy` | `h264` | 2 | 74.27 | 115.95 | 1.56x | 167.1 | 452.72 MB | GIF 1920x1080 264f [encode] |
| `gif-1080p-8.8s-30fps` | `ducks_take_off` | `h264` | 2 | 38.18 | 87.06 | 2.28x | 159.8 | 498.09 MB | GIF 1920x1080 264f [encode] |
| `gif-1080p-8.8s-30fps` | `noise_1920x1080_300MB_30s` | `h264` | 2 | 231.78 | 268.31 | 1.16x | 302.3 | 482.17 MB | GIF 1920x1080 264f [encode] |

## Suite: `transcode`

| Task | Input Clip | Codec | Workers | Wall (s) | CPU (s) | CPU/Wall | Peak RSS (MB) | Output Size | Output Details |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `mp4-auto` | `FourPeople` | `h264` | 2 | 0.28 | 0.28 | 1.02x | 66.4 | 7.93 MB | mov 1280x720 10.02s h264 [copy] |
| `mp4-720p-ultrafast` | `FourPeople` | `h264` | 2 | 1.31 | 3.52 | 2.70x | 105.5 | 3.05 MB | mov 1280x720 5.00s h264 [encode] |
| `mp4-720p-veryfast` | `FourPeople` | `h264` | 2 | 1.84 | 5.74 | 3.12x | 176.3 | 739.4 KB | mov 1280x720 5.00s h264 [encode] |
| `mp4-720p-medium` | `FourPeople` | `h264` | 2 | 5.10 | 11.87 | 2.33x | 269.1 | 1.06 MB | mov 1280x720 5.00s h264 [encode] |
| `webm-720p-speed8` | `FourPeople` | `h264` | 2 | 2.19 | 5.20 | 2.37x | 231.5 | 1.53 MB | matroska 1280x720 5.00s vp9 [encode] |
| `webm-720p-speed4` | `FourPeople` | `h264` | 2 | 8.33 | 16.95 | 2.04x | 232.2 | 1.22 MB | matroska 1280x720 5.00s vp9 [encode] |
| `trim-copy-5s` | `FourPeople` | `h264` | 2 | 0.52 | 0.53 | 1.01x | 66.3 | 5.03 MB | mov 1280x720 6.03s h264 |
| `trim-accurate-5s` | `FourPeople` | `h264` | 2 | 2.00 | 5.40 | 2.70x | 176.6 | 600.2 KB | mov 1280x720 4.00s h264 |
| `mp4-auto` | `park_joy` | `h264` | 2 | 0.56 | 0.46 | 0.82x | 66.6 | 112.62 MB | mov 1920x1080 10.00s h264 [copy] |
| `mp4-720p-ultrafast` | `park_joy` | `h264` | 2 | 5.24 | 12.07 | 2.30x | 129.1 | 22.09 MB | mov 1280x720 5.00s h264 [encode] |
| `mp4-720p-veryfast` | `park_joy` | `h264` | 2 | 5.62 | 17.36 | 3.09x | 200.3 | 8.35 MB | mov 1280x720 5.00s h264 [encode] |
| `mp4-720p-medium` | `park_joy` | `h264` | 2 | 11.96 | 31.72 | 2.65x | 295.7 | 9.27 MB | mov 1280x720 5.00s h264 [encode] |
| `webm-720p-speed8` | `park_joy` | `h264` | 2 | 5.78 | 18.67 | 3.23x | 262.6 | 20.01 MB | matroska 1280x720 5.00s vp9 [encode] |
| `webm-720p-speed4` | `park_joy` | `h264` | 2 | 22.00 | 49.81 | 2.26x | 265.9 | 17.75 MB | matroska 1280x720 5.00s vp9 [encode] |
| `trim-copy-5s` | `park_joy` | `h264` | 2 | 0.70 | 0.71 | 1.01x | 69.2 | 72.56 MB | mov 1920x1080 6.04s h264 |
| `trim-accurate-5s` | `park_joy` | `h264` | 2 | 7.51 | 24.57 | 3.27x | 314.8 | 14.70 MB | mov 1920x1080 4.00s h264 |
| `mp4-auto` | `ducks_take_off` | `h264` | 2 | 0.47 | 0.46 | 0.99x | 67.9 | 154.78 MB | mov 1920x1080 10.00s h264 [copy] |
| `mp4-720p-ultrafast` | `ducks_take_off` | `h264` | 2 | 5.94 | 14.20 | 2.39x | 130.9 | 20.95 MB | mov 1280x720 5.00s h264 [encode] |
| `mp4-720p-veryfast` | `ducks_take_off` | `h264` | 2 | 6.23 | 19.83 | 3.18x | 201.7 | 9.30 MB | mov 1280x720 5.00s h264 [encode] |
| `mp4-720p-medium` | `ducks_take_off` | `h264` | 2 | 13.91 | 35.97 | 2.59x | 297.7 | 10.05 MB | mov 1280x720 5.00s h264 [encode] |
| `webm-720p-speed8` | `ducks_take_off` | `h264` | 2 | 6.34 | 20.79 | 3.28x | 263.2 | 20.67 MB | matroska 1280x720 5.00s vp9 [encode] |
| `webm-720p-speed4` | `ducks_take_off` | `h264` | 2 | 22.48 | 53.43 | 2.38x | 267.2 | 19.99 MB | matroska 1280x720 5.00s vp9 [encode] |
| `trim-copy-5s` | `ducks_take_off` | `h264` | 2 | 0.76 | 0.71 | 0.93x | 68.8 | 98.72 MB | mov 1920x1080 6.04s h264 |
| `trim-accurate-5s` | `ducks_take_off` | `h264` | 2 | 9.06 | 30.56 | 3.37x | 318.5 | 19.79 MB | mov 1920x1080 4.00s h264 |
| `mp4-auto` | `noise_1280x720_200MB` | `h264` | 2 | 0.71 | 0.60 | 0.84x | 72.3 | 199.20 MB | mov 1280x720 9.43s h264 [copy] |
| `mp4-720p-ultrafast` | `noise_1280x720_200MB` | `h264` | 2 | 5.90 | 13.49 | 2.29x | 117.1 | 95.45 MB | mov 1280x720 5.00s h264 [encode] |
| `mp4-720p-veryfast` | `noise_1280x720_200MB` | `h264` | 2 | 6.70 | 25.69 | 3.83x | 182.0 | 79.30 MB | mov 1280x720 5.00s h264 [encode] |
| `mp4-720p-medium` | `noise_1280x720_200MB` | `h264` | 2 | 24.42 | 57.88 | 2.37x | 281.7 | 84.52 MB | mov 1280x720 5.00s h264 [encode] |
| `webm-720p-speed8` | `noise_1280x720_200MB` | `h264` | 2 | 9.94 | 28.10 | 2.83x | 261.0 | 108.64 MB | matroska 1280x720 5.00s vp9 [encode] |
| `webm-720p-speed4` | `noise_1280x720_200MB` | `h264` | 2 | 44.79 | 96.76 | 2.16x | 259.5 | 106.48 MB | matroska 1280x720 5.00s vp9 [encode] |
| `trim-copy-5s` | `noise_1280x720_200MB` | `h264` | 2 | 0.90 | 0.93 | 1.03x | 72.8 | 128.07 MB | mov 1280x720 6.07s h264 |
| `trim-accurate-5s` | `noise_1280x720_200MB` | `h264` | 2 | 7.47 | 23.85 | 3.19x | 180.9 | 63.42 MB | mov 1280x720 4.00s h264 |

## Suite: `codecs`

| Task | Input Clip | Codec | Workers | Wall (s) | CPU (s) | CPU/Wall | Peak RSS (MB) | Output Size | Output Details |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `sheet-static-jpeg` | `FourPeople` | `h264` | 2 | 10.15 | 20.37 | 2.01x | 92.5 | 736.6 KB | JPEG 2560x1335 |
| `sheet-animated-webp` | `FourPeople` | `h264` | 2 | 8.82 | 15.16 | 1.72x | 143.5 | 1.83 MB | WEBP 1280x853 24f |
| `gif-diff-bayer` | `FourPeople` | `h264` | 2 | 2.63 | 5.12 | 1.95x | 105.2 | 3.50 MB | GIF 480x270 60f [encode] |
| `webp-480p-5s-12fps` | `FourPeople` | `h264` | 2 | 1.15 | 2.76 | 2.39x | 84.9 | 586.2 KB | WEBP 480x270 60f [encode] |
| `mp4-auto` | `FourPeople` | `h264` | 2 | 0.28 | 0.28 | 1.02x | 66.4 | 7.93 MB | mov 1280x720 10.02s h264 [copy] |
| `sheet-static-jpeg` | `FourPeople` | `hevc` | 2 | 9.55 | 19.31 | 2.02x | 93.0 | 730.9 KB | JPEG 2560x1335 |
| `sheet-animated-webp` | `FourPeople` | `hevc` | 2 | 8.12 | 14.01 | 1.73x | 143.8 | 1.54 MB | WEBP 1280x853 24f |
| `gif-diff-bayer` | `FourPeople` | `hevc` | 2 | 2.59 | 4.83 | 1.87x | 104.5 | 2.81 MB | GIF 480x270 60f [encode] |
| `webp-480p-5s-12fps` | `FourPeople` | `hevc` | 2 | 1.14 | 2.46 | 2.16x | 87.3 | 507.3 KB | WEBP 480x270 60f [encode] |
| `mp4-auto` | `FourPeople` | `hevc` | 2 | 2.76 | 9.03 | 3.27x | 177.1 | 1.17 MB | mov 1280x720 10.02s h264 [encode] |
| `sheet-static-jpeg` | `park_joy` | `h264` | 2 | 67.78 | 132.65 | 1.96x | 93.9 | 1.10 MB | JPEG 2560x1335 |
| `sheet-animated-webp` | `park_joy` | `h264` | 2 | 43.62 | 82.67 | 1.90x | 145.2 | 5.76 MB | WEBP 1280x853 24f |
| `gif-diff-bayer` | `park_joy` | `h264` | 2 | 10.79 | 19.94 | 1.85x | 155.3 | 7.01 MB | GIF 480x270 60f [encode] |
| `webp-480p-5s-12fps` | `park_joy` | `h264` | 2 | 4.87 | 9.52 | 1.96x | 111.8 | 1.97 MB | WEBP 480x270 60f [encode] |
| `mp4-auto` | `park_joy` | `h264` | 2 | 0.56 | 0.46 | 0.82x | 66.6 | 112.62 MB | mov 1920x1080 10.00s h264 [copy] |
| `sheet-static-jpeg` | `park_joy` | `hevc` | 2 | 88.68 | 173.03 | 1.95x | 98.6 | 1.10 MB | JPEG 2560x1335 |
| `sheet-animated-webp` | `park_joy` | `hevc` | 2 | 47.91 | 91.33 | 1.91x | 146.5 | 5.75 MB | WEBP 1280x853 24f |
| `gif-diff-bayer` | `park_joy` | `hevc` | 2 | 14.52 | 25.80 | 1.78x | 158.0 | 7.01 MB | GIF 480x270 60f [encode] |
| `webp-480p-5s-12fps` | `park_joy` | `hevc` | 2 | 6.70 | 12.63 | 1.89x | 116.6 | 1.97 MB | WEBP 480x270 60f [encode] |
| `mp4-auto` | `park_joy` | `hevc` | 2 | 14.27 | 52.45 | 3.67x | 313.7 | 29.79 MB | mov 1920x1080 10.00s h264 [encode] |
| `sheet-static-jpeg` | `ducks_take_off` | `h264` | 2 | 82.74 | 162.03 | 1.96x | 94.3 | 1.05 MB | JPEG 2560x1335 |
| `sheet-animated-webp` | `ducks_take_off` | `h264` | 2 | 44.09 | 84.59 | 1.92x | 141.6 | 5.37 MB | WEBP 1280x853 24f |
| `gif-diff-bayer` | `ducks_take_off` | `h264` | 2 | 10.91 | 21.86 | 2.00x | 123.3 | 7.35 MB | GIF 480x270 60f [encode] |
| `webp-480p-5s-12fps` | `ducks_take_off` | `h264` | 2 | 5.70 | 12.01 | 2.11x | 113.2 | 1.84 MB | WEBP 480x270 60f [encode] |
| `mp4-auto` | `ducks_take_off` | `h264` | 2 | 0.47 | 0.46 | 0.99x | 67.9 | 154.78 MB | mov 1920x1080 10.00s h264 [copy] |
| `sheet-static-jpeg` | `ducks_take_off` | `hevc` | 2 | 101.94 | 198.55 | 1.95x | 99.2 | 1.05 MB | JPEG 2560x1335 |
| `sheet-animated-webp` | `ducks_take_off` | `hevc` | 2 | 55.10 | 105.60 | 1.92x | 144.7 | 5.32 MB | WEBP 1280x853 24f |
| `gif-diff-bayer` | `ducks_take_off` | `hevc` | 2 | 14.54 | 28.06 | 1.93x | 125.4 | 7.34 MB | GIF 480x270 60f [encode] |
| `webp-480p-5s-12fps` | `ducks_take_off` | `hevc` | 2 | 7.26 | 14.42 | 1.99x | 116.1 | 1.83 MB | WEBP 480x270 60f [encode] |
| `mp4-auto` | `ducks_take_off` | `hevc` | 2 | 15.70 | 63.29 | 4.03x | 315.0 | 35.12 MB | mov 1920x1080 10.00s h264 [encode] |
| `sheet-static-jpeg` | `noise_1280x720_200MB_30s` | `h264` | 2 | 53.79 | 107.35 | 2.00x | 93.6 | 1.46 MB | JPEG 2560x1335 |
| `sheet-animated-webp` | `noise_1280x720_200MB_30s` | `h264` | 2 | 26.68 | 50.21 | 1.88x | 146.6 | 8.37 MB | WEBP 1280x853 24f |
| `gif-diff-bayer` | `noise_1280x720_200MB_30s` | `h264` | 2 | 22.25 | 32.19 | 1.45x | 289.0 | 8.63 MB | GIF 480x270 60f [encode] |
| `webp-480p-5s-12fps` | `noise_1280x720_200MB_30s` | `h264` | 2 | 4.30 | 8.76 | 2.04x | 104.1 | 3.28 MB | WEBP 480x270 60f [encode] |
| `mp4-auto` | `noise_1280x720_200MB_30s` | `h264` | 2 | 0.97 | 0.91 | 0.94x | 74.3 | 199.30 MB | mov 1280x720 30.00s h264 [copy] |
| `sheet-static-jpeg` | `noise_1280x720_200MB_30s` | `hevc` | 2 | 70.36 | 137.76 | 1.96x | 93.5 | 1.43 MB | JPEG 2560x1335 |
| `sheet-animated-webp` | `noise_1280x720_200MB_30s` | `hevc` | 2 | 33.74 | 63.69 | 1.89x | 146.4 | 8.45 MB | WEBP 1280x853 24f |
| `gif-diff-bayer` | `noise_1280x720_200MB_30s` | `hevc` | 2 | 20.36 | 31.28 | 1.54x | 262.2 | 8.66 MB | GIF 480x270 60f [encode] |
| `webp-480p-5s-12fps` | `noise_1280x720_200MB_30s` | `hevc` | 2 | 3.66 | 8.09 | 2.21x | 100.5 | 3.26 MB | WEBP 480x270 60f [encode] |
| `mp4-auto` | `noise_1280x720_200MB_30s` | `hevc` | 2 | 31.08 | 109.66 | 3.53x | 185.1 | 429.40 MB | mov 1280x720 30.00s h264 [encode] |
| `sheet-static-jpeg` | `noise_1920x1080_300MB_30s` | `h264` | 2 | 90.09 | 177.84 | 1.97x | 124.2 | 1.01 MB | JPEG 2560x1335 |
| `sheet-animated-webp` | `noise_1920x1080_300MB_30s` | `h264` | 2 | 40.51 | 78.47 | 1.94x | 141.6 | 5.06 MB | WEBP 1280x853 24f |
| `gif-diff-bayer` | `noise_1920x1080_300MB_30s` | `h264` | 2 | 22.16 | 37.31 | 1.68x | 256.8 | 7.84 MB | GIF 480x270 60f [encode] |
| `webp-480p-5s-12fps` | `noise_1920x1080_300MB_30s` | `h264` | 2 | 7.87 | 14.84 | 1.89x | 142.0 | 1.94 MB | WEBP 480x270 60f [encode] |
| `mp4-auto` | `noise_1920x1080_300MB_30s` | `h264` | 2 | 1.46 | 1.46 | 1.00x | 88.7 | 298.19 MB | mov 1920x1080 30.00s h264 [copy] |
| `sheet-static-jpeg` | `noise_1920x1080_300MB_30s` | `hevc` | 2 | 120.08 | 235.13 | 1.96x | 116.5 | 1.04 MB | JPEG 2560x1335 |
| `sheet-animated-webp` | `noise_1920x1080_300MB_30s` | `hevc` | 2 | 54.60 | 105.25 | 1.93x | 145.5 | 5.76 MB | WEBP 1280x853 24f |
| `gif-diff-bayer` | `noise_1920x1080_300MB_30s` | `hevc` | 2 | 17.98 | 31.87 | 1.77x | 225.6 | 8.02 MB | GIF 480x270 60f [encode] |
| `webp-480p-5s-12fps` | `noise_1920x1080_300MB_30s` | `hevc` | 2 | 5.78 | 11.91 | 2.06x | 133.0 | 2.16 MB | WEBP 480x270 60f [encode] |
| `mp4-auto` | `noise_1920x1080_300MB_30s` | `hevc` | 2 | 67.26 | 226.91 | 3.37x | 329.3 | 883.21 MB | mov 1920x1080 30.00s h264 [encode] |

## Suite: `workers`

| Task | Input Clip | Codec | Workers | Wall (s) | CPU (s) | CPU/Wall | Peak RSS (MB) | Output Size | Output Details |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `sheet-static-jpeg` | `ducks_take_off` | `h264` | 1 | 153.16 | 153.96 | 1.01x | 94.7 | 1.05 MB | JPEG 2560x1335 |
| `sheet-static-jpeg` | `ducks_take_off` | `h264` | 2 | 82.74 | 162.03 | 1.96x | 94.3 | 1.05 MB | JPEG 2560x1335 |
| `sheet-static-jpeg` | `ducks_take_off` | `h264` | 4 | 46.93 | 174.71 | 3.72x | 94.5 | 1.05 MB | JPEG 2560x1335 |
| `sheet-static-jpeg` | `ducks_take_off` | `h264` | 6 | 36.04 | 192.40 | 5.34x | 94.4 | 1.05 MB | JPEG 2560x1335 |
| `sheet-animated-webp` | `ducks_take_off` | `h264` | 1 | 82.25 | 83.77 | 1.02x | 141.7 | 5.37 MB | WEBP 1280x853 24f |
| `sheet-animated-webp` | `ducks_take_off` | `h264` | 2 | 44.09 | 84.59 | 1.92x | 141.6 | 5.37 MB | WEBP 1280x853 24f |
| `sheet-animated-webp` | `ducks_take_off` | `h264` | 4 | 29.19 | 93.93 | 3.22x | 142.1 | 5.37 MB | WEBP 1280x853 24f |
| `sheet-animated-webp` | `ducks_take_off` | `h264` | 6 | 24.17 | 101.64 | 4.21x | 142.3 | 5.37 MB | WEBP 1280x853 24f |
| `gif-720p-6s-30fps` | `ducks_take_off` | `h264` | 1 | 32.62 | 60.89 | 1.87x | 155.5 | 155.15 MB | GIF 1280x720 180f [encode] |
| `gif-720p-6s-30fps` | `ducks_take_off` | `h264` | 2 | 28.85 | 58.63 | 2.03x | 165.1 | 155.15 MB | GIF 1280x720 180f [encode] |
| `gif-720p-6s-30fps` | `ducks_take_off` | `h264` | 4 | 32.99 | 64.90 | 1.97x | 181.9 | 155.15 MB | GIF 1280x720 180f [encode] |
| `gif-720p-6s-30fps` | `ducks_take_off` | `h264` | 6 | 31.83 | 63.87 | 2.01x | 192.4 | 155.15 MB | GIF 1280x720 180f [encode] |
| `mp4-720p-veryfast` | `ducks_take_off` | `h264` | 1 | 10.99 | 18.86 | 1.72x | 171.2 | 9.30 MB | mov 1280x720 5.00s h264 [encode] |
| `mp4-720p-veryfast` | `ducks_take_off` | `h264` | 2 | 6.23 | 19.83 | 3.18x | 201.7 | 9.30 MB | mov 1280x720 5.00s h264 [encode] |
| `mp4-720p-veryfast` | `ducks_take_off` | `h264` | 4 | 4.95 | 26.31 | 5.32x | 230.6 | 9.30 MB | mov 1280x720 5.00s h264 [encode] |
| `mp4-720p-veryfast` | `ducks_take_off` | `h264` | 6 | 4.69 | 38.29 | 8.16x | 263.4 | 9.30 MB | mov 1280x720 5.00s h264 [encode] |
| `sheet-static-jpeg` | `noise_1280x720_200MB_30s` | `h264` | 1 | 104.82 | 105.56 | 1.01x | 93.4 | 1.46 MB | JPEG 2560x1335 |
| `sheet-static-jpeg` | `noise_1280x720_200MB_30s` | `h264` | 2 | 53.79 | 107.35 | 2.00x | 93.6 | 1.46 MB | JPEG 2560x1335 |
| `sheet-static-jpeg` | `noise_1280x720_200MB_30s` | `h264` | 4 | 31.77 | 123.45 | 3.89x | 93.6 | 1.46 MB | JPEG 2560x1335 |
| `sheet-static-jpeg` | `noise_1280x720_200MB_30s` | `h264` | 6 | 23.97 | 132.55 | 5.53x | 94.1 | 1.46 MB | JPEG 2560x1335 |
| `sheet-animated-webp` | `noise_1280x720_200MB_30s` | `h264` | 1 | 48.59 | 49.81 | 1.03x | 145.9 | 8.37 MB | WEBP 1280x853 24f |
| `sheet-animated-webp` | `noise_1280x720_200MB_30s` | `h264` | 2 | 26.68 | 50.21 | 1.88x | 146.6 | 8.37 MB | WEBP 1280x853 24f |
| `sheet-animated-webp` | `noise_1280x720_200MB_30s` | `h264` | 4 | 18.94 | 56.19 | 2.97x | 146.4 | 8.37 MB | WEBP 1280x853 24f |
| `sheet-animated-webp` | `noise_1280x720_200MB_30s` | `h264` | 6 | 15.64 | 59.51 | 3.81x | 146.3 | 8.37 MB | WEBP 1280x853 24f |
| `gif-720p-6s-30fps` | `noise_1280x720_200MB_30s` | `h264` | 1 | 77.87 | 93.70 | 1.20x | 248.5 | 180.43 MB | GIF 1280x720 180f [encode] |
| `gif-720p-6s-30fps` | `noise_1280x720_200MB_30s` | `h264` | 2 | 72.97 | 88.50 | 1.21x | 256.4 | 180.43 MB | GIF 1280x720 180f [encode] |
| `gif-720p-6s-30fps` | `noise_1280x720_200MB_30s` | `h264` | 4 | 75.26 | 91.50 | 1.22x | 262.5 | 180.43 MB | GIF 1280x720 180f [encode] |
| `gif-720p-6s-30fps` | `noise_1280x720_200MB_30s` | `h264` | 6 | 74.46 | 91.08 | 1.22x | 271.6 | 180.43 MB | GIF 1280x720 180f [encode] |
| `mp4-720p-veryfast` | `noise_1280x720_200MB_30s` | `h264` | 1 | 12.98 | 17.32 | 1.33x | 169.5 | 52.84 MB | mov 1280x720 5.00s h264 [encode] |
| `mp4-720p-veryfast` | `noise_1280x720_200MB_30s` | `h264` | 2 | 6.76 | 19.34 | 2.86x | 190.1 | 52.84 MB | mov 1280x720 5.00s h264 [encode] |
| `mp4-720p-veryfast` | `noise_1280x720_200MB_30s` | `h264` | 4 | 5.07 | 22.70 | 4.48x | 219.6 | 52.84 MB | mov 1280x720 5.00s h264 [encode] |
| `mp4-720p-veryfast` | `noise_1280x720_200MB_30s` | `h264` | 6 | 4.74 | 29.22 | 6.16x | 247.6 | 52.84 MB | mov 1280x720 5.00s h264 [encode] |

## Suite: `image`

| Task | Input Clip | Codec | Workers | Wall (s) | CPU (s) | CPU/Wall | Peak RSS (MB) | Output Size | Output Details |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `image-gif-edit` | `FourPeople` | `h264` | 2 | 1.05 | 1.05 | 1.00x | 104.1 | 5.43 MB | GIF 270x480 60f |
| `image-gif-frames` | `FourPeople` | `h264` | 2 | 0.52 | 0.52 | 1.01x | 103.9 | 2.68 MB | GIF 480x270 30f |
| `image-gif-strip` | `FourPeople` | `h264` | 2 | 0.48 | 0.48 | 1.00x | 104.1 | 230.6 KB | WEBP 1710x576 |
| `image-sheet-lossless-webp` | `FourPeople` | `h264` | 2 | 1.63 | 1.65 | 1.01x | 150.1 | 2.43 MB | WEBP 1920x1214 |
| `image-gif-edit` | `ducks_take_off` | `h264` | 2 | 1.24 | 1.23 | 0.99x | 122.7 | 9.03 MB | GIF 270x480 60f |
| `image-gif-frames` | `ducks_take_off` | `h264` | 2 | 0.59 | 0.59 | 1.01x | 122.8 | 3.54 MB | GIF 480x270 30f |
| `image-gif-strip` | `ducks_take_off` | `h264` | 2 | 0.48 | 0.49 | 1.01x | 122.6 | 292.5 KB | WEBP 1710x576 |
| `image-sheet-lossless-webp` | `ducks_take_off` | `h264` | 2 | 1.13 | 1.13 | 1.00x | 154.2 | 3.37 MB | WEBP 1920x1214 |
| `image-gif-edit` | `noise_1280x720_200MB` | `h264` | 2 | 1.26 | 1.25 | 0.99x | 327.0 | 10.50 MB | GIF 270x480 60f |
| `image-gif-frames` | `noise_1280x720_200MB` | `h264` | 2 | 0.63 | 0.64 | 1.01x | 324.7 | 4.55 MB | GIF 480x270 30f |
| `image-gif-strip` | `noise_1280x720_200MB` | `h264` | 2 | 0.54 | 0.55 | 1.01x | 326.9 | 319.9 KB | WEBP 1710x576 |
| `image-sheet-lossless-webp` | `noise_1280x720_200MB` | `h264` | 2 | 0.52 | 0.52 | 1.00x | 159.8 | 5.73 MB | WEBP 1920x1214 |
