"""Pure UI tests remain free threaded; native tests release their own windows."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.gui.window_cleanup import owned_gtk_windows

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _release_test_windows() -> Iterator[None]:
    with owned_gtk_windows():
        yield
