"""Apply the application's light and dark palettes to VTE."""

import gi

gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, Vte  # noqa: E402

from aconsole.ui.viewmodels.logic.terminal_palette import (  # noqa: E402
    DARK_BACKGROUND,
    DARK_FOREGROUND,
    DARK_PALETTE,
    LIGHT_BACKGROUND,
    LIGHT_FOREGROUND,
    LIGHT_PALETTE,
)


def _color(value: str) -> Gdk.RGBA:
    color = Gdk.RGBA()
    if not color.parse(value):
        message = f"invalid terminal color: {value}"
        raise ValueError(message)
    return color


class TerminalPaletteBinding:
    """Keep a terminal's explicit colors synchronized with the desktop theme."""

    def __init__(self, terminal: Vte.Terminal, style_manager: Adw.StyleManager | None = None) -> None:
        self._terminal: Vte.Terminal = terminal
        self._style: Adw.StyleManager = style_manager if style_manager is not None else Adw.StyleManager.get_default()
        self._handler: int | None = None
        self._apply()
        self._handler = self._style.connect("notify::dark", self._changed)

    def _apply(self) -> None:
        dark = self._style.get_dark()
        foreground = DARK_FOREGROUND if dark else LIGHT_FOREGROUND
        background = DARK_BACKGROUND if dark else LIGHT_BACKGROUND
        palette = DARK_PALETTE if dark else LIGHT_PALETTE
        self._terminal.set_colors(
            _color(foreground),  # pyright: ignore[reportArgumentType] -- VTE 3.91 uses Gdk4, stub names Gdk3
            _color(background),  # pyright: ignore[reportArgumentType] -- VTE 3.91 uses Gdk4, stub names Gdk3
            [_color(value) for value in palette],  # pyright: ignore[reportArgumentType] -- VTE 3.91 uses Gdk4
        )

    def _changed(self, _style: Adw.StyleManager, _pspec: object) -> None:
        self._apply()

    def close(self) -> None:
        if self._handler is not None:
            self._style.disconnect(self._handler)
            self._handler = None
