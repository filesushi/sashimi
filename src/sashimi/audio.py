"""Audio extraction from video, and audio conversion (MP3, M4A, WAV, OGG/Opus, FLAC). Requires ffmpeg."""

from sashimi.core.audio import build_convert_command, convert

__all__ = ["build_convert_command", "convert"]
