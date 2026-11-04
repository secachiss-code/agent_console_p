"""The contrast gate reasons from a table of theme colors, so the table must be the theme's."""

from __future__ import annotations

import sys
from pathlib import Path

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "tools"))

import contrast_check  # noqa: E402

_ALLOWED_DRIFT = 1 / 255 + 1e-6
_FLOOR = (1, 5)


def _settle() -> None:
    context = GLib.MainContext.default()
    for _ in range(200):
        _ = context.iteration(False)  # noqa: FBT003 -- GLib names this argument only in C


def test_the_table_is_the_floor_theme_and_the_dim_label_is_readable() -> None:
    _ = Adw.init()
    if (Adw.get_major_version(), Adw.get_minor_version()) != _FLOOR:
        pytest.skip("the table holds the colors of libadwaita 1.5, the oldest supported")
    display = Gdk.Display.get_default()
    assert display is not None
    names = sorted(contrast_check.LIGHT_THEME)
    probes = Gtk.CssProvider()
    probes.load_from_string("".join(f".probe-{name} {{ color: @{name}; }}" for name in names))
    sheet = Gtk.CssProvider()
    sheet.load_from_path(str(_ROOT / "src" / "aconsole" / "ui" / "style" / "style.css"))
    Gtk.StyleContext.add_provider_for_display(display, probes, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Gtk.StyleContext.add_provider_for_display(display, sheet, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    box = Gtk.Box()
    labels: dict[str, Gtk.Label] = {}
    for name in names:
        labels[name] = Gtk.Label()
        labels[name].add_css_class(f"probe-{name}")
        box.append(labels[name])
    dim = Gtk.Label(label="dim")
    dim.add_css_class("dim-label")
    box.append(dim)
    window = Gtk.Window(child=box)
    window.present()
    manager = Adw.StyleManager.get_default()
    schemes = {"light": Adw.ColorScheme.FORCE_LIGHT, "dark": Adw.ColorScheme.FORCE_DARK}
    try:
        for theme, scheme in schemes.items():
            manager.set_color_scheme(scheme)
            _settle()
            for name, label in labels.items():
                real = label.get_color()
                expected = contrast_check.THEMES[theme][name]
                seen = (real.red, real.green, real.blue, real.alpha)
                assert seen == pytest.approx(expected, abs=_ALLOWED_DRIFT), f"{theme}: {name}"
            assert dim.get_opacity() == pytest.approx(1.0)
    finally:
        manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
        window.destroy()
