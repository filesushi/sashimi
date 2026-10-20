# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""Image conversion, resizing, favicons (ICO) and favicon packs (.zip). Pillow only; no ffmpeg needed."""

from sashimi.core.image import convert, favicon_html, favicon_manifest, favicon_pack, heif_available, to_ico

__all__ = ["convert", "favicon_html", "favicon_manifest", "favicon_pack", "heif_available", "to_ico"]
