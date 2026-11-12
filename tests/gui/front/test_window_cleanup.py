"""Real GTK windows are released on success and failure, preserving outer owners."""

from __future__ import annotations

import pytest

from tests.gui.window_cleanup import owned_gtk_windows


@pytest.mark.parametrize("fails", [False, True])
def test_cleanup_releases_owned_windows_and_preserves_existing(*, fails: bool) -> None:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    existing = Gtk.Window()
    created: list[Gtk.Window] = []

    def scenario() -> None:
        with owned_gtk_windows():
            window = Gtk.Window()
            created.append(window)
            window.present()
            assert window in Gtk.Window.list_toplevels()
            if fails:
                message = "scenario failure"
                raise RuntimeError(message)

    try:
        if fails:
            with pytest.raises(RuntimeError, match="scenario failure"):
                scenario()
        else:
            scenario()
        remaining = Gtk.Window.list_toplevels()
        assert existing in remaining
        assert created
        assert all(window not in remaining and not window.get_mapped() for window in created)
    finally:
        existing.destroy()
