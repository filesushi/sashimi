import re
import pathlib
import subprocess
import logging
from typing import Tuple, Optional

logger = logging.getLogger("sashimi")


def parse_time_to_seconds(time_str: str) -> float:
    """
    Parses flexible human-entered time strings into flat seconds.
    Supported inputs:
      - Colon notation: '00:24:09', '24:09', '1:22:45.50'
      - Suffix text format: '42s', '1h22m', '24 min 09 s', '1.5h'
      - Pure numeric strings: '1250' (interpreted as raw seconds)
    """
    clean_str = time_str.strip().lower()

    if not clean_str:
        raise ValueError("Time offset string cannot be empty.")

    # 1. Handle standard colon layouts (HH:MM:SS or MM:SS)
    if ":" in clean_str:
        parts = clean_str.split(":")
        if len(parts) == 3:  # hh:mm:ss
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        elif len(parts) == 2:  # mm:ss
            m, s = parts
            return int(m) * 60 + float(s)
        else:
            raise ValueError(f"Unrecognized colon time scheme: '{time_str}'")

    # 2. Handle alphanumeric unit suffixes (h, m, min, s)
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(hours|hour|h|minutes|minute|min|m|seconds|second|s)", clean_str)
    if matches:
        total_seconds = 0.0
        for value, unit in matches:
            val = float(value)
            if unit in ("h", "hour", "hours"):
                total_seconds += val * 3600
            elif unit in ("m", "min", "minute", "minutes"):
                total_seconds += val * 60
            elif unit in ("s", "second", "seconds"):
                total_seconds += val
        return total_seconds

    # 3. Handle standalone numeric seconds strings
    try:
        return float(clean_str)
    except ValueError as e:
        raise ValueError(f"Could not interpret format of time string: '{time_str}'") from e


def extract_video_segment(
    input_path: pathlib.Path,
    start_str: str,
    end_str: str,
    output_path: Optional[pathlib.Path] = None,
    force: bool = False,
) -> Tuple[str, str, pathlib.Path]:
    """
    Coordinates time calculation and invokes FFmpeg for fast slicing.
    Returns: (status_string, diagnostic_message, target_output_path)
    """
    try:
        start_secs = parse_time_to_seconds(start_str)
        end_secs = parse_time_to_seconds(end_str)
    except ValueError as err:
        return ("error", f"Time Parsing Failure: {err}", input_path)

    if start_secs >= end_secs:
        return (
            "error",
            f"Invalid Bounds: Start time ({start_secs}s) cannot meet or exceed End time ({end_secs}s).",
            input_path,
        )

    if not output_path:
        output_path = input_path.parent / f"{input_path.stem}_extracted{input_path.suffix}"

    if output_path.exists() and not force:
        return ("exists", f"File already exists: {output_path.name}. Pass -f to overwrite.", output_path)

    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_secs),
        "-to",
        str(end_secs),
        "-i",
        str(input_path),
        "-c",
        "copy",
        str(output_path),
    ]

    logger.debug(f"Executing: {' '.join(ffmpeg_cmd)}")

    try:
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        duration_delta = end_secs - start_secs
        return ("success", f"Extracted {duration_delta:.1f}s segment ➔ {output_path.name}", output_path)
    except subprocess.CalledProcessError as ffmpeg_err:
        err_log = ffmpeg_err.stderr.decode(errors="replace").strip().split("\n")[-1]
        return ("error", f"FFmpeg execution failed: {err_log}", output_path)
    except FileNotFoundError:
        return ("error", "System requirements unmet: 'ffmpeg' binary was not found in PATH.", output_path)
