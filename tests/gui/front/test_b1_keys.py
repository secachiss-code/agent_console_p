"""B1 keyboard routing through the window shortcut controller and VTE."""

from __future__ import annotations

import ctypes
import ctypes.util
from textwrap import dedent
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkX11", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, GdkX11, GLib, Gtk, Vte  # noqa: E402

from aconsole.ui.bootstrap import run_app  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.views.app import AConsoleApp, SnapshotRequest  # noqa: E402
from aconsole.ui.views.shortcuts import ShortcutManager  # noqa: E402, F401

if TYPE_CHECKING:
    from pathlib import Path


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
        self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.x11.XCloseDisplay(self.display)


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
        self._pty_polls = 0
        self._result_polls = 0

    async def _open(self) -> None:
        await super()._open()
        window = self.window
        if window is None:
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
        window.present()
        self.keyboard = _XTestKeyboard(window)
        terminal.grab_focus()
        GLib.timeout_add(10, self._wait_for_pty_and_focus)

    def _committed(self, _terminal: Vte.Terminal, text: str, _length: int) -> None:
        self.commit_stream.extend(text.encode("utf-8"))

    def _spawned(
        self,
        _terminal: Vte.Terminal,
        _pid: int,
        error: GLib.Error | None,
        _data: object,
    ) -> None:
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

    def _finish_app(self) -> bool:
        self.quit()
        return GLib.SOURCE_REMOVE


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
    assert run_app(lambda: app, ["aconsole-test-b1-keys"]) == 0
    assert app.input_error is None, app.input_error
    assert app.actions == ["palette"]
    expected = b"\x0b\x14\x17\x16\x1b[Z"
    assert bytes(app.commit_stream) == expected
    assert result_path.read_bytes() == expected


def test_b1_keys_narrow_window(running_app: str, tmp_path: Path) -> None:
    """At 700 px the sidebar collapses and the content header keeps title-button space."""
    request = SnapshotRequest(
        view="window", theme="light", width=700, height=800, out=tmp_path / "b1-narrow.png",
    )
    app = AConsoleApp(CoreClient(running_app), request)
    assert run_app(lambda: app, ["aconsole-test-b1-narrow"]) == 0
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
