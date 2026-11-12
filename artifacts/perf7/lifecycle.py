"""Inventory GTK windows; optional destruction is a separate diagnostic intervention."""

# ruff: noqa: INP001
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

if TYPE_CHECKING:
    import pytest


def snapshot(stage: str, nodeid: str) -> None:
    windows = []
    for window in Gtk.Window.list_toplevels():
        clock = window.get_frame_clock()
        application = window.get_application()
        windows.append({
            "type": window.__gtype__.name,
            "mapped": window.get_mapped(),
            "visible": window.get_visible(),
            "size": [window.get_width(), window.get_height()],
            "application": application.get_application_id() if application else None,
            "frame": clock.get_frame_counter() if clock else None,
        })
    data = {
        "stage": stage,
        "nodeid": nodeid,
        "at": time.monotonic(),
        "windows": windows,
        "pending": GLib.MainContext.default().pending(),
        "threads": len(list(Path("/proc/self/task").iterdir())),
        "fds": len(list(Path("/proc/self/fd").iterdir())),
    }
    with Path(os.environ["ACONSOLE_PERF7_LIFECYCLE"]).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(data) + "\n")


def pytest_runtest_setup(item: pytest.Item) -> None:
    snapshot("setup", item.nodeid)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    # Teardown reports arrive after fixture finalizers. The default observer does
    # not iterate GLib, collect garbage, destroy windows or change callbacks.
    if report.when == "teardown":
        snapshot("teardown", report.nodeid)
        if os.environ.get("ACONSOLE_PERF7_DESTROY_WINDOWS") == "1":
            for window in Gtk.Window.list_toplevels():
                window.destroy()
            snapshot("destroyed", report.nodeid)
