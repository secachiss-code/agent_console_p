"""Cursor CLI reads MCP servers from `~/.cursor/mcp.json`.

One entry serves every tab: Cursor fills `${env:...}` from the tab's environment, so the file holds neither the port
nor the token. The user's other servers stay as they are, and a file that cannot be read is left alone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final, final

from aconsole.core.domain.approval_policy import ApprovalPolicyChoice, ApprovalPolicyMode
from aconsole.core.infrastructure.agents.private_files import atomic_write, check_home, private_dir, read_bytes

_COMMAND: Final = ("agent",)
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

    def launch(self, agent_home: Path, policy: ApprovalPolicyChoice | None = None) -> tuple[str, ...]:
        if not agent_home.is_absolute() or agent_home.is_symlink() or not agent_home.is_dir():
            raise ValueError(_HOME)
        check_home(agent_home)
        if policy is not None and policy.mode in {ApprovalPolicyMode.ALLOW, ApprovalPolicyMode.DENY}:
            return self.unavailable()
        folder = agent_home / ".cursor"
        if folder.is_symlink():
            raise ValueError(_SYMLINK)
        private_dir(folder)
        _merge(folder / "mcp.json")
        return _COMMAND

    @staticmethod
    def unavailable() -> tuple[str, ...]:
        """Do not silently run native permissions under an unverified automatic mode."""
        message = (
            "echo 'AConsole: Cursor automatic modes are not verified; select Ask for native permissions' >&2; exit 1"
        )
        return ("/bin/sh", "-c", message)


def _merge(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(_SYMLINK)
    document: object = json.loads(read_bytes(path)) if path.exists() else {}
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
    atomic_write(path, payload, _FILE_MODE)
