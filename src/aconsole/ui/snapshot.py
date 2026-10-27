"""Capture a widget after the first frame-clock paint."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Gdk, Gtk

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def capture_window(widget: Gtk.Widget, path: Path) -> None:
    """Rasterise ``widget`` and write a PNG. Call this from ``after-paint``."""
    width = widget.get_width()
    height = widget.get_height()
    if width <= 0 or height <= 0:
        message = f"widget has no size ({width}x{height})"
        raise RuntimeError(message)
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    if node is None:
        message = "snapshot produced no node"
        raise RuntimeError(message)
    native = widget.get_native()
    if native is None:
        message = "widget has no native surface"
        raise RuntimeError(message)
    renderer = native.get_renderer()
    if renderer is None:
        message = "native surface has no renderer"
        raise RuntimeError(message)
    texture = renderer.render_texture(node, None)
    _ = texture.save_to_png(str(path))


_HANDLES: dict[int, int] = {}


def after_first_paint(widget: Gtk.Widget, callback: Callable[[], None]) -> None:
    """Run ``callback`` once, on the first ``Gdk.FrameClock`` ``after-paint``."""

    def _on_map(mapped: Gtk.Widget) -> None:
        clock = mapped.get_frame_clock()
        if clock is None:
            return
        _HANDLES[id(mapped)] = clock.connect("after-paint", _on_paint)

    def _on_paint(clock: Gdk.FrameClock) -> None:
        handle = _HANDLES.pop(id(widget), None)
        if handle is not None:
            clock.disconnect(handle)
        callback()

    _ = widget.connect("map", _on_map)
