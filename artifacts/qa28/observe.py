"""One passive snapshot after the existing all-rows wait, before scrolling."""

import json
import os
import sys
import time
from pathlib import Path


def pytest_runtest_call(item):
    if item.name != "test_scrolling_a_long_history":
        return
    from gi.repository import GLib, Gtk
    from tests.gui.front import test_git_scale_ui as test

    original_pane = test._on_pane
    original_settled = test._settled
    state = {}

    def on_pane(tmp_path, monkeypatch, repo, check):
        async def observed(pane, toasts, window):
            state.update(pane=pane, window=window)
            await check(pane, toasts, window)

        return original_pane(tmp_path, monkeypatch, repo, observed)

    async def settled(check):
        await original_settled(check)
        pane = state.get("pane")
        if pane is None or state.get("recorded") or pane.store.get_n_items() < 10_000:
            return
        state["recorded"] = True
        started = time.perf_counter()
        window = state["window"]
        context = GLib.MainContext.default()
        sources = []
        # GLib has no public source enumeration. This is explicitly a bounded
        # sample of IDs, not a claim to enumerate every native source.
        for number in range(1, 65_537):
            source = context.find_source_by_id(number)
            if source is not None:
                sources.append({"id": number, "name": source.get_name(), "priority": source.get_priority()})
        bootstrap = sys.modules["aconsole.ui.bootstrap"]
        windows = list(Gtk.Window.list_toplevels())

        def allocation(widget):
            return {"type": widget.__gtype__.name, "width": widget.get_width(),
                    "height": widget.get_height(), "mapped": widget.get_mapped()}

        data = {
            "stage": "after existing all-rows wait, before original scrolling measurement",
            "rows": pane.store.get_n_items(),
            "renderer": window.get_renderer().__gtype__.name,
            "gtk": [Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version()],
            "display": os.environ.get("DISPLAY"), "renderer_override": os.environ.get("GSK_RENDERER"),
            "allocation": {name: allocation(widget) for name, widget in
                           [("window", window), ("pane", pane), ("list", pane.list_view),
                            ("scroller", pane.scroller)]},
            "windows": [allocation(widget) for widget in windows],
            "tasks": [{"coroutine": getattr(task.get_coro(), "__qualname__", "unknown"),
                       "done": task.done(), "loop_closed": task.get_loop().is_closed()}
                      for task in bootstrap._TASKS],
            "sources": sources, "source_id_sample": [1, 65_536],
            "source_enumeration_complete": False,
            "rss_mb": test._rss_mb(), "load": os.getloadavg(),
            "observation_seconds": time.perf_counter() - started,
        }
        with Path(os.environ["QA28_OBSERVATION"]).open("x") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")

    patch = item.funcargs["monkeypatch"]
    patch.setattr(test, "_on_pane", on_pane)
    patch.setattr(test, "_settled", settled)
