"""Executable Git settings stay disabled across config and environment precedence."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from aconsole.core.infrastructure.git.runner import GitCall, GitRunner
from tests.core.git_helpers import commit, git, new_repo
from tests.core.test_git_host_exec import _script

_CORE = Path(__file__).parents[2] / "src/aconsole/core"


def _repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "f.txt", "one\n", "init")
    _ = commit(repo, "f.txt", "two\n", "second")
    marker = tmp_path / "ran"
    script = tmp_path / "evil.sh"
    _script(script, marker)
    return repo, marker, script


@pytest.mark.parametrize("key", ["core.askPass", "core.editor", "sequence.editor"])
def test_repository_askpass_and_editors_do_not_run(tmp_path: Path, key: str) -> None:
    repo, marker, script = _repo(tmp_path)
    _ = git(repo, "config", key, str(script))
    _ = _exercise(repo, key)
    assert not marker.exists()


@pytest.mark.parametrize("name", ["GIT_ASKPASS", "SSH_ASKPASS", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR"])
@pytest.mark.parametrize("source", ["process", "call"])
def test_environment_cannot_restore_askpass_or_an_editor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    source: str,
) -> None:
    repo, marker, script = _repo(tmp_path)
    if source == "process":
        monkeypatch.setenv(name, str(script))
        extra = None
    else:
        extra = {name: str(script)}
    key = "core.askPass" if "ASKPASS" in name else "sequence.editor" if "SEQUENCE" in name else "core.editor"
    _ = _exercise(repo, key, extra)
    assert not marker.exists()


def test_safety_programs_are_not_resolved_in_an_untrusted_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, marker, _script_path = _repo(tmp_path)
    binaries = tmp_path / "bin"
    _script(binaries / "true", marker)
    monkeypatch.setenv("PATH", f"{binaries}:{os.environ.get('PATH', '')}")
    _ = _exercise(repo, "core.editor")
    assert not marker.exists()


def _exercise(repo: Path, key: str, env: dict[str, str] | None = None) -> int:
    if key == "core.askPass":
        args = ("credential", "fill")
        payload = b"url=https://example.invalid\n\n"
    else:
        args = ("rebase", "-i", "--root") if key == "sequence.editor" else ("commit", "--allow-empty")
        payload = None
    return GitRunner().invoke(repo, args, GitCall(env=env, stdin=payload, write=True)).code


def test_core_has_no_direct_git_spawn_including_async_and_other_runner_modules() -> None:
    allowed = _CORE / "infrastructure/git/runner.py"
    spawns = {"run", "Popen", "call", "check_call", "check_output", "create_subprocess_exec", "create_subprocess_shell"}
    offenders = []
    for path in sorted(_CORE.rglob("*.py")):
        if path == allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
                or node.func.attr not in spawns
            ):
                continue
            for child in ast.walk(node):
                literal = (
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and child.value in {"git", "/usr/bin/git"}
                )
                named = isinstance(child, ast.Name) and child.id in {"_git", "_GIT"}
                attribute = isinstance(child, ast.Attribute) and child.attr == "_git"
                if literal or named or attribute:
                    offenders.append(f"{path.relative_to(_CORE)}:{node.lineno}")
                    break
    assert offenders == []
