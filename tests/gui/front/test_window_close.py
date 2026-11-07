"""Closing after confirmation ends the whole process, whatever state the window is in."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

_DRIVER = textwrap.dedent(
    """
    import faulthandler, sys, tempfile, time
    from pathlib import Path
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib
    from aconsole.ui.bootstrap import run_app
    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.views.app import AConsoleApp

    faulthandler.dump_traceback_later(25, exit=True)
    delay_ms, state = int(sys.argv[1]), sys.argv[2]
    start = time.monotonic()

    def log(text):
        print(f"{time.monotonic() - start:.2f} {text}", flush=True)

    class App(AConsoleApp):
        def do_activate(self):
            AConsoleApp.do_activate(self)
            GLib.timeout_add(delay_ms, self.drive)

        def drive(self):
            window = self.window
            if window is None:
                return GLib.SOURCE_CONTINUE
            if state == "dialog":
                window.quota_card.open_details(window)
            if state == "plan":
                window.plan_card.emit("clicked")
            GLib.timeout_add(500, self.close_it)
            return GLib.SOURCE_REMOVE

        def _close_requested(self, window):
            log(f"close-request visible={window.get_visible()} dialog={window.get_visible_dialog()}")
            return AConsoleApp._close_requested(self, window)

        async def _finish(self):
            log("finish start")
            await AConsoleApp._finish(self)
            log("finish end")

        def close_it(self):
            log("close")
            self.window.close()
            GLib.timeout_add(50, self.confirm_close)
            # libadwaita gives the first close to an open dialog; the next one closes the window.
            return GLib.SOURCE_CONTINUE if state == "dialog" else GLib.SOURCE_REMOVE

        def confirm_close(self):
            if self._quit_dialog is not None:
                self._quit_dialog.emit("response", "close")
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

    with tempfile.TemporaryDirectory() as directory:
        client = CoreClient(str(Path(directory) / "core.sock"))
        status = run_app(lambda: App(client, None, scenario="demo"), ["aconsole-close-test"])
        log(f"run returned {status}")
    log("leaving")
    """,
)


@pytest.mark.parametrize(
    ("delay_ms", "state"),
    [(1000, "plain"), (6000, "plain"), (1000, "dialog"), (1000, "plan")],
)
def test_closing_the_window_ends_the_process(delay_ms: int, state: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _DRIVER, str(delay_ms), state],
        capture_output=True, text=True, timeout=40, check=False, env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "run returned 0" in result.stdout, result.stdout + result.stderr
