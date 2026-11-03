"""Cursor CLI reads MCP servers from `~/.cursor/mcp.json`.

One entry serves every tab: Cursor fills `${env:...}` from the tab's environment, so the file holds neither the port
nor the token. The user's other servers stay as they are, and a file that cannot be read is left alone.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Final, final

_COMMAND: Final = ("agent",)
_DIR_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_ENTRY: Final = {
    "url": "http://127.0.0.1:${env:ACONSOLE_PORT}/mcp",
    "headers": {"Authorization": "Bearer ${env:ACONSOLE_TAB_TOKEN}"},
}
_HOME: Final = "agent home must be an absolute directory"
_SYMLINK: Final = "refusing to follow a symlink"
_NOT_OBJECT: Final = "mcp.json must hold a JSON object with an object in mcpServers"


@final
class CursorLauncher:
    """Keep the MCP entry in place and return the command."""

    def launch(self, agent_home: Path) -> tuple[str, ...]:
        if not agent_home.is_absolute() or agent_home.is_symlink() or not agent_home.is_dir():
            raise ValueError(_HOME)
        folder = agent_home / ".cursor"
        if folder.is_symlink():
            raise ValueError(_SYMLINK)
        folder.mkdir(mode=_DIR_MODE, exist_ok=True)
        _merge(folder / "mcp.json")
        return _COMMAND


def _merge(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(_SYMLINK)
    document: object = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(document, dict):
        raise ValueError(_NOT_OBJECT)  # noqa: TRY004 -- a bad file, handled like one that is not JSON
    servers: object = document.setdefault("mcpServers", {})  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(servers, dict):
        raise ValueError(_NOT_OBJECT)  # noqa: TRY004 -- a bad file, handled like one that is not JSON
    if servers.get("aconsole") == _ENTRY:  # pyright: ignore[reportUnknownMemberType]
        return
    servers["aconsole"] = _ENTRY
    _write(path, (json.dumps(document, indent=2) + "\n").encode())


def _write(path: Path, payload: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".aconsole-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            _ = stream.write(payload)
        temporary.chmod(_FILE_MODE)
        _ = temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
