"""Host setup observations via injectable, read-only environment operations."""

from __future__ import annotations

import contextlib
import getpass
import hashlib
import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

import msgspec

from aconsole.contracts.app import CheckResult
from aconsole.core.application.ports.setup import SetupFixExecutionError, SetupFixPlan
from aconsole.core.infrastructure.containers.login import LOGIN_SHELL


class SetupEnvironment(Protocol):
    @property
    def home(self) -> Path: ...
    def which(self, name: str) -> str | None: ...
    def run(self, argv: tuple[str, ...]) -> tuple[bool, str]: ...
    def is_dir(self, path: Path) -> bool: ...
    @property
    def user(self) -> str: ...
    def environ(self, name: str) -> str | None: ...


class SystemSetupEnvironment:
    @property
    def home(self) -> Path:
        return Path.home()

    @property
    def user(self) -> str:
        return getpass.getuser()

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def run(self, argv: tuple[str, ...]) -> tuple[bool, str]:
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)  # noqa: S603 -- fixed argv
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        return result.returncode == 0, (result.stdout or result.stderr).strip()[-400:]

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def environ(self, name: str) -> str | None:
        return os.environ.get(name)


class HostSetupProbe:
    """Inspect host dependencies, containers and user-facing services."""

    _env: SetupEnvironment

    def __init__(self, env: SetupEnvironment | None = None) -> None:
        self._env = SystemSetupEnvironment() if env is None else env

    def run_checks(self) -> tuple[CheckResult, ...]:
        env = self._env
        home = env.home
        dependencies = all(env.which(tool) is not None for tool in ("podman", "distrobox"))
        listed, container_detail = env.run(("podman", "ps", "-a", "--format", "{{.Names}}"))
        names = tuple(line for line in container_detail.splitlines() if line.startswith("agent-")) if listed else ()
        agents = tuple((name, _agent_command(name)) for name in names)
        known = bool(agents) and all(command is not None for _, command in agents)
        versions = known and all(_version(env.run(_in_container(env, name, command, "--version")))
            for name, command in agents if command is not None)
        login = known and all(_claude_login(env.run(_in_container(env, name, command, "auth", "status")))
            for name, command in agents if command == "claude") and all(command == "claude" for _, command in agents)
        vault = home / "Obsidian" / "AConsole"
        worktrees = home / ".local" / "share" / "aconsole" / "worktrees"
        mounts = bool(names) and all(env.run(_in_container(env, name, "test", "-d", str(path)))[0]
            for name in names for path in (worktrees, vault))
        graph = all(env.run(_in_container(env, "devtools", tool, "--version"))[0]
            for tool in ("python3", "cargo", "dotnet", "node"))
        notifications = env.which("notify-send") is not None and bool(env.environ("DBUS_SESSION_BUS_ADDRESS"))
        return (
            _check("host_dependencies", dependencies, "podman and distrobox"),
            _check("agent_containers", bool(names), "Agent containers found" if names else "No agent containers found"),
            _check("agent_login", login, "Agent authentication status; unsupported agents require manual verification"),
            _check("agent_versions", versions, "Agent version commands in agent containers"),
            _check("storage", env.is_dir(vault), str(vault)),
            _check("worktree_storage_mounts", mounts, "Worktree and storage paths in agent containers",
                   fix_id="create_worktree_directory" if not env.is_dir(worktrees) else None),
            _check("devtools_graph_tools", graph, "Python, Cargo, .NET and Node in devtools"),
            _check("notifications", notifications, "notify-send and session D-Bus"),
        )


def _in_container(env: SetupEnvironment, name: str, *command: str) -> tuple[str, ...]:
    """The same user and login as a tab, so a check sees what an agent will see."""
    return ("podman", "exec", "--user", env.user, name, *LOGIN_SHELL, *command)


def _agent_command(name: str) -> str | None:
    for agent, command in (
        ("claude", "claude"), ("codex", "codex"), ("grok", "grok"), ("opencode", "opencode"), ("cursor", "agent"),
    ):
        if name == f"agent-{agent}" or name.startswith(f"agent-{agent}-"):
            return command
    return None


def _version(result: tuple[bool, str]) -> bool:
    return result[0] and re.search(r"\b\d+\.\d+\.\d+\b", result[1]) is not None


class _ClaudeStatus(msgspec.Struct, frozen=True, kw_only=True):
    auth_method: str = msgspec.field(name="authMethod")


def _claude_login(result: tuple[bool, str]) -> bool:
    if not result[0]:
        return False
    try:
        payload = msgspec.json.decode(result[1], type=_ClaudeStatus)
    except msgspec.DecodeError:
        return False
    return payload.auth_method in {"claude.ai", "oauth_token", "api_key", "api_key_helper", "third_party"}


def _check(identifier: str, ok: bool, detail: str, *, fix_id: str | None = None) -> CheckResult:  # noqa: FBT001 -- result status
    return CheckResult(id=identifier, title=identifier.replace("_", " ").title(), ok=ok,
        severity="info" if ok else "warning", detail=detail, fix_id=fix_id, requires_confirmation=fix_id is not None)


class HostSetupFixes:
    """Create the app-owned worktree directory after an explicit preview."""

    def __init__(self, home: Path | None = None) -> None:
        self._home: Path = (Path.home() if home is None else home).resolve()
        self._target: Path = self._home / ".local" / "share" / "aconsole" / "worktrees"

    def preview(self, fix_id: str) -> SetupFixPlan | None:
        if fix_id != "create_worktree_directory":
            return None
        state = "directory" if self._target.is_dir() else "other" if self._target.exists() else "absent"
        return SetupFixPlan(fix_id=fix_id, summary=f"Create {self._target}", diff=None,
            commands=(f"Create directory {self._target}",), reversible=True,
            source_fingerprint=state, target_fingerprint=hashlib.sha256(str(self._target).encode()).hexdigest())

    def apply(self, plan: SetupFixPlan) -> CheckResult:
        current = self.preview(plan.fix_id)
        if (current != plan or plan.source_fingerprint != "absent"
                or not self._target.resolve().is_relative_to(self._home)):
            raise SetupFixExecutionError(step="revalidate", stderr_tail="Target changed or is outside HOME")
        try:
            _create_worktree_directory(self._home)
        except OSError as exc:
            raise SetupFixExecutionError(step="create directory", stderr_tail=str(exc)) from exc
        return _check("worktree_storage_mounts", False, "Directory created; verify container mounts")  # noqa: FBT003 -- check result


def _create_worktree_directory(home: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(home, flags)
    try:
        for part in (".local", "share", "aconsole"):
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, dir_fd=descriptor)
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        os.mkdir("worktrees", dir_fd=descriptor)
    finally:
        os.close(descriptor)


class SetupTokens:
    def new_token(self) -> str:
        return secrets.token_urlsafe(32)
