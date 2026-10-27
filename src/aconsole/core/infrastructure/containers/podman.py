"""Podman inspect, start, exec argv, and orphan search by the tab marker."""

import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final

import msgspec

_TIMEOUT_S: Final = 10.0
_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_USER: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,31}")
_ENV_KEY: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TAB: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
# `if` keeps the loop status 0 when the last process does not match; an unreadable environ (another user,
# a process that just exited) is skipped. The script exits 0 after a full pass, so any other code is a failure.
_ORPHAN_SCAN: Final = (
    "for envf in /proc/[0-9]*/environ; do "
    "pid=${envf#/proc/}; pid=${pid%/environ}; "
    "if tr '\\0' '\\n' < \"$envf\" | grep -qx \"ACONSOLE_TAB=$ACONSOLE_ORPHAN_TAB\"; "
    'then echo "$pid"; fi; '
    "done 2>/dev/null; exit 0"
)
_OK: Final = 0
_FAILED: Final = "podman command failed"
_TIMED_OUT: Final = "podman timed out"
_MISSING: Final = "podman binary missing"
_BAD_INSPECT: Final = "podman inspect was not a container"
_BAD_PID: Final = "podman orphan pid was not a number"
_ONE: Final = 1


@final
class ContainerError(Exception):
    """Podman returned a failure, or its output was not the shape this adapter reads."""

    def __init__(self, message: str, *, code: int = 1, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class _State(msgspec.Struct, frozen=True):
    Running: bool


class _Row(msgspec.Struct, frozen=True):
    Id: str
    Name: str
    State: _State


@dataclass(frozen=True, kw_only=True)
class Inspected:
    """One container, as `podman inspect` reported it."""

    name: str
    ident: str
    running: bool


@dataclass(frozen=True, kw_only=True)
class ExecRequest:
    """What `podman exec` should run inside a container."""

    name: str
    command: tuple[str, ...]
    env: Mapping[str, str]
    workdir: str
    user: str


def check_container_name(name: str) -> None:
    """Reject a name that is not a single distrobox container token."""
    if _NAME.fullmatch(name) is None:
        message = "container name must be a single token"
        raise ValueError(message)


@final
class Podman:
    """Run one podman binary. Tests pass the fake under `tests/fixtures/bin/`."""

    def __init__(self, binary: Path, *, timeout_s: float = _TIMEOUT_S) -> None:
        if not binary.is_absolute():
            message = "podman binary path must be absolute"
            raise ValueError(message)
        if timeout_s <= 0:
            message = "podman timeout must be positive"
            raise ValueError(message)
        self._binary = binary
        self._timeout_s = timeout_s

    def inspect(self, name: str) -> Inspected:
        """Return id, name, and whether the container is running."""
        check_container_name(name)
        text = self._run(("inspect", "--format", "json", name))
        try:
            rows = msgspec.json.decode(text, type=list[_Row])
        except msgspec.DecodeError as exc:
            raise ContainerError(_BAD_INSPECT, detail=text) from exc
        if len(rows) != _ONE:
            raise ContainerError(_BAD_INSPECT, detail=text)
        row = rows[0]
        reported = row.Name.removeprefix("/")
        return Inspected(name=reported, ident=row.Id, running=row.State.Running)

    def start(self, name: str) -> None:
        """Start a container that inspect reported as stopped."""
        check_container_name(name)
        _ = self._run(("start", name))

    def exec_spec(self, request: ExecRequest) -> tuple[str, ...]:
        """Build the argv VTE will run. This does not start the process."""
        check_container_name(request.name)
        if _USER.fullmatch(request.user) is None:
            message = "user must be a single token"
            raise ValueError(message)
        if request.workdir == "" or not request.workdir.startswith("/") or "\n" in request.workdir:
            message = "workdir must be an absolute path"
            raise ValueError(message)
        if not request.command:
            message = "command must not be empty"
            raise ValueError(message)
        argv = [
            str(self._binary),
            "exec",
            "--interactive",
            "--tty",
            "--detach-keys=",
            "--user",
            request.user,
            "--workdir",
            request.workdir,
        ]
        for key in sorted(request.env):
            value = request.env[key]
            if _ENV_KEY.fullmatch(key) is None or "\n" in value or "\x00" in value:
                message = "env entry must be one line"
                raise ValueError(message)
            argv.extend(("-e", f"{key}={value}"))
        argv.append(request.name)
        argv.append("--")
        argv.extend(request.command)
        return tuple(argv)

    def orphans(self, name: str, tab_id: str) -> tuple[int, ...]:
        """Pids inside the container whose environ contains `ACONSOLE_TAB=<tab_id>`."""
        check_container_name(name)
        if _TAB.fullmatch(tab_id) is None:
            message = "tab id must be a single token"
            raise ValueError(message)
        text = self._run(
            (
                "exec",
                "-e",
                f"ACONSOLE_ORPHAN_TAB={tab_id}",
                name,
                "/bin/sh",
                "-c",
                _ORPHAN_SCAN,
            )
        )
        found: list[int] = []
        for line in text.splitlines():
            if line == "":
                continue
            if not line.isascii() or not line.isdigit():
                raise ContainerError(_BAD_PID, detail=line)
            found.append(int(line))
        return tuple(found)

    def _run(self, args: tuple[str, ...]) -> str:
        try:
            completed = subprocess.run(  # noqa: S603 -- argv is a list; the binary path is absolute
                [str(self._binary), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise ContainerError(_TIMED_OUT) from exc
        except FileNotFoundError as exc:
            raise ContainerError(_MISSING) from exc
        if completed.returncode != _OK:
            raise ContainerError(_FAILED, code=completed.returncode, detail=completed.stderr)
        return completed.stdout
