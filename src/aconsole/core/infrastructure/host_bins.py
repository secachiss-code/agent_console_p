"""Where this machine keeps a host tool."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Final

# System directories first: `~/.local/bin` often holds distrobox wrappers that would run inside a container.
_SYSTEM: Final = ("/usr/bin", "/usr/local/bin", "/bin", "/run/current-system/sw/bin", "/opt/homebrew/bin")


def host_binary(name: str) -> Path:
    """The absolute path of a tool. A missing one stays `/usr/bin/<name>`, so its call reports it by that path."""
    for directory in _SYSTEM:
        candidate = Path(directory) / name
        if candidate.is_file():
            return candidate
    found = shutil.which(name)
    if found is not None and Path(found).is_absolute():
        return Path(found)
    return Path(_SYSTEM[0]) / name
