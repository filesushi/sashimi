#!/bin/sh
# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=${SASHIMI_SANDBOX_IMAGE:-sashimi-sandbox-check}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
SKIP_LONG=0
[ "${1:-}" = "--skip-long" ] && SKIP_LONG=1

echo "--- building wheel ---"
mkdir -p "$WORK/context/wheels"
(cd "$ROOT" && uv build --wheel --out-dir "$WORK/context/wheels" -q)
cp "$ROOT/scripts/sandbox/Dockerfile" "$ROOT/scripts/sandbox/driver.py" "$WORK/context/"

echo "--- building worker image (python:3.14-alpine + apk ffmpeg + wheels only) ---"
docker build -q -t "$IMAGE" "$WORK/context" >/dev/null
docker image inspect -f 'image size: {{.Size}} bytes' "$IMAGE" | awk '{printf "%s %.0f MB\n", "image size:", $3/1000000}'

echo "--- generating fixtures ---"
F="$WORK/fixtures"
mkdir -p "$F"
gen() { docker run --rm --user "$(id -u):$(id -g)" -v "$F:/f" "$IMAGE" "$@"; }
gen ffmpeg -loglevel error -f lavfi -i testsrc2=s=1280x720:r=25:d=103 -f lavfi -i sine=f=440:d=103 \
    -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p -c:a aac -b:a 128k -f matroska /f/ref.mkv
gen ffmpeg -loglevel error -f lavfi -i anoisesrc=d=180:c=pink:a=0.3:seed=3 -f lavfi -i anoisesrc=d=180:c=brown:a=0.3:seed=7 \
    -filter_complex "[0][1]amerge=inputs=2" -c:a libmp3lame -b:a 192k -f mp3 /f/audio.mp3
gen ffmpeg -loglevel error -f lavfi -i testsrc2=s=4000x3000:d=1 -frames:v 1 -f image2 -c:v mjpeg -q:v 2 /f/photo.jpg
gen python -c "
from PIL import Image
import pillow_heif
img = Image.new('RGBA', (64, 64), (255, 0, 0, 0)); img.paste((0, 0, 255, 255), (0, 0, 32, 64)); img.save('/f/rgba.png')
pillow_heif.from_pillow(Image.open('/f/photo.jpg').convert('RGB')).save('/f/photo.heic', quality=80)
"
gen ffmpeg -loglevel error -f lavfi -i testsrc2=s=1280x720:r=25:d=20 -f lavfi -i sine=f=330:d=20 \
    -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p -c:a libopus -b:a 96k -f matroska /f/ref-opus.mkv
gen ffmpeg -loglevel error -f lavfi -i testsrc2=s=1280x720:r=25:d=20 -f lavfi -i sine=f=330:d=20 \
    -c:v libvpx-vp9 -deadline realtime -cpu-used 8 -c:a libopus -f webm /f/ref-vp9.webm
gen ffmpeg -loglevel error -f lavfi -i testsrc2=s=640x360:r=14:d=20 \
    -vf "split[a][b];[a]palettegen[p];[b][p]paletteuse" -f gif /f/budget.gif
gen ffmpeg -loglevel error -f lavfi -i testsrc2=s=640x360:r=14:d=20 -c:v libwebp_anim -quality 75 -loop 0 \
    -f webp /f/budget.webp
gen ffmpeg -loglevel error -f lavfi -i testsrc2=s=640x360:r=15:d=20 \
    -vf "split[a][b];[a]palettegen[p];[b][p]paletteuse" -f gif /f/over-budget.gif
head -c 200000 /dev/urandom > "$F/junk.bin"
if [ "$SKIP_LONG" = 0 ]; then
    gen ffmpeg -loglevel error -f lavfi -i testsrc2=s=1920x1080:r=25:d=600 -f lavfi -i sine=d=600 \
        -c:v libx264 -preset ultrafast -b:v 3900k -minrate 3900k -maxrate 3900k -bufsize 3900k \
        -x264-params nal-hrd=cbr -pix_fmt yuv420p -c:a aac -b:a 128k -f matroska /f/long.mkv
    ls -l "$F/long.mkv" | awk '{printf "long.mkv: %.0f MB\n", $5/1000000}'
