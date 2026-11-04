"""AConsole application lifecycle on the verified GTK asyncio loop."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, override

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_age import core_is_older_than_code  # noqa: E402
from aconsole.ui.snapshot import after_first_paint, capture_window  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel  # noqa: E402
from aconsole.contracts.common import TabId  # noqa: E402
from aconsole.ui.views.approvals.alert import OPEN_APPROVAL_ACTION  # noqa: E402
from aconsole.ui.views.startup import StartupWindow  # noqa: E402
from aconsole.ui.views.window import MainWindow  # noqa: E402

if TYPE_CHECKING:
    from aconsole.ui.client.core_client import CoreClient

_LOG = logging.getLogger(__name__)
_STOP_DEADLINE_S = 5.0
_STARTUP_WINDOW_DELAY_MS = 300


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotRequest:
    view: str
    theme: Literal["light", "dark"]
    width: int
    height: int
    out: Path


class AConsoleApp(Adw.Application):
    def __init__(
        self,
        client: CoreClient,
        snapshot: SnapshotRequest | None = None,
        *,
        scenario: str | None = None,
        core_command: list[str] | None = None,
    ) -> None:
        # A non-unique app owns no bus name, so the shell's own backend could not call it back on a click.
        _ = os.environ.setdefault("GNOTIFICATION_BACKEND", "freedesktop")
        super().__init__(application_id="io.github.aconsole.AConsole", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.client: CoreClient = client
        self.snapshot_request: SnapshotRequest | None = snapshot
        self.scenario: str | None = scenario
        self.core_command: list[str] | None = core_command
        self.window: MainWindow | None = None
        self.failed: bool = False
        self._starting: bool = False
        self._finishing: bool = False
        self.startup_window: StartupWindow | None = None
        self._startup_timer: int | None = None

    @override
    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_path(str(Path(__file__).parents[1] / "style" / "style.css"))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        open_tab = Gio.SimpleAction.new(OPEN_APPROVAL_ACTION, GLib.VariantType.new("s"))
        _ = open_tab.connect("activate", self._open_approval)
        self.add_action(open_tab)

    @override
    def do_activate(self) -> None:
        if self.window is not None:
            self.window.present()
        elif not self._starting:
            self._starting = True
            self.hold()
            if self.snapshot_request is None:
                self.startup_window = StartupWindow(self)
                _ = self.startup_window.connect("close-request", self._startup_closed)
                # A start that takes no time should not flash a window first.
                self._startup_timer = GLib.timeout_add(_STARTUP_WINDOW_DELAY_MS, self._show_starting)
            _ = spawn_task(self._open())

    def _open_approval(self, _action: Gio.SimpleAction, tab: GLib.Variant | None) -> None:
        if self.window is not None and tab is not None:
            self.window.open_approval(TabId(tab.get_string()))

    def _show_starting(self) -> bool:
        self._startup_timer = None
        if self.startup_window is not None:
            self.startup_window.present()
        return GLib.SOURCE_REMOVE

    def _startup_over(self) -> None:
        if self._startup_timer is not None:
            _ = GLib.source_remove(self._startup_timer)
            self._startup_timer = None
        if self.startup_window is not None:
            window, self.startup_window = self.startup_window, None
            window.destroy()

    async def _open(self) -> None:
        try:
            if self.core_command is not None:
                self.client.keep_daemon = True
                Path(self.client.path).parent.mkdir(parents=True, exist_ok=True)
                runtime = Path(self.client.path).parents[1]
                started = await self.client.start_daemon(self.core_command, runtime=runtime)
                if core_is_older_than_code(started.server_pid):
                    _LOG.info("Core %d is older than the code, restarting it", started.server_pid)
                    _ = await self.client.replace_daemon(started.server_pid, self.core_command, runtime=runtime)
            elif self.scenario is None:
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
            self._startup_over()
        except Exception as error:  # noqa: BLE001 -- a start that failed must say so and never hang the process
            self.failed = True
            _LOG.exception("Cannot start AConsole")
            if self.startup_window is None:
                await self._finish()
            else:
                self._show_failure(error)
        finally:
            self.release()

    def _startup_closed(self, _window: Gtk.Window) -> bool:
        if self.failed:
            self._give_up()
        else:
            _ = spawn_task(self._finish())
        return False

    def _show_failure(self, error: Exception) -> None:
        """Stay open, or the person is left with a window that vanished and no reason."""
        if self._startup_timer is not None:
            _ = GLib.source_remove(self._startup_timer)
            self._startup_timer = None
        if self.startup_window is None:
            return
        self.hold()
        self.startup_window.show_failure(str(error) or type(error).__name__, self._retry_start, self._give_up)

    def _retry_start(self) -> None:
        if self.startup_window is None:
            return
        self.failed = False
        self.startup_window.show_starting()
        # The hold from the failure stays: _open releases it when it is done.
        _ = spawn_task(self._open())

    def _give_up(self) -> None:
        self._startup_over()
        self.release()
        _ = spawn_task(self._finish())

    def _close_requested(self, window: Gtk.Window) -> bool:
        # The window goes at once; stopping the core must never be what the user is left looking at.
        window.set_visible(False)
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
            await asyncio.wait_for(self.client.close(), _STOP_DEADLINE_S)
        except TimeoutError:
            _LOG.warning("The core did not stop in time")
        except Exception:  # noqa: BLE001 -- cleanup failure must produce a failing CLI status
            self.failed = True
            _LOG.exception("Cannot stop AConsole")
        finally:
            self.quit()
