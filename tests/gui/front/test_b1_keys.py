"""B1 keyboard routing through the window shortcut controller and VTE."""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import os
import signal
from contextlib import suppress
from textwrap import dedent
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkX11", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, GdkX11, GLib, Gtk, Vte  # noqa: E402

from aconsole.contracts.settings import Settings  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.views.app import AConsoleApp, SnapshotRequest  # noqa: E402
from aconsole.ui.views.shortcuts import ShortcutManager  # noqa: E402, F401

if TYPE_CHECKING:
    from pathlib import Path

    import msgspec
    import pytest

    from aconsole.ui.viewmodels.logic.keymap import BindingSpec


class _XTestKeyboard:
    """Send real key events through the test window's X11 focus."""

    def __init__(self, window: Gtk.Window) -> None:
        x11_path = ctypes.util.find_library("X11")
        xtst_path = ctypes.util.find_library("Xtst")
        assert x11_path is not None, "X11 library is required"
        assert xtst_path is not None, "XTest library is required"
        self.x11 = ctypes.CDLL(x11_path)
        self.xtst = ctypes.CDLL(xtst_path)
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        self.x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.x11.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x11.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        self.x11.XGetInputFocus.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int),
        ]
        self.xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        self.xtst.XTestFakeKeyEvent.restype = ctypes.c_int
        self.display = self.x11.XOpenDisplay(None)
        assert self.display, "XTest needs an X11 display; this GUI session has no X display"
        try:
            surface = window.get_surface()
            assert isinstance(surface, GdkX11.X11Surface), "the GUI window is not backed by X11"
            self.window_id = GdkX11.X11Surface.get_xid(surface)
            self.x11.XRaiseWindow(self.display, self.window_id)
            self.x11.XSetInputFocus(self.display, self.window_id, 2, 0)
            self.x11.XSync(self.display, 0)
            focused = ctypes.c_ulong()
            revert_to = ctypes.c_int()
            self.x11.XGetInputFocus(self.display, ctypes.byref(focused), ctypes.byref(revert_to))
            assert focused.value == self.window_id, "test window did not take X keyboard focus"
        except (AssertionError, TypeError, ValueError):
            self.close()
            raise

    def _event(self, keysym: int, *, pressed: bool) -> None:
        keycode = self.x11.XKeysymToKeycode(self.display, keysym)
        assert keycode, f"X11 has no keycode for keysym {keysym:#x}"
        assert self.xtst.XTestFakeKeyEvent(self.display, keycode, int(pressed), 0)

    def press(self, keysym: int, *, control: bool = False, shift: bool = False) -> None:
        modifiers: list[int] = []
        if control:
            modifiers.append(Gdk.KEY_Control_L)
        if shift:
            modifiers.append(Gdk.KEY_Shift_L)
        for modifier in modifiers:
            self._event(modifier, pressed=True)
        self._event(keysym, pressed=True)
        self._event(keysym, pressed=False)
        for modifier in reversed(modifiers):
            self._event(modifier, pressed=False)
        self.x11.XSync(self.display, 0)

    def close(self) -> None:
        if self.display:
            self.x11.XCloseDisplay(self.display)
            self.display = None


