"""WCAG contrast ratio for hex pairs. No arguments means there is nothing to check."""

from __future__ import annotations

import sys

WCAG_AA = 4.5


def _channel(value: str) -> float:
    number = int(value, 16) / 255
    if number <= 0.04045:
        return number / 12.92
    return ((number + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    color = hex_color.removeprefix("#")
    red = _channel(color[0:2])
    green = _channel(color[2:4])
    blue = _channel(color[4:6])
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    lighter = max(relative_luminance(foreground), relative_luminance(background))
    darker = min(relative_luminance(foreground), relative_luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


def main(argv: list[str]) -> int:
    if not argv:
        sys.stdout.write("contrast_check: no pairs\n")
        return 0
    if len(argv) % 2 != 0:
        sys.stdout.write("usage: contrast_check.py FG BG [FG BG ...]\n")
        return 2
    failed = False
    for index in range(0, len(argv), 2):
        ratio = contrast_ratio(argv[index], argv[index + 1])
        ok = ratio >= WCAG_AA
        sys.stdout.write(f"{argv[index]} on {argv[index + 1]}: {ratio:.2f}\n")
        failed = failed or not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
