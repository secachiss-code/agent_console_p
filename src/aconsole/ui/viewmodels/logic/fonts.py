"""Font lists: the terminal and every piece of code in the window take several families, in order.

A family the first one lacks a glyph for (Cyrillic, CJK, emoji, powerline symbols) comes from the next. The generic
`monospace` always closes the list, so text never falls back to a proportional face.
"""

from __future__ import annotations

from typing import Final

GENERIC: Final = "monospace"
_MAX_FAMILIES: Final = 8
_MAX_NAME: Final = 96


def font_families(text: str | None) -> tuple[str, ...]:
    """Comma-separated names, cleaned: no quotes, no blanks, no repeats. Empty when the user named none."""
    found: list[str] = []
    for part in (text or "").split(","):
        name = " ".join(part.strip().strip("\"'").split())[:_MAX_NAME]
        if name and name.casefold() not in {item.casefold() for item in found}:
            found.append(name)
    return tuple(found[:_MAX_FAMILIES])


def with_fallback(families: tuple[str, ...]) -> tuple[str, ...]:
    """The list as Pango and CSS get it: the generic monospace last, once."""
    named = tuple(name for name in families if name.casefold() != GENERIC)
    return (*named, GENERIC)


def pango_family(families: tuple[str, ...]) -> str:
    """Pango takes a family list separated by commas and picks per glyph."""
    return ",".join(with_fallback(families))


def css_font_family(families: tuple[str, ...]) -> str:
    """A CSS `font-family` value. Names are quoted; the generic one must not be."""
    quoted = [f'"{_css_escape(name)}"' for name in with_fallback(families)[:-1]]
    return ", ".join((*quoted, GENERIC))


def add_family(text: str | None, family: str) -> str:
    """The setting with one more family at the end, for the font chooser."""
    return ", ".join(font_families(f"{text or ''},{family}"))


def _css_escape(name: str) -> str:
    return "".join(ch for ch in name if ch not in '"\\;{}<>\n\r')


ZOOM_DEFAULT = 1.0
_ZOOM_MIN = 0.5
_ZOOM_MAX = 3.0
_ZOOM_STEP = 0.1


def zoomed(current: float, steps: int) -> float:
    """The terminal font scale `steps` away, kept in range. Rounded so that steps do not drift."""
    return round(min(_ZOOM_MAX, max(_ZOOM_MIN, current + steps * _ZOOM_STEP)), 1)
