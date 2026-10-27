"""Print the interpreter that is actually running the smoke tests."""

from __future__ import annotations

import sys


def pytest_sessionfinish(session: object, exitstatus: int) -> None:
    _ = (session, exitstatus)
    gil = sys._is_gil_enabled()
    sys.__stdout__.write(f"\npython {sys.version.split()[0]} gil={gil}\n")
    sys.__stdout__.flush()
