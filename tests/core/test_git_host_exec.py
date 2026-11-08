"""Repository config must not run a program when the core calls git."""

from __future__ import annotations

import ast
import stat
from pathlib import Path

import pytest

from aconsole.core.application.ports.git_repo import GitCommandError
from aconsole.core.infrastructure.git import runner as runner_module
from aconsole.core.infrastructure.git.checkpoints import GitCheckpoints
from aconsole.core.infrastructure.git.diff import GitDiff
from aconsole.core.infrastructure.git.history import GitHistory
from aconsole.core.infrastructure.git.operations import GitMutations
from aconsole.core.infrastructure.git.runner import GitCall, GitRunner
from tests.core.git_helpers import commit, git, new_repo

_CORE = Path(__file__).parents[2] / "src/aconsole/core"
_REF = "refs/aconsole/checkpoints/tab-1/1"


def test_only_the_runner_spawns_git() -> None:
    offenders = [
        f"{path.name}:{node.lineno}"
        for path in sorted(_CORE.rglob("*.py"))
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


@pytest.mark.parametrize("key", ["clean", "process"])
def test_a_filter_from_the_repository_does_not_run_on_status_or_diff(tmp_path: Path, key: str) -> None:
    repo, marker, script = _dirty_repo(tmp_path, "* filter=evil\n")
    _ = git(repo, "config", f"filter.evil.{key}", str(script))
    _ = git(repo, "config", "filter.evil.required", "true")
    runner = GitRunner()
    assert runner.invoke(repo, ("status", "--porcelain")).code == 0
    assert runner.invoke(repo, ("diff",)).code == 0
    _ = GitHistory(runner).status(repo)
    assert not marker.exists()


def test_a_filter_name_holding_an_equals_sign_is_still_overridden(tmp_path: Path) -> None:
    repo, marker, script = _dirty_repo(tmp_path, "* filter=a=b\n")
    _ = git(repo, "config", "filter.a=b.clean", str(script))
    assert GitRunner().invoke(repo, ("status", "--porcelain")).code == 0
    assert not marker.exists()


def test_a_smudge_filter_does_not_run_when_a_file_is_restored(tmp_path: Path) -> None:
    repo, marker, script = _dirty_repo(tmp_path, "")
    _ = git(repo, "config", "filter.evil.smudge", str(script))
    (repo / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    assert GitRunner().invoke(repo, ("checkout", "--", "f.txt"), GitCall(write=True)).code == 0
    assert not marker.exists()


@pytest.mark.parametrize("command", [("show", "HEAD"), ("log", "-p", "-1")])
def test_textconv_does_not_run_outside_git_diff(tmp_path: Path, command: tuple[str, ...]) -> None:
    repo, marker, script = _dirty_repo(tmp_path, "* diff=evil\n")
    _ = git(repo, "config", "diff.evil.textconv", str(script))
    assert GitRunner().invoke(repo, command).code == 0
    assert not marker.exists()


def test_a_merge_driver_does_not_run_and_the_conflict_stays_visible(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "f.txt", "base\n", "init")
    (repo / ".gitattributes").write_text("* merge=evil\n", encoding="utf-8")
    _ = commit(repo, ".gitattributes", "* merge=evil\n", "attrs")
    marker = tmp_path / "ran"
    script = tmp_path / "evil.sh"
    _script(script, marker)
    _ = git(repo, "config", "merge.evil.driver", str(script))
    _ = git(repo, "checkout", "-q", "-b", "other")
    _ = commit(repo, "f.txt", "other\n", "other")
    _ = git(repo, "checkout", "-q", "-")
    _ = commit(repo, "f.txt", "ours\n", "ours")
    merged = GitRunner().invoke(repo, ("merge", "other"), GitCall(write=True))
    assert not marker.exists()
    assert merged.code != 0


def test_signing_and_its_program_from_the_repository_do_not_run(tmp_path: Path) -> None:
    repo, marker, script = _dirty_repo(tmp_path, "")
    _ = git(repo, "config", "gpg.program", str(script))
    _ = git(repo, "config", "commit.gpgSign", "true")
    assert GitRunner().invoke(repo, ("commit", "-qam", "y"), GitCall(write=True)).code == 0
    assert not marker.exists()


@pytest.mark.parametrize("command", [("show", "HEAD"), ("log", "-p", "-1"), ("diff", "HEAD~0")])
def test_a_diff_driver_command_does_not_run(tmp_path: Path, command: tuple[str, ...]) -> None:
    repo, marker, script = _dirty_repo(tmp_path, "* diff=evil\n")
    _ = git(repo, "add", ".gitattributes", "f.txt")
    _ = git(repo, "commit", "-qm", "y")
    _ = git(repo, "config", "diff.evil.command", str(script))
    assert GitRunner().invoke(repo, command).code == 0
    assert not marker.exists()


def test_showing_a_signature_does_not_run_a_verifier_from_the_repository(tmp_path: Path) -> None:
    repo, marker, script = _dirty_repo(tmp_path, "")
    _ = git(repo, "config", "gpg.format", "ssh")
    _ = git(repo, "config", "gpg.ssh.program", str(script))
    (repo / "signers").write_text("", encoding="utf-8")
    _ = git(repo, "config", "gpg.ssh.allowedSignersFile", str(repo / "signers"))
    _ = git(repo, "config", "log.showSignature", "true")
    tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
    who = "A <a@example.com> 1700000000 +0000"
    signed = (
        f"tree {tree}\nauthor {who}\ncommitter {who}\n"
        "gpgsig -----BEGIN SSH SIGNATURE-----\n x\n -----END SSH SIGNATURE-----\n\nsigned\n"
    )
    (repo / "object").write_text(signed, encoding="utf-8")
    head = git(repo, "hash-object", "-t", "commit", "-w", "object").strip()
    _ = git(repo, "update-ref", "HEAD", head)
    assert GitRunner().invoke(repo, ("log", "-1")).code == 0
    assert not marker.exists()


@pytest.mark.parametrize("key", ["cmd", "command"])
def test_a_trailer_command_from_the_repository_does_not_run(tmp_path: Path, key: str) -> None:
    repo, marker, script = _dirty_repo(tmp_path, "")
    _ = git(repo, "config", f"trailer.evil.{key}", str(script))
    asked = ("--trailer", "Node: X", "--trailer", "evil: y")
    said = GitRunner().read(repo, "interpret-trailers", *asked, stdin=b"subject\n").text
    assert not marker.exists()
    assert "Node: X" in said


def test_a_folder_that_is_not_there_is_an_exit_code_not_an_exception(tmp_path: Path) -> None:
    assert GitRunner().invoke(tmp_path / "gone", ("status",)).code != 0


def _dirty_repo(tmp_path: Path, attributes: str) -> tuple[Path, Path, Path]:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "f.txt", "one\n", "init")
    (repo / "f.txt").write_text("two\n", encoding="utf-8")
    if attributes:
        (repo / ".gitattributes").write_text(attributes, encoding="utf-8")
    marker = tmp_path / "ran"
    script = tmp_path / "evil.sh"
    _script(script, marker)
    return repo, marker, script


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


@pytest.mark.parametrize(("command", "write"), [
    (("status", "--porcelain"), False),
    (("checkout", "--", "f.txt"), True),
    (("commit", "--dry-run"), False),
    (("-c", "alias.go=!true", "go"), True),
])
def test_hooks_permission_is_limited_to_explicit_commit_or_merge_writes(
    tmp_path: Path, command: tuple[str, ...], *, write: bool,
) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "f.txt", "one\n", "init")
    with pytest.raises(GitCommandError, match="hooks require an explicit commit or merge write"):
        _ = GitRunner().invoke(repo, command, GitCall(write=write, hooks=True))


