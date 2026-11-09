"""Shared design tokens, expanded before GTK parses the stylesheet.

GTK 4.14 (the supported floor) has no CSS custom properties. Keep the template
portable by substituting named tokens in Python, rather than requiring newer GTK.
"""

from pathlib import Path
from string import Template
from typing import Final

SPACE: Final = {"xxs": 2, "xs": 4, "sm": 6, "md": 8, "lg": 12, "xl": 16, "xxl": 24}
RADIUS: Final = {"control": 8, "card": 14, "pill": 999}
FONT: Final = {"caption": "0.85em", "body": "1em", "heading": "1.1em"}


def stylesheet() -> str:
    values = {f"space_{name}": f"{size}px" for name, size in SPACE.items()}
    values.update({f"radius_{name}": f"{size}px" for name, size in RADIUS.items()})
    values.update({f"font_{name}": value for name, value in FONT.items()})
    return Template(Path(__file__).with_name("style.css").read_text(encoding="utf-8")).substitute(values)
