"""Contrast helper used by later UI checks."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import contrast_check


def test_black_on_white_passes_wcag() -> None:
    assert contrast_check.contrast_ratio("#000000", "#ffffff") >= contrast_check.WCAG_AA
