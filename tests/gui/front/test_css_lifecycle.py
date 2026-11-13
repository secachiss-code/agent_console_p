"""Native CSS cleanup on success and failure, preserving another owner's provider."""

import subprocess
import sys

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from tests.gui.front.css_support import production_styles  # noqa: E402


def _settle() -> None:
    context = GLib.MainContext.default()
    for _ in range(200):
        context.iteration(False)  # noqa: FBT003 -- native GLib argument


@pytest.mark.parametrize("fail", [False, True])
def test_styles_are_removed_and_other_provider_survives(*, fail: bool) -> None:
    subprocess.run(
        [sys.executable, "-c", ("from tests.gui.front.test_css_lifecycle import _check_lifecycle; "
         "import sys; _check_lifecycle(fail=sys.argv[1] == 'True')"), str(fail)],
        check=True, timeout=15,
    )


def _check_lifecycle(*, fail: bool) -> None:
    # A fresh process prevents pre-existing Application providers from hiding removal.
    Adw.init()
    display = Gdk.Display.get_default()
    assert display is not None
    other_provider = Gtk.CssProvider()
    other_provider.load_from_string(".qa29-sentinel > label { padding: 3px 7px; }")
    Gtk.StyleContext.add_provider_for_display(display, other_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    toolbar = Gtk.Box(css_classes=["ac-git-toolbar", "qa29-sentinel"])
    label = Gtk.Label(label="other provider")
    toolbar.append(label)
    window = Gtk.Window(child=toolbar)
    window.present()

    def padding() -> list[int]:
        _settle()
        value = toolbar.get_style_context().get_padding()
        return [value.top, value.right, value.bottom, value.left]

    def styled() -> None:
        with production_styles():
            assert padding() == [6, 16, 6, 16]
            if fail:
                message = "deliberate CSS owner failure"
                raise RuntimeError(message)

    try:
        assert padding() == [0, 0, 0, 0]
        if fail:
            with pytest.raises(RuntimeError, match="deliberate CSS owner failure"):
                styled()
        else:
            styled()
        assert padding() == [0, 0, 0, 0]
        other_padding = label.get_style_context().get_padding()
        assert [other_padding.top, other_padding.right, other_padding.bottom, other_padding.left] == [3, 7, 3, 7]
    finally:
        window.destroy()
        Gtk.StyleContext.remove_provider_for_display(display, other_provider)
