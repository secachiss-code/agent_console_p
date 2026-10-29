"""Terminal colors must match the accessible design palettes exactly."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("theme", "expected_palette", "expected_foreground", "expected_background"),
    [
        (
            "light",
            (
                "#241f31",
                "#c01c28",
                "#26743e",
                "#8a5a00",
                "#1a5fb4",
                "#813d9c",
                "#0b6e7a",
                "#5e5c64",
                "#5e5c64",
                "#a51d2d",
                "#1f6b38",
                "#704700",
                "#154e96",
                "#613583",
                "#095a64",
                "#3d3846",
            ),
            "#241f31",
            "#ffffff",
        ),
        (
            "dark",
            (
                "#3d3846",
                "#f66151",
                "#57e389",
                "#f6d32d",
                "#62a0ea",
                "#dc8add",
                "#5bc8d2",
                "#deddda",
                "#9a9996",
                "#ff8a7a",
                "#8ff0a4",
                "#f9f06b",
                "#99c1f1",
                "#e8b4ea",
                "#93e1e8",
                "#ffffff",
            ),
            "#deddda",
            "#1e1e1e",
        ),
    ],
)
def test_b5a_terminal_palette(
    theme: str,
    expected_palette: tuple[str, ...],
    expected_foreground: str,
    expected_background: str,
) -> None:
    from aconsole.ui.viewmodels.logic.terminal_palette import (
        DARK_BACKGROUND,
        DARK_FOREGROUND,
        DARK_PALETTE,
        LIGHT_BACKGROUND,
        LIGHT_FOREGROUND,
        LIGHT_PALETTE,
    )

    if theme == "light":
        palette = LIGHT_PALETTE
        foreground = LIGHT_FOREGROUND
        background = LIGHT_BACKGROUND
    else:
        palette = DARK_PALETTE
        foreground = DARK_FOREGROUND
        background = DARK_BACKGROUND

    assert len(palette) == 16
    assert palette == expected_palette
    assert foreground == expected_foreground
    assert background == expected_background
