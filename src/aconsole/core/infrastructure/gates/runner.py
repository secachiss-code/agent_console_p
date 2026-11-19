"""Execute trusted profile argv through Podman, with no host command fallback."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, final, runtime_checkable

from aconsole.core.application.ports.gate_runner import GateResult, GateRunner, GateRunnerUnavailableError
from aconsole.core.application.ports.git_repo import GitCommandError
from aconsole.core.application.services.gates import GateState, MicroOutcome, MicroProfile
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.scope import check_scope
from aconsole.core.infrastructure.containers.podman import ContainerError, Podman
from aconsole.core.infrastructure.gates.verdict import compact_verdict
from aconsole.core.infrastructure.git.runner import GitRunner

_NUMSTAT_FIELDS = 3
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

    def preflight(self, source: Path, root: Path, commands: tuple[tuple[str, ...], ...]) -> tuple[GateResult, ...]:
        """Run the selected image with exactly one bind; exec cannot add mounts."""
        if (
            not source.is_absolute()
            or not root.is_absolute()
            or ".." in source.parts
            or ".." in root.parts
            or source.resolve() != source
            or root.resolve() != root
            or not source.is_relative_to(root)
            or source == root
            or any(char in str(source) for char in (",", "\x00", "\n"))
        ):
            message = "preflight mount must stay inside its root"
            raise ValueError(message)
        if not commands or any(
            not argv or argv not in self._profile or any("\x00" in arg for arg in argv) for argv in commands
        ):
            message = "gate command is not in the frozen profile"
            raise ValueError(message)
        try:
            inspected = self._podman.inspect(self._container)
            if not inspected.running:
                raise GateRunnerUnavailableError(_UNAVAILABLE)
            binary = self._podman.pipe_argv(inspected.ident, self._user, ("true",))[0]
            image_probe = subprocess.run(  # noqa: S603 -- trusted Podman binary and validated container id
                (binary, "inspect", "--format", "{{.Image}}", inspected.ident),
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            image = "sha256:" + image_probe.stdout.strip().removeprefix("sha256:")
            if re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None:
                raise GateRunnerUnavailableError(_UNAVAILABLE)
            results: list[GateResult] = []
            for argv in commands:
                result = self._preflight_command(binary, image, source, argv)
                results.append(result)
                if result.returncode or result.timed_out:
                    break
            return tuple(results)
        except (ContainerError, OSError, subprocess.SubprocessError) as exc:
            raise GateRunnerUnavailableError(_UNAVAILABLE) from exc

    def _preflight_command(self, binary: str, image: str, source: Path, argv: tuple[str, ...]) -> GateResult:
        command = (
            binary,
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user",
            self._user,
            "--tmpfs",
            "/tmp:rw,nosuid,nodev",  # noqa: S108 -- private container tmpfs
            "--workdir",
            "/tmp/aconsole-preflight",  # noqa: S108 -- isolated container mount destination
            "--mount",
            f"type=bind,src={source},dst=/tmp/aconsole-preflight,rw",
            "--entrypoint",
            "/usr/bin/env",
            image,
            "-i",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "LANG=C.UTF-8",
            "PYTHONNOUSERSITE=1",
            "PYTHONDONTWRITEBYTECODE=1",
            "XDG_CACHE_HOME=/tmp/cache",
            "RUFF_CACHE_DIR=/tmp/ruff",
            "/usr/bin/timeout",
            "--signal=TERM",
            "--kill-after=5s",
            "120s",
            *argv,
        )
        self._logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        started = time.monotonic()
        with tempfile.NamedTemporaryFile(dir=self._logs, suffix=".log", delete=False) as log:
            completed = subprocess.run(  # noqa: S603 -- Podman, immutable image, frozen argv, no shell
                command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, check=False
            )
            log_path = Path(log.name)
        if completed.returncode in {125, 126, 127}:
            raise GateRunnerUnavailableError(_UNAVAILABLE)
        with log_path.open("rb") as output:
            text = output.read(_OUTPUT_LIMIT).decode("utf-8", errors="replace")
        return GateResult(
            argv=argv,
            returncode=completed.returncode,
            timed_out=completed.returncode in {124, 137},
            output=text.encode("utf-8")[:_OUTPUT_LIMIT].decode("utf-8", errors="ignore"),
            log_path=str(log_path),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


@runtime_checkable
class PreflightRunner(Protocol):
    def preflight(self, source: Path, root: Path, commands: tuple[tuple[str, ...], ...]) -> tuple[GateResult, ...]: ...


@dataclass
class StagedPatch:
    worktree: Path
    source: Path
    originals: dict[str, bytes | None]
    results: tuple[GateResult, ...]
    outcome: MicroOutcome | None = None

    @property
    def passed(self) -> bool:
        if self.outcome is not None:
            return self.outcome.receipt.passed
        return bool(self.results) and all(result.returncode == 0 and not result.timed_out for result in self.results)

    @property
    def verdict(self) -> str:
        if self.outcome is not None:
            return self.outcome.receipt.compact_verdict
        return compact_verdict(list(self.results))

    def publish(self) -> None:
        # Recheck every original before the first write, including new-file parents.
        for name, original in self.originals.items():
            target = _regular_path(self.worktree, name)
            if (target.read_bytes() if target.exists() else None) != original:
                raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
        for name in self.originals:
            target = _regular_path(self.worktree, name)
            staged = _regular_path(self.source, name)
            if staged.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                _ = target.write_bytes(staged.read_bytes())
                target.chmod(staged.stat().st_mode & 0o777)
            elif target.exists():
                target.unlink()


def _regular_path(root: Path, name: str) -> Path:
    target = root / name
    for item in (target, *target.parents):
        if item == root:
            break
        if item.is_symlink():
            raise DomainError(code=4015, name="SCOPE_VIOLATION", retryable=False)
    if target.exists() and (not target.is_file() or target.stat().st_nlink != 1):
        raise DomainError(code=4015, name="SCOPE_VIOLATION", retryable=False)
    return target


@contextmanager
def prepare_patch(  # noqa: C901, PLR0913, PLR0915 -- validate diff, copy, apply, check, publish under one cleanup scope
    bind_runner: Callable[[tuple[tuple[str, ...], ...]], GateRunner],
    worktree: Path,
    paths: tuple[str, ...],
    profile: MicroProfile,
    patch: str,
    *,
    check: Callable[[GateState, tuple[tuple[str, ...], ...], Callable[[], tuple[GateResult, ...]]], MicroOutcome]
    | None = None,
) -> Generator[StagedPatch]:
    """Apply only to regular-file copies. Git parses paths; tools never execute on the host."""
    root = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "aconsole/preflight"
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise GateRunnerUnavailableError(_UNAVAILABLE) from exc
    if (
        root.stat().st_uid != os.getuid()
        or root.stat().st_mode & 0o077
        or root.is_symlink()
        or root.resolve() != root.absolute()
    ):
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        source = Path(directory)
        git = GitRunner()
        data = patch.encode("utf-8")
        try:
            stats = git.read(source, "apply", "--numstat", "-z", "-", stdin=data).stdout
            summary = git.read(source, "apply", "--summary", "-", stdin=data).text
        except GitCommandError as exc:
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False) from exc
        names: list[str] = []
        for row in stats.rstrip(b"\x00").split(b"\x00"):
            parts = row.split(b"\t", 2)
            if len(parts) != _NUMSTAT_FIELDS or not parts[0].isdigit() or not parts[1].isdigit() or not parts[2]:
                raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
            try:
                names.append(parts[2].decode("utf-8"))
            except UnicodeError as exc:
                raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False) from exc
        check_scope(paths, tuple(names)).require_allowed()
        if any(name.startswith("-") for name in names):
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
        if any(
            not line.strip().startswith(
                ("create mode 100644 ", "create mode 100755 ", "delete mode 100644 ", "delete mode 100755 ")
            )
            for line in summary.splitlines()
        ):
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
        originals: dict[str, bytes | None] = {}
        for name in names:
            target = _regular_path(worktree, name)
            originals[name] = target.read_bytes() if target.exists() else None
            if target.exists():
                copied = source / name
                copied.parent.mkdir(parents=True, exist_ok=True)
                _ = shutil.copyfile(target, copied)
                copied.chmod(target.stat().st_mode & 0o777)
        applied = git.write(source, "apply", "--whitespace=nowarn", "-", stdin=data)
        if applied.code:
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
        # Deleted files have no source text to check. Keep the profile's tools running
        # on the scratch cwd even for a deletion-only patch.
        files = tuple(sorted(name for name in names if (source / name).exists()))
        commands = tuple((*argv, *files) for argv in (profile.format, profile.fix, profile.lint, profile.types))
        runner = bind_runner(commands)
        if not isinstance(runner, PreflightRunner):
            raise GateRunnerUnavailableError(_UNAVAILABLE)
        fingerprint = tuple(
            (name, hashlib.sha256((source / name).read_bytes()).hexdigest(), (source / name).stat().st_mode & 0o777)
            if (source / name).exists()
            else (name, None, None)
            for name in sorted(originals)
        )
        digest = hashlib.sha256(json.dumps(fingerprint).encode()).hexdigest()
        state = GateState(state_hash=digest, delta_ref="", delta_sha256=digest)
        outcome = check(state, commands, lambda: runner.preflight(source, root, commands)) if check else None
        results = outcome.receipt.results if outcome is not None else runner.preflight(source, root, commands)
        yield StagedPatch(worktree=worktree, source=source, originals=originals, results=results, outcome=outcome)
