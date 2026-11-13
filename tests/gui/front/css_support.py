"""Display CSS owned by a test, with symmetric registration and removal."""

from contextlib import contextmanager
lazy from collections.abc import Iterator

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from aconsole.ui.style import stylesheet  # noqa: E402


@contextmanager
def production_styles() -> Iterator[Gtk.CssProvider]:
    """Use the application's stylesheet without removing another owner's CSS."""
    Adw.init()
    display = Gdk.Display.get_default()
    assert display is not None
    provider = Gtk.CssProvider()
    provider.load_from_string(stylesheet())
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    try:
        yield provider
    finally:
        Gtk.StyleContext.remove_provider_for_display(display, provider)
