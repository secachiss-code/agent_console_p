"""Release windows created in a native test without touching existing windows."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def owned_gtk_windows() -> Iterator[None]:
    # Importing gi enables the GIL. Pure UI/viewmodel tests share an interpreter
    # with core smoke checks, so this helper must never import it on their behalf.
    gtk = sys.modules.get("gi.repository.Gtk")
    existing = set(gtk.Window.list_toplevels()) if gtk is not None else set()
    try:
        yield
    finally:
        gtk = sys.modules.get("gi.repository.Gtk")
        if gtk is not None:
            for window in gtk.Window.list_toplevels():
                if window not in existing:
                    # Cleanup bypasses close-request guards after the test has
                    # asserted their behavior. It does not run GLib sources.
                    window.destroy()
