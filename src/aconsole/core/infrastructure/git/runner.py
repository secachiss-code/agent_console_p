"""One way to run git: argv lists, machine output, a time limit, and a bound on processes.

`LC_ALL=C` keeps messages out of the parsing. Reads set `GIT_OPTIONAL_LOCKS=0`, so a background look never
competes with an agent's own `git` for the index lock.

A repository config can name programs (`core.fsmonitor`, `diff.external`, `core.sshCommand`, a textconv, or
`core.hooksPath`). Every spawn overrides those. Status, diff, and snapshots point `core.hooksPath` at `/dev/null`.
A user commit or merge (`hooks=True`) runs the hooks in the repository's own `hooks` directory, not the path from
that config. Diffs also pass `--no-ext-diff` and `--no-textconv`.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Mapping
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
# These override the repository config for this one process. `core.hooksPath` is added per call.
_SAFE_CONFIG: Final = (
    "core.fsmonitor=false",
    f"core.sshCommand={host_binary('true')}",
    "diff.external=",
    "core.pager=cat",
)
_NO_HOOKS: Final = "/dev/null"
_DIFF_GUARDS: Final = ("--no-ext-diff", "--no-textconv")
# Environment forms of the same programs. The child must not inherit them.
_DROPPED_ENV: Final = ("GIT_EXTERNAL_DIFF", "GIT_SSH_COMMAND")


@dataclass(frozen=True, slots=True, kw_only=True)
class GitCall:
    """How one git command is run. `hooks` is only for a commit or merge the user asked for."""

    stdin: bytes | None = None
    env: Mapping[str, str] | None = None
    drop: tuple[str, ...] = ()
    hooks: bool = False
    timeout_s: float | None = None
    write: bool = False


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
        self,
        repo: Path,
        *args: str,
        stdin: bytes | None = None,
        codes: frozenset[int] = _SUCCESS,
    ) -> GitOutput:
        """A command that changes no ref and no file. A code outside `codes` raises. Hooks do not run."""
        output = self.invoke(repo, args, GitCall(stdin=stdin))
        if len(output.stdout) > self._max_bytes:
            raise GitCommandError(_TOO_LARGE)
        return self._checked(output, codes)

    def write(
        self,
        repo: Path,
        *args: str,
        stdin: bytes | None = None,
        timeout_s: float | None = None,
        hooks: bool = False,
    ) -> GitOutput:
        """A command that may change things. The caller reads the exit code.

        `hooks` is for a commit or merge the user asked for. Other writes leave hooks off.
        """
        return self.invoke(repo, args, GitCall(stdin=stdin, timeout_s=timeout_s, hooks=hooks, write=True))

    def invoke(self, repo: Path, args: tuple[str, ...], call: GitCall | None = None) -> GitOutput:
        """The only git spawn. Repository config that names a program is overridden for this process."""
        if not repo.is_absolute():
            raise GitCommandError(_ABSOLUTE)
        options = GitCall() if call is None else call
        with self._slots:
            return self._spawn(repo, args, options)

    def _checked(self, output: GitOutput, codes: frozenset[int]) -> GitOutput:
        if output.code not in codes:
            raise GitCommandError(_FAILED, detail=output.stderr)
        return output

    def _spawn(self, repo: Path, args: tuple[str, ...], call: GitCall) -> GitOutput:
        child = _environment(call.env, drop=call.drop, write=call.write)
        hooks_path = _NO_HOOKS
        if call.hooks:
            located = self._spawn(
                repo,
                ("rev-parse", "--path-format=absolute", "--git-common-dir"),
                GitCall(env=child, timeout_s=call.timeout_s),
            )
            if located.code != 0:
                raise GitCommandError(_FAILED, detail=located.stderr)
            hooks_path = str(Path(located.text.strip()) / "hooks")
        limit = self._timeout_s if call.timeout_s is None else call.timeout_s
        try:
            completed = subprocess.run(  # noqa: S603 -- argv is a list; the git path is absolute
                (str(self._git), *_config(hooks_path), "-C", str(repo), *_guard_diff(args)),
                check=False,
                capture_output=True,
                input=call.stdin,
                stdin=subprocess.DEVNULL if call.stdin is None else None,
                timeout=limit,
                env=child,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitCommandError(_TIMED_OUT) from exc
        except OSError as exc:
            raise GitCommandError(_MISSING, detail=str(exc)) from exc
        return GitOutput(
            code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr.decode("utf-8", errors="replace"),
        )


def _config(hooks_path: str) -> tuple[str, ...]:
    settings = (*_SAFE_CONFIG, f"core.hooksPath={hooks_path}")
    return tuple(part for setting in settings for part in ("-c", setting))


def _guard_diff(args: tuple[str, ...]) -> tuple[str, ...]:
    """`git diff` runs textconv and external drivers unless these flags are present."""
    command = next((word for word in args if not word.startswith("-")), "")
    if command != "diff":
        return args
    missing = tuple(flag for flag in _DIFF_GUARDS if flag not in args)
    if not missing:
        return args
    index = args.index(command)
    return args[: index + 1] + missing + args[index + 1 :]


def _environment(
    extra: Mapping[str, str] | None,
    *,
    drop: tuple[str, ...],
    write: bool,
) -> dict[str, str]:
    env = {
        **os.environ,
        "LC_ALL": "C",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_EDITOR": "true",
        "GIT_PAGER": "cat",
    }
    for name in (*_DROPPED_ENV, *drop):
        env.pop(name, None)
    if not write:
        env["GIT_OPTIONAL_LOCKS"] = "0"
    if extra:
        env.update(extra)
    return env
