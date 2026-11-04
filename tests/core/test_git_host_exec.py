"""Repository config must not run a program when the core calls git."""

from __future__ import annotations

import ast
import stat
from pathlib import Path

from aconsole.core.infrastructure.git.checkpoints import GitCheckpoints
from aconsole.core.infrastructure.git.diff import GitDiff
from aconsole.core.infrastructure.git.history import GitHistory
from aconsole.core.infrastructure.git.operations import GitMutations
from aconsole.core.infrastructure.git.runner import GitRunner
from tests.core.git_helpers import commit, git, new_repo

_PACKAGE = Path(__file__).parents[2] / "src/aconsole/core/infrastructure/git"
_REF = "refs/aconsole/checkpoints/tab-1/1"


def test_only_the_runner_spawns_git() -> None:
    offenders = [
        f"{path.name}:{node.lineno}"
        for path in sorted(_PACKAGE.glob("*.py"))
        if path.name != "runner.py"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and _is_subprocess(node.func) and _mentions_git(node)
    ]
    assert offenders == []


def test_status_diff_and_snapshot_do_not_run_fsmonitor_or_hooks_path(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "f.txt", "hi\n", "init")
    marker = tmp_path / "ran"
    script = tmp_path / "evil.sh"
    _script(script, marker)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    _script(hooks / "pre-commit", marker)
    _ = git(repo, "config", "core.fsmonitor", str(script))
    _ = git(repo, "config", "core.hooksPath", str(hooks))
    _ = GitHistory(GitRunner()).status(repo)
    _ = GitDiff().summary(repo, None)
    _ = GitCheckpoints().capture(repo, _REF, "snap")
    assert not marker.exists()


def test_commit_runs_the_repository_hooks_and_not_hooks_path(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "f.txt", "hi\n", "init")
    own = tmp_path / "own"
    foreign = tmp_path / "foreign"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    _script(hooks / "pre-commit", foreign)
    _script(repo / ".git" / "hooks" / "pre-commit", own)
    _ = git(repo, "config", "core.hooksPath", str(hooks))
    (repo / "g.txt").write_text("more\n", encoding="utf-8")
    done = GitMutations().stage(repo, ("g.txt",))
    assert done.ok
    done = GitMutations().commit(repo, "second\n")
    assert done.ok
    assert own.read_text(encoding="utf-8") == "ran\n"
    assert not foreign.exists()


def test_external_diff_and_textconv_do_not_run(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "f.txt", "hi\n", "init")
    marker = tmp_path / "ran"
    script = tmp_path / "driver.sh"
    _script(script, marker)
    _ = git(repo, "config", "diff.external", str(script))
    _ = git(repo, "config", "diff.txt.textconv", str(script))
    _ = (repo / ".gitattributes").write_text("*.txt diff=txt\n", encoding="utf-8")
    _ = (repo / "f.txt").write_text("changed\n", encoding="utf-8")
    _ = GitDiff().summary(repo, None)
    _ = GitCheckpoints().capture(repo, _REF, "snap")
    assert not marker.exists()


def test_ssh_command_from_the_repository_does_not_run(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "f.txt", "hi\n", "init")
    marker = tmp_path / "ran"
    script = tmp_path / "ssh.sh"
    _script(script, marker)
    _ = git(repo, "config", "core.sshCommand", str(script))
    _ = GitRunner().read(repo, "ls-remote", "git@127.0.0.1:none.git", codes=frozenset(range(256)))
    assert not marker.exists()


def _script(path: Path, marker: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(f"#!/bin/sh\necho ran >> {marker}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _is_subprocess(func: ast.expr) -> bool:
    return isinstance(func, ast.Attribute) and func.attr in {"run", "Popen", "call", "check_output"}


def _mentions_git(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and child.value == "git":
            return True
        if isinstance(child, ast.Name) and child.id in {"_git", "_GIT"}:
            return True
        if isinstance(child, ast.Attribute) and child.attr == "_git":
            return True
    return False
