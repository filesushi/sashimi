# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""CPU budgeting. Containers often limit CPU with a CFS quota that os.cpu_count() doesn't see."""

import math
import os

DEFAULT_MAX_WORKERS = 4


def _cgroup_cpu_limit() -> int | None:
    """Returns the cgroup CPU quota rounded up to whole CPUs, or None if there is no quota."""
    try:
        with open("/sys/fs/cgroup/cpu.max", encoding="ascii") as f:
            quota, period = f.read().split()[:2]
        if quota != "max":
            return max(1, math.ceil(int(quota) / int(period)))
        return None
    except (OSError, ValueError):
        pass
    try:
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", encoding="ascii") as f:
            quota_us = int(f.read())
        with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", encoding="ascii") as f:
            period_us = int(f.read())
        if quota_us > 0 and period_us > 0:
            return max(1, math.ceil(quota_us / period_us))
    except (OSError, ValueError):
        pass
    return None


def available_cpus() -> int:
    """CPUs this process may actually use: affinity mask and cgroup CPU quota both count."""
    process_cpu_count = getattr(os, "process_cpu_count", None)
    if process_cpu_count is not None:
        cpus = process_cpu_count() or 1
    elif hasattr(os, "sched_getaffinity"):
        cpus = len(os.sched_getaffinity(0)) or 1
    else:
        cpus = os.cpu_count() or 1
    limit = _cgroup_cpu_limit()
    return min(cpus, limit) if limit else cpus


def resolve_max_workers(max_workers: int | None) -> int:
    """`None` means the default: min(4, available CPUs)."""
    if max_workers is None:
        return min(DEFAULT_MAX_WORKERS, available_cpus())
    if max_workers < 1:
        raise ValueError(f"max_workers must be at least 1, got {max_workers}")
    return max_workers
