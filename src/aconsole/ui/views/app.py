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

from aconsole.contracts.app import EmptyParams, QuitCheckResult  # noqa: E402
from aconsole.contracts.common import TabId  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_age import core_is_older_than_code  # noqa: E402
from aconsole.ui.client.error_text import core_log_path  # noqa: E402
from aconsole.ui.snapshot import after_first_paint, capture_window  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel  # noqa: E402
from aconsole.ui.views.approvals.alert import OPEN_APPROVAL_ACTION  # noqa: E402
from aconsole.ui.views.setup.messages import _ as translate  # noqa: E402
from aconsole.ui.views.window import MainWindow  # noqa: E402

if TYPE_CHECKING:
    from aconsole.ui.client.core_client import CoreClient

_LOG = logging.getLogger(__name__)
_STOP_DEADLINE_S = 5.0


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
        # A non-unique app needs the backend that can route notification clicks back to this process.
        _ = os.environ.setdefault("GNOTIFICATION_BACKEND", "freedesktop")
        super().__init__(application_id="io.github.aconsole.AConsole", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.client: CoreClient = client
        self.snapshot_request: SnapshotRequest | None = snapshot
        self.scenario: str | None = scenario
        self.core_command: list[str] | None = core_command
        self.window: MainWindow | None = None
        self.startup_window: Adw.ApplicationWindow | None = None
        self.startup_page: Adw.StatusPage | None = None
        self.startup_retry: Gtk.Button | None = None
        self.startup_diagnostics: Gtk.Button | None = None
        self._startup_error: str = ""
        self._retrying: bool = False
        self._opening_task: asyncio.Task[None] | None = None
        self.failed: bool = False
        self._starting: bool = False
        self._finishing: bool = False
        self._quit_checking: bool = False
        self._quit_dialog: Adw.AlertDialog | None = None

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

    def _open_approval(self, _action: Gio.SimpleAction, tab: GLib.Variant | None) -> None:
        if self.window is not None and tab is not None:
            self.window.open_approval(TabId(tab.get_string()))

    @override
    def do_activate(self) -> None:
        if self.window is not None:
            self.window.present()
        elif self.startup_window is not None:
            self.startup_window.present()
        elif not self._starting:
            self._starting = True
            if self.snapshot_request is None:
                self._show_startup()
            self.hold()
            self._opening_task = spawn_task(self._open())

    def _show_startup(self) -> None:
        self.startup_page = Adw.StatusPage(title=translate("startup.loading"))
        actions = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        self.startup_retry = Gtk.Button(label=translate("startup.retry"), visible=False)
        self.startup_retry.add_css_class("suggested-action")
        _ = self.startup_retry.connect("clicked", self._retry_startup)
        actions.append(self.startup_retry)
        self.startup_diagnostics = Gtk.Button(label=translate("startup.diagnostics"), visible=False)
        _ = self.startup_diagnostics.connect("clicked", self._copy_diagnostics)
        actions.append(self.startup_diagnostics)
        self.startup_page.set_child(actions)
        toolbar = Adw.ToolbarView(content=self.startup_page)
        toolbar.add_top_bar(Adw.HeaderBar())
        self.startup_window = Adw.ApplicationWindow(
            application=self, title="AConsole", content=toolbar, default_width=600, default_height=400,
        )
        _ = self.startup_window.connect("close-request", self._close_requested)
        self.startup_window.present()

    def _show_startup_error(self, error: Exception) -> None:
        self._startup_error = f"{error}\n{core_log_path()}"
        if self.startup_page is not None:
            self.startup_page.set_title(translate("startup.failed"))
            self.startup_page.set_description(str(error))
        for button in (self.startup_retry, self.startup_diagnostics):
            if button is not None:
                button.set_visible(True)
                button.set_sensitive(True)

    def _copy_diagnostics(self, _button: Gtk.Button) -> None:
        if self.startup_window is not None:
            self.startup_window.get_clipboard().set(self._startup_error)

    def _retry_startup(self, _button: Gtk.Button) -> None:
        if self._starting or self._finishing:
            return
        self._starting = True
        self._retrying = True
        self.failed = False
        if self.startup_page is not None:
            self.startup_page.set_title(translate("startup.loading"))
            self.startup_page.set_description("")
        for button in (self.startup_retry, self.startup_diagnostics):
            if button is not None:
                button.set_sensitive(False)
        self.hold()
        self._opening_task = spawn_task(self._open())

    async def _connect_core(self) -> None:
        if self._retrying:
            self._retrying = False
            await self.client.close()
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

    async def _open(self) -> None:
        try:
            await self._connect_core()
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
            if self.startup_window is not None:
                self.startup_window.destroy()
                self.startup_window = None
        except Exception as error:  # noqa: BLE001 -- keep startup failures visible and offer an explicit retry
            self.failed = True
            _LOG.exception("Cannot start AConsole")
            if self.startup_window is None:
                await self._finish()
            else:
                self._show_startup_error(error)
        finally:
            self._starting = False
            self.release()

    def _close_requested(self, window: Gtk.Window) -> bool:
        if self._finishing:
            return True
        if window is self.window:
            if self._quit_dialog is not None:
                self._quit_dialog.present(window)
            elif not self._quit_checking:
                self._quit_checking = True
                _ = spawn_task(self._confirm_quit(window))
        else:
            self._close_now(window)
        return True

    async def _confirm_quit(self, window: Gtk.Window) -> None:
        try:
            result = await self.client.request("app/quitCheck", EmptyParams(), QuitCheckResult)
        except Exception:  # noqa: BLE001 -- unknown process state requires explicit confirmation
            result = None
        finally:
            self._quit_checking = False
        if self._finishing:
            return
        if result is not None and not result.busy_tabs and not result.pending_approvals:
            self._close_now(window)
            return
        body = translate("quit.unknown") if result is None else translate("quit.counts").format(
            agents=len(result.busy_tabs), approvals=result.pending_approvals,
        )
        dialog = Adw.AlertDialog(heading=translate("quit.heading"), body=body + "\n\n" + translate("quit.effect"))
        dialog.add_response("cancel", translate("quit.cancel"))
        dialog.add_response("close", translate("quit.close"))
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        _ = dialog.connect("response", self._quit_decided, window)
        self._quit_dialog = dialog
        dialog.present(window)

    def _quit_decided(self, _dialog: Adw.AlertDialog, response: str, window: Gtk.Window) -> None:
        self._quit_dialog = None
        if response == "close":
            self._close_now(window)

    def _close_now(self, window: Gtk.Window) -> None:
        window.set_visible(False)
        if self._opening_task is not None and not self._opening_task.done():
            _ = self._opening_task.cancel()
        _ = spawn_task(self._finish())

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
