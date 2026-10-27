"""AConsole application lifecycle on the verified GTK asyncio loop."""

from __future__ import annotations

import logging
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, override

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.snapshot import after_first_paint, capture_window  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel  # noqa: E402
from aconsole.ui.views.window import MainWindow  # noqa: E402

if TYPE_CHECKING:
    from aconsole.ui.client.core_client import CoreClient

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotRequest:
    view: str
    theme: Literal["light", "dark"]
    width: int
    height: int
    out: Path


class AConsoleApp(Adw.Application):
    def __init__(
        self, client: CoreClient, snapshot: SnapshotRequest | None = None, *, scenario: str | None = None,
    ) -> None:
        super().__init__(application_id="io.github.aconsole.AConsole", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.client: CoreClient = client
        self.snapshot_request: SnapshotRequest | None = snapshot
        self.scenario: str | None = scenario
        self.window: MainWindow | None = None
        self.failed: bool = False
        self._starting: bool = False
        self._finishing: bool = False

    @override
    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_path(str(Path(__file__).parents[1] / "style" / "style.css"))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    @override
    def do_activate(self) -> None:
        if self.window is not None:
            self.window.present()
        elif not self._starting:
            self._starting = True
            self.hold()
            _ = spawn_task(self._open())

    async def _open(self) -> None:
        try:
            if self.scenario is None:
                _ = await self.client.connect()
            else:
                _ = await self.client.start_daemon([
                    sys.executable, "-m", "aconsole.core", "--fake", self.scenario, "--socket", self.client.path,
                ])
            _ = await self.client.pull_snapshot()
            request = self.snapshot_request
            if request is not None:
                settings = Gtk.Settings.get_default()
                if settings is not None:
                    settings.set_property("gtk-enable-animations", False)  # noqa: FBT003 -- GObject setter takes a positional value
                scheme = Adw.ColorScheme.FORCE_LIGHT if request.theme == "light" else Adw.ColorScheme.FORCE_DARK
                Adw.StyleManager.get_default().set_color_scheme(scheme)
            self.window = MainWindow(self, TabsViewModel(self.client))
            _ = self.window.connect("close-request", self._close_requested)
            if request is not None:
                self.window.set_default_size(request.width, request.height)
                after_first_paint(self.window, self._capture)
            self.window.present()
        except Exception:  # noqa: BLE001 -- report startup errors and exit instead of hanging the process
            self.failed = True
            _LOG.exception("Cannot start AConsole")
            await self._finish()
        finally:
            self.release()

    def _close_requested(self, _window: Gtk.Window) -> bool:
        _ = spawn_task(self._finish())
        return True

    def _capture(self) -> None:
        request = self.snapshot_request
        window = self.window
        if request is not None and window is not None:
            try:
                request.out.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    dir=request.out.parent, prefix=f".{request.out.stem}-", suffix=".png", delete=False
                ) as temporary:
                    capture_path = Path(temporary.name)
                try:
                    capture_window(window, capture_path)
                    if not capture_path.is_file() or capture_path.stat().st_size == 0:
                        message = "window capture did not produce a PNG"
                        raise RuntimeError(message)
                    _ = capture_path.replace(request.out)
                finally:
                    if capture_path.exists():
                        capture_path.unlink()
            except Exception:  # noqa: BLE001 -- snapshot failure must produce a failing CLI status
                self.failed = True
                _LOG.exception("Cannot capture AConsole")
        _ = spawn_task(self._finish())

    async def _finish(self) -> None:
        if self._finishing:
            return
        self._finishing = True
        try:
            await self.client.close()
        except Exception:  # noqa: BLE001 -- cleanup failure must produce a failing CLI status
            self.failed = True
            _LOG.exception("Cannot stop AConsole")
        finally:
            self.quit()
