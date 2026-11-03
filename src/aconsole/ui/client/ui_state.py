"""What the interface remembers between runs that is nobody else's business: which summary sections are folded.

It lives in `$XDG_STATE_HOME/aconsole/ui.json`. A file that is missing or unreadable means the defaults; a write that
fails is forgotten. Nothing here may stop the window from opening.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

_LOG = logging.getLogger(__name__)
_FOLDED = "folded"


def ui_state_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME", "")
    root = Path(state) if state else Path.home() / ".local" / "state"
    return root / "aconsole" / "ui.json"


class UiState:
    """Folded sections by name. Every change is written at once: it happens on a click, not in a loop."""

    def __init__(self, path: Path | None = None) -> None:
        self._path: Path = path if path is not None else ui_state_path()
        self._folded: set[str] = _read_folded(self._path)

    def folded(self, name: str) -> bool:
        return name in self._folded

    def set_folded(self, name: str, *, folded: bool) -> None:
        if folded == (name in self._folded):
            return
        if folded:
            self._folded.add(name)
        else:
            self._folded.discard(name)
        self._write()

    def _write(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".ui-", dir=self._path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({_FOLDED: sorted(self._folded)}, stream)
            _ = Path(name).replace(self._path)
        except OSError:
            _LOG.warning("cannot keep the interface state in %s", self._path, exc_info=True)


def _read_folded(path: Path) -> set[str]:
    try:
        document: object = json.loads(path.read_text(encoding="utf-8"))  # pyright: ignore[reportAny] -- checked below
    except (OSError, ValueError):
        return set()
    names = document.get(_FOLDED) if isinstance(document, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(names, list):
        return set()
    return {name for name in names if isinstance(name, str)}  # pyright: ignore[reportUnknownVariableType]
