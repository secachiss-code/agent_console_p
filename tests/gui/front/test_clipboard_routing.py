"""Actual GDK clipboard transfers through window capture shortcuts and a VTE PTY."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, GLib, Gtk, Vte  # noqa: E402

from aconsole.contracts.common import TabId  # noqa: E402
from aconsole.ui.bootstrap import run_app  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.views.app import AConsoleApp  # noqa: E402
from aconsole.ui.views.terminal.page import TerminalPage  # noqa: E402
from tests.core.test_clipboard import png  # noqa: E402
from tests.gui.front.test_b1_keys import _XTestKeyboard  # noqa: E402

if TYPE_CHECKING:
    from pathlib import Path

_TEXT = "Привет 🌍\nsecond line\n\n"


class _ClipboardApp(AConsoleApp):
    def __init__(self, client: CoreClient, directory: Path, mode: str) -> None:
        super().__init__(client)
        self.directory = directory
        self.mode = mode
        self.problem: Exception | None = None
        self.received = bytearray()
        self.images: list[bytes] = []

    async def _open(self) -> None:  # noqa: C901, PLR0912, PLR0915 -- one end-to-end keyboard/clipboard scenario
        keyboard = None
        page = None
        try:
            await super()._open()
            window = self.window
            assert window is not None
            page = TerminalPage(self.client, TabId("tab-a"), lambda: None, lambda _tab: None)
            window.set_content(page)
            page.terminal.connect("commit", lambda _vte, text, _length: self.received.extend(text.encode()))
            ready = self.directory / "ready"
            page.terminal.spawn_async(
                Vte.PtyFlags.DEFAULT, None,
                ["/usr/bin/python3", "-c", (
                    "import os,tty,time;tty.setraw(0);os.write(1,b'\\x1b[?2004h');"
                    f"open({str(ready)!r},'w').close();time.sleep(30)"
                )], None, GLib.SpawnFlags.DEFAULT, None, None, -1, None, None, None,
            )
            page.focus_terminal()
            for _ in range(100):
                if ready.exists() and page.terminal.has_focus():
                    break
                await asyncio.sleep(0.02)
            assert ready.exists()
            keyboard = _XTestKeyboard(window)
            await asyncio.sleep(0.1)

            async def prepare(data: bytes) -> str:
                self.images.append(data)
                await asyncio.sleep(0.02)
                return "/attachments/image with spaces.png"

            page.clipboard.prepare = prepare
            clipboard = page.get_clipboard()
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png()))
            if self.mode in {"image", "cancel"}:
                clipboard.set(texture)
            elif self.mode == "mixed":
                clipboard.set_content(Gdk.ContentProvider.new_union([
                    Gdk.ContentProvider.new_for_value(_TEXT), Gdk.ContentProvider.new_for_value(texture),
                ]))
            elif self.mode == "empty":
                clipboard.set_content(None)
            else:
                clipboard.set(_TEXT)
            await asyncio.sleep(0.05)
            # This first chord passes through MainWindow's real CAPTURE controller.
            keyboard.press(Gdk.KEY_v, control=True, shift=True)
            if self.mode == "cancel":
                entry = Gtk.Entry()
                page.add_overlay(entry)
                entry.grab_focus()
            await asyncio.sleep(0.2)
            if self.mode in {"empty", "cancel"}:
                assert self.received == b""
                return
            value = "'/attachments/image with spaces.png'" if self.mode == "image" else _TEXT
            expected = b"\x1b[200~" + value.replace("\n", "\r").encode() + b"\x1b[201~"
            assert bytes(self.received) == expected, page.error_label.get_label()
            keyboard.press(Gdk.KEY_v, control=True)
            await asyncio.sleep(0.2)
            keyboard.press(Gdk.KEY_Insert, shift=True)
            await asyncio.sleep(0.2)
            assert bytes(self.received) == expected * 3
            if self.mode == "mixed":
                page._menu_paste(image_only=True)
                await asyncio.sleep(0.2)
                assert len(self.images) == 1, page.error_label.get_label()
                assert bytes(self.received).endswith(b"\x1b[200~'/attachments/image with spaces.png'\x1b[201~")
            else:
                assert len(self.images) == (3 if self.mode == "image" else 0)
            # Terminal shortcuts must leave editable fields alone.
            entry = Gtk.Entry()
            page.add_overlay(entry)
            entry.grab_focus()
            clipboard.set("field text")
            keyboard.press(Gdk.KEY_v, control=True)
            await asyncio.sleep(0.1)
            assert entry.get_text() == "field text"
        except Exception as error:  # noqa: BLE001 -- expose async application failures to pytest
            self.problem = error
        finally:
            if keyboard is not None:
                keyboard.close()
            if page is not None:
                page.close()
                pty = page.terminal.get_pty()
                if pty is not None:
                    pty.close()
            await self.client.close()
            if self.window is not None:
                self.window.destroy()
            self.quit()


@pytest.mark.parametrize("mode", ["text", "image", "mixed", "empty", "cancel"])
def test_real_clipboard_shortcuts(running_app: str, tmp_path: Path, mode: str) -> None:
    app = _ClipboardApp(CoreClient(running_app), tmp_path, mode)
    assert run_app(lambda: app, ["aconsole-test-clipboard"]) == 0
    if app.problem is not None:
        raise app.problem