@pytest.mark.parametrize("source", ["include", "worktree"])
def test_background_reads_override_indirect_repository_config(tmp_path: Path, source: str) -> None:
    repo, marker, script = _dirty_repo(tmp_path, "* diff=evil\n")
    hooks = tmp_path / "foreign-hooks"
    _script(hooks / "post-index-change", marker)
    config = tmp_path / "included.config"
    if source == "include":
        _ = git(repo, "config", "include.path", str(config))
    else:
        _ = git(repo, "config", "extensions.worktreeConfig", "true")
        config = repo / ".git" / "config.worktree"
    config.write_text(
        f'[core]\n fsmonitor = {script}\n hooksPath = {hooks}\n'
        f'[diff]\n external = {script}\n[diff "evil"]\n textconv = {script}\n',
        encoding="utf-8",
    )
    _ = GitHistory(GitRunner()).status(repo)
    _ = GitDiff().summary(repo, None)
    _ = GitCheckpoints().capture(repo, _REF, "safe")
    assert not marker.exists()


def test_process_environment_does_not_enable_external_diff_or_ssh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, marker, script = _dirty_repo(tmp_path, "")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(script))
    monkeypatch.setenv("GIT_SSH_COMMAND", str(script))
    runner = GitRunner()
    _ = runner.read(repo, "diff")
    _ = runner.read(repo, "ls-remote", "git@127.0.0.1:none.git", codes=frozenset(range(256)))
    assert not marker.exists()


@pytest.mark.parametrize("command", [("show", "HEAD"), ("log", "-p", "-1")])
def test_patch_reads_do_not_need_even_a_neutral_textconv_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: tuple[str, ...],
) -> None:
    repo, marker, script = _dirty_repo(tmp_path, "* diff=evil\n")
    _ = git(repo, "config", "diff.evil.textconv", str(script))
    # Detect invocation of the replacement as well as the repository's driver.
    monkeypatch.setattr(runner_module, "_CAT", str(script))
    result = GitRunner().invoke(repo, command)
    assert result.code == 0
    assert not marker.exists(), "patch reads should use Git's built-in diff, without a textconv helper"
    assert b"one" in result.stdout
