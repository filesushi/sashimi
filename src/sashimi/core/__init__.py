# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
Single-file processing core.

Everything here takes explicit input and output paths, detects input types from file content,
writes atomically, performs no implicit I/O (no config files, no HOME, no network) and raises
the typed errors from :mod:`sashimi.errors`. The public entry points are re-exported by
:mod:`sashimi.video`, :mod:`sashimi.image`, :mod:`sashimi.audio` and :mod:`sashimi.sheets`.
"""
