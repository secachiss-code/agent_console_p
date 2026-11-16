"""Execute trusted profile argv through Podman, with no host command fallback."""

import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import final

from aconsole.core.application.ports.gate_runner import GateResult, GateRunnerUnavailableError
from aconsole.core.infrastructure.containers.podman import ContainerError, Podman

_OUTPUT_LIMIT = 1024 * 1024
_TIMEOUTS = {1: 120, 2: 900, 3: 900}
_DIRECTION_TIER = 3
_UNAVAILABLE = "GATE_RUNNER_UNAVAILABLE"


@final
class ContainerGateRunner:
    """Bind a tab and its trusted, frozen profile before accepting execution requests.

    The caller supplies profile argv from the frozen L0 record, never from agent input.
    GNU timeout runs inside the container and signals the command's process group.
    """

    def __init__(  # noqa: PLR0913 -- binds the container context and trusted profile
        self,
        *,
        podman: Podman,
        container: str,
        user: str,
        worktree: str,
        profile: tuple[tuple[str, ...], ...],
        state_home: Path | None = None,
    ) -> None:
        if not worktree.startswith("/") or "\x00" in worktree:
            message = "gate worktree must be absolute"
            raise ValueError(message)
        self._podman = podman
        self._container = container
        self._user = user
        self._worktree = worktree
        self._profile = frozenset(profile)
        default_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        root = state_home if state_home is not None else default_home
        self._logs = root / "aconsole" / "gates"

    def run(self, argv: tuple[str, ...], *, tier: int) -> GateResult:
        if not argv or argv not in self._profile or any("\x00" in arg for arg in argv):
            message = "gate command is not in the frozen profile"
            raise ValueError(message)
        if tier not in _TIMEOUTS:
            message = "gate tier must be 1, 2 or 3"
            raise ValueError(message)
        timeout = _TIMEOUTS[tier]
        try:
            inspected = self._podman.inspect(self._container)
            if not inspected.running:
                raise GateRunnerUnavailableError(_UNAVAILABLE)
            sealed = self._podman.gate_environment(inspected.ident, self._worktree) if tier == _DIRECTION_TIER else None
            command = self._podman.pipe_argv(
                inspected.ident,
                self._user,
                (
                    "/usr/bin/env",
                    "-i",
                    "PATH=/usr/local/bin:/usr/bin:/bin",
                    "LANG=C.UTF-8",
                    "PYTHONNOUSERSITE=1",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "XDG_CACHE_HOME=/tmp/aconsole-gates/cache",
                    "RUFF_CACHE_DIR=/tmp/aconsole-gates/ruff",
                    "/usr/bin/timeout",
                    "--signal=TERM",
                    "--kill-after=5s",
                    f"{timeout}s",
                    *argv,
                ),
            )
            # Workdir is a Podman option; it is never interpreted by a shell.
            command = (*command[:2], "--workdir", self._worktree, *command[2:])
            self._logs.mkdir(parents=True, exist_ok=True, mode=0o700)
            started = time.monotonic()
            with tempfile.NamedTemporaryFile(dir=self._logs, suffix=".log", delete=False) as log:
                completed = subprocess.run(  # noqa: S603 -- only Podman argv, no shell or host fallback
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                    env={
                        key: value
                        for key, value in os.environ.items()
                        if key in {"PATH", "HOME", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME"}
                    },
                )
                log_path = Path(log.name)
            duration = int((time.monotonic() - started) * 1000)
            if sealed is not None and sealed != self._podman.gate_environment(inspected.ident, self._worktree):
                raise GateRunnerUnavailableError(_UNAVAILABLE)
            if completed.returncode in {125, 126, 127}:
                raise GateRunnerUnavailableError(_UNAVAILABLE)
            with log_path.open("rb") as output:
                # Replacement characters can expand invalid input to three bytes each.
                text = output.read(_OUTPUT_LIMIT).decode("utf-8", errors="replace")
                text = text.encode("utf-8")[:_OUTPUT_LIMIT].decode("utf-8", errors="ignore")
            return GateResult(
                argv=argv,
                returncode=completed.returncode,
                timed_out=completed.returncode in {124, 137},
                output=text,
                log_path=str(log_path),
                duration_ms=duration,
            )
        except (ContainerError, OSError) as exc:
            raise GateRunnerUnavailableError(_UNAVAILABLE) from exc
