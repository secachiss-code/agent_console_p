"""Codex ``-c`` argv, the hook command, and the notify wrapper.

Command hooks are emitted only for keys the trust cache already holds (ADR-0002).
The tab token stays in the environment. ``~/.codex/config.toml`` and ``AGENTS.md`` are not written.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import msgspec

from aconsole.core.infrastructure.agents.codex.trust import HookTrustCache

_DIR_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_SCRIPT_MODE: Final = 0o700
_PORT_MAX: Final = 65535
_PERMISSION_TIMEOUT: Final = 600
_HOOK_TIMEOUT: Final = 5
_SESSION_END_TIMEOUT: Final = 3
_STATE_PREFIX: Final = "/<session-flags>/config.toml"
_ENV_PORT: Final = "ACONSOLE_PORT"
_ENV_TAB: Final = "ACONSOLE_TAB"
_ENV_AUTH: Final = "ACONSOLE_TAB_TOKEN"
_TAB_ID: Final = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# PascalCase `-c` name, snake_case key segment, timeout seconds. SessionEnd is 3: Codex clamps a larger value.
_HOOKS: Final = (
    ("PermissionRequest", "permission_request", _PERMISSION_TIMEOUT),
    ("SessionStart", "session_start", _HOOK_TIMEOUT),
    ("UserPromptSubmit", "user_prompt_submit", _HOOK_TIMEOUT),
    ("PreToolUse", "pre_tool_use", _HOOK_TIMEOUT),
    ("PostToolUse", "post_tool_use", _HOOK_TIMEOUT),
    ("Stop", "stop", _HOOK_TIMEOUT),
    ("SessionEnd", "session_end", _SESSION_END_TIMEOUT),
)
_ABS: Final = "agent home must be absolute"
_HOME: Final = "agent home must be a directory"
_TAB: Final = "tab id must be 1-64 characters of letters, digits, '_' or '-'"
_PORT: Final = "port must be from 1 to 65535"
_EMPTY: Final = "tab token must be non-empty"
_SYMLINK: Final = "refusing to follow a symlink"
_NOT_DIR: Final = "path exists and is not a directory"
_QUOTE: Final = "path cannot contain a quote or backslash"
_SHORT: Final = "short write"
_HOOK: Final = """#!/bin/sh
curl -sS --max-time 590 -H "Authorization: Bearer $ACONSOLE_TAB_TOKEN" \\
  --data-binary @- "http://127.0.0.1:$ACONSOLE_PORT/v1/tabs/$ACONSOLE_TAB/codex/hook" \\
  2>/dev/null || true
exit 0
"""
_NOTIFY: Final = """#!/bin/sh
if [ -n "$2" ]; then
  printf '%s' "$2" | curl -sS -m 2 -H "Authorization: Bearer $ACONSOLE_TAB_TOKEN" \\
    --data-binary @- "http://127.0.0.1:$ACONSOLE_PORT/v1/tabs/$ACONSOLE_TAB/codex/notify" \\
    >/dev/null 2>&1 || true
else
  curl -sS -m 2 -H "Authorization: Bearer $ACONSOLE_TAB_TOKEN" \\
    --data-binary @- "http://127.0.0.1:$ACONSOLE_PORT/v1/tabs/$ACONSOLE_TAB/codex/notify" \\
    >/dev/null 2>&1 || true
fi
exit 0
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class CodexTab:
    """One Codex tab. The token is never placed on argv or in a file."""

    agent_home: Path
    tab_id: str
    port: int
    token: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CodexLaunch:
    """``argv`` is the ``codex -c ...`` command. ``env`` carries the tab token."""

    argv: tuple[str, ...]
    env: dict[str, str]
    hook_path: Path
    notify_path: Path


