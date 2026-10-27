"""A0.4 spike window: Adw, VTE, and a short IPC round-trip."""

from __future__ import annotations

import asyncio
import socket
import sys
import tempfile
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gtk, Vte  # noqa: E402

from aconsole.ui.bootstrap import run_gtk  # noqa: E402
from aconsole.ui.snapshot import after_first_paint, capture_window  # noqa: E402


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = round((pct / 100) * (len(ordered) - 1))
    return ordered[min(index, len(ordered) - 1)] * 1000


async def _echo_server(path: Path, ready: asyncio.Event) -> None:
    async def _client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            line = await reader.readline()
            if not line:
                break
            writer.write(line)
            await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(_client, path=str(path))
    ready.set()
    try:
        await server.serve_forever()
    finally:
        server.close()


def _ping(path: Path, count: int) -> float:
    latencies: list[float] = []
    for index in range(count):
        started = time.perf_counter()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(str(path))
            sock.sendall(f"{index}\n".encode())
            data = bytearray()
            while not data.endswith(b"\n"):
                chunk = sock.recv(64)
                if not chunk:
                    break
                data.extend(chunk)
        latencies.append(time.perf_counter() - started)
    return _percentile(latencies, 99)


def run_spike(*, snapshot: Path | None, autoquit: bool) -> float:
    """Open the spike window. Return the IPC p99 in milliseconds."""
    app = Adw.Application(application_id="com.aconsole.ui.spike")
    measured = {"p99": -1.0}

    def _on_activate(application: Adw.Application) -> None:
        window = Adw.ApplicationWindow(application=application, title="AConsole spike")
        window.set_default_size(640, 400)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        terminal = Vte.Terminal()
        terminal.set_vexpand(True)
        if not isinstance(terminal, Gtk.Widget):
            message = "Vte.Terminal is not a GTK widget"
            raise TypeError(message)
        box.append(terminal)
        window.set_content(box)

        async def _work() -> None:
            sock_path = Path(tempfile.mkdtemp(prefix="aconsole-spike-")) / "ipc.sock"
            ready = asyncio.Event()
            server = asyncio.create_task(_echo_server(sock_path, ready))
            _ = await ready.wait()
            measured["p99"] = await asyncio.to_thread(_ping, sock_path, 100)
            _ = sys.stdout.write(f"ipc_p99_ms={measured['p99']:.3f}\n")
            _ = sys.stdout.flush()
            _ = server.cancel()
            if snapshot is not None:
                capture_window(window, snapshot)
            if autoquit or snapshot is not None:
                application.quit()

        def _painted() -> None:
            _ = application.create_asyncio_task(_work())

        after_first_paint(window, _painted)
        _ = window.present()

    _ = app.connect("activate", _on_activate)
    _ = run_gtk(app, ["aconsole"])
    return measured["p99"]