fi

run() {
    job=$1
    out="$WORK/out/$job"
    mkdir -p "$out"
    if docker run --rm --network none --read-only \
        --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m -e HOME=/tmp \
        --cap-drop ALL --security-opt no-new-privileges:true \
        --memory 1g --memory-swap 1g --cpus 2 --pids-limit 128 \
        --user "$(id -u):$(id -g)" \
        -v "$2:/vault/input/original.blob:ro" \
        -v "$out:/vault/output:rw" \
        "$IMAGE" python /opt/sandbox/driver.py "$job"; then
        :
    else
        FAILED="$FAILED $job"
    fi
}

echo "--- running converters in the sandbox (--network none --read-only --pids-limit 128 --cpus 2 --memory 1g) ---"
FAILED=""
run image-png-to-jpeg "$F/rgba.png"
run image-jpeg-to-png "$F/photo.jpg"
run image-to-webp-lossless "$F/rgba.png"
run image-to-gif "$F/rgba.png"
run image-resize "$F/photo.jpg"
run image-to-ico "$F/rgba.png"
run image-to-ico-background "$F/rgba.png"
run favicon-pack "$F/photo.jpg"
run heic-to-jpeg "$F/photo.heic"
run video-to-gif "$F/ref.mkv"
run gif-to-webm "$WORK/out/video-to-gif/result.gif"
run video-to-webm "$F/ref.mkv"
run video-to-mp4-640 "$F/ref.mkv"
run video-to-webp "$F/ref.mkv"
run video-preview-gif "$F/ref.mkv"
run video-preview-webp-portrait "$F/ref.mkv"
run video-remux-to-mov "$F/ref.mkv"
run video-trim "$F/ref.mkv"
run video-trim-accurate "$F/ref.mkv"
run video-trim-wrong-container "$F/ref.mkv"
run video-rotate "$F/ref.mkv"
run sheet-static "$F/ref.mkv"
run sheet-animated "$F/ref.mkv"
[ "$SKIP_LONG" = 0 ] && run sheet-animated-long "$F/long.mkv"
run video-to-mp3 "$F/ref.mkv"
run audio-to-m4a "$F/audio.mp3"
run audio-to-wav "$F/audio.mp3"
run audio-to-ogg "$F/audio.mp3"
run audio-to-flac "$F/audio.mp3"
run junk-to-mp4 "$F/junk.bin"
run probe "$F/ref.mkv"
run image-edit-photo "$F/photo.jpg"
run image-edit-gif "$WORK/out/video-to-gif/result.gif"
run image-edit-webp "$WORK/out/video-to-webp/result.webp"
run image-edit-gif-budget "$F/budget.gif"
run image-edit-webp-budget "$F/budget.webp"
run image-edit-over-budget "$F/over-budget.gif"
run image-edit-raised-budget "$F/over-budget.gif"
run video-to-gif-over-limit "$F/ref.mkv"
run video-to-gif-raised-limit "$F/ref.mkv"
run video-to-gif-60s-full-size "$F/ref.mkv"
[ "$SKIP_LONG" = 0 ] && run video-to-gif-60s-1080p "$F/long.mkv"
run image-frames-gif "$WORK/out/video-to-gif/result.gif"
run image-frames-webp "$WORK/out/video-to-webp/result.webp"
run image-frame-strip "$WORK/out/video-to-gif/result.gif"
run video-to-mp4-copy "$F/ref.mkv"
run video-to-mp4-copy-video "$F/ref-opus.mkv"
run video-to-mp4-encode "$F/ref-vp9.webm"
run audio-to-ogg-mono-320 "$F/ref.mkv"
run video-to-webm-mono-320 "$F/ref.mkv"
run image-remove-location "$F/photo.jpg"
run video-to-mp4-remove-all "$F/ref.mkv"
run strip-photo "$F/photo.jpg"
run strip-video "$F/ref.mkv"
run read-metadata "$F/ref.mkv"

if [ -n "$FAILED" ]; then
    echo "--- FAILED:$FAILED ---"
    exit 1
fi
echo "--- all sandbox checks passed ---"
