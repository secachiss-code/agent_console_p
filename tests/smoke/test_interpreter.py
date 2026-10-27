"""Smoke check for the core and UI virtualenvs."""

from __future__ import annotations

import sys


def test_smoke_interpreter() -> None:
    version = sys.version.split()[0]
    gil = sys._is_gil_enabled()
    assert version.startswith("3.15."), version
    if ".venv-ui" in sys.prefix:
        assert gil is True
    else:
        assert gil is False
