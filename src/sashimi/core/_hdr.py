# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""HDR video: telling it apart, tone-mapping it to standard range, and keeping it as HDR in re-encodes."""

import subprocess
import threading

from sashimi.core.probe import MediaInfo
from sashimi.errors import MissingFiltersError

__all__ = [
    "HDR_TRANSFERS",
    "MissingFiltersError",
    "TONE_MAP_OPERATOR",
    "hdr_name",
    "is_hdr",
    "require_tone_mapping",
    "tone_map_filter",
    "with_tone_map",
]

HDR_TRANSFERS = {"smpte2084": 100, "arib-std-b67": 203}
"""
The HDR transfer functions sashimi tone-maps (PQ and HLG, as ffprobe names them), each with the
brightness in nits that becomes standard-range white (zscale's ``npl``). PQ uses 100, as FFmpeg's
own examples do; HLG uses BT.2408's reference white, 203 nits, without which faces in phone video
come out close to white. Chosen by comparing real PQ (Dolby Vision 8.1) and HLG (phone) footage.
"""
TONE_MAP_OPERATOR = "mobius"
"""
FFmpeg's ``tonemap`` curve: Möbius keeps mid-tones and colour as they are and rolls off only the
highlights. Hable (the usual example) left PQ landscapes dim and grey; Reinhard and clip blew out
skies and skin.
"""
NEEDED_FILTERS = ("zscale", "tonemap", "sidedata")
HDR_SIDE_DATA = (
    "MASTERING_DISPLAY_METADATA",
    "CONTENT_LIGHT_LEVEL",
    "DYNAMIC_HDR_PLUS",
    "DYNAMIC_HDR_VIVID",
    "DOVI_RPU_BUFFER",
    "DOVI_METADATA",
    "AMBIENT_VIEWING_ENVIRONMENT",
)
"""
HDR frame metadata that a tone-mapped picture no longer matches, removed with it: otherwise
encoders write it into the standard-range output (an H.264 MP4 with HDR10 mastering metadata).
"""
_PRIMARIES = {"bt2020": "bt2020", "bt709": "bt709", "smpte432": "smpte432", "smpte431": "smpte431"}
_MATRICES = {"bt2020nc": "bt2020nc", "bt2020c": "bt2020c", "bt709": "bt709"}


def is_hdr(source: MediaInfo | None) -> bool:
    """True when `source`'s video uses an HDR transfer function (PQ or HLG; Dolby Vision 8.1 and 8.4 included). Pure."""
    return source is not None and source.color_transfer in HDR_TRANSFERS


def hdr_name(source: MediaInfo) -> str:
    """What kind of HDR `source` is, for people: ``HDR (PQ)`` or ``HDR (HLG)``. Pure."""
    return "HDR (HLG)" if source.color_transfer == "arib-std-b67" else "HDR (PQ)"


def tone_map_filter(source: MediaInfo | None) -> str | None:
    """
    The filters that turn `source`'s HDR pictures into standard-range BT.709 (8-bit 4:4:4, so any
    size works), or None for standard-range video. Pure.

    The pictures are made linear (zscale, reading the transfer, primaries, matrix and range from
    the probe), converted to BT.709 primaries, tone-mapped with :data:`TONE_MAP_OPERATOR` at full
    colour, then encoded as BT.709, and the HDR frame metadata (:data:`HDR_SIDE_DATA`) is removed. Put it after any scaling down: the picture then costs less to
    tone-map, and looks the same.
    """
    if not is_hdr(source):
        return None
    assert source is not None and source.color_transfer is not None
    transfer = source.color_transfer
    primaries = _PRIMARIES.get(source.color_primaries or "", "bt2020")
    matrix = _MATRICES.get(source.color_space or "", "bt2020nc")
    linear = f"zscale=tin={transfer}:pin={primaries}:min={matrix}:rin=tv:t=linear:npl={HDR_TRANSFERS[transfer]}"
    return ",".join(
        [
            "format=yuv444p16le",
            linear,
            "format=gbrpf32le",
            "zscale=p=bt709",
            f"tonemap={TONE_MAP_OPERATOR}:desat=0",
            "zscale=t=bt709:m=bt709:r=tv",
            "format=yuv444p",
            *(f"sidedata=mode=delete:type={kind}" for kind in HDR_SIDE_DATA),
        ]
    )


def with_tone_map(vf: str, tone_map: str | None) -> str:
    """`vf` followed by `tone_map` (from :func:`tone_map_filter`), or `vf` unchanged without one. Pure."""
    if not tone_map:
        return vf
    return f"{vf},{tone_map}" if vf else tone_map


def _available_filters(ffmpeg: str) -> frozenset[str]:
    from sashimi.core._capabilities import filters_of

    return filters_of(ffmpeg)


def require_tone_mapping(source: MediaInfo | None, ffmpeg: str) -> str | None:
    """
    The tone-mapping filters for `source` (see :func:`tone_map_filter`), after checking `ffmpeg`
    has them (once per executable). Raises MissingFiltersError for HDR video when it doesn't:
    without them the output would look washed out, which is worse than no output.
    """
    tone_map = tone_map_filter(source)
    if tone_map is None:
        return None
    listed = _available_filters(ffmpeg)
    missing = [name for name in NEEDED_FILTERS if listed and name not in listed]
    if missing:
        from sashimi.core._capabilities import advice, ffmpeg_info

        info = ffmpeg_info(ffmpeg)
        raise MissingFiltersError(
            f"This is HDR video, and {info.describe()} can't tone-map it to standard colours: it lacks the "
            f"{' and '.join(missing)} filter{'s' if len(missing) > 1 else ''} (an FFmpeg built with libzimg has "
            f"them). Without tone mapping the result would look washed out, so nothing was written. {advice(missing)}",
            missing=missing,
            info=info,
        )
    return tone_map
