"""Changes to a worktree.

Commit and merge are the user operations that run hooks. They do not pass `--no-verify`. The hooks that run are
the scripts in the repository's own `hooks` directory (`$GIT_COMMON_DIR/hooks`), including a linked worktree.
`core.hooksPath` from the repository config is not followed: an agent can write that key, and a commit would
otherwise execute it on the host. A refusing hook is the answer, with its text. Signing stays on.

Status, diff, snapshots, and every other git call point `core.hooksPath` at `/dev/null` and force
`core.fsmonitor=false`, an empty `diff.external`, and `core.sshCommand` to the host `true`. Diffs also pass
`--no-ext-diff` and `--no-textconv`. A merge that has no checkout still runs `pre-merge-commit` (or `pre-commit`
when squashing) and `commit-msg` from that same hooks directory, against a throwaway index.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Final, final

from aconsole.core.application.ports.git_repo import GitCommandError, GitDone
from aconsole.core.infrastructure.git.checkpoints import GitCheckpoints, remove_worktree_file
from aconsole.core.infrastructure.git.runner import GitCall, GitOutput, GitRunner
from aconsole.core.infrastructure.git.worktrees import forget_record, has_ignored_files

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
        return _done(
            self._git.write(
                worktree,
                "commit",
                "-F",
                "-",
                stdin=message.encode(),
                timeout_s=_HOOK_TIMEOUT_S,
                hooks=True,
            )
        )

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
        try:
            remove_worktree_file(worktree, path)
        except OSError as exc:
            message = "could not safely remove worktree file"
            raise GitCommandError(message, detail=str(exc)) from exc
        return GitDone(ok=True, said="")

    def merge(self, worktree: Path, oid: str, mode: str, message: str) -> GitDone:
        if mode == "fast_forward":
            done = _done(
                self._git.write(
                    worktree,
                    "merge",
                    "--ff-only",
                    "--no-edit",
                    oid,
                    timeout_s=_HOOK_TIMEOUT_S,
                    hooks=True,
                )
            )
        elif mode == "merge":
            done = _done(
                self._git.write(
                    worktree,
                    "merge",
                    "--no-ff",
                    "-m",
                    message,
                    oid,
                    timeout_s=_HOOK_TIMEOUT_S,
                    hooks=True,
                )
            )
        else:
            done = _done(self._git.write(worktree, "merge", "--squash", oid, timeout_s=_HOOK_TIMEOUT_S, hooks=True))
            if done.ok:
                done = self.commit(worktree, message)
        if not done.ok:
            _ = self._git.write(worktree, "reset", "--merge")
        return done

    def with_trailers(self, worktree: Path, message: str, trailers: tuple[tuple[str, str], ...]) -> str:
        if not trailers:
            return message
        named = [part for key, value in trailers for part in ("--trailer", f"{key}: {value}")]
        # Without its last newline a one-line message gets the trailers glued to the subject, where nobody finds them.
        text = message if message.endswith("\n") else message + "\n"
        return self._git.read(worktree, "interpret-trailers", *named, stdin=text.encode()).text

    def merge_hooks(self, root: Path, tree: str, message: str, *, squash: bool) -> GitDone:
        """A throwaway index holds the merged tree, as a hook looking at it would expect. The checked-out files in
        `root` belong to another branch, so a hook that reads files instead of the index sees those."""
        before = self._hook(root, "pre-commit" if squash else "pre-merge-commit")
        after = self._hook(root, "commit-msg")
        if before is None and after is None:
            return GitDone(ok=True, said="", text=message)
        with tempfile.TemporaryDirectory(prefix="aconsole-hook-") as folder:
            index = Path(folder) / "index"
            environment = {**os.environ, "GIT_INDEX_FILE": str(index)}
            for name in ("GIT_DIR", "GIT_WORK_TREE"):
                _ = environment.pop(name, None)
            loaded = self._load_tree(root, tree, index)
            if not loaded.ok:
                return loaded
            if before is not None:
                done = self._run_hook((str(before),), root, environment)
                if not done.ok:
                    return done
            text_file = Path(folder) / "COMMIT_EDITMSG"
            _ = text_file.write_text(message, encoding="utf-8")
            if after is not None:
                done = self._run_hook((str(after), str(text_file)), root, environment)
                if not done.ok:
                    return done
            return GitDone(ok=True, said="", text=text_file.read_text(encoding="utf-8"))

    def _hook(self, root: Path, name: str) -> Path | None:
        """The repository's own hook, not the path `core.hooksPath` names."""
        common = self._git.read(root, "rev-parse", "--path-format=absolute", "--git-common-dir").text.strip()
        path = Path(common) / "hooks" / name
        return path if path.is_file() and os.access(path, os.X_OK) else None

    def _load_tree(self, root: Path, tree: str, index: Path) -> GitDone:
        try:
            output = self._git.invoke(
                root,
                ("read-tree", tree),
                GitCall(env={"GIT_INDEX_FILE": str(index)}, drop=("GIT_DIR", "GIT_WORK_TREE"), write=True),
            )
        except GitCommandError as exc:
            return GitDone(ok=False, said=str(exc))
        return GitDone(ok=output.code == 0, said=output.said)

    def _run_hook(self, argv: tuple[str, ...], root: Path, environment: dict[str, str]) -> GitDone:
        try:
            done = subprocess.run(  # noqa: S603 -- the repository's own hook, as git itself would run it
                argv, cwd=root, env=environment, capture_output=True, text=True, check=False, timeout=_HOOK_TIMEOUT_S
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return GitDone(ok=False, said=str(exc))
        return GitDone(ok=done.returncode == 0, said=(done.stdout + done.stderr).strip())

    def tree_of(self, root: Path, oid: str) -> str | None:
        found = self._git.read(root, "rev-parse", "--verify", "--quiet", f"{oid}^{{tree}}", codes=frozenset({0, 1}))
        return found.text.strip() or None

    def commit_tree(self, root: Path, tree: str, parents: tuple[str, ...], message: str) -> GitDone:
        linked = [part for parent in parents for part in ("-p", parent)]
        output = self._git.write(root, "commit-tree", tree, *linked, "-F", "-", stdin=message.encode())
        return GitDone(ok=output.code == 0, said=output.said, oid=output.text.strip() or None)

    def move_ref(self, root: Path, ref: str, new: str, old: str) -> GitDone:
        return _done(self._git.write(root, "update-ref", ref, new, old))

    def rebase(self, worktree: Path, onto: str) -> GitDone:
        return _done(self._git.write(worktree, "rebase", "--no-autostash", onto, timeout_s=_HOOK_TIMEOUT_S))

    def cherry_pick(self, worktree: Path, oid: str) -> GitDone:
        return _done(self._git.write(worktree, "cherry-pick", oid, timeout_s=_HOOK_TIMEOUT_S))

    def step(self, worktree: Path, kind: str, action: str) -> GitDone:
        return _done(self._git.write(worktree, kind, f"--{action}", timeout_s=_HOOK_TIMEOUT_S))

    def has_ignored(self, worktree: Path) -> bool:
        return has_ignored_files(
            self._git.read(worktree, "status", "--porcelain", "--untracked-files=all", "--ignored=matching").text
        )

    def remove_worktree(self, root: Path, worktree: Path, *, discard_ignored: bool) -> GitDone:
        forced = ("--force",) if discard_ignored else ()
        done = _done(self._git.write(root, "worktree", "remove", *forced, str(worktree)))
        if done.ok:
            common = self._git.read(root, "rev-parse", "--path-format=absolute", "--git-common-dir").text.strip()
            forget_record(Path(common), worktree)
        return done

    def delete_branch(self, root: Path, branch: str) -> GitDone:
        return _done(self._git.write(root, "branch", "-D", "--", branch))

    def keep_ref(self, root: Path, ref: str, oid: str) -> GitDone:
        return _done(self._git.write(root, "update-ref", ref, oid))

    def safety_ref(self, worktree: Path, ref: str, label: str) -> None:
        _ = self._checkpoints.capture(worktree, ref, label)
