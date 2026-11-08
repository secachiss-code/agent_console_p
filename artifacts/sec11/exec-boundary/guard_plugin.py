# Standalone filesystem-loaded pytest plugin, not a Python package.
# ruff: noqa: INP001
"""Apply the experimental guard only within this standalone pytest run."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


import subprocess
import sys
from pathlib import Path

import pytest

from aconsole.core.infrastructure.git.runner import GitOutput, GitRunner


@pytest.fixture(autouse=True)
def guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    def execute(
        _self: GitRunner, argv: tuple[str, ...], stdin: bytes | None, env: Mapping[str, str], limit: float
    ) -> GitOutput:
        done = subprocess.run(  # noqa: S603 -- argv comes from the test-owned GitRunner in the standalone guard experiment
            [sys.executable, str(Path(__file__).with_name("guard.py.txt")), *argv],
            input=stdin,
            capture_output=True,
            env=env,
            timeout=limit,
            check=False,
        )
        return GitOutput(code=done.returncode, stdout=done.stdout, stderr=done.stderr.decode(errors="replace"))

    monkeypatch.setattr(GitRunner, "_execute", execute)
