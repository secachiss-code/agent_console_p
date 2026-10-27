"""Per-tab Claude settings and the shared statusline script.

The hook URL is baked (ADR-0003): ``${ACONSOLE_PORT}`` inside ``url`` is an invalid URL and drops
the permissions from the same file. The bearer header keeps ``${ACONSOLE_TAB_TOKEN}``.
This module does not execute ``claude`` and does not write ``~/.claude/settings.json``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final

_DIR_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_SCRIPT_MODE: Final = 0o700
_PERMISSION_TIMEOUT: Final = 600
_HOOK_TIMEOUT: Final = 5
_REFRESH_S: Final = 5
_PORT_MAX: Final = 65535
_ENV_PORT: Final = "ACONSOLE_PORT"
_ENV_TAB: Final = "ACONSOLE_TAB"
_ENV_AUTH: Final = "ACONSOLE_TAB_TOKEN"
_BEARER: Final = "Bearer ${ACONSOLE_TAB_TOKEN}"
_ALLOW: Final = "mcp__aconsole__*"
_EVENTS: Final = (
    "PermissionRequest",
    "Notification",
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "StopFailure",
    "PreCompact",
    "PostCompact",
)
_TAB_ID: Final = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ABS: Final = "agent home must be absolute"
_HOME: Final = "agent home must be a directory"
_TAB: Final = "tab id must be 1-64 characters of letters, digits, '_' or '-'"
_PORT: Final = "port must be from 1 to 65535"
_EMPTY: Final = "tab token must be non-empty"
_SYMLINK: Final = "refusing to follow a symlink"
_NOT_DIR: Final = "path exists and is not a directory"
_SHORT: Final = "short write"
_CURL: Final = (
    'curl -sS -m 2 -H "Authorization: Bearer ${ACONSOLE_TAB_TOKEN}" '
    '--data-binary @"$copy" '
    '"http://127.0.0.1:${ACONSOLE_PORT}/v1/tabs/${ACONSOLE_TAB}/claude/statusline" '
    ">/dev/null 2>&1 &"
)
_STATUSLINE: Final = "\n".join(
    (
        "#!/bin/sh",
        "# Copy stdin to aconsole in the background, then print the user's statusline.",
        "copy=$(mktemp) || exit 0",
        'tee "$copy" >/dev/null || { rm -f "$copy"; exit 0; }',
        _CURL,
        "curl_pid=$!",
        'if [ -n "${ACONSOLE_USER_STATUSLINE:-}" ] && [ -x "${ACONSOLE_USER_STATUSLINE}" ]; then',
        '  "${ACONSOLE_USER_STATUSLINE}" < "$copy"',
        "fi",
        'wait "$curl_pid" || true',
        'rm -f "$copy"',
        "exit 0",
        "",
    )
)

type _Json = bool | int | str | list[_Json] | dict[str, _Json] | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ClaudeTab:
    """The inputs that identify one Claude tab. The token is never written to disk."""

    agent_home: Path
    tab_id: str
    port: int
    token: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ClaudeLaunch:
    """``argv`` is the Claude command a ``TerminalLaunchSpec`` can carry. The token stays in ``env``."""

    argv: tuple[str, ...]
    env: dict[str, str]
    settings_path: Path
    script_path: Path


@final
class ClaudeLauncher:
    """Write ``run/<tab>/settings.json`` and the shared ``bin/statusline.sh``."""

    def launch(self, tab: ClaudeTab) -> ClaudeLaunch:
        """Create the tab files and return ``claude --settings <path>``. Does not start Claude."""
        _require_home(tab.agent_home)
        _require_tab(tab.tab_id)
        _require_port(tab.port)
        _require_token(tab.token)
        root = tab.agent_home / ".aconsole"
        run = root / "run"
        tab_dir = run / tab.tab_id
        bin_dir = root / "bin"
        _private_dir(root)
        _private_dir(run)
        _private_dir(tab_dir)
        _private_dir(bin_dir)
        script = bin_dir / "statusline.sh"
        settings = tab_dir / "settings.json"
        _atomic_write(script, _STATUSLINE.encode("utf-8"), _SCRIPT_MODE)
        _atomic_write(settings, _settings_payload(tab.port, tab.tab_id, script), _FILE_MODE)
        env = {_ENV_PORT: str(tab.port), _ENV_TAB: tab.tab_id, _ENV_AUTH: tab.token}
        return ClaudeLaunch(
            argv=("claude", "--settings", str(settings)),
            env=env,
            settings_path=settings,
            script_path=script,
        )

    def close(self, agent_home: Path, tab_id: str) -> None:
        """Delete ``run/<tab>/`` only. The shared statusline script and the user's settings stay."""
        _require_home(agent_home)
        _require_tab(tab_id)
        target = agent_home / ".aconsole" / "run" / tab_id
        if target.is_symlink():
            raise ValueError(_SYMLINK)
        if not target.exists():
            return
        if not target.is_dir():
            raise ValueError(_NOT_DIR)
        shutil.rmtree(target)


def _require_home(agent_home: Path) -> None:
    if not agent_home.is_absolute():
        raise ValueError(_ABS)
    if agent_home.is_symlink() or not agent_home.is_dir():
        raise ValueError(_HOME)


def _require_tab(tab_id: str) -> None:
    if _TAB_ID.fullmatch(tab_id) is None:
        raise ValueError(_TAB)


def _require_port(port: int) -> None:
    if isinstance(port, bool) or port < 1 or port > _PORT_MAX:
        raise ValueError(_PORT)


def _require_token(token: str) -> None:
    if token == "":
        raise ValueError(_EMPTY)


def _private_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(_SYMLINK)
    if path.exists() and not path.is_dir():
        raise ValueError(_NOT_DIR)
    path.mkdir(mode=_DIR_MODE, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(_SYMLINK)
    path.chmod(_DIR_MODE)


def _settings_payload(port: int, tab_id: str, script: Path) -> bytes:
    url = f"http://127.0.0.1:{port}/v1/tabs/{tab_id}/claude/hook"
    hooks: dict[str, _Json] = {}
    for name in _EVENTS:
        timeout = _PERMISSION_TIMEOUT if name == "PermissionRequest" else _HOOK_TIMEOUT
        hooks[name] = [_hook_group(url, timeout)]
    document: dict[str, _Json] = {
        "hooks": hooks,
        "statusLine": {"type": "command", "command": str(script), "refreshInterval": _REFRESH_S},
        "permissions": {"allow": [_ALLOW]},
    }
    return json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"


def _hook_group(url: str, timeout: int) -> dict[str, _Json]:
    hook: dict[str, _Json] = {
        "type": "http",
        "url": url,
        "headers": {"Authorization": _BEARER},
        "allowedEnvVars": [_ENV_PORT, _ENV_TAB, _ENV_AUTH],
        "timeout": timeout,
    }
    return {"hooks": [hook]}


def _atomic_write(path: Path, payload: bytes, mode: int) -> None:
    """Temp file in the destination directory, fsync, chmod, then replace."""
    if path.is_symlink():
        raise ValueError(_SYMLINK)
    fd, name = tempfile.mkstemp(prefix=".aconsole-", dir=path.parent)
    temporary = Path(name)
    try:
        _write_all(fd, payload)
        os.fsync(fd)
        os.fchmod(fd, mode)
    except (OSError, ValueError):
        os.close(fd)
        temporary.unlink(missing_ok=True)
        raise
    os.close(fd)
    try:
        _ = temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while len(view) > 0:
        written = os.write(fd, view)
        if written == 0:
            raise ValueError(_SHORT)
        view = view[written:]
