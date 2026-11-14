# ruff: noqa: ANN201, ANN001, INP001, SLF001, T201
"""Dump lingering thread stacks after pytest finishes; do not alter shutdown."""

import faulthandler
import sys


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 -- pytest hook signature
    print(f"QA36 sessionfinish: exitstatus={exitstatus}, gil={sys._is_gil_enabled()}", file=sys.stderr, flush=True)
    faulthandler.dump_traceback_later(45, repeat=True)
