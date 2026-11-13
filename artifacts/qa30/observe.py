"""One scalar style/allocation snapshot after the original all-rows wait."""

import json
import os
import time
from pathlib import Path


def pytest_runtest_call(item):
    if item.name != "test_scrolling_a_long_history":
        return
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
        # Toolbar is a direct child; no recursive widget/source traversal.
        toolbar = pane.get_first_child()
        while toolbar is not None and not toolbar.has_css_class("ac-git-toolbar"):
            toolbar = toolbar.get_next_sibling()
        assert toolbar is not None
        window = state["window"]
        data = {
            "stage": "after original all-rows wait, before original scrolling measurement",
            "rows": pane.store.get_n_items(),
            "renderer": window.get_renderer().__gtype__.name,
            "allocation": {
                name: {"width": widget.get_width(), "height": widget.get_height(),
                       "padding": [getattr(widget.get_style_context().get_padding(), edge)
                                   for edge in ("top", "right", "bottom", "left")]}
                for name, widget in (("window", window), ("pane", pane), ("list", pane.list_view),
                                     ("scroller", pane.scroller), ("toolbar", toolbar))
            },
            "observation_seconds": time.perf_counter() - started,
        }
        with Path(os.environ["QA30_OBSERVATION"]).open("x") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")

    patch = item.funcargs["monkeypatch"]
    patch.setattr(test, "_on_pane", on_pane)
    patch.setattr(test, "_settled", settled)
