"""Diagnostic Python GLib source ownership; timings are not acceptance runs."""

# ruff: noqa: INP001
from __future__ import annotations

import functools
import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from gi.repository import GLib

if TYPE_CHECKING:
    import pytest

CURRENT = "collection"
ACTIVE = {}
ORIGINALS = {}
EVENTS = []


def instrument(name: str) -> None:
    original = getattr(GLib, name)
    ORIGINALS[name] = original

    @functools.wraps(original)
    def register(*args: object, **kwargs: object) -> int:
        position = 0 if name == "idle_add" else 1
        callback = args[position]
        record = {
            "owner": CURRENT,
            "kind": name,
            "callback": getattr(callback, "__module__", "") + "." + getattr(
                callback, "__qualname__", type(callback).__qualname__
            ),
            "interval": None if position == 0 else args[0],
            "created": time.monotonic(),
            "calls": 0,
            "late_calls": 0,
            "cpu_ns": 0,
        }
        source_id = 0

        def dispatch(*values: object) -> bool:
            start = time.thread_time_ns()
            keep = False
            late = record["owner"] != CURRENT
            try:
                record["calls"] += 1
                if late:
                    record["late_calls"] += 1
                keep = callback(*values)
                return keep
            finally:
                record["cpu_ns"] += time.thread_time_ns() - start
                if late:
                    EVENTS.append({"stage": "late", "current": CURRENT, "source": dict(record)})
                if not keep:
                    ACTIVE.pop(source_id, None)

        values = list(args)
        values[position] = dispatch
        source_id = original(*values, **kwargs)
        record["id"] = source_id
        ACTIVE[source_id] = record
        return source_id

    setattr(GLib, name, register)


def pytest_configure() -> None:
    for name in ("timeout_add", "timeout_add_seconds", "idle_add"):
        instrument(name)


def snapshot(stage: str, nodeid: str) -> None:
    context = GLib.MainContext.default()
    for source_id in list(ACTIVE):
        if context.find_source_by_id(source_id) is None:
            del ACTIVE[source_id]
    bootstrap = sys.modules.get("aconsole.ui.bootstrap")
    tasks = getattr(bootstrap, "_TASKS", ())
    EVENTS.append({
        "stage": stage, "nodeid": nodeid, "at": time.monotonic(),
        "sources": [dict(record) for record in ACTIVE.values()],
        "tasks": [{
            "coroutine": getattr(task.get_coro(), "__qualname__", "unknown"),
            "done": task.done(), "loop_closed": task.get_loop().is_closed(),
        } for task in tasks],
    })


def pytest_runtest_setup(item: pytest.Item) -> None:
    global CURRENT  # noqa: PLW0603 -- scalar ownership label, no test objects retained
    CURRENT = item.nodeid
    snapshot("setup", item.nodeid)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when == "teardown":
        snapshot("teardown", report.nodeid)


def pytest_unconfigure() -> None:
    for name, original in ORIGINALS.items():
        setattr(GLib, name, original)
    path = Path(os.environ["ACONSOLE_PERF7_SOURCES"])
    path.write_text("".join(json.dumps(event) + "\n" for event in EVENTS), encoding="utf-8")