class _KeyboardApp(AConsoleApp):
    def __init__(self, client: CoreClient, ready_path: Path, result_path: Path) -> None:
        super().__init__(client)
        self.terminal: Vte.Terminal | None = None
        self.ready_path = ready_path
        self.result_path = result_path
        self.actions: list[str] = []
        self.commit_stream = bytearray()
        self.input_error: Exception | None = None
        self.focus_in = False
        self.keyboard: _XTestKeyboard | None = None
        self.child_pid: int | None = None
        self._pty_polls = 0
        self._result_polls = 0
        self._watchdog_id: int | None = None
        self._finished = False

    async def _open(self) -> None:
        self._watchdog_id = GLib.timeout_add_seconds(10, self._watchdog)
        try:
            await super()._open()
            window = self.window
            if window is None:
                self.input_error = AssertionError("application opened without a window")
                self._finish_app()
                return
            terminal = Vte.Terminal()
            self.terminal = terminal
            window.set_content(terminal)
            assert window.shortcut_manager is not None
            window.app_vm.on_action = self.actions.append
            terminal.connect("commit", self._committed)
            child_code = dedent(
                f"""
                import os
                import sys
                import tty

                fd = sys.stdin.fileno()
                tty.setraw(fd)
                open({str(self.ready_path)!r}, "wb").close()
                data = bytearray()
                while len(data) < 7:
                    chunk = os.read(fd, 7 - len(data))
                    if not chunk:
                        break
                    data.extend(chunk)
                open({str(self.result_path)!r}, "wb").write(data)
                """
            )
            terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                None,
                ["/usr/bin/python3", "-c", child_code],
                None,
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                -1,
                None,
                self._spawned,
                None,
            )
            focus_controller = Gtk.EventControllerFocus()
            focus_controller.connect("enter", self._focus_entered)
            window.add_controller(focus_controller)
            self.keyboard = _XTestKeyboard(window)
            window.present()
            terminal.grab_focus()
            GLib.timeout_add(10, self._wait_for_pty_and_focus)
        except Exception as error:  # noqa: BLE001 -- keep async startup failures observable to the test
            self.input_error = error
            self._finish_app()

    def _committed(self, _terminal: Vte.Terminal, text: str, _length: int) -> None:
        self.commit_stream.extend(text.encode("utf-8"))

    def _spawned(
        self,
        _terminal: Vte.Terminal,
        _pid: int,
        error: GLib.Error | None,
        _data: object,
    ) -> None:
        self.child_pid = _pid
        if error is not None:
            self.input_error = RuntimeError(str(error))

    def _focus_entered(self, _controller: Gtk.EventControllerFocus) -> None:
        self.focus_in = True

    def _wait_for_pty_and_focus(self) -> bool:
        terminal = self.terminal
        waiting_for_pty = not self.ready_path.exists()
        waiting_for_focus = not self.focus_in or terminal is None or not terminal.has_focus()
        if waiting_for_pty or waiting_for_focus:
            self._pty_polls += 1
            if self._pty_polls < 100:
                return GLib.SOURCE_CONTINUE
            self.input_error = AssertionError("GTK focus or VTE PTY did not become ready")
            GLib.idle_add(self._finish_app)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(self._send_keys)
        return GLib.SOURCE_REMOVE

    def _send_keys(self) -> bool:
        keyboard = self.keyboard
        try:
            assert keyboard is not None
            keyboard.press(Gdk.KEY_p, control=True, shift=True)
            for keyval, control, shift in (
                (Gdk.KEY_k, True, False),
                (Gdk.KEY_t, True, False),
                (Gdk.KEY_w, True, False),
                (Gdk.KEY_v, True, False),
                (Gdk.KEY_Tab, False, True),
            ):
                keyboard.press(keyval, control=control, shift=shift)
        except (AssertionError, OSError, RuntimeError, TypeError) as error:
            self.input_error = error
        finally:
            if keyboard is not None:
                keyboard.close()
                self.keyboard = None
        GLib.timeout_add(10, self._wait_for_result)
        return GLib.SOURCE_REMOVE

    def _wait_for_result(self) -> bool:
        if not self.result_path.exists() and self._result_polls < 100:
            self._result_polls += 1
            return GLib.SOURCE_CONTINUE
        if not self.result_path.exists():
            self.input_error = AssertionError("VTE PTY child did not return the key stream")
        return self._finish_app()

    def _watchdog(self) -> bool:
        self._watchdog_id = None
        self.input_error = TimeoutError("B1 keyboard GUI test exceeded its 10 second watchdog")
        return self._finish_app()

    def _finish_app(self) -> bool:
        if self._finished:
            return GLib.SOURCE_REMOVE
        self._finished = True
        try:
            self._close_keyboard()
            self._stop_child()
            self._close_pty()
            self._close_window()
        finally:
            self._remove_watchdog()
            self.quit()
        return GLib.SOURCE_REMOVE

    def _remove_watchdog(self) -> None:
        if self._watchdog_id is not None:
            GLib.source_remove(self._watchdog_id)
            self._watchdog_id = None

    def _close_keyboard(self) -> None:
        if self.keyboard is None:
            return
        try:
            self.keyboard.close()
        except (OSError, RuntimeError, TypeError):
            self.input_error = self.input_error or RuntimeError("failed to close XTest display")
        self.keyboard = None

    def _stop_child(self) -> None:
        if self.child_pid is None or self.result_path.exists():
            return
        with suppress(ProcessLookupError, PermissionError):
            os.kill(self.child_pid, signal.SIGTERM)

    def _close_pty(self) -> None:
        if self.terminal is None:
            return
        try:
            pty = self.terminal.get_pty()
            if pty is not None:
                pty.close()
        except (GLib.Error, OSError, RuntimeError, TypeError):
            self.input_error = self.input_error or RuntimeError("failed to close VTE PTY")

    def _close_window(self) -> None:
        if self.window is None:
            return
        try:
            self.window.set_visible(False)
        except (GLib.Error, OSError, RuntimeError, TypeError):
            self.input_error = self.input_error or RuntimeError("failed to hide B1 test window")
        try:
            self.window.destroy()
        except (GLib.Error, OSError, RuntimeError, TypeError):
            self.input_error = self.input_error or RuntimeError("failed to destroy B1 test window")


