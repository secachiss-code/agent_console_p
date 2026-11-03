"""Changes to a worktree. Hooks and signing stay on: `--no-verify` would hide what the user set up."""

from __future__ import annotations

from pathlib import Path
from typing import Final, final

from aconsole.core.application.ports.git_repo import GitDone
from aconsole.core.infrastructure.git.checkpoints import GitCheckpoints
from aconsole.core.infrastructure.git.runner import GitOutput, GitRunner

_HOOK_TIMEOUT_S: Final = 300.0
_LITERAL: Final = "--literal-pathspecs"
_NUMSTAT_FIELDS: Final = 3


def _done(output: GitOutput) -> GitDone:
    return GitDone(ok=output.code == 0, said=output.said)


@final
class GitMutations:
    """`GitWriter` on one git binary."""

    def __init__(self, runner: GitRunner | None = None, checkpoints: GitCheckpoints | None = None) -> None:
        self._git = GitRunner() if runner is None else runner
        self._checkpoints = GitCheckpoints() if checkpoints is None else checkpoints

    def stage(self, worktree: Path, paths: tuple[str, ...]) -> GitDone:
        return _done(self._git.write(worktree, _LITERAL, "add", "-A", "--", *paths))

    def unstage(self, worktree: Path, paths: tuple[str, ...], *, has_head: bool) -> GitDone:
        if has_head:
            return _done(self._git.write(worktree, _LITERAL, "restore", "--staged", "--", *paths))
        return _done(self._git.write(worktree, _LITERAL, "rm", "-q", "--cached", "--", *paths))

    def commit(self, worktree: Path, message: str) -> GitDone:
        return _done(self._git.write(worktree, "commit", "-F", "-", stdin=message.encode(), timeout_s=_HOOK_TIMEOUT_S))

    def head(self, worktree: Path) -> str | None:
        output = self._git.read(worktree, "rev-parse", "--verify", "--quiet", "HEAD", codes=frozenset({0, 1}))
        return output.text.strip() or None

    def in_source(self, worktree: Path, oid: str, path: str) -> bool:
        output = self._git.read(worktree, "cat-file", "-e", f"{oid}:{path}", codes=frozenset({0, 1, 128}))
        return output.code == 0

    def file_counts(self, worktree: Path, oid: str, path: str) -> tuple[int, int]:
        """Going from the file as it is now to the file in `oid`."""
        raw = self._git.read(
            worktree,
            _LITERAL,
            "diff",
            "--numstat",
            "-R",
            "--no-ext-diff",
            "--no-textconv",
            oid,
            "--",
            path,
        ).text
        fields = raw.split("\t", _NUMSTAT_FIELDS - 1)
        if len(fields) < _NUMSTAT_FIELDS or fields[0] == "-":
            return 0, 0
        return int(fields[0]), int(fields[1])

    def restore_file(self, worktree: Path, oid: str, path: str) -> GitDone:
        if self.in_source(worktree, oid, path):
            return _done(self._git.write(worktree, _LITERAL, "restore", "--source", oid, "--worktree", "--", path))
        target = worktree / path
        if target.is_symlink() or target.is_file():
            target.unlink()
        return GitDone(ok=True, said="")

    def merge(self, worktree: Path, oid: str, mode: str, message: str) -> GitDone:
        if mode == "fast_forward":
            done = _done(self._git.write(worktree, "merge", "--ff-only", "--no-edit", oid, timeout_s=_HOOK_TIMEOUT_S))
        elif mode == "merge":
            done = _done(self._git.write(worktree, "merge", "--no-ff", "-m", message, oid, timeout_s=_HOOK_TIMEOUT_S))
        else:
            done = _done(self._git.write(worktree, "merge", "--squash", oid, timeout_s=_HOOK_TIMEOUT_S))
            if done.ok:
                done = self.commit(worktree, message)
        if not done.ok:
            _ = self._git.write(worktree, "reset", "--merge")
        return done

    def tree_of(self, root: Path, oid: str) -> str | None:
        found = self._git.read(root, "rev-parse", "--verify", "--quiet", f"{oid}^{{tree}}", codes=frozenset({0, 1}))
        return found.text.strip() or None

    def commit_tree(self, root: Path, tree: str, parents: tuple[str, ...], message: str) -> GitDone:
        linked = [part for parent in parents for part in ("-p", parent)]
        output = self._git.write(root, "commit-tree", tree, *linked, "-F", "-", stdin=message.encode())
        return GitDone(ok=output.code == 0, said=output.said, oid=output.text.strip() or None)

    def move_ref(self, root: Path, ref: str, new: str, old: str) -> GitDone:
        return _done(self._git.write(root, "update-ref", ref, new, old))

    def safety_ref(self, worktree: Path, ref: str, label: str) -> None:
        _ = self._checkpoints.capture(worktree, ref, label)