class CodexLauncher:
    """Write the shared hook and notify scripts and return the launch argv."""

    def launch(self, tab: CodexTab, trust: HookTrustCache) -> CodexLaunch:
        """Create ``bin/aconsole-hook`` and ``bin/aconsole-notify``. Does not start Codex."""
        _require_home(tab.agent_home)
        _require_tab(tab.tab_id)
        _require_port(tab.port)
        _require_token(tab.token)
        bin_dir = tab.agent_home / ".aconsole" / "bin"
        _private_dir(tab.agent_home / ".aconsole")
        _private_dir(bin_dir)
        hook = bin_dir / "aconsole-hook"
        notify = bin_dir / "aconsole-notify"
        _refuse_quote(hook)
        _refuse_quote(notify)
        _atomic_write(hook, _HOOK.encode("utf-8"), _SCRIPT_MODE)
        _atomic_write(notify, _NOTIFY.encode("utf-8"), _SCRIPT_MODE)
        env = {_ENV_PORT: str(tab.port), _ENV_TAB: tab.tab_id, _ENV_AUTH: tab.token}
        return CodexLaunch(
            argv=_argv(tab.port, hook, notify, trust),
            env=env,
            hook_path=hook,
            notify_path=notify,
        )


class ThreadStartParams(msgspec.Struct, frozen=True, omit_defaults=True, rename="camel"):
    """``thread/start`` params. ``sessionStartSource`` is present only for ``/clear``."""

    cwd: str
    session_start_source: str | None = None


class ThreadStartRequest(msgspec.Struct, frozen=True, kw_only=True):
    """JSON-RPC ``thread/start``. The response does not echo ``sessionStartSource``."""

    method: str
    id: int
    params: ThreadStartParams


def thread_start_request(*, cwd: str, request_id: int, clear: bool) -> ThreadStartRequest:
    """Build ``/new`` or ``/clear``. They differ only by ``params.sessionStartSource``."""
    source = "clear" if clear else None
    return ThreadStartRequest(
        method="thread/start",
        id=request_id,
        params=ThreadStartParams(cwd=cwd, session_start_source=source),
    )


def _state_key(snake: str) -> str:
    return f"{_STATE_PREFIX}:{snake}:0:0"


def _trusted_hooks(trust: HookTrustCache) -> tuple[tuple[str, str, str, int], ...]:
    """One row per hook whose hash is in the cache. A hash already used by another hook is skipped."""
    chosen: list[tuple[str, str, str, int]] = []
    used: set[str] = set()
    for name, snake, timeout in _HOOKS:
        digest = trust.hash_for(_state_key(snake))
        if digest is None or digest in used:
            continue
        used.add(digest)
        chosen.append((name, _state_key(snake), digest, timeout))
    return tuple(chosen)


def _argv(port: int, hook: Path, notify: Path, trust: HookTrustCache) -> tuple[str, ...]:
    command = f"{hook} codex"
    trusted = _trusted_hooks(trust)
    args = [
        "codex",
        "-c",
        f'mcp_servers.aconsole.url="http://127.0.0.1:{port}/mcp"',
        "-c",
        "mcp_servers.aconsole.bearer_token_env_var=ACONSOLE_TAB_TOKEN",
        "-c",
        "mcp_servers.aconsole.tool_timeout_sec=60",
        "-c",
        f'notify=["{notify}","codex"]',
    ]
    for name, _key, _digest, timeout in trusted:
        args.extend(("-c", f'hooks.{name}=[{{hooks=[{{type="command",command="{command}",timeout={timeout}}}]}}]'))
    if trusted:
        body = ", ".join(f'"{key}" = {{ trusted_hash = "{digest}" }}' for _name, key, digest, _timeout in trusted)
        args.extend(("-c", f"hooks.state={{ {body} }}"))
    args.extend(("-c", 'tui.notification_method="bel"', "-c", 'tui.notification_condition="always"'))
    return tuple(args)


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


def _refuse_quote(path: Path) -> None:
    text = str(path)
    if '"' in text or "\\" in text or "\n" in text:
        raise ValueError(_QUOTE)


def _private_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(_SYMLINK)
    if path.exists() and not path.is_dir():
        raise ValueError(_NOT_DIR)
    path.mkdir(mode=_DIR_MODE, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(_SYMLINK)
    path.chmod(_DIR_MODE)


def _atomic_write(path: Path, payload: bytes, mode: int) -> None:
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
