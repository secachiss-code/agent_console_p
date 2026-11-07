"""Host setup observations via injectable, read-only environment operations."""

from __future__ import annotations

import contextlib
import getpass
import hashlib
import os
import re
import secrets
import shlex
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
        metadata = argv[:2] in {("podman", "inspect"), ("podman", "ps")}
        limit = 65536 if metadata else 400
        output = (result.stdout or result.stderr).strip()
        if metadata and len(output) > limit:
            return False, "Container mount metadata exceeded the diagnostic limit"
        return result.returncode == 0, output[-limit:]

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
        missing_tools = tuple(tool for tool in ("podman", "distrobox") if env.which(tool) is None)
        listed, container_detail = env.run(("podman", "ps", "-a", "--format", "{{.Names}}"))
        names = tuple(line for line in container_detail.splitlines() if line.startswith("agent-")) if listed else ()
        agents = tuple((name, _agent_command(name)) for name in names)
        no_cli = tuple(
            name for name, command in agents
            if command is None or not _version(env.run(_in_container(env, name, command, "--version")))
        )
        # Only Claude can say whether it is logged in here. The others are not failures for being unknown:
        # the wizard's agent rows show what the core learned about them.
        no_login = tuple(
            name for name, command in agents
            if command == "claude" and not _claude_login(env.run(_in_container(env, name, command, "auth", "status")))
        )
        vault = home / "Obsidian" / "AConsole"
        worktrees = home / ".local" / "share" / "aconsole" / "worktrees"
        no_mount = tuple(
            name for name in names
            if not all(env.run(_in_container(env, name, "test", "-d", str(path)))[0] for path in (worktrees, vault))
        )
        no_graph = tuple(
            tool for tool in ("python3", "cargo", "dotnet", "node")
            if not env.run(_in_container(env, "devtools", tool, "--version"))[0]
        )
        notification_program = env.which("notify-send") is not None
        notification_bus = bool(env.environ("DBUS_SESSION_BUS_ADDRESS"))
        notifications = notification_program and notification_bus
        return (
            _check("host_dependencies", not missing_tools,
                   ", ".join(missing_tools) if missing_tools else "podman and distrobox",
                   command=f"sudo apt install {' '.join(missing_tools)}"),
            _check("agent_containers", bool(names), ", ".join(names) if names else
                   "No agent containers. See README: Container isolation (SEC-7) before creating one.",
                   command="podman create --help"),
            _check("agent_login", not no_login,
                   ", ".join(no_login) if no_login else "Claude is checked here; other agents show their state above",
                   command=_login_command(no_login)),
            _check("agent_versions", not no_cli,
                   ", ".join(no_cli) if no_cli else "Agent version commands in agent containers",
                   command=_shell_command(no_cli)),
            _check("storage", env.is_dir(vault), str(vault),
                   fix_id="create_storage_directory" if not env.is_dir(vault) else None,
                   command=f"mkdir -p {shlex.quote(str(vault))}"),
            _check("worktree_storage_mounts", not no_mount,
                   ", ".join(no_mount) if no_mount else "Worktree and storage paths in agent containers",
                   fix_id="create_worktree_directory" if not env.is_dir(worktrees) else None,
                   command=_recreate_command(no_mount)),
            _check("devtools_graph_tools", not no_graph,
                   ", ".join(no_graph) if no_graph else "Python, Cargo, .NET and Node in devtools",
                   command="distrobox enter devtools"),
            self.isolation_check(names, listed=listed),
            _check("notifications", notifications,
                   "notify-send and session D-Bus" if notifications else
                   "notify-send missing" if not notification_program else "Session D-Bus unavailable",
                   command="sudo apt install libnotify-bin" if not notification_program else
                   "systemctl --user status dbus.service"),
        )


    def isolation_check(self, names: tuple[str, ...] | None = None, *, listed: bool = True) -> CheckResult:
        """Read engine metadata without executing an agent profile or modifying a container."""
        if names is None:
            listed, detail = self._env.run(("podman", "ps", "-a", "--format", "{{.Names}}"))
            names = tuple(name for name in detail.splitlines() if name.startswith("agent-") or name == "devtools")
        findings: list[str] = []
        sensitive = _host_sensitive_paths(self._env)
        if not listed:
            findings.append("Container list unavailable; isolation is unknown")
        # Devtools executes checkout code too; inspect it even when called from the wizard.
        for name in dict.fromkeys((*names, "devtools")):
            ok, raw = self._env.run(("podman", "inspect", "--format", "{{json .Mounts}}", name))
            try:
                mounts = msgspec.json.decode(raw, type=tuple[_HostMount, ...]) if ok else None
            except msgspec.DecodeError:
                mounts = None
            if mounts is None:
                findings.append(f"{name}: mount metadata unavailable; isolation is unknown")
                continue
            for mount in mounts:
                source = Path(mount.source)
                if not source.is_absolute() or not Path(mount.destination).is_absolute():
                    findings.append(f"{name}: invalid mount metadata; isolation is unknown")
                    continue
                try:
                    source = source.resolve()
                except (OSError, RuntimeError, ValueError):
                    findings.append(f"{name}: unresolved mount source; isolation is unknown")
                    continue
                if any(source.is_relative_to(path) or path.is_relative_to(source)
                       for path in sensitive):
                    mode = "writable" if mount.writable else "read-only"
                    findings.append(f"{name}: {source} -> {mount.destination} ({mode})")
        instruction = ("Use a separate container HOME; bind only worktrees and vault, without host HOME, "
                       "AConsole database, startup files, host sockets or host-exec integration. "
                       "See README: Container isolation (SEC-7). Recreate containers manually after reviewing mounts.")
        return _check("container_isolation", not findings,
                      "; ".join(findings) + ". " + instruction if findings else
                      "No sensitive host binds detected; this is a metadata check, not an isolation guarantee.",
                      command="podman inspect --format '{{json .Mounts}}' " +
                      " ".join(shlex.quote(name) for name in dict.fromkeys((*names, "devtools"))))


