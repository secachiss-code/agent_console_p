"""Where the UI finds the core. The core needs the free-threaded interpreter, the UI cannot use it."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final

_ENV_PYTHON: Final = "ACONSOLE_CORE_PYTHON"
# Beside the UI environment: `.venv-core` in a checkout, `core` in an installed package.
_SIBLINGS: Final = (".venv-core", "core")


def runtime_dir() -> Path:
    """The directory `aconsoled` keeps its socket under by default."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    return Path(runtime) if runtime else Path.home() / ".local" / "state"


def core_socket() -> str:
    """`aconsole.core.bootstrap.default_socket`, without importing the core."""
    return str(runtime_dir() / "aconsole" / "core.sock")


def core_python(prefix: Path | None = None) -> Path:
    """The interpreter for `aconsoled`. Raises `FileNotFoundError` with what was tried."""
    override = os.environ.get(_ENV_PYTHON, "")
    if override:
        return Path(override)
    parent = Path(sys.prefix if prefix is None else prefix).parent
    tried = tuple(parent / name / "bin" / "python" for name in _SIBLINGS)
    for candidate in tried:
        if candidate.exists():
            return candidate
    listed = ", ".join(str(item) for item in tried)
    message = f"no core interpreter: set {_ENV_PYTHON} or install one of {listed}"
    raise FileNotFoundError(message)


def core_command() -> list[str]:
    return [str(core_python()), "-m", "aconsole.core", "--serve"]
