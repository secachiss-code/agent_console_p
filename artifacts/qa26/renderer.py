"""Observe the original scale window once, before its unchanged measurement."""

import json
import os
from pathlib import Path


def pytest_runtest_call(item):
    if item.name != "test_scrolling_a_long_history":
        return
    from tests.gui.front import test_git_scale_ui as test

    original = test._on_pane

    def on_pane(tmp_path, monkeypatch, repo, check):
        async def observed(pane, toasts, window):
            data = {
                "renderer": window.get_renderer().__gtype__.name,
                "gtk": [test.Gtk.get_major_version(), test.Gtk.get_minor_version(), test.Gtk.get_micro_version()],
                "display": os.environ.get("DISPLAY"),
                "renderer_override": os.environ.get("GSK_RENDERER"),
                "load": os.getloadavg(),
            }
            with Path(os.environ["QA26_RENDERER_REPORT"]).open("x") as stream:
                json.dump(data, stream, indent=2)
                stream.write("\n")
            await check(pane, toasts, window)

        return original(tmp_path, monkeypatch, repo, observed)

    item.funcargs["monkeypatch"].setattr(test, "_on_pane", on_pane)
