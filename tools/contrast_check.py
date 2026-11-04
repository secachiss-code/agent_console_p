"""WCAG contrast of what the application really draws, in both themes.

With no arguments this is the gate: every `color:` in style.css, the theme's own text pairs, the shapes
that carry meaning, and the terminal palettes. With hex pairs it just reports on those.

A rule says what it paints on only when it sets a background itself, and a rule can end up on several
surfaces, so a rule without one has to pass on all of them. The theme colors are the floor libadwaita's;
tests/gui/front/test_contrast_table.py compares them with the installed theme.
"""

from __future__ import annotations

import re
import runpy
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

WCAG_AA: Final = 4.5
WCAG_GRAPHICS: Final = 3.0

_ROOT: Final = Path(__file__).resolve().parents[1]
_STYLE: Final = _ROOT / "src" / "aconsole" / "ui" / "style" / "style.css"
_TERMINAL: Final = _ROOT / "src" / "aconsole" / "ui" / "viewmodels" / "logic" / "terminal_palette.py"
_HEX_SHORT: Final = 3
_FULL_ALPHA: Final = 1.0
_OPAQUE_LENGTH: Final = 6

type Rgba = tuple[float, float, float, float]


def _hex(value: str, alpha: float = _FULL_ALPHA) -> Rgba:
    color = value.removeprefix("#")
    if len(color) == _HEX_SHORT:
        color = "".join(char * 2 for char in color)
    return (int(color[0:2], 16) / 255, int(color[2:4], 16) / 255, int(color[4:6], 16) / 255, alpha)


LIGHT_THEME: Final[dict[str, Rgba]] = {
    "window_bg_color": _hex("#fafafa"), "window_fg_color": _hex("#000000", 0.8),
    "view_bg_color": _hex("#ffffff"), "view_fg_color": _hex("#000000", 0.8),
    "sidebar_bg_color": _hex("#ebebeb"), "sidebar_fg_color": _hex("#000000", 0.8),
    "sidebar_backdrop_color": _hex("#f2f2f2"),
    "headerbar_bg_color": _hex("#ffffff"), "headerbar_fg_color": _hex("#000000", 0.8),
    "popover_bg_color": _hex("#ffffff"), "popover_fg_color": _hex("#000000", 0.8),
    "card_bg_color": _hex("#ffffff"), "card_fg_color": _hex("#000000", 0.8),
    "dialog_bg_color": _hex("#fafafa"),
    "accent_color": _hex("#1c71d8"), "accent_bg_color": _hex("#3584e4"), "accent_fg_color": _hex("#ffffff"),
    "success_color": _hex("#1b8553"), "success_bg_color": _hex("#2ec27e"), "success_fg_color": _hex("#ffffff"),
    "warning_color": _hex("#9c6e03"), "warning_bg_color": _hex("#e5a50a"), "warning_fg_color": _hex("#000000", 0.8),
    "error_color": _hex("#c01c28"), "error_bg_color": _hex("#e01b24"), "error_fg_color": _hex("#ffffff"),
    "destructive_bg_color": _hex("#e01b24"), "destructive_fg_color": _hex("#ffffff"),
}
DARK_THEME: Final[dict[str, Rgba]] = {
    "window_bg_color": _hex("#242424"), "window_fg_color": _hex("#ffffff"),
    "view_bg_color": _hex("#1e1e1e"), "view_fg_color": _hex("#ffffff"),
    "sidebar_bg_color": _hex("#303030"), "sidebar_fg_color": _hex("#ffffff"),
    "sidebar_backdrop_color": _hex("#2a2a2a"),
    "headerbar_bg_color": _hex("#303030"), "headerbar_fg_color": _hex("#ffffff"),
    "popover_bg_color": _hex("#383838"), "popover_fg_color": _hex("#ffffff"),
    "card_bg_color": _hex("#ffffff", 0.08), "card_fg_color": _hex("#ffffff"),
    "dialog_bg_color": _hex("#383838"),
    "accent_color": _hex("#78aeed"), "accent_bg_color": _hex("#3584e4"), "accent_fg_color": _hex("#ffffff"),
    "success_color": _hex("#8ff0a4"), "success_bg_color": _hex("#26a269"), "success_fg_color": _hex("#ffffff"),
    "warning_color": _hex("#f8e45c"), "warning_bg_color": _hex("#cd9309"), "warning_fg_color": _hex("#000000", 0.8),
    "error_color": _hex("#ff7b63"), "error_bg_color": _hex("#c01c28"), "error_fg_color": _hex("#ffffff"),
    "destructive_bg_color": _hex("#c01c28"), "destructive_fg_color": _hex("#ffffff"),
}
THEMES: Final[dict[str, dict[str, Rgba]]] = {"light": LIGHT_THEME, "dark": DARK_THEME}

