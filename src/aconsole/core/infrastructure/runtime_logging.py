"""Core-owned rotating logs, including when no UI is running."""

from __future__ import annotations

import io
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import override

_DEFAULT_BYTES = 4 * 1024 * 1024
_DEFAULT_BACKUPS = 3


class CoreLogHandler(RotatingFileHandler):
    @override
    def _open(self) -> io.TextIOWrapper:
        descriptor = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        return io.TextIOWrapper(io.FileIO(descriptor, "a", closefd=True), encoding="utf-8")


def configure_logging(
    path: Path | None = None, *, max_bytes: int = _DEFAULT_BYTES, backups: int = _DEFAULT_BACKUPS,
) -> CoreLogHandler:
    """Idempotent for one path. Root handlers owned by another caller are left alone."""
    if path is None:
        state = os.environ.get("XDG_STATE_HOME", "")
        path = (Path(state) if state else Path.home() / ".local/state") / "aconsole/core.log"
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    root = logging.getLogger()
    for handler in tuple(root.handlers):
        if isinstance(handler, CoreLogHandler):
            if handler.baseFilename == str(path.absolute()):
                return handler
            root.removeHandler(handler)
            handler.close()
    result = CoreLogHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    result.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(result)
    root.setLevel(logging.INFO)
    return result
