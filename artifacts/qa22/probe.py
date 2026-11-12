"""Native controls; Gala itself can persist their geometry when they close.

SQLite connections are read-only. The fixed control nevertheless changes Gala's
saved AConsole geometry, so this is an observation script, not an isolated test.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkX11", "4.0")
from gi.repository import Adw, GdkX11, Gio, GLib, Gtk

output = Path(__file__).resolve().parent
mode = sys.argv[1]
if mode not in {"normal", "fixed", "control", "normal-repeat", "fixed-restore", "normal-restored"}:
    raise ValueError(mode)
target = output / (mode + ".json")
if target.exists():
    raise FileExistsError(target)
events = []
app_id = "io.github.aconsole.GeometryProbe" if mode == "control" else "io.github.aconsole.AConsole"
app = Adw.Application(application_id=app_id, flags=Gio.ApplicationFlags.NON_UNIQUE)


def rows():
    path = Path.home() / ".local/share/io.elementary.gala/windowstate.db"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        return connection.execute(
            "SELECT * FROM apps WHERE app_id = ? ORDER BY window_index", (app_id + ".desktop",)
        ).fetchall()


before = rows()


def activate(application):
    restoring = mode == "fixed-restore"
    window = Adw.ApplicationWindow(application=application, title="Geometry diagnostic",
                                   default_width=1920 if restoring else 700,
                                   default_height=1080 if restoring else 800)
    window.set_content(Gtk.Label(label="QA-22 geometry control"))
    if mode.startswith("fixed"):
        window.set_resizable(False)

    def sample(label):
        xid = GdkX11.X11Surface.get_xid(window.get_surface())
        events.append(dict(label=label, size=[window.get_width(), window.get_height()],
                           default=list(window.get_default_size()), active=window.is_active(),
                           maximized=window.is_maximized(), fullscreen=window.is_fullscreen(),
                           xprop=subprocess.check_output([
                               "/usr/bin/xprop", "-id", hex(xid), "WM_CLASS", "_GTK_APPLICATION_ID",
                               "WM_NORMAL_HINTS", "_NET_WM_STATE", "_GTK_FRAME_EXTENTS",
                           ], text=True)))
        return GLib.SOURCE_REMOVE

    def finish():
        sample("700ms")
        window.destroy()
        application.quit()
        return GLib.SOURCE_REMOVE

    window.present()
    GLib.timeout_add(100, sample, "100ms")
    GLib.timeout_add(700, finish)


app.connect("activate", activate)
app.run(["qa22-geometry"])
binary = Path("/usr/bin/gala").read_bytes()
symbols = [s.decode() for s in re.findall(rb"[ -~]{6,}", binary)
           if b"window_state_saver" in s or b"WindowStateSaver.vala:" in s]
target.write_text(json.dumps(dict(mode=mode, application_id=app_id, display=os.getenv("DISPLAY"),
                                  database_before=before, database_after=rows(), events=events,
                                  gala_symbols=symbols), indent=2) + "\n")
print(target)
