"""Inspect actual desktop geometry without changing window or layout policy."""

# ruff: noqa: INP001
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gtk  # noqa: E402

from aconsole.core.infrastructure.fake.server import FakeCore  # noqa: E402
from aconsole.ui.bootstrap import run_app  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.views.app import AConsoleApp, SnapshotRequest  # noqa: E402

OUTPUT = Path(__file__).resolve().parent


class Probe(AConsoleApp):
    def _capture(self) -> None:
        window = self.window
        if window is not None:
            surface = window.get_surface()
            data = {
                "requested": [700, 800],
                "default_size": list(window.get_default_size()),
                "actual": [window.get_width(), window.get_height()],
                "maximized": window.is_maximized(),
                "fullscreen": window.is_fullscreen(),
                "surface_state": str(surface.get_state()) if isinstance(surface, Gdk.Toplevel) else None,
                "surface_state_nicks": surface.get_state().value_nicks if isinstance(surface, Gdk.Toplevel) else None,
                "collapsed": window.split.get_collapsed(),
                "environment": {name: os.environ.get(name) for name in (
                    "GDK_BACKEND", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "DESKTOP_SESSION",
                )},
            }
            (OUTPUT / "geometry.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        super()._capture()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="qa19-geometry-", dir="/tmp") as directory:
        root = Path(directory)
        for name in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
            os.environ[name] = str(root / name)
        core = FakeCore(str(root / "core.sock"), "demo")
        core.start()
        try:
            request = SnapshotRequest(view="window", theme="light", width=700, height=800, out=root / "window.png")
            app = Probe(CoreClient(str(root / "core.sock")), request)
            status = run_app(lambda: app, ["qa19-geometry"])
        finally:
            for window in Gtk.Window.list_toplevels():
                window.destroy()
            core.stop()
        raise SystemExit(status)