# Where text without a background of its own can land. Each layer is painted over the one before it.
_SURFACES: Final[tuple[tuple[str, ...], ...]] = (
    ("@window_bg_color",),
    ("@view_bg_color",),
    ("@sidebar_bg_color",),
    ("@sidebar_backdrop_color",),
    ("@headerbar_bg_color",),
    ("@popover_bg_color",),
    ("@sidebar_bg_color", "alpha(@window_fg_color, 0.05)"),  # the summary card
    ("@sidebar_bg_color", "alpha(@window_fg_color, 0.1)"),  # the selected tab
    ("@window_bg_color", "alpha(@window_fg_color, 0.06)"),  # a command on an approval card
)
# The theme's own pairs: text the theme paints that the application relies on.
_THEME_TEXT: Final[tuple[tuple[str, str, str], ...]] = (
    ("window text", "@window_fg_color", "@window_bg_color"),
    ("sidebar text", "@sidebar_fg_color", "@sidebar_bg_color"),
    ("popover text", "@popover_fg_color", "@popover_bg_color"),
    ("suggested button", "@accent_fg_color", "@ac_accent_button"),
    ("destructive button", "@destructive_fg_color", "@destructive_bg_color"),
)
# Shapes that mean something and have no text to fall back on: status dots, the focus ring, the bars.
_GRAPHICS: Final[tuple[tuple[str, str, str], ...]] = (
    ("status: waiting", "@accent_bg_color", "@sidebar_bg_color"),
    ("status: working", "@accent_color", "@sidebar_bg_color"),
    ("status: idle", "@success_color", "@sidebar_bg_color"),
    ("status: error", "@ac_error_shape", "@sidebar_bg_color"),
    ("status: exited or unknown", "@ac_quiet_shape", "@sidebar_bg_color"),
    ("focus ring", "@accent_color", "@window_bg_color"),
    ("bar: warning", "@ac_warning_shape", "@sidebar_bg_color"),
    ("bar: critical", "@ac_error_shape", "@sidebar_bg_color"),
)
_TERMINAL_EXEMPT: Final = {
    # The slot is near the background by definition: it is what a program picks to draw "nothing much".
    "dark": frozenset({0}),
}


@dataclass(frozen=True)
class Failure:
    theme: str
    what: str
    ratio: float
    needed: float

    def __str__(self) -> str:
        return f"{self.theme}: {self.what}: {self.ratio:.2f} < {self.needed}"


def _channel(number: float) -> float:
    if number <= 0.04045:
        return number / 12.92
    return ((number + 0.055) / 1.055) ** 2.4


def _luminance(color: Rgba) -> float:
    return 0.2126 * _channel(color[0]) + 0.7152 * _channel(color[1]) + 0.0722 * _channel(color[2])


def relative_luminance(hex_color: str) -> float:
    return _luminance(_hex(hex_color))


def contrast_ratio(foreground: str, background: str) -> float:
    return _ratio(_hex(foreground), _hex(background))


