"""Keep only AF_UNIX addresses short; test data may still live under arbitrarily deep paths."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_SOCKET_BUDGET = 100
_RUNTIME_BUDGET = 60


def _private_path(path: Path) -> Path:
    root = Path(os.environ["ACONSOLE_TEST_SOCKET_DIR"])
    digest = hashlib.sha256(os.fsencode(path)).hexdigest()[:24]
    parent = root / digest
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return parent


def short_socket_path(path: Path) -> Path:
    """Preserve ordinary paths; use a deterministic private address when sun_path would overflow."""
    if len(os.fsencode(path)) <= _SOCKET_BUDGET:
        return path
    result = _private_path(path) / path.name
    if len(os.fsencode(result)) > _SOCKET_BUDGET:
        message = "test socket filename exceeds the AF_UNIX address budget"
        raise ValueError(message)
    return result


def short_runtime_path(path: Path) -> Path:
    """Leave enough room for the socket names created by the daemon and wire fixtures."""
    return path if len(os.fsencode(path)) <= _RUNTIME_BUDGET else _private_path(path)
