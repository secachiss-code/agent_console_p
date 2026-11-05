"""Read the desktop clipboard once, then paste into the original focused terminal."""

import asyncio
import shlex
from collections.abc import Awaitable, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gio, GObject, Gtk, Vte  # noqa: E402

from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.views.terminal.messages import _ as translate  # noqa: E402

_MAX_BYTES = 8 * 1024 * 1024
_MAX_PIXELS = 32 * 1024 * 1024


class TerminalClipboard:
    def __init__(
        self, page: Gtk.Widget, terminal: Vte.Terminal,
        prepare: Callable[[bytes], Awaitable[str]], error: Callable[[str], None],
    ) -> None:
        self.page: Gtk.Widget = page
        self.terminal: Vte.Terminal = terminal
        self.prepare: Callable[[bytes], Awaitable[str]] = prepare
        self.error: Callable[[str], None] = error
        self.task: asyncio.Task[None] | None = None
        self.cancellable: Gio.Cancellable | None = None
        self.closed: bool = False

    def cancel(self) -> None:
        if self.cancellable is not None:
            self.cancellable.cancel()
        if self.task is not None:
            _ = self.task.cancel()

    def focused(self) -> bool:
        root = self.page.get_root()
        if self.closed or not self.page.get_mapped() or not isinstance(root, Gtk.Window) or not root.is_active():
            return False
        focus = root.get_focus()
        terminal_focused = False
        while focus is not None:
            if focus is self.page:
                return terminal_focused
            terminal_focused |= focus.has_css_class("ac-terminal")
            focus = focus.get_parent()
        return False

    def paste(self, *, image_only: bool = False) -> None:
        if self.closed or (self.task is not None and not self.task.done()):
            return
        self.cancellable = Gio.Cancellable()
        self.task = spawn_task(self._paste(image_only=image_only))

    async def _text(self, clipboard: Gdk.Clipboard) -> str | None:
        future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()

        def ready(source: Gdk.Clipboard, result: Gio.AsyncResult) -> None:
            if future.done():
                return
            try:
                future.set_result(source.read_text_finish(result))
            except Exception as error:  # noqa: BLE001 -- async GDK callback forwards errors
                future.set_exception(error)

        clipboard.read_text_async(self.cancellable, ready)
        return await future

    async def _image(self, clipboard: Gdk.Clipboard) -> bytes:
        future: asyncio.Future[Gdk.Texture | None] = asyncio.get_running_loop().create_future()

        def ready(source: Gdk.Clipboard, result: Gio.AsyncResult) -> None:
            if future.done():
                return
            try:
                future.set_result(source.read_texture_finish(result))
            except Exception as error:  # noqa: BLE001 -- async GDK callback forwards errors
                future.set_exception(error)

        clipboard.read_texture_async(self.cancellable, ready)
        texture = await future
        if texture is None:
            raise ValueError(translate("clipboard.empty"))
        if texture.get_width() * texture.get_height() > _MAX_PIXELS:
            raise ValueError(translate("clipboard.large"))
        data = texture.save_to_png_bytes().get_data()
        if len(data) > _MAX_BYTES:
            raise ValueError(translate("clipboard.large"))
        return data

    async def _paste(self, *, image_only: bool) -> None:
        try:
            clipboard = self.page.get_clipboard()
            formats = clipboard.get_formats()
            has_text = formats.contain_gtype(GObject.TYPE_STRING) or any(
                kind.startswith("text/plain") or kind == "UTF8_STRING" for kind in formats.get_mime_types()
            )
            text = await self._text(clipboard) if has_text and not image_only else None
            if text is None:
                has_image = any(GObject.type_is_a(kind, Gdk.Texture) for kind in formats.get_gtypes()) or any(
                    kind.startswith("image/") for kind in formats.get_mime_types()
                )
                if not has_image:
                    raise ValueError(translate("clipboard.empty"))  # noqa: TRY301 -- report a single localized paste error
                png = await self._image(clipboard)
                if not self.focused():
                    return
                text = shlex.quote(await self.prepare(png))
            if self.focused():
                self.terminal.paste_text(text)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- clipboard owners may disappear during an async transfer
            if self.focused():
                self.error(translate("clipboard.error").format(error=error))
