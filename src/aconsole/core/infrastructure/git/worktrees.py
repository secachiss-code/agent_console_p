"""App-managed git worktrees.

Create uses `git worktree add -b ac/<slug>-<id>`. A lock file means the tab is
still running. `.worktreeinclude` copies matching files from the main checkout.
`.aconsole/setup.sh` runs on the host, so it runs only when the request carries
the sha256 of its exact bytes (the user approved that script); its whole process
group is killed on timeout. A create that fails after `worktree add` is rolled back.
A dirty tree, or one with commits ahead of the base, is kept. A tree with ignored
files (`.venv/`, included copies) is kept unless the caller discards them. A clean
tree is removed and its branch deleted. `--force` is used only to roll back a
create or when the caller discards ignored files.
"""

import contextlib
import fnmatch
import hashlib
import os
import re
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, final

_TIMEOUT_S: Final = 30.0
_SETUP_TIMEOUT_S: Final = 30.0
_OK: Final = 0
_MIN_STATUS: Final = 4
_META_LINES: Final = 3
_TOKEN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,40}")
_SHA: Final = re.compile(r"[0-9a-f]{7,64}")
_DIGEST_LEN: Final = 16
_STATUS_PATH: Final = 3
_ZERO: Final = "0"
_LOCK_REL: Final = ".aconsole/lock"
_SHELL: Final = Path("/bin/sh")
_FAILED: Final = "git command failed"
_TIMED_OUT: Final = "git timed out"
_MISSING: Final = "git binary missing"
_SETUP_FAILED: Final = "setup script failed"
_SETUP_TIMED_OUT: Final = "setup script timed out"
_SHELL_MISSING: Final = "shell binary missing"
_BAD_RECORD: Final = "worktree record is unreadable"
_UNREGISTERED: Final = "worktree is not registered"
_BAD_REVISION: Final = "git revision was not a commit"
_BAD_SLUG: Final = "slug must be a single token"
_BAD_TAB: Final = "tab id must be a single token"
_BAD_BASE: Final = "base must be a git revision"
_BAD_INCLUDE: Final = "include pattern must stay inside the repository"
_ABS_REPO: Final = "repository path must be absolute"
_ABS_DEST: Final = "worktree path must be absolute"
_ABS_GIT: Final = "git binary path must be absolute"
_ABS_SHELL: Final = "shell path must be absolute"
_POSITIVE: Final = "timeout must be positive"
_SETUP_POSITIVE: Final = "setup timeout must be positive"
_DEST_EXISTS: Final = "worktree path already exists"
_PARENT_MISSING: Final = "worktree parent is missing"
_DEST_INSIDE: Final = "worktree must be outside the repository"

CloseReason = Literal["locked", "dirty", "ahead", "ignored", "clean"]
TreeState = Literal["dirty", "ahead", "ignored", "clean"]


@final
class WorktreeError(Exception):
    """Git failed, or a worktree record was not usable."""

    def __init__(self, message: str, *, code: int = 1, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, kw_only=True)
class WorktreeRequest:
    """Where to add a worktree and which branch name to create."""

    repo: Path
    dest: Path
    slug: str
    tab_id: str
    base: str = "HEAD"
    setup_timeout_s: float = _SETUP_TIMEOUT_S
    # sha256 of the `.aconsole/setup.sh` bytes the user approved. Any other script is not run.
    setup_sha256: str | None = None


@dataclass(frozen=True, kw_only=True)
class Created:
    """A worktree that exists and is locked."""

    dest: Path
    branch: str
    base: str
    copied: tuple[str, ...]
    setup_skipped: bool = False


@dataclass(frozen=True, kw_only=True)
class CloseResult:
    """Whether close deleted the worktree."""

    removed: bool
    reason: CloseReason


def _patterns(repo: Path) -> tuple[str, ...]:
    spec = repo / ".worktreeinclude"
    if not spec.is_file():
        return ()
    found: list[str] = []
    for raw in spec.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line == "" or line.startswith("#"):
            continue
        if Path(line).is_absolute() or ".." in Path(line).parts:
            raise ValueError(_BAD_INCLUDE)
        found.append(line)
    return tuple(found)


def _copy_included(repo: Path, dest: Path, patterns: tuple[str, ...]) -> tuple[str, ...]:
    if not patterns:
        return ()
    copied: list[str] = []
    for path in repo.rglob("*"):
        relative = path.relative_to(repo)
        if ".git" in relative.parts or path.is_symlink() or not path.is_file():
            continue
        name = relative.as_posix()
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns):
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.copy2(path, target)
            copied.append(name)
    return tuple(sorted(copied))


def _status_ignored(text: str) -> bool:
    """True when `status --ignored` lists an ignored entry other than the tab lock."""
    for line in text.splitlines():
        if line.startswith("!! ") and line[_STATUS_PATH:].rstrip("/") not in {_LOCK_REL, ".aconsole"}:
            return True
    return False


