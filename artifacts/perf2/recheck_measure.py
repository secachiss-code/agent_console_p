# Standalone filesystem-loaded pytest plugin, not a Python package.
# ruff: noqa: INP001
"""Observe the original helper's result; do not change its work, timing or assertions."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


import contextlib
import functools
import json
import os
import sys
import time
from pathlib import Path


def context() -> dict[str, object]:
    data = {"loadavg": os.getloadavg(), "monotonic": time.monotonic(), "pid": os.getpid()}
    for name in ["pressure/cpu", "pressure/io", "pressure/memory", "self/cgroup"]:
        with contextlib.suppress(OSError):
            data[name] = Path("/proc", name).read_text()
    for name in ["cpu.max", "cpu.stat", "memory.max", "memory.current"]:
        with contextlib.suppress(OSError):
            data["cgroup/" + name] = Path("/sys/fs/cgroup", name).read_text()
    return data


def pytest_runtest_setup(item: pytest.Function) -> None:
    if item.name != "test_persistence_b_tick":
        return
    module = item.module
    original = module._tick_lags  # noqa: SLF001 -- observe the original private fixture without editing its workload

    @functools.wraps(original)
    async def measured(path: Path) -> tuple[list[float], int]:
        before = context()
        lags, stored = await original(path)
        after = context()
        ordered = sorted(lags)
        row = {
            "test": item.nodeid,
            "python": sys.version,
            "gil_enabled": sys._is_gil_enabled(),  # noqa: SLF001 -- observe the original private fixture without editing its workload
            "before": before,
            "after": after,
            "n": len(ordered),
            "p50_s": ordered[len(ordered) // 2],
            "p99_s": ordered[len(ordered) * 99 // 100],
            "max_s": ordered[-1],
            "stored": stored,
            "limit_s": module._TICK_LIMIT_S,  # noqa: SLF001 -- observe the original private fixture without editing its workload
            "rate": module._TICK_RATE,  # noqa: SLF001 -- observe the original private fixture without editing its workload
            "span_s": module._TICK_SPAN_S,  # noqa: SLF001 -- observe the original private fixture without editing its workload
        }
        with Path(os.environ["ACONSOLE_PERF2_LOG"]).open("a") as stream:  # noqa: ASYNC230 -- write the diagnostic after the measured helper has completed
            stream.write(json.dumps(row) + "\n")
        return lags, stored

    module._tick_lags = measured  # noqa: SLF001 -- observe the original private fixture without editing its workload
    item._perf2_original = original  # noqa: SLF001 -- observe the original private fixture without editing its workload


def pytest_runtest_teardown(item: pytest.Function) -> None:
    if hasattr(item, "_perf2_original"):
        item.module._tick_lags = item._perf2_original  # noqa: SLF001 -- observe the original private fixture without editing its workload
