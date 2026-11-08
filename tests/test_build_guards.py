"""Build and contract gates reject omissions after commits, not just in dirty worktrees."""

from __future__ import annotations

import json
lazy from pathlib import Path

import pytest
from tools.check_contracts import check_breaking
from tools.check_ownership import check as check_ownership
from tools.check_ownership import main as ownership_main

from tests.core.git_helpers import commit, git, new_repo
from tests.test_packaging import _builder


@pytest.mark.parametrize("relative", ["src/aconsole/new.py", "migrations/versions/new.py"])
def test_package_refuses_to_drop_untracked_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "src/aconsole/__init__.py", "", "base")
    extra = repo / relative
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("# new module\n", encoding="utf-8")
    builder = _builder()
    monkeypatch.setattr(builder, "ROOT", repo)
    with pytest.raises(SystemExit, match="untracked application files") as error:
        builder._copy_app(tmp_path / "site")
    assert relative in str(error.value)
    assert not (tmp_path / "site").exists()


def test_package_keeps_tracked_resource_names_with_spaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "src/aconsole/assets/display name.svg", "<svg/>\n", "base")
    builder = _builder()
    monkeypatch.setattr(builder, "ROOT", repo)
    builder._copy_app(tmp_path / "site")
    assert (tmp_path / "site/aconsole/assets/display name.svg").read_text() == "<svg/>\n"


def _contract_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = new_repo(tmp_path / "repo")
    relative = "contracts/schema/ipc/Sample.schema.json"
    old = {"$defs": {"Sample": {"type": "object", "required": ["keep"], "properties": {"keep": {"type": "string"}}}}}
    base = commit(repo, relative, json.dumps(old), "old API")
    new = {"$defs": {"Sample": {"type": "object", "required": [], "properties": {}}}}
    _ = commit(repo, relative, json.dumps(new), "committed breaking API")
    return repo, base


def test_committed_breaking_schema_is_compared_with_previous_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _base = _contract_repo(tmp_path)
    monkeypatch.delenv("ACONSOLE_CONTRACT_BASE", raising=False)
    assert any("removed required field" in problem for problem in check_breaking(repo))


def test_ci_contract_base_is_explicit_and_missing_base_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = _contract_repo(tmp_path)
    monkeypatch.setenv("ACONSOLE_CONTRACT_BASE", base)
    assert any("removed required field" in problem for problem in check_breaking(repo))
    monkeypatch.setenv("ACONSOLE_CONTRACT_BASE", "missing-baseline")
    assert any("baseline unavailable" in problem for problem in check_breaking(repo))


def test_ownership_refusal_propagates_when_main_is_missing(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "README.md", "base\n", "base")
    _ = git(repo, "branch", "-m", "other")
    assert check_ownership(repo, "agent-codex")
    assert ownership_main([str(repo), "agent-codex"]) == 1


@pytest.mark.parametrize(
    ("relative", "ui"),
    [("src/aconsole/ui/views/spaced name.py", True), ("src/aconsole/core/domain/sample.py", False)],
)
def test_ownership_checks_complete_paths_on_real_branches(tmp_path: Path, relative: str, *, ui: bool) -> None:
    repo = new_repo(tmp_path / "repo", branch="main")
    _ = commit(repo, "README.md", "base\n", "base")
    _ = git(repo, "checkout", "-b", "feature")
    _ = commit(repo, relative, "# implementation\n", "feature")
    assert bool(check_ownership(repo, "agent-codex")) is not ui
    assert bool(check_ownership(repo, "agent-claude")) is ui
