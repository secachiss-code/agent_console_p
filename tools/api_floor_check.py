"""Reject UI APIs newer than GTK 4.14, libadwaita 1.5, and VTE 0.76.

Scan ``.py`` and ``.css`` files. Exit 1 when a banned token is present.
``tests/fixtures/ui_banned/`` is the fixture that must fail this check.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_PY = (
    (re.compile(r"\bAdw\.Spinner\b"), "Adw.Spinner"),
    (re.compile(r"\bAdw\.Sidebar"), "Adw.Sidebar"),
    (re.compile(r"\bAdw\.ToggleGroup\b"), "Adw.ToggleGroup"),
    (re.compile(r"\bAdw\.WrapBox\b"), "Adw.WrapBox"),
    (re.compile(r"\bAdw\.BottomSheet\b"), "Adw.BottomSheet"),
    (re.compile(r"\bAdw\.InlineViewSwitcher\b"), "Adw.InlineViewSwitcher"),
    (re.compile(r"\bAdw\.ShortcutsDialog\b"), "Adw.ShortcutsDialog"),
    (re.compile(r"\b(?:get_accent_color|set_accent_color)\b"), "Adw.StyleManager accent API"),
    (re.compile(r"\bAdw\.Easing\.(?:EASE|EASE_IN|EASE_OUT|EASE_IN_OUT)(?!_)"), "Adw.Easing >= 31"),
    (re.compile(r"\bset_button_style\b"), "Adw.Banner.set_button_style"),
    (re.compile(r"\btermprops?\b"), "VTE termprops"),
)
_CSS = (
    (re.compile(r"\bvar\s*\("), "var("),
    (re.compile(r"\bcolor-mix\s*\("), "color-mix("),
    (re.compile(r"\boklch\s*\("), "oklch("),
    (re.compile(r"(?:^|[^\w-]):root\b"), ":root"),
    (re.compile(r"(?:^|[^\w-])@media\b"), "@media"),
    (re.compile(r"\.monospace\b"), ".monospace"),
)


def scan(path: Path) -> list[str]:
    """Return ``path:line: token`` hits under ``path``."""
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    hits: list[str] = []
    for file in files:
        rules = _PY if file.suffix == ".py" else _CSS if file.suffix == ".css" else ()
        if not rules:
            continue
        for lineno, line in enumerate(file.read_text(encoding="utf-8").splitlines(), start=1):
            for pattern, token in rules:
                if pattern.search(line):
                    hits.append(f"{file}:{lineno}: {token}")
    return hits


def main(argv: list[str]) -> int:
    roots = [Path(arg) for arg in argv] if argv else [Path("src")]
    hits: list[str] = []
    for root in roots:
        if not root.exists():
            sys.stdout.write(f"api_floor_check: missing {root}\n")
            return 2
        hits.extend(scan(root))
    if not hits:
        sys.stdout.write("api_floor_check: clean\n")
        return 0
    sys.stdout.write("\n".join(hits) + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
