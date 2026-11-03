"""One way to run git: argv lists, machine output, a time limit, and a bound on processes.

`LC_ALL=C` keeps messages out of the parsing. Reads set `GIT_OPTIONAL_LOCKS=0`, so a background look never
competes with an agent's own `git` for the index lock.
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final

from aconsole.core.application.ports.git_repo import GitCommandError
from aconsole.core.infrastructure.host_bins import host_binary

_GIT: Final = host_binary("git")
_TIMEOUT_S: Final = 30.0
_MAX_PROCESSES: Final = 4
_MAX_BYTES: Final = 16_000_000
_SUCCESS: Final = frozenset({0})
_FAILED: Final = "git command failed"
_TIMED_OUT: Final = "git timed out"
_MISSING: Final = "git binary missing"
_TOO_LARGE: Final = "git answer is over the byte cap"
_ABSOLUTE: Final = "git and repository paths must be absolute"


@dataclass(frozen=True, slots=True, kw_only=True)
class GitOutput:
    code: int
    stdout: bytes
    stderr: str

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    @property
    def said(self) -> str:
        """Everything git told the user: a hook may write to either stream."""
        return "\n".join(part for part in (self.text.strip(), self.stderr.strip()) if part)


@final
class GitRunner:
    def __init__(
        self,
        git: Path = _GIT,
        *,
        timeout_s: float = _TIMEOUT_S,
        max_processes: int = _MAX_PROCESSES,
        max_bytes: int = _MAX_BYTES,
    ) -> None:
        if not git.is_absolute():
            raise GitCommandError(_ABSOLUTE)
        self._git = git
        self._timeout_s = timeout_s
        self._slots = threading.BoundedSemaphore(max_processes)
        self._max_bytes = max_bytes

    def read(
        self, repo: Path, *args: str, stdin: bytes | None = None, codes: frozenset[int] = _SUCCESS,
    ) -> GitOutput:
        """A command that changes no ref and no file. A code outside `codes` raises."""
        return self._checked(self._run(repo, args, stdin=stdin, write=False, timeout_s=self._timeout_s), codes)

    def write(
        self,
        repo: Path,
        *args: str,
        stdin: bytes | None = None,
        timeout_s: float | None = None,
    ) -> GitOutput:
        """A command that may change things. The caller reads the exit code: a hook may refuse."""
        limit = self._timeout_s if timeout_s is None else timeout_s
        return self._run(repo, args, stdin=stdin, write=True, timeout_s=limit)

    def _checked(self, output: GitOutput, codes: frozenset[int]) -> GitOutput:
        if output.code not in codes:
            raise GitCommandError(_FAILED, detail=output.stderr)
        return output

    def _run(
        self,
        repo: Path,
        args: tuple[str, ...],
        *,
        stdin: bytes | None,
        write: bool,
        timeout_s: float,
    ) -> GitOutput:
        if not repo.is_absolute():
            raise GitCommandError(_ABSOLUTE)
        env = {**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"}
        if not write:
            env["GIT_OPTIONAL_LOCKS"] = "0"
        with self._slots:
            try:
                completed = subprocess.run(  # noqa: S603 -- argv is a list; the git path is absolute
                    (str(self._git), "-C", str(repo), *args),
                    check=False,
                    capture_output=True,
                    input=stdin,
                    stdin=subprocess.DEVNULL if stdin is None else None,
                    timeout=timeout_s,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise GitCommandError(_TIMED_OUT) from exc
            except OSError as exc:
                raise GitCommandError(_MISSING, detail=str(exc)) from exc
        if len(completed.stdout) > self._max_bytes:
            raise GitCommandError(_TOO_LARGE)
        return GitOutput(
            code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr.decode("utf-8", errors="replace"),
        )