class _SettingsProbeApp(AConsoleApp):
    def __init__(self, client: CoreClient) -> None:
        super().__init__(client)
        self.applied_bindings: tuple[BindingSpec, ...] = ()
        self.controller_triggers: set[tuple[int, int]] = set()
        self.input_error: Exception | None = None
        self._polls = 0
        self._watchdog_id: int | None = None
        self._finished = False

    async def _open(self) -> None:
        self._watchdog_id = GLib.timeout_add_seconds(8, self._watchdog)
        try:
            await super()._open()
            if self.window is None:
                self.input_error = AssertionError("application opened without a window")
                self._finish_probe()
                return
            GLib.timeout_add(10, self._wait_for_override)
        except Exception as error:  # noqa: BLE001 -- retain async startup failures for assertions
            self.input_error = error
            self._finish_probe()

    def _wait_for_override(self) -> bool:
        window = self.window
        if window is None:
            self.input_error = AssertionError("window disappeared before settings applied")
            self._finish_probe()
            return GLib.SOURCE_REMOVE
        self.applied_bindings = tuple(window.app_vm.bindings)
        palette = next((item for item in self.applied_bindings if item.action_id == "palette"), None)
        if palette is not None and palette.accelerators == ("<Control><Alt>p",):
            controller = window.shortcut_manager.controller
            self.controller_triggers = _controller_triggers(controller)
            self._finish_probe()
            return GLib.SOURCE_REMOVE
        self._polls += 1
        if self._polls >= 100:
            self.input_error = TimeoutError("settings override was not applied within one second")
            self._finish_probe()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _watchdog(self) -> bool:
        self._watchdog_id = None
        self.input_error = TimeoutError("settings GUI test exceeded its 8 second watchdog")
        self._close_window()
        self.quit()
        return GLib.SOURCE_REMOVE

    def _finish_probe(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._close_window()
        _ = spawn_task(self._close_client())

    async def _close_client(self) -> None:
        try:
            await self.client.close()
        except Exception as error:  # noqa: BLE001 -- surface close failures rather than leaving app active
            self.input_error = self.input_error or error
        finally:
            if self._watchdog_id is not None:
                GLib.source_remove(self._watchdog_id)
                self._watchdog_id = None
            self.quit()

    def _close_window(self) -> None:
        if self.window is None:
            return
        try:
            self.window.set_visible(False)
        except (GLib.Error, OSError, RuntimeError, TypeError):
            self.input_error = self.input_error or RuntimeError("failed to hide settings test window")
        try:
            self.window.destroy()
        except (GLib.Error, OSError, RuntimeError, TypeError):
            self.input_error = self.input_error or RuntimeError("failed to destroy settings test window")


class _SettingsRaceProbeApp(AConsoleApp):
    def __init__(self, client: CoreClient) -> None:
        super().__init__(client)
        self.settings_started = 0
        self.settings_returned = 0
        self.old_response: asyncio.Future[Settings] | None = None
        self.old_response_released = False
        self.final_bindings: tuple[BindingSpec, ...] = ()
        self.final_triggers: set[tuple[int, int]] = set()
        self.input_error: Exception | None = None
        self._settle_polls = 0
        self._polls = 0
        self._watchdog_id: int | None = None

    async def _open(self) -> None:
        self._watchdog_id = GLib.timeout_add_seconds(8, self._watchdog)
        try:
            await super()._open()
            if self.window is None:
                self.input_error = AssertionError("application opened without a window")
                self._finish_probe()
                return
            window = self.window
            _ = spawn_task(window._refresh_settings())
            GLib.timeout_add(10, self._wait_for_race)
        except Exception as error:  # noqa: BLE001 -- retain async startup failures for assertions
            self.input_error = error
            self._finish_probe()

    def _release_old_response(self) -> bool:
        future = self.old_response
        if future is not None and not future.done():
            future.set_result(
                Settings(
                    rev=1,
                    vault_path="/vault",
                    worktrees_dir="/worktrees",
                    keymap_overrides={"palette": "<Control><Alt>p"},
                )
            )
        self.old_response_released = True
        return GLib.SOURCE_REMOVE

    def _wait_for_race(self) -> bool:
        if self.settings_returned >= 2 and self.old_response_released:
            self._settle_polls += 1
            if self._settle_polls >= 5:
                window = self.window
                if window is None:
                    self.input_error = AssertionError("window disappeared before race completed")
                else:
                    self.final_bindings = tuple(window.app_vm.bindings)
                    self.final_triggers = _controller_triggers(window.shortcut_manager.controller)
                self._finish_probe()
                return GLib.SOURCE_REMOVE
        self._polls += 1
        if self._polls >= 100:
            self.input_error = TimeoutError("settings revision race did not settle within one second")
            self._finish_probe()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _watchdog(self) -> bool:
        self._watchdog_id = None
        self.input_error = TimeoutError("settings revision GUI test exceeded its 8 second watchdog")
        self._close_window()
        self.quit()
        return GLib.SOURCE_REMOVE

    def _finish_probe(self) -> None:
        self._close_window()
        _ = spawn_task(self._close_client())

    async def _close_client(self) -> None:
        try:
            await self.client.close()
        except Exception as error:  # noqa: BLE001 -- surface close failures rather than leave app active
            self.input_error = self.input_error or error
        finally:
            if self._watchdog_id is not None:
                GLib.source_remove(self._watchdog_id)
                self._watchdog_id = None
            self.quit()

    def _close_window(self) -> None:
        if self.window is None:
            return
        try:
            self.window.set_visible(False)
        except (GLib.Error, OSError, RuntimeError, TypeError):
            self.input_error = self.input_error or RuntimeError("failed to hide settings-race test window")
        try:
            self.window.destroy()
        except (GLib.Error, OSError, RuntimeError, TypeError):
            self.input_error = self.input_error or RuntimeError("failed to destroy settings-race test window")


class _ShortcutActionProbeApp(AConsoleApp):
    def __init__(self, client: CoreClient) -> None:
        super().__init__(client)
        self.actions: list[str] = []
        self.action_activated = False
        self.input_error: Exception | None = None
        self._watchdog_id: int | None = None

    async def _open(self) -> None:
        self._watchdog_id = GLib.timeout_add_seconds(8, self._watchdog)
        try:
            await super()._open()
            if self.window is None:
                self.input_error = AssertionError("application opened without a window")
                self._finish_probe()
                return
            self.window.app_vm.on_action = self.actions.append
            GLib.idle_add(self._activate_palette)
        except Exception as error:  # noqa: BLE001 -- retain async startup failures for assertions
            self.input_error = error
            self._finish_probe()

    def _activate_palette(self) -> bool:
        window = self.window
        try:
            assert window is not None
            controller = window.shortcut_manager.controller
            modifiers = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
            palette_found = False
            for index in range(controller.get_n_items()):
                shortcut = controller.get_item(index)
                if not isinstance(shortcut, Gtk.Shortcut):
                    continue
                trigger = shortcut.get_trigger()
                if not isinstance(trigger, Gtk.KeyvalTrigger):
                    continue
                if trigger.get_keyval() != Gdk.KEY_p or trigger.get_modifiers() != modifiers:
                    continue
                action = shortcut.get_action()
                assert isinstance(action, Gtk.ShortcutAction)
                self.action_activated = action.activate(
                    Gtk.ShortcutActionFlags.EXCLUSIVE,
                    window,
                    None,
                )
                palette_found = True
                break
            if not palette_found:
                self.input_error = AssertionError("registered Ctrl+Shift+P palette shortcut was not found")
        except (AssertionError, GLib.Error, RuntimeError, TypeError, ValueError) as error:
            self.input_error = error
        self._finish_probe()
        return GLib.SOURCE_REMOVE

    def _watchdog(self) -> bool:
        self._watchdog_id = None
        self.input_error = TimeoutError("shortcut-action GUI test exceeded its 8 second watchdog")
        self._close_window()
        self.quit()
        return GLib.SOURCE_REMOVE

    def _finish_probe(self) -> None:
        self._close_window()
        _ = spawn_task(self._close_client())

    async def _close_client(self) -> None:
        try:
            await self.client.close()
        except Exception as error:  # noqa: BLE001 -- surface close failures rather than leave app active
            self.input_error = self.input_error or error
        finally:
            if self._watchdog_id is not None:
                GLib.source_remove(self._watchdog_id)
                self._watchdog_id = None
            self.quit()

    def _close_window(self) -> None:
        if self.window is None:
            return
        try:
            self.window.set_visible(False)
        except (GLib.Error, OSError, RuntimeError, TypeError):
            self.input_error = self.input_error or RuntimeError("failed to hide shortcut-action test window")
        try:
            self.window.destroy()
        except (GLib.Error, OSError, RuntimeError, TypeError):
            self.input_error = self.input_error or RuntimeError("failed to destroy shortcut-action test window")


def _controller_triggers(controller: Gtk.ShortcutController) -> set[tuple[int, int]]:
    triggers: set[tuple[int, int]] = set()
    for index in range(controller.get_n_items()):
        shortcut = controller.get_item(index)
        if not isinstance(shortcut, Gtk.Shortcut):
            continue
        trigger = shortcut.get_trigger()
        if isinstance(trigger, Gtk.KeyvalTrigger):
            triggers.add((trigger.get_keyval(), int(trigger.get_modifiers())))
    return triggers


def test_b1_keys_settings_override_updates_controller(
    running_app: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A settings override replaces the palette accelerator in the active window controller."""
    client = CoreClient(running_app)
    original_request = client.request
    settings = Settings(
        rev=17,
        vault_path="/vault",
        worktrees_dir="/worktrees",
        keymap_overrides={"palette": "<Control><Alt>p"},
    )
    requests: list[str] = []

    async def request_with_override(
        method: str,
        params: msgspec.Struct,
        result_type: type[msgspec.Struct],
        *,
        long: bool = False,
    ) -> msgspec.Struct:
        requests.append(method)
        if method == "settings/get":
            assert result_type is Settings
            return settings
        return await original_request(method, params, result_type, long=long)

    monkeypatch.setattr(client, "request", request_with_override)
    app = _SettingsProbeApp(client)
    try:
        assert run_app(lambda: app, ["aconsole-test-b1-settings"]) == 0
    finally:
        if not app._finished:
            app._finish_probe()
        app._close_window()

    assert app.input_error is None, app.input_error
    assert "settings/get" in requests
    assert {"app/snapshot", "events/subscribe"} <= set(requests)
    palette = next(item for item in app.applied_bindings if item.action_id == "palette")
    assert palette.accelerators == ("<Control><Alt>p",)
    assert (Gdk.KEY_p, int(Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK)) in app.controller_triggers
    assert (Gdk.KEY_p, int(Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)) not in app.controller_triggers


def test_b1_keys_older_settings_response_cannot_replace_keymap(
    running_app: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An older valid settings reply cannot replace defaults after a newer invalid reply."""
    client = CoreClient(running_app)
    original_request = client.request
    app = _SettingsRaceProbeApp(client)
    settings_rev_2 = Settings(
        rev=2,
        vault_path="/vault",
        worktrees_dir="/worktrees",
        keymap_overrides={"unknown-action": "<Control>x"},
    )

    async def request_with_race(
        method: str,
        params: msgspec.Struct,
        result_type: type[msgspec.Struct],
        *,
        long: bool = False,
    ) -> msgspec.Struct:
        if method != "settings/get":
            return await original_request(method, params, result_type, long=long)
        app.settings_started += 1
        if app.settings_started == 1:
            app.old_response = asyncio.get_running_loop().create_future()
            response = await app.old_response
        elif app.settings_started == 2:
            GLib.timeout_add(50, app._release_old_response)
            response = settings_rev_2
        else:
            response = settings_rev_2
        app.settings_returned += 1
        return response

    monkeypatch.setattr(client, "request", request_with_race)
    try:
        assert run_app(lambda: app, ["aconsole-test-b1-settings-race"]) == 0
    finally:
        app._close_window()
        if app._watchdog_id is not None:
            GLib.source_remove(app._watchdog_id)
            app._watchdog_id = None

    assert app.input_error is None, app.input_error
    assert app.settings_started >= 2
    assert app.settings_returned >= 2
    assert app.old_response_released
    palette = next(item for item in app.final_bindings if item.action_id == "palette")
    assert palette.accelerators == ("<Control><Shift>p",)
    assert (Gdk.KEY_p, int(Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)) in app.final_triggers
    assert (Gdk.KEY_p, int(Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK)) not in app.final_triggers


def test_b1_keys_palette_shortcut_action_dispatches(running_app: str) -> None:
    """Activating the registered palette ShortcutAction calls the app view model action."""
    app = _ShortcutActionProbeApp(CoreClient(running_app))
    try:
        assert run_app(lambda: app, ["aconsole-test-b1-palette-action"]) == 0
    finally:
        app._close_window()
        if app._watchdog_id is not None:
            GLib.source_remove(app._watchdog_id)
            app._watchdog_id = None
    assert app.input_error is None, app.input_error
    assert app.action_activated
    assert app.actions == ["palette"]


def _walk(widget: Gtk.Widget) -> list[Gtk.Widget]:
    result = [widget]
    child = widget.get_first_child()
    while child is not None:
        result.extend(_walk(child))
        child = child.get_next_sibling()
    return result


def test_b1_keys_terminal_passthrough(running_app: str, tmp_path: Path) -> None:
    """Ctrl+Shift+P opens the palette; Ctrl+K/T/W/V and Shift+Tab reach focused VTE."""
    ready_path = tmp_path / "pty-ready"
    result_path = tmp_path / "pty-result"
    app = _KeyboardApp(CoreClient(running_app), ready_path, result_path)
    try:
        assert run_app(lambda: app, ["aconsole-test-b1-keys"]) == 0
    finally:
        app._finish_app()
    assert app.input_error is None, app.input_error
    assert app.actions == ["palette"]
    expected = b"\x0b\x14\x17\x16\x1b[Z"
    assert bytes(app.commit_stream) == expected
    assert result_path.read_bytes() == expected


def test_b1_keys_initial_shortcut_controller(running_app: str, tmp_path: Path) -> None:
    """Ctrl+Shift+P is registered on the window controller in CAPTURE phase at startup."""
    request = SnapshotRequest(
        view="window", theme="light", width=1280, height=800, out=tmp_path / "b1-controller.png",
    )
    app = AConsoleApp(CoreClient(running_app), request)
    try:
        assert run_app(lambda: app, ["aconsole-test-b1-controller"]) == 0
        assert not app.failed
        window = app.window
        assert window is not None
        controller = window.shortcut_manager.controller
        assert controller.get_propagation_phase() is Gtk.PropagationPhase.CAPTURE
        assert controller.get_n_items() > 0
        palette_registered = False
        for index in range(controller.get_n_items()):
            shortcut = controller.get_item(index)
            assert isinstance(shortcut, Gtk.Shortcut)
            trigger = shortcut.get_trigger()
            if not isinstance(trigger, Gtk.KeyvalTrigger):
                continue
            modifiers = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
            if trigger.get_keyval() == Gdk.KEY_p and trigger.get_modifiers() == modifiers:
                palette_registered = True
                break
        assert palette_registered, "Ctrl+Shift+P palette shortcut was not installed at startup"
    finally:
        if app.window is not None:
            app.window.set_visible(False)
            app.window.destroy()


def test_b1_keys_narrow_window(running_app: str, tmp_path: Path) -> None:
    """At 700 px the sidebar collapses and the content header keeps title-button space."""
    request = SnapshotRequest(
        view="window", theme="light", width=700, height=800, out=tmp_path / "b1-narrow.png",
    )
    app = AConsoleApp(CoreClient(running_app), request)
    try:
        assert run_app(lambda: app, ["aconsole-test-b1-narrow"]) == 0
    finally:
        if app.window is not None:
            app.window.set_visible(False)
            app.window.destroy()
    assert not app.failed
    window = app.window
    assert window is not None
    assert window.shortcut_manager is not None
    assert window.split.get_collapsed()
    headers = [item for item in _walk(window) if isinstance(item, Adw.HeaderBar)]
    assert len(headers) == 2
    sidebar_header = next(header for header in headers if not header.get_show_end_title_buttons())
    content_header = next(header for header in headers if header.get_show_end_title_buttons())
    assert not sidebar_header.get_show_start_title_buttons()
    assert content_header.get_show_start_title_buttons()
    assert content_header.get_show_end_title_buttons()
