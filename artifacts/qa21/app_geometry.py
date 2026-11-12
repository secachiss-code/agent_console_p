"""Observe MainWindow at snapshot time and after WM settlement; no size override."""

import json
import sys
from pathlib import Path

import gi

gi.require_version("GdkX11", "4.0")
import subprocess
import tempfile

from gi.repository import GLib

from aconsole.ui.bootstrap import run_app
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.views.app import AConsoleApp, SnapshotRequest

events = []
output = Path(__file__).resolve().parent
label = sys.argv[2] if len(sys.argv) > 2 else "app-desktop"


class Probe(AConsoleApp):
    def observe(self, label):
        from gi.repository import GdkX11, Gtk

        window = self.window
        xid = GdkX11.X11Surface.get_xid(window.get_surface())
        events.append(
            dict(
                label=label,
                width=window.get_width(),
                height=window.get_height(),
                default=list(window.get_default_size()),
                maximized=window.is_maximized(),
                fullscreen=window.is_fullscreen(),
                active=window.is_active(),
                horizontal=list(window.measure(Gtk.Orientation.HORIZONTAL, -1)),
                vertical=list(window.measure(Gtk.Orientation.VERTICAL, -1)),
                xprop=subprocess.check_output(
                    ["/usr/bin/xprop", "-id", hex(xid), "WM_NORMAL_HINTS", "_NET_WM_STATE", "_GTK_FRAME_EXTENTS"],
                    text=True,
                ),
            )
        )

    def _capture(self):
        self.observe("first-paint")
        GLib.timeout_add(700, self.settled)

    def settled(self):
        self.observe("settled")
        super()._capture()
        return GLib.SOURCE_REMOVE


with tempfile.TemporaryDirectory(prefix="qa21-app-") as directory:
    request = SnapshotRequest(view="window", theme="light", width=700, height=800, out=output / (label + ".png"))
    app = Probe(CoreClient(directory + "/core.sock"), request, scenario="demo")
    if len(sys.argv) > 1:
        app.set_application_id(sys.argv[1])
    run_app(lambda: app, ["geometry-probe"])
    (output / (label + ".json")).write_text(json.dumps(events, indent=2) + "\n")
    print(json.dumps(events, indent=2))
