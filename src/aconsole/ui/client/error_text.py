
"""What a core error means to the person reading it. The core sends only a name and a number."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from aconsole.ui.i18n import _, known

if TYPE_CHECKING:
    from aconsole.contracts.errors import ErrorObject

def core_log_path() -> Path:
    """`$XDG_STATE_HOME/aconsole/core.log`. The core logs to stderr, and only this run and the one before stay."""
    state = os.environ.get("XDG_STATE_HOME", "")
    return (Path(state) if state else Path.home() / ".local" / "state") / "aconsole" / "core.log"


def error_text(error: ErrorObject) -> str:
    key = f"error.{error.message}"
    if known(key):
        return _(key)
    return _("error.fallback").format(name=error.message, log=core_log_path())
