"""Session restore must apply the recorded commit even if its ref is replaced."""

from __future__ import annotations

import asyncio
from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.app import ProjectAddParams, ProjectView, TabIdParams, TabView, TerminalLaunchSpec
from aconsole.contracts.work_sessions import (
    WorkSessionCreateParams,
    WorkSessionHandoffParams,
    WorkSessionHistoryResult,
    WorkSessionParams,
    WorkSessionRestoreParams,
    WorkSessionRestorePreview,
    WorkSessionRestorePreviewParams,
    WorkSessionView,
)
from tests.core.git_helpers import commit, git, new_repo
from tests.core.test_wire import _connect, _daemon, _open, _stop


@pytest.mark.parametrize("when", ["before_preview", "after_preview"])
def test_restore_uses_the_confirmed_commit_instead_of_a_moved_ref(tmp_path: Path, when: str) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "f", "reviewed\n", "seed")
        daemon = _daemon(tmp_path, serve_ingest=False)
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            session = await client.call(
                "workSession/create", WorkSessionCreateParams(project_id=project.id, title="Restore recorded commit"),
                WorkSessionView,
            )
            tab = await client.call(
                "tab/open", msgspec.structs.replace(_open(project.id), work_session_id=session.id), TabView,
            )
            _ = await client.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
            history = await client.call("workSession/history", WorkSessionParams(
                project_id=project.id, work_session_id=session.id,
            ), WorkSessionHistoryResult)
            point = history.snapshots[0]
            _ = await client.call("workSession/handoff", WorkSessionHandoffParams(
                project_id=project.id, work_session_id=session.id, to_tab=tab.id,
            ), TabView)
            workspace = Path(session.worktree)
            replacement = commit(workspace, "f", "unreviewed\n", "later commit")
            (workspace / "f").write_text("current staged content\n", encoding="utf-8")
            _ = git(workspace, "add", "f")
            head = git(workspace, "rev-parse", "HEAD")
            index = git(workspace, "write-tree")
            if when == "before_preview":
                _ = git(repo, "update-ref", point.ref, replacement)
            preview = await client.call("workSession/restorePreview", WorkSessionRestorePreviewParams(
                project_id=project.id, work_session_id=session.id, sequence=point.sequence,
            ), WorkSessionRestorePreview)
            assert preview.oid == point.oid
            if when == "after_preview":
                _ = git(repo, "update-ref", point.ref, replacement)
            restored = await client.call("workSession/restore", WorkSessionRestoreParams(
                project_id=project.id, work_session_id=session.id, sequence=point.sequence,
                confirm_token=preview.confirm_token,
            ), WorkSessionHistoryResult)
            assert (workspace / "f").read_text(encoding="utf-8") == "reviewed\n"
            assert git(workspace, "rev-parse", "HEAD") == head
            assert git(workspace, "write-tree") == index
            safety = restored.snapshots[-1]
            assert safety.kind == "safety"
            assert git(repo, "show", f"{safety.oid}:f") == "current staged content"
        finally:
            await _stop(daemon, serving, client)

    asyncio.run(scenario())