class _HostMount(msgspec.Struct, frozen=True, kw_only=True):
    source: str = msgspec.field(name="Source")
    destination: str = msgspec.field(name="Destination")
    writable: bool = msgspec.field(name="RW")


def _host_sensitive_paths(env: SetupEnvironment) -> tuple[Path, ...]:
    home = env.home
    data = Path(env.environ("XDG_DATA_HOME") or home / ".local/share")
    config = Path(env.environ("XDG_CONFIG_HOME") or home / ".config")
    state = Path(env.environ("XDG_STATE_HOME") or home / ".local/state")
    return tuple(path.resolve() for path in (
        data / "aconsole/aconsole.db", data / "aconsole/aconsole.db-wal", data / "aconsole/aconsole.db-shm",
        config, state / "aconsole-hostrun", home / ".containers/hostrun", home / ".containers/host-bins",
        home / ".local/bin", home / ".bashrc", home / ".profile", home / ".bash_profile", home / ".zshrc",
        home / ".ssh", home / ".aws",
        Path(env.environ("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"),
    ))


def _login_command(names: tuple[str, ...]) -> str | None:
    if not names:
        return None
    return f'podman exec -it --user "$USER" {shlex.quote(names[0])} bash -lc "claude auth login"'


def _shell_command(names: tuple[str, ...]) -> str | None:
    return f"distrobox enter {shlex.quote(names[0])}" if names else None


def _recreate_command(names: tuple[str, ...]) -> str | None:
    if not names:
        return None
    name = shlex.quote(names[0])
    return f"podman inspect --format '{{{{json .Mounts}}}}' {name}"


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


def _check(
    identifier: str,
    ok: bool,  # noqa: FBT001 -- reads as a sentence at every call
    detail: str,
    *,
    fix_id: str | None = None,
    command: str | None = None,
) -> CheckResult:
    return CheckResult(
        id=identifier, title=identifier.replace("_", " ").title(), ok=ok, severity="info" if ok else "warning",
        detail=detail, fix_id=fix_id, requires_confirmation=fix_id is not None, command=None if ok else command,
    )


class HostSetupFixes:
    """Create the directories the application owns, after an explicit preview."""

    def __init__(self, home: Path | None = None) -> None:
        self._home: Path = (Path.home() if home is None else home).resolve()
        self._parts: dict[str, tuple[str, ...]] = {
            "create_worktree_directory": (".local", "share", "aconsole", "worktrees"),
            "create_storage_directory": ("Obsidian", "AConsole"),
        }

    def _target(self, fix_id: str) -> Path:
        return self._home.joinpath(*self._parts[fix_id])

    def preview(self, fix_id: str) -> SetupFixPlan | None:
        if fix_id not in self._parts:
            return None
        target = self._target(fix_id)
        state = "directory" if target.is_dir() else "other" if target.exists() else "absent"
        return SetupFixPlan(fix_id=fix_id, summary=f"Create {target}", diff=None,
            commands=(f"Create directory {target}",), reversible=True,
            source_fingerprint=state, target_fingerprint=hashlib.sha256(str(target).encode()).hexdigest())

    def apply(self, plan: SetupFixPlan) -> CheckResult:
        current = self.preview(plan.fix_id)
        if (current != plan or plan.source_fingerprint != "absent"
                or not self._target(plan.fix_id).resolve().is_relative_to(self._home)):
            raise SetupFixExecutionError(step="revalidate", stderr_tail="Target changed or is outside HOME")
        try:
            _create_directories(self._home, self._parts[plan.fix_id])
        except OSError as exc:
            raise SetupFixExecutionError(step="create directory", stderr_tail=str(exc)) from exc
        if plan.fix_id == "create_storage_directory":
            return _check("storage", True, str(self._target(plan.fix_id)))  # noqa: FBT003 -- reads as a sentence
        return _check("worktree_storage_mounts", False, "Directory created; verify container mounts")  # noqa: FBT003


def _create_directories(home: Path, parts: tuple[str, ...]) -> None:
    """One level at a time and never through a link, so nothing lands outside the home directory."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(home, flags)
    try:
        for part in parts[:-1]:
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, dir_fd=descriptor)
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        os.mkdir(parts[-1], dir_fd=descriptor)
    finally:
        os.close(descriptor)


class SetupTokens:
    def new_token(self) -> str:
        return secrets.token_urlsafe(32)
