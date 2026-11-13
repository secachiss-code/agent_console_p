"""Codex ``-c`` argv, the hook command, and the notify wrapper.

Command hooks are emitted only for keys the trust cache already holds (ADR-0002).
The tab token stays in the environment. ``~/.codex/config.toml`` and ``AGENTS.md`` are not written.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import msgspec

from aconsole.core.domain.approval_policy import ApprovalPolicyChoice, ApprovalPolicyMode, DangerousSubmode
from aconsole.core.infrastructure.agents.codex.trust import HookTrustCache, verified_hooks
from aconsole.core.infrastructure.agents.private_files import atomic_write, check_home, private_dir

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
    ("PreToolUse", "pre_tool_use", _PERMISSION_TIMEOUT),
    ("PostToolUse", "post_tool_use", _HOOK_TIMEOUT),
    ("Stop", "stop", _HOOK_TIMEOUT),
    ("SessionEnd", "session_end", _SESSION_END_TIMEOUT),
)
_ABS: Final = "agent home must be absolute"
_HOME: Final = "agent home must be a directory"
_TAB: Final = "tab id must be 1-64 characters of letters, digits, '_' or '-'"
_PORT: Final = "port must be from 1 to 65535"
_EMPTY: Final = "tab token must be non-empty"
_QUOTE: Final = "path cannot contain a quote or backslash"
_HOOK: Final = """#!/bin/sh
curl -fsS --max-time 590 -H @/proc/self/fd/3 \\
  --data-binary @- "http://127.0.0.1:$ACONSOLE_PORT/v1/tabs/$ACONSOLE_TAB/codex/hook" \\
  3<<ACONSOLE_AUTH 2>/dev/null || { echo AConsole-hook-failed >&2; exit 2; }
Authorization: Bearer $ACONSOLE_TAB_TOKEN
ACONSOLE_AUTH
exit 0
"""
_NOTIFY: Final = """#!/bin/sh
if [ -n "$2" ]; then
  printf '%s' "$2" | curl -sS -m 2 -H @/proc/self/fd/3 \\
    --data-binary @- "http://127.0.0.1:$ACONSOLE_PORT/v1/tabs/$ACONSOLE_TAB/codex/notify" \\
    3<<ACONSOLE_AUTH >/dev/null 2>&1 || true
Authorization: Bearer $ACONSOLE_TAB_TOKEN
ACONSOLE_AUTH
else
  curl -sS -m 2 -H @/proc/self/fd/3 \\
    --data-binary @- "http://127.0.0.1:$ACONSOLE_PORT/v1/tabs/$ACONSOLE_TAB/codex/notify" \\
    3<<ACONSOLE_AUTH >/dev/null 2>&1 || true
Authorization: Bearer $ACONSOLE_TAB_TOKEN
ACONSOLE_AUTH
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
    policy: ApprovalPolicyChoice = field(default_factory=ApprovalPolicyChoice)


@dataclass(frozen=True, slots=True, kw_only=True)
class CodexLaunch:
    """``argv`` is the ``codex -c ...`` command. ``env`` carries the tab token."""

    argv: tuple[str, ...]
    env: dict[str, str]
    hook_path: Path
    notify_path: Path


class CodexLauncher:
    """Write the shared hook and notify scripts and return the launch argv."""

    def probe(self, tab: CodexTab) -> CodexLaunch:
        """List all generated definitions without granting trust or starting a model turn."""
        launch = self.launch(tab, HookTrustCache())
        command = _hook_command(launch.hook_path)
        args = list(launch.argv)
        for name, _snake, timeout in _HOOKS:
            args.extend(("-c", _hook_flag(name, command, timeout)))
        return CodexLaunch(argv=tuple(args), env=launch.env, hook_path=launch.hook_path, notify_path=launch.notify_path)

    def verified(self, tab: CodexTab, raw: bytes, *, require_trusted: bool = False) -> CodexLaunch:
        """Use hashes returned by the installed Codex for precisely these generated definitions."""
        hook = tab.agent_home / ".aconsole/bin/aconsole-hook"
        command = _hook_command(hook)
        expected = {
            _state_key(snake): (name[0].lower() + name[1:], command, timeout) for name, snake, timeout in _HOOKS
        }
        trust = verified_hooks(raw, expected, require_trusted=require_trusted)
        return self.launch(tab, trust)

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
            argv=_argv(tab.port, hook, notify, trust, tab.policy),
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


def _hook_flag(name: str, command: str, timeout: int) -> str:
    quoted = msgspec.json.encode(command).decode("utf-8")
    return f'hooks.{name}=[{{hooks=[{{type="command",command={quoted},timeout={timeout}}}]}}]'


def _hook_command(hook: Path) -> str:
    # The outer shell survives a missing or crashed script and translates its
    # failure into Codex's explicit blocking exit code, instead of a hook error.
    return f"{shlex.quote(str(hook))} codex || {{ echo AConsole-hook-failed >&2; exit 2; }}"


def _argv(
    port: int, hook: Path, notify: Path, trust: HookTrustCache, policy: ApprovalPolicyChoice,
) -> tuple[str, ...]:
    command = _hook_command(hook)
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
    approval, sandbox = "on-request", "workspace-write"
    if policy.mode is ApprovalPolicyMode.DENY:
        approval, sandbox = "never", "read-only"
    elif policy.mode is ApprovalPolicyMode.ALLOW and policy.dangerous is DangerousSubmode.ALLOW:
        approval, sandbox = "never", "danger-full-access"
    args.extend(("-c", f'approval_policy="{approval}"', "-c", f'sandbox_mode="{sandbox}"'))
    for name, _key, _digest, timeout in trusted:
        args.extend(("-c", _hook_flag(name, command, timeout)))
    if trusted:
        body = ", ".join(f'"{key}" = {{ trusted_hash = "{digest}" }}' for _name, key, digest, _timeout in trusted)
        args.extend(("-c", f"hooks.state={{ {body} }}"))
    args.extend(("-c", 'tui.notification_method="osc9"', "-c", 'tui.notification_condition="always"'))
    return tuple(args)


def _require_home(agent_home: Path) -> None:
    if not agent_home.is_absolute():
        raise ValueError(_ABS)
    if agent_home.is_symlink() or not agent_home.is_dir():
        raise ValueError(_HOME)
    check_home(agent_home)


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
    private_dir(path)


def _atomic_write(path: Path, payload: bytes, mode: int) -> None:
    atomic_write(path, payload, mode)
