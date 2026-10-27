"""Prove Gio.Application.run plus GLibEventLoop on this PyGObject."""

from __future__ import annotations

import asyncio
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.events import GLibEventLoop
from gi.repository import Adw


async def _prove(app: Adw.Application) -> None:
    await asyncio.sleep(0.3)
    loop = asyncio.get_running_loop()
    sys.stdout.write(f"loop={type(loop).__name__}\n")
    sys.stdout.flush()
    app.quit()


def _on_activate(app: Adw.Application) -> None:
    window = Adw.ApplicationWindow(application=app, title="probe")
    window.set_default_size(320, 200)
    window.present()
    app.create_asyncio_task(_prove(app))


def main() -> None:
    with GLibEventLoop() as loop:
        asyncio.set_event_loop(loop)
        app = Adw.Application(application_id="com.aconsole.probe")
        app.connect("activate", _on_activate)
        status = app.run([sys.argv[0]])
    sys.stdout.write(f"exit={status}\n")
    raise SystemExit(status)


if __name__ == "__main__":
    main()
