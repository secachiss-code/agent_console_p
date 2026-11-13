"""Finite same-pane CSS control; no scrolling or performance acceptance."""

import asyncio
import json
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gtk  # noqa: E402
from tests.gui.front.test_git_pane import _on_pane, _project, _settled  # noqa: E402

from aconsole.ui.style import stylesheet  # noqa: E402


def test_bare_styled_removed(tmp_path, monkeypatch):
    repo, _ = _project(tmp_path)
    observations = {}

    async def check(pane, toasts, window):
        await _settled(lambda: pane.store.get_n_items() >= 4)
        display = Gdk.Display.get_default()
        assert display is not None
        provider = Gtk.CssProvider()
        provider.load_from_string(stylesheet())

        async def snapshot(label):
            # Allow native style invalidation and subsequent frame allocations.
            await asyncio.sleep(0.4)
            widgets = {"window": window, "pane": pane, "list": pane.list_view,
                       "scroller": pane.scroller}
            pending = [pane]
            while pending:
                widget = pending.pop()
                if widget.has_css_class("ac-git-toolbar"):
                    widgets["toolbar"] = widget
                child = widget.get_first_child()
                while child is not None:
                    pending.append(child)
                    child = child.get_next_sibling()
            observations[label] = {
                name: {"width": widget.get_width(), "height": widget.get_height(),
                       "padding": [getattr(widget.get_style_context().get_padding(), edge)
                                   for edge in ("top", "right", "bottom", "left")]}
                for name, widget in widgets.items()
            }

        await snapshot("bare")
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        try:
            await snapshot("styled")
        finally:
            Gtk.StyleContext.remove_provider_for_display(display, provider)
        await snapshot("removed")
        path = Path(__file__).with_name("control.json")
        with path.open("x") as stream:
            json.dump(observations, stream, indent=2)
            stream.write("\n")
        assert observations["bare"] == observations["removed"]
        assert observations["styled"]["toolbar"]["padding"] == [6, 16, 6, 16]
        assert observations["bare"]["toolbar"]["padding"] == [0, 0, 0, 0]
        assert observations["bare"]["list"]["height"] - observations["styled"]["list"]["height"] == 12

    _on_pane(tmp_path, monkeypatch, repo, check)
