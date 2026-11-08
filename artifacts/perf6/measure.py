# Standalone filesystem-loaded pytest plugin, not a Python package.
# ruff: noqa: INP001
"""Observe one existing benchmark without changing its assertions, GC policy or data."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

    from aconsole.contracts.app import PaletteItem


import gc
import json
import os
import resource
import time
from pathlib import Path

record = None
original = None
module = None


def collection(phase: str, info: dict[str, int]) -> None:
    if record is not None and record["active"]:
        record["gc"].append(
            {
                "phase": phase,
                "generation": info["generation"],
                "at": time.perf_counter_ns(),
                "collected": info.get("collected", 0),
                "uncollectable": info.get("uncollectable", 0),
            }
        )


def pytest_runtest_setup(item: pytest.Function) -> None:
    global record, original, module  # noqa: PLW0603 -- pytest setup and teardown deliberately share this diagnostic record
    if item.name != "test_rank_2000_under_16ms":
        return
    from aconsole.ui.viewmodels.logic import rank  # noqa: PLC0415 -- load the optional diagnostic only when its target test runs

    module = rank
    original = rank.rank_items
    record = {
        "active": False,
        "gc": [],
        "wall_ns": 0,
        "thread_ns": 0,
        "process_ns": 0,
        "items": 0,
        "before_gc": gc.get_count(),
        "gc_threshold": gc.get_threshold(),
    }
    gc.callbacks.append(collection)

    def measured(items: Sequence[PaletteItem], query: str, **options: object) -> tuple[PaletteItem, ...]:
        record["items"] = len(items)
        start_wall = time.perf_counter_ns()
        start_thread = time.thread_time_ns()
        start_process = time.process_time_ns()
        record["active"] = True
        try:
            return original(items, query, **options)
        finally:
            record["active"] = False
            record["wall_ns"] = time.perf_counter_ns() - start_wall
            record["thread_ns"] = time.thread_time_ns() - start_thread
            record["process_ns"] = time.process_time_ns() - start_process

    rank.rank_items = measured


def pytest_runtest_teardown(item: pytest.Function) -> None:
    global record  # noqa: PLW0603 -- pytest setup and teardown deliberately share this diagnostic record
    if item.name != "test_rank_2000_under_16ms" or record is None:
        return
    module.rank_items = original
    gc.callbacks.remove(collection)
    record["after_gc"] = gc.get_count()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    record["max_rss_kb"] = usage.ru_maxrss
    record["pid"] = os.getpid()
    with Path(os.environ["ACONSOLE_PERF6_LOG"]).open("a") as stream:
        stream.write(json.dumps(record) + "\n")
    record = None
