# Standalone filesystem-loaded pytest plugin.
# ruff: noqa: INP001
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

import contextlib
import cProfile
import functools
import gc
import io
import json
import os
import pstats
import threading
import time
from pathlib import Path

profiles = []

records = []


def resources() -> dict[str, str]:
    data = {}
    for name in ("/proc/self/cgroup", "/sys/fs/cgroup/cpu.max", "/sys/fs/cgroup/cpu.stat", "/proc/pressure/cpu"):
        with contextlib.suppress(OSError):
            data[name] = Path(name).read_text()
    return data


def record(name: str, start: float, cpu: float, **extra: object) -> None:
    records.append(
        dict(
            name=name,
            start=start,
            end=time.perf_counter(),
            cpu=time.thread_time() - cpu,
            thread=threading.current_thread().name,
            **extra,
        )
    )


def gc_event(phase: str, info: dict[str, int | float]) -> None:
    records.append(dict(name="gc", phase=phase, at=time.perf_counter(), thread=threading.current_thread().name, **info))


def pytest_runtest_call(item: pytest.Function) -> None:  # noqa: C901 -- keep paired measurement hooks together
    if item.name != "test_real_core_palette_route_and_latency":
        return
    from tests.core.test_wire import _Client  # noqa: PLC0415 -- instrument only the selected test after its fixtures are ready

    from aconsole.core.application.services.palette import PaletteService  # noqa: PLC0415 -- instrument only the selected test after its fixtures are ready
    from aconsole.core.bootstrap import Daemon  # noqa: PLC0415 -- instrument only the selected test after its fixtures are ready
    from aconsole.core.infrastructure.mcp.tools import PlanBoard  # noqa: PLC0415 -- instrument only the selected test after its fixtures are ready
    from aconsole.core.infrastructure.persistence.writer import Writer  # noqa: PLC0415 -- instrument only the selected test after its fixtures are ready

    mp = item.funcargs["monkeypatch"]

    def observe(cls: type, name: str) -> None:
        original = getattr(cls, name)

        @functools.wraps(original)
        def wrapped(*args: object, **kwargs: object) -> object:
            start = time.perf_counter()
            cpu = time.thread_time()
            profiler = (
                cProfile.Profile()
                if os.environ.get("ACONSOLE_PERF8_PROFILE") and name == "dispatch" and not profiles
                else None
            )
            if profiler:
                profiles.append(profiler)
                profiler.enable()
            try:
                return original(*args, **kwargs)
            finally:
                if profiler:
                    profiler.disable()
                record(cls.__name__ + "." + name, start, cpu, gil=os.sys._is_gil_enabled())  # noqa: SLF001 -- report the interpreter GIL mode

        mp.setattr(cls, name, wrapped)

    for cls, name in [
        (PaletteService, "dispatch"),
        (Daemon, "_palette_snapshot"),
        (Daemon, "_publish_notes"),
        (PlanBoard, "node_notes"),
    ]:
        observe(cls, name)
    orig = Writer.call

    def writer(self: object, work: object) -> object:
        start = time.perf_counter()
        cpu = time.thread_time()
        try:
            return orig(self, work)
        finally:
            if work.__name__ == "read_settings":
                record("Writer.read_settings", start, cpu)

    mp.setattr(Writer, "call", writer)
    orig_call = _Client.call

    async def call(self: object, method: str, *args: object, **kwargs: object) -> object:
        before = resources() if method == "palette/items" else None
        start = time.perf_counter()
        cpu = time.thread_time()
        try:
            return await orig_call(self, method, *args, **kwargs)
        finally:
            if method == "palette/items":
                record("Client.palette/items", start, cpu, before=before, after=resources())

    mp.setattr(_Client, "call", call)
    records.append(
        {
            "name": "context",
            "at": time.perf_counter(),
            "load": os.getloadavg(),
            "gc_counts": gc.get_count(),
            "gc_thresholds": gc.get_threshold(),
            "gil": os.sys._is_gil_enabled(),  # noqa: SLF001 -- report the interpreter GIL mode
        }
    )
    gc.callbacks.append(gc_event)


def pytest_runtest_teardown(item: pytest.Function) -> None:
    if item.name != "test_real_core_palette_route_and_latency":
        return
    gc.callbacks.remove(gc_event)
    Path(os.environ["ACONSOLE_PERF8_LOG"]).write_text(json.dumps(records, indent=2))

    if profiles:
        stream = io.StringIO()
        pstats.Stats(profiles[0], stream=stream).sort_stats("cumulative").print_stats(35)
        Path(os.environ["ACONSOLE_PERF8_LOG"] + ".profile.txt").write_text(stream.getvalue())