def _ratio(foreground: Rgba, background: Rgba) -> float:
    lighter = max(_luminance(foreground), _luminance(background))
    darker = min(_luminance(foreground), _luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


def over(top: Rgba, bottom: Rgba) -> Rgba:
    """`top` painted on an opaque `bottom`."""
    alpha = top[3]
    return (
        top[0] * alpha + bottom[0] * (1 - alpha),
        top[1] * alpha + bottom[1] * (1 - alpha),
        top[2] * alpha + bottom[2] * (1 - alpha),
        _FULL_ALPHA,
    )


def _arguments(text: str) -> list[str]:
    """Split on the commas that are not inside a nested call."""
    parts: list[str] = []
    depth = 0
    current = ""
    for char in text:
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        depth += (char == "(") - (char == ")")
        current += char
    parts.append(current)
    return [part.strip() for part in parts]


def resolve(expression: str, colors: dict[str, Rgba], current: Rgba | None = None) -> Rgba:
    """The value of a CSS color. Raises ValueError on one the gate cannot judge, so nothing slips past."""
    text = expression.strip()
    if text.startswith("@"):
        name = text[1:]
        if name not in colors:
            message = f"unknown color {text}"
            raise ValueError(message)
        return colors[name]
    if text.startswith("#") and len(text) - 1 in {_HEX_SHORT, _OPAQUE_LENGTH}:
        return _hex(text)
    if text == "black":
        return _hex("#000000")
    if text == "white":
        return _hex("#ffffff")
    if text == "currentColor" and current is not None:
        return current
    call = re.fullmatch(r"(alpha|mix)\((.*)\)", text, re.DOTALL)
    if call is None:
        message = f"cannot read color {text!r}"
        raise ValueError(message)
    arguments = _arguments(call.group(2))
    first = resolve(arguments[0], colors, current)
    if call.group(1) == "alpha":
        return (first[0], first[1], first[2], first[3] * float(arguments[1]))
    second = resolve(arguments[1], colors, current)
    factor = float(arguments[2])
    return (
        first[0] * (1 - factor) + second[0] * factor,
        first[1] * (1 - factor) + second[1] * factor,
        first[2] * (1 - factor) + second[2] * factor,
        first[3] * (1 - factor) + second[3] * factor,
    )


@dataclass(frozen=True)
class _Rule:
    selector: str
    declarations: dict[str, str]


def parse_css(css: str) -> tuple[dict[str, str], list[_Rule]]:
    """The `@define-color`s and the plain rules. Keyframes paint nothing a person has to read at rest."""
    text = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    text = re.sub(r"@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", text)
    defined = dict(re.findall(r"@define-color\s+(\S+)\s+([^;]+);", text))
    text = re.sub(r"@define-color[^;]+;", "", text)
    rules: list[_Rule] = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
        declarations: dict[str, str] = {}
        for declaration in body.split(";"):
            name, separator, value = declaration.partition(":")
            if separator:
                declarations[name.strip()] = value.strip()
        rules.append(_Rule(selector.strip(), declarations))
    return defined, rules


def _colors(theme: dict[str, Rgba], defined: dict[str, str]) -> dict[str, Rgba]:
    colors = dict(theme)
    for name, expression in defined.items():
        colors[name] = resolve(expression, colors)
    return colors


def _paint(layers: tuple[str, ...], colors: dict[str, Rgba]) -> Rgba:
    painted = resolve(layers[0], colors)
    for layer in layers[1:]:
        painted = over(resolve(layer, colors), painted)
    return painted


def _gradient_tint(value: str | None) -> str | None:
    """The color of a flat `linear-gradient(c, c)`, the way a tint goes over a background that stays."""
    if value is None or not value.startswith("linear-gradient("):
        return None
    return _arguments(value[len("linear-gradient(") : -1])[0]


def _surfaces_of(rule: _Rule, rules: list[_Rule]) -> list[tuple[str, ...]]:
    own = rule.declarations.get("background-color")
    tint = _gradient_tint(rule.declarations.get("background-image"))
    if own is None:
        own = _plan_node_background(rule, rules)
    if own is None:
        bases = list(_SURFACES)
    elif own.startswith("alpha(") or "currentColor" in own:
        bases = [(*surface, own) for surface in _SURFACES]
    else:
        bases = [(own,)]
    return [(*base, tint) for base in bases] if tint is not None else bases


def _plan_node_background(rule: _Rule, rules: list[_Rule]) -> str | None:
    """A plan state class only tints: it goes on a node, and the node brings the background."""
    if not rule.selector.startswith(".ac-plan-"):
        return None
    return next(
        (other.declarations.get("background-color") for other in rules if other.selector == ".ac-plan-node"),
        None,
    )


def check_css(css: str, themes: dict[str, dict[str, Rgba]] = THEMES) -> list[Failure]:
    defined, rules = parse_css(css)
    failures: list[Failure] = []
    for theme_name, theme in themes.items():
        colors = _colors(theme, defined)
        for rule in rules:
            value = rule.declarations.get("color")
            if value is None:
                continue
            fg = resolve(value, colors)
            opacity = float(rule.declarations.get("opacity", _FULL_ALPHA))
            fg = (fg[0], fg[1], fg[2], fg[3] * opacity)
            worst = min(
                _ratio(over(fg, painted), painted)
                for painted in (_paint(layers, colors) for layers in _surfaces_of(rule, rules))
            )
            if worst < WCAG_AA:
                failures.append(Failure(theme_name, f"{rule.selector} ({value})", worst, WCAG_AA))
        failures.extend(_named_pairs(theme_name, colors))
    return failures


def _named_pairs(theme_name: str, colors: dict[str, Rgba]) -> list[Failure]:
    failures: list[Failure] = []
    for pairs, needed in ((_THEME_TEXT, WCAG_AA), (_GRAPHICS, WCAG_GRAPHICS)):
        for what, foreground, background in pairs:
            painted = resolve(background, colors)
            ratio = _ratio(over(resolve(foreground, colors), painted), painted)
            if ratio < needed:
                failures.append(Failure(theme_name, what, ratio, needed))
    return failures


def check_terminal() -> list[Failure]:
    palettes = runpy.run_path(str(_TERMINAL))
    failures: list[Failure] = []
    for theme, prefix in (("light", "LIGHT"), ("dark", "DARK")):
        background = str(palettes[f"{prefix}_BACKGROUND"])
        pairs: list[tuple[str, str]] = [("foreground", str(palettes[f"{prefix}_FOREGROUND"]))]
        for slot, color in enumerate(palettes[f"{prefix}_PALETTE"]):
            if slot not in _TERMINAL_EXEMPT.get(theme, frozenset()):
                pairs.append((f"terminal slot {slot}", str(color)))
        for what, color in pairs:
            ratio = contrast_ratio(color, background)
            if ratio < WCAG_AA:
                failures.append(Failure(theme, what, ratio, WCAG_AA))
    return failures


def _gate() -> int:
    failures = [*check_css(_STYLE.read_text(encoding="utf-8")), *check_terminal()]
    for failure in failures:
        sys.stdout.write(f"{failure}\n")
    sys.stdout.write(f"contrast_check: {len(failures)} below the minimum\n")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    if not argv:
        return _gate()
    if len(argv) % 2 != 0:
        sys.stdout.write("usage: contrast_check.py [FG BG ...]\n")
        return 2
    failed = False
    for index in range(0, len(argv), 2):
        ratio = contrast_ratio(argv[index], argv[index + 1])
        sys.stdout.write(f"{argv[index]} on {argv[index + 1]}: {ratio:.2f}\n")
        failed = failed or ratio < WCAG_AA
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
