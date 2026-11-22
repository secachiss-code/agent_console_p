"""Run project analysis in bubblewrap with a read-only workspace and private scratch."""

from __future__ import annotations

import os
import signal
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from aconsole.core.application.services.project_analysis import Refusal

if TYPE_CHECKING:
    from collections.abc import Callable

    from aconsole.core.application.services.project_analysis import RunSpec

_UNAVAILABLE: Final = Refusal(code=2015, name="CONFINEMENT_UNAVAILABLE")
_SYSTEM_PATHS: Final = ("/usr", "/bin", "/lib", "/lib64")
_PROBE_COMMAND: Final = ("/usr/bin/true",)
_TIMEOUT: Final = 5.0


@dataclass(frozen=True, slots=True, kw_only=True)
class Bubblewrap:
    binary: Path = Path("/usr/bin/bwrap")

    def run(self, spec: RunSpec, cancel: Callable[[], bool]) -> int | Refusal:
        """Probe the same sandbox that will run the tool, and fail closed."""
        if cancel():
            return -signal.SIGKILL
        try:
            sandbox = self._sandbox(spec)
            if sandbox is None:
                return _UNAVAILABLE
            probe = subprocess.run(  # noqa: S603 -- fixed command in a verified sandbox configuration
                (*sandbox, *_PROBE_COMMAND),
                env={},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_TIMEOUT,
                check=False,
            )
            if probe.returncode != 0:
                return _UNAVAILABLE
            if cancel():
                return -signal.SIGKILL
            process = subprocess.Popen(  # noqa: S603 -- tool argv stays inside the probed sandbox
                (*sandbox, *spec.command),
                env={},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError, subprocess.TimeoutExpired:
            return _UNAVAILABLE
        return _wait(process, cancel)

    def _sandbox(self, spec: RunSpec) -> tuple[str, ...] | None:
        if not self.binary.is_absolute() or not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            return None
        project = spec.project.resolve(strict=True)
        scratch = spec.scratch.resolve(strict=True)
        system = tuple(Path(path) for path in _SYSTEM_PATHS if Path(path).is_dir())
        reserved = (*system, Path("/dev"), Path("/proc"))
        if (
            not project.is_dir()
            or not scratch.is_dir()
            or scratch.is_relative_to(project)
            or project.is_relative_to(scratch)
            or Path.home().resolve().is_relative_to(project)
            or any(
                path.is_relative_to(mount) or mount.is_relative_to(path)
                for path in (project, scratch)
                for mount in reserved
            )
            or not spec.command
            or not Path(spec.command[0]).is_absolute()
        ):
            return None
        argv = [
            str(self.binary),
            "--die-with-parent",
            "--unshare-user",
            "--disable-userns",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-ipc",
            "--unshare-uts",
            "--cap-drop",
            "ALL",
            "--proc",
            "/proc",
            "--remount-ro",
            "/proc",
            "--dev",
            "/dev",
            "--remount-ro",
            "/dev",
        ]
        for mount in system:
            argv.extend(("--ro-bind", str(mount), str(mount)))
        argv.extend(
            (
                "--ro-bind",
                str(project),
                str(project),
                "--bind",
                str(scratch),
                str(scratch),
                "--chdir",
                str(scratch),
                "--remount-ro",
                "/",
            )
        )
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            **spec.tool_env,
            "HOME": str(scratch),
            "TMPDIR": str(scratch),
        }
        for key, value in env.items():
            argv.extend(("--setenv", key, value))
        argv.append("--")
        return tuple(argv)


def _wait(process: subprocess.Popen[bytes], cancel: Callable[[], bool]) -> int:
    try:
        while True:
            try:
                return process.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                if cancel():
                    break
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            _ = process.wait(timeout=_TIMEOUT)
    return -signal.SIGKILL
