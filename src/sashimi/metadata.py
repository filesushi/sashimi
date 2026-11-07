# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Reading what metadata an image, video or audio file holds (for showing it to people), and
removing it, all of it or just location data, without re-encoding.
"""

from sashimi.core.metadata import (
    MetadataField,
    MetadataInfo,
    MetadataSection,
    StripResult,
    UncountedDetail,
    build_strip_command,
    read,
    strip,
    strip_format,
)

__all__ = [
    "MetadataField",
    "MetadataInfo",
    "MetadataSection",
    "StripResult",
    "UncountedDetail",
    "build_strip_command",
    "read",
    "strip",
    "strip_format",
]
