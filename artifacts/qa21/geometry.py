"""Observe a minimal GTK window without changing desktop preferences."""

import json
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkX11", "4.0")
from gi.repository import Adw, GdkX11, Gio, GLib, Gtk

events = []
app = Adw.Application(
    application_id=sys.argv[1] if len(sys.argv) > 1 else "io.github.aconsole.GeometryProbe",
    flags=Gio.ApplicationFlags.NON_UNIQUE,
)


def activate(app):
    window = Adw.ApplicationWindow(application=app, title="AConsole", default_width=700, default_height=800)
    window.set_content(Gtk.Label(label="Geometry probe"))

    def sample(label):
        surface = window.get_surface()
        xid = GdkX11.X11Surface.get_xid(surface)
        events.append(
            dict(
                label=label,
                width=window.get_width(),
                height=window.get_height(),
                default=list(window.get_default_size()),
                maximized=window.is_maximized(),
                fullscreen=window.is_fullscreen(),
                active=window.is_active(),
                xprop=subprocess.check_output(
                    ["/usr/bin/xprop", "-id", hex(xid), "WM_NORMAL_HINTS", "_NET_WM_STATE", "_GTK_FRAME_EXTENTS"],
                    text=True,
                ),
            )
        )
        return GLib.SOURCE_REMOVE

    def finish():
        sample("settled")
        window.destroy()
        app.quit()
        return GLib.SOURCE_REMOVE

    def mapped(window):
        clock = window.get_frame_clock()
        count = [0]

        def painted(clock):
            count[0] += 1
            if count[0] <= 4:
                sample(f"paint-{count[0]}")
                window.queue_draw()

        clock.connect("after-paint", painted)

    window.connect("map", mapped)
    window.present()
    GLib.timeout_add(100, sample, "100ms")
    GLib.timeout_add(700, finish)


app.connect("activate", activate)
app.run(["geometry-probe"])
print(json.dumps(events, indent=2))