def _status_dirty(text: str) -> bool:
    for line in text.splitlines():
        if line.startswith("!! "):
            continue
        if len(line) < _MIN_STATUS:
            return True
        path = line[_STATUS_PATH:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path == _LOCK_REL:
            continue
        return True
    return False


def _meta_path(git_dir: Path, dest: Path) -> Path:
    digest = hashlib.sha256(str(dest).encode()).hexdigest()[:_DIGEST_LEN]
    return git_dir / "aconsole-worktrees" / digest


def _read_meta(path: Path) -> tuple[str, str, Path]:
    if not path.is_file():
        raise WorktreeError(_UNREGISTERED)
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) != _META_LINES:
        raise WorktreeError(_BAD_RECORD)
    return lines[0], lines[1], Path(lines[2])


@final
class Worktrees:
    """Create, lock, and remove worktrees with one git binary."""

    def __init__(self, git: Path, *, shell: Path = _SHELL, timeout_s: float = _TIMEOUT_S) -> None:
        _check_absolute(git, _ABS_GIT)
        _check_absolute(shell, _ABS_SHELL)
        if timeout_s <= 0:
            raise ValueError(_POSITIVE)
        self._git = git
        self._shell = shell
        self._timeout_s = timeout_s

    def create(self, request: WorktreeRequest) -> Created:
        """Add a branch and a worktree, copy includes, run setup, then lock."""
        repo, dest = _locations(request)
        if _TOKEN.fullmatch(request.slug) is None:
            raise ValueError(_BAD_SLUG)
        if _TOKEN.fullmatch(request.tab_id) is None:
            raise ValueError(_BAD_TAB)
        if request.base == "" or request.base.startswith("-") or "\n" in request.base:
            raise ValueError(_BAD_BASE)
        if request.setup_timeout_s <= 0:
            raise ValueError(_SETUP_POSITIVE)
        patterns = _patterns(repo)
        branch = f"ac/{request.slug}-{request.tab_id}"
        _ = self._run(("-C", str(repo), "worktree", "add", "-b", branch, str(dest), request.base))
        try:
            copied = _copy_included(repo, dest, patterns)
            base = self._revision(repo, request.base)
            self._write_meta(repo, dest, branch, base)
            ran = self._setup(dest, request.setup_timeout_s, request.setup_sha256)
            self._write_lock(dest, request.tab_id)
        except WorktreeError, OSError:
            self._roll_back(repo, dest, branch)
            raise
        return Created(dest=dest, branch=branch, base=base, copied=copied, setup_skipped=not ran)

    def _roll_back(self, repo: Path, dest: Path, branch: str) -> None:
        """Undo a create that failed after `worktree add`. Nothing in the new tree is the user's yet."""
        for args in (
            ("-C", str(repo), "worktree", "remove", "--force", str(dest)),
            ("-C", str(repo), "branch", "-D", branch),
        ):
            try:
                _ = self._run(args)
            except WorktreeError:
                continue
        with contextlib.suppress(WorktreeError, OSError):
            _meta_path(self._git_dir(repo), dest).unlink(missing_ok=True)

    def locked(self, dest: Path) -> bool:
        """True when the tab lock file is present."""
        return (dest / _LOCK_REL).is_file()

    def release(self, dest: Path) -> None:
        """Remove the tab lock. The worktree stays."""
        (dest / _LOCK_REL).unlink(missing_ok=True)

    def state(self, repo: Path, dest: Path) -> TreeState:
        """Report dirty, ahead, ignored, or clean. A lock does not hide a dirty tree."""
        _check_absolute(repo, _ABS_REPO)
        _check_absolute(dest, _ABS_DEST)
        repo = repo.resolve()
        dest = dest.resolve()
        _branch, base, recorded = _read_meta(_meta_path(self._git_dir(repo), dest))
        if recorded != dest:
            raise WorktreeError(_BAD_RECORD)
        status = self._status(dest)
        if _status_dirty(status):
            return "dirty"
        if self._ahead(dest, base):
            return "ahead"
        if _status_ignored(status):
            return "ignored"
        return "clean"

    def close(self, repo: Path, dest: Path, *, discard_ignored: bool = False) -> CloseResult:
        """Keep a locked, dirty, or ahead tree, and one with ignored files unless `discard_ignored`.

        `git worktree remove` deletes ignored files without asking, so they are checked here first.
        """
        _check_absolute(repo, _ABS_REPO)
        _check_absolute(dest, _ABS_DEST)
        repo = repo.resolve()
        dest = dest.resolve()
        if self.locked(dest):
            return CloseResult(removed=False, reason="locked")
        branch, base, recorded = _read_meta(_meta_path(self._git_dir(repo), dest))
        if recorded != dest:
            raise WorktreeError(_BAD_RECORD)
        status = self._status(dest)
        if _status_dirty(status):
            return CloseResult(removed=False, reason="dirty")
        if self._ahead(dest, base):
            return CloseResult(removed=False, reason="ahead")
        ignored = _status_ignored(status)
        if ignored and not discard_ignored:
            return CloseResult(removed=False, reason="ignored")
        remove = ("-C", str(repo), "worktree", "remove", *(("--force",) if ignored else ()), str(dest))
        _ = self._run(remove)
        # The branch has no commits beyond the base (checked above), so `-D` loses nothing even when
        # the base is not merged into the current HEAD, where `-d` would refuse and leave the branch.
        _ = self._run(("-C", str(repo), "branch", "-D", branch))
        _meta_path(self._git_dir(repo), dest).unlink(missing_ok=True)
        return CloseResult(removed=True, reason="ignored" if ignored else "clean")

    def _status(self, dest: Path) -> str:
        return self._run(("-C", str(dest), "status", "--porcelain", "--untracked-files=all", "--ignored=matching"))

    def prune(self, repo: Path) -> tuple[str, ...]:
        """Drop stale git worktree entries and records whose directory is gone."""
        _check_absolute(repo, _ABS_REPO)
        repo = repo.resolve()
        _ = self._run(("-C", str(repo), "worktree", "prune"))
        folder = self._git_dir(repo) / "aconsole-worktrees"
        if not folder.is_dir():
            return ()
        removed: list[str] = []
        for meta in sorted(folder.iterdir()):
            if not meta.is_file():
                continue
            try:
                branch, _base, dest = _read_meta(meta)
            except WorktreeError:
                meta.unlink()
                continue
            if dest.exists():
                continue
            meta.unlink()
            removed.append(branch)
        return tuple(removed)

    def _revision(self, repo: Path, base: str) -> str:
        sha = self._run(("-C", str(repo), "rev-parse", "--verify", base)).strip()
        if _SHA.fullmatch(sha) is None:
            raise WorktreeError(_BAD_REVISION, detail=sha)
        return sha

    def _git_dir(self, repo: Path) -> Path:
        text = self._run(("-C", str(repo), "rev-parse", "--absolute-git-dir")).strip()
        return Path(text)

    def _write_meta(self, repo: Path, dest: Path, branch: str, base: str) -> None:
        path = _meta_path(self._git_dir(repo), dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(f"{branch}\n{base}\n{dest}\n", encoding="utf-8")

    def _write_lock(self, dest: Path, tab_id: str) -> None:
        lock = dest / _LOCK_REL
        lock.parent.mkdir(parents=True, exist_ok=True)
        _ = lock.write_text(f"{tab_id}\n", encoding="utf-8")

    def _ahead(self, dest: Path, base: str) -> bool:
        text = self._run(("-C", str(dest), "rev-list", "--count", f"{base}..HEAD"))
        count = text.strip()
        if count == "":
            count = _ZERO
        return int(count) > 0

    def _setup(self, dest: Path, timeout_s: float, approved: str | None) -> bool:
        """Run the approved setup script. False when there is a script but it is not the approved one."""
        script = dest / ".aconsole" / "setup.sh"
        if not script.is_file():
            return True
        if approved is None or hashlib.sha256(script.read_bytes()).hexdigest() != approved:
            return False
        try:
            process = subprocess.Popen(  # noqa: S603 -- argv is a list; the shell path is absolute
                [str(self._shell), str(script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=dest,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise WorktreeError(_SHELL_MISSING) from exc
        try:
            _out, err = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            # The script runs in its own session: kill every process it started, not only the shell.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            _ = process.communicate()
            raise WorktreeError(_SETUP_TIMED_OUT) from exc
        if process.returncode != _OK:
            raise WorktreeError(_SETUP_FAILED, code=process.returncode, detail=err)
        return True

    def _run(self, args: tuple[str, ...]) -> str:
        try:
            completed = subprocess.run(  # noqa: S603 -- argv is a list; the git path is absolute
                [str(self._git), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorktreeError(_TIMED_OUT) from exc
        except FileNotFoundError as exc:
            raise WorktreeError(_MISSING) from exc
        if completed.returncode != _OK:
            raise WorktreeError(_FAILED, code=completed.returncode, detail=completed.stderr)
        return completed.stdout


def _locations(request: WorktreeRequest) -> tuple[Path, Path]:
    _check_absolute(request.repo, _ABS_REPO)
    _check_absolute(request.dest, _ABS_DEST)
    repo = request.repo.resolve()
    dest = request.dest.resolve()
    if dest.exists():
        raise ValueError(_DEST_EXISTS)
    if not dest.parent.is_dir():
        raise ValueError(_PARENT_MISSING)
    if dest.is_relative_to(repo):
        raise ValueError(_DEST_INSIDE)
    return repo, dest


def _check_absolute(path: Path, message: str) -> None:
    if not path.is_absolute():
        raise ValueError(message)
