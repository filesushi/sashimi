# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""
What an output kept of its source. Every processing function returns a result with a
``fidelity`` (:class:`Fidelity`): lossless, picture or sound identical with something else
dropped, or lossy, with what changed, what was dropped and what was removed as asked.
"""

from sashimi.core.fidelity import Fidelity, FidelityLevel, Outcome

__all__ = ["Fidelity", "FidelityLevel", "Outcome"]
