"""Read-only product audit. Exit zero means probes ran, not that every observation is safe."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.app import CloseTabParams, CloseTabResult, ProjectAddParams, ProjectView, TabView
from aconsole.contracts.common import WorkspaceMode
from aconsole.contracts.work_sessions import WorkSessionCreateParams, WorkSessionParams, WorkSessionView
from aconsole.core.application.services.checkpoints import CheckpointRestore, CheckpointService
from aconsole.core.domain.tab_status import TabStatus
from aconsole.core.application.ports.git_repo import GitCommandError
from aconsole.core.infrastructure.git.checkpoints import GitCheckpoints
from aconsole.core.infrastructure.git.history import GitHistory
from aconsole.core.infrastructure.git.operations import GitMutations
from aconsole.core.infrastructure.git.runner import GitRunner
from aconsole.core.infrastructure.git.worktrees import WorktreeRequest, Worktrees
from aconsole.core.infrastructure.tabs import HostRepos
from tests.core.git_helpers import commit, git, new_repo
from tests.core.test_wire import _connect, _daemon, _open, _stop

OBSERVATIONS = []


def record(name: str, **observed: object) -> None:
    OBSERVATIONS.append({"scenario": name, **observed})


@pytest.fixture(scope="session", autouse=True)
def save_report():
    yield
    (Path(__file__).parent / "observations.json").write_text(
        json.dumps({"audited_commit": "0b7717f", "observations": OBSERVATIONS}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FAKE_HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    with tempfile.TemporaryDirectory(prefix="ac-audit-") as directory:
        monkeypatch.setenv("XDG_RUNTIME_DIR", directory)
        yield


def test_rollback_keeps_safety_for_untracked_and_broken_symlink(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "f", "base\n", "seed")
    store = GitCheckpoints()
    service = CheckpointService(store)
    now = datetime(2026, 11, 6, tzinfo=UTC)
    point = service.on_user_prompt(repo, "audit", "target", now)
    (repo / "new").write_bytes(b"recover exact bytes\x00\xff")
    (repo / "broken").symlink_to("nonexistent-target")
    preview = service.preview("audit", point.id, now=now)
    result = service.restore(CheckpointRestore(
        repo=repo, tab_id="audit", checkpoint_id=point.id, confirm=preview.confirm_token,
        status=TabStatus.IDLE, at=now,
    ))
    safety = next(row for row in service.list("audit") if row.id == result.safety_checkpoint)
    assert not (repo / "new").exists()
    assert not (repo / "broken").is_symlink()
    store.materialize(repo, safety.ref)
    assert (repo / "new").read_bytes() == b"recover exact bytes\x00\xff"
    assert (repo / "broken").is_symlink()
    assert os.readlink(repo / "broken") == "nonexistent-target"
    record("untracked and dangling symlink rollback", safety_recovers_bytes=True, safety_recovers_link=True)


@pytest.mark.parametrize("adapter", ["worktrees", "mutations"])
@pytest.mark.parametrize("discard", [False, True])
def test_observe_write_between_preflight_and_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter: str, discard: bool,
) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, ".gitignore", "cache/\n", "seed")
    trees = Worktrees(Path("/usr/bin/git"))
    dest = tmp_path / "worker"
    _ = trees.create(WorktreeRequest(repo=repo, dest=dest, slug="audit", tab_id="w"))
    trees.release(dest)
    if discard:
        (dest / "cache").mkdir()
        (dest / "cache/approved-discard").write_text("ignored data", encoding="utf-8")
    writer = GitMutations()
    runner = trees._runner if adapter == "worktrees" else writer._git
    execute = runner._execute
    inserted = []

    def before_remove(argv, stdin, env, limit):
        if "worktree" in argv and "remove" in argv:
            (dest / "late-untracked").write_text("new data after preflight", encoding="utf-8")
            inserted.append(True)
        return execute(argv, stdin, env, limit)

    monkeypatch.setattr(runner, "_execute", before_remove)
    status = GitHistory().status(dest)
    assert status.untracked == status.unstaged == status.staged == 0
    if adapter == "worktrees":
        removed = trees.close(repo, dest, discard_ignored=discard).removed
    else:
        removed = writer.remove_worktree(repo, dest, discard_ignored=discard).ok
    survived = (dest / "late-untracked").exists()
    assert inserted
    if not discard:
        assert not removed
        assert survived
    record("write after preflight", adapter=adapter, discard_ignored=discard,
           removed=removed, new_untracked_survives=survived, unsafe=removed and not survived)


@pytest.mark.parametrize("hidden", [False, True])
def test_observe_dirty_submodule_with_ignored_discard(tmp_path: Path, hidden: bool) -> None:
    module = new_repo(tmp_path / "module")
    _ = commit(module, "private", "tracked baseline\n", "seed module")
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, ".gitignore", "cache/\n", "seed")
    _ = git(repo, "-c", "protocol.file.allow=always", "submodule", "add", str(module), "sub")
    _ = git(repo, "commit", "-qam", "add module")
    trees = Worktrees(Path("/usr/bin/git"))
    dest = tmp_path / "worker"
    _ = trees.create(WorktreeRequest(repo=repo, dest=dest, slug="audit", tab_id="w"))
    trees.release(dest)
    _ = git(dest, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
    (dest / "sub/private").write_text("unsaved submodule work\n", encoding="utf-8")
    (dest / "cache").mkdir()
    (dest / "cache/approved-discard").write_text("ignored", encoding="utf-8")
    if hidden:
        _ = git(repo, "config", "submodule.sub.ignore", "all")
    status = GitHistory().status(dest)
    outcome = trees.close(repo, dest, discard_ignored=True)
    survived = (dest / "sub/private").exists()
    if not hidden:
        assert not outcome.removed
        assert survived
    record("dirty submodule", config_hides_dirty=hidden, reported_unstaged=status.unstaged,
           removed=outcome.removed, unsaved_file_survives=survived, unsafe=outcome.removed and not survived)


def test_bare_repository_is_refused_without_mutation(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    head = commit(repo, "f", "base\n", "seed")
    bare = tmp_path / "bare.git"
    _ = git(repo, "clone", "--bare", str(repo), str(bare))
    found = HostRepos().toplevel(bare)
    assert found is None
    assert git(bare, "rev-parse", "HEAD") == head
    record("bare repository", refused=True, head_preserved=True)


@pytest.mark.parametrize("mode", [WorkspaceMode.MAIN_CHECKOUT, WorkspaceMode.EXISTING_WORKTREE])
def test_closing_and_archiving_imported_session_keeps_its_files(tmp_path: Path, mode: WorkspaceMode) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "f", "base\n", "seed")
        dest = repo
        if mode is WorkspaceMode.EXISTING_WORKTREE:
            dest = tmp_path / "external"
            _ = git(repo, "worktree", "add", "-b", "user-branch", str(dest))
        daemon = _daemon(tmp_path, serve_ingest=False)
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            session = await client.call("workSession/create", WorkSessionCreateParams(
                project_id=project.id, title="Imported", workspace=mode,
                existing_worktree=str(dest) if mode is WorkspaceMode.EXISTING_WORKTREE else None,
            ), WorkSessionView)
            tab = await client.call("tab/open", msgspec.structs.replace(
                _open(project.id), work_session_id=session.id,
            ), TabView)
            (dest / "untracked").write_text("keep local data", encoding="utf-8")
            closed = await client.call("tab/close", CloseTabParams(tab_id=tab.id), CloseTabResult)
            archived = await client.call("workSession/close", WorkSessionParams(
                project_id=project.id, work_session_id=session.id,
            ), WorkSessionView)
            assert closed.closed
            assert archived.status == "archived"
            assert (dest / "untracked").read_text(encoding="utf-8") == "keep local data"
            assert git(dest, "rev-parse", "HEAD")
            record("imported session close/archive", mode=mode.value, local_data_preserved=True)
        finally:
            await _stop(daemon, serving, client)
    asyncio.run(scenario())



def test_literal_options_and_foreign_index_lock(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "f", "base\n", "seed")
    writer = GitMutations()
    (repo / "--help").write_text("literal file", encoding="utf-8")
    assert writer.stage(repo, ("--help",)).ok
    assert "--help" in git(repo, "diff", "--cached", "--name-only")
    with pytest.raises(ValueError):
        _ = Worktrees(Path("/usr/bin/git")).create(WorktreeRequest(
            repo=repo, dest=tmp_path / "bad-option", slug="audit", tab_id="bad", base="--help",
        ))
    before = (repo / ".git/index").read_bytes()
    lock = repo / ".git/index.lock"
    lock.write_text("other owner", encoding="utf-8")
    (repo / "f").write_text("working change", encoding="utf-8")
    try:
        refused = not writer.stage(repo, ("f",)).ok
    except GitCommandError:
        refused = True
    assert refused
    ref = "refs/aconsole/checkpoints/audit/1"
    _ = GitCheckpoints().capture(repo, ref, "private index")
    assert (repo / ".git/index").read_bytes() == before
    assert lock.read_text(encoding="utf-8") == "other owner"
    assert git(repo, "show", f"{ref}:f") == "working change"
    record("literal options and foreign index.lock", filename_is_literal=True, option_base_refused=True,
           real_index_and_lock_preserved=True, private_snapshot_succeeds=True)


def test_conflict_and_linked_worktree_keep_unrelated_changes(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "f", "base\n", "seed")
    _ = git(repo, "checkout", "-b", "topic")
    topic = commit(repo, "f", "topic\n", "topic")
    _ = git(repo, "checkout", "trunk")
    _ = commit(repo, "f", "main\n", "main")
    head = commit(repo, "other", "clean\n", "other")
    (repo / "other").write_text("unrelated local change\n", encoding="utf-8")
    done = GitMutations().merge(repo, topic, "merge", "conflict probe")
    assert not done.ok
    assert git(repo, "rev-parse", "HEAD") == head
    assert (repo / "f").read_text(encoding="utf-8") == "main\n"
    assert (repo / "other").read_text(encoding="utf-8") == "unrelated local change\n"
    dest = tmp_path / "linked"
    trees = Worktrees(Path("/usr/bin/git"))
    _ = trees.create(WorktreeRequest(repo=repo, dest=dest, slug="audit", tab_id="linked"))
    assert (dest / ".git").is_file()
    ref = "refs/aconsole/checkpoints/audit/2"
    store = GitCheckpoints()
    _ = store.capture(dest, ref, "linked root")
    (dest / "f").write_text("local linked change", encoding="utf-8")
    store.materialize(dest, ref)
    assert (dest / "f").read_text(encoding="utf-8") == "main\n"
    assert (repo / "other").read_text(encoding="utf-8") == "unrelated local change\n"
    trees.release(dest)
    record("merge conflict and linked worktree", conflict_refused=True, head_and_local_changes_preserved=True,
           dot_git_is_file=True, linked_restore_preserves_main=True)


def test_unborn_repository_checkpoint_does_not_create_head(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "unborn")
    (repo / "f").write_text("uncommitted initial file\n", encoding="utf-8")
    store = GitCheckpoints()
    ref = "refs/aconsole/checkpoints/audit/3"
    _ = store.capture(repo, ref, "before first commit")
    assert git(repo, "symbolic-ref", "HEAD") == "refs/heads/trunk"
    assert not (repo / ".git/refs/heads/trunk").exists()
    assert not (repo / ".git/index").exists()
    (repo / "f").write_text("later", encoding="utf-8")
    store.materialize(repo, ref)
    assert (repo / "f").read_text(encoding="utf-8") == "uncommitted initial file\n"
    assert not (repo / ".git/index").exists()
    record("unborn repository checkpoint", head_remains_unborn=True, real_index_absent=True, bytes_restored=True)
