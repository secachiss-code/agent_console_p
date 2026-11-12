"""Read-only Git RPCs at file, revision, and history-selection boundaries."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.app import DiffFileResult, DiffSummaryResult, TabIdParams, TabView, TerminalLaunchSpec
from aconsole.contracts.common import ProjectId, TabId, WorkspaceMode
from aconsole.contracts.git import (
    DiffSource,
    GitDiffFileParams,
    GitDiffParams,
    GitGraphParams,
    GitGraphResult,
    GitStateParams,
    GitStateResult,
)
from aconsole.contracts.work_sessions import (
    WorkSessionCreateParams,
    WorkSessionHistoryResult,
    WorkSessionResultParams,
    WorkSessionView,
)
from tests.core.git_helpers import add_worktree, commit, git, new_repo
from tests.core.test_git_durability import _start
from tests.core.test_wire import _open, _stop


def test_empty_index_and_root_commit_diffs(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo", branch="develop")
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            state = await client.call("git/state", GitStateParams(project_id=project), GitStateResult)
            assert state.empty
            assert state.base_oid is None
            assert state.worktrees[0].head_oid is None
            graph = await client.call("git/graph", GitGraphParams(project_id=project), GitGraphResult)
            assert graph.nodes == ()
            assert graph.next_cursor is None
            (repo / "first.txt").write_text("one\ntwo\n", encoding="utf-8")
            _ = git(repo, "add", "first.txt")
            before = git(repo, "status", "--porcelain")
            index = await client.call(
                "git/diffSummary",
                GitDiffParams(project_id=project, source=DiffSource(kind="index", worktree=str(repo))),
                DiffSummaryResult,
            )
            assert [(row.path, row.insertions, row.deletions) for row in index.files] == [("first.txt", 2, 0)]
            assert git(repo, "status", "--porcelain") == before
            _ = git(repo, "commit", "-qm", "root")
            oid = git(repo, "rev-parse", "HEAD")
            root = await client.call(
                "git/diffSummary", GitDiffParams(project_id=project, source=DiffSource(kind="commit", oid=oid)),
                DiffSummaryResult,
            )
            assert root.files == index.files
            assert git(repo, "status", "--porcelain") == ""
            assert git(repo, "rev-parse", "HEAD") == oid
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["binary", "empty", "symlink", "fifo"])
def test_nontext_untracked_files_do_not_read_targets_or_block(tmp_path: Path, kind: str) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        head = commit(repo, "a.txt", "base\n", "root")
        outside = tmp_path / "private.txt"
        outside.write_text("private text must never be returned\n", encoding="utf-8")
        path = repo / "fresh"
        if kind == "binary":
            path.write_bytes(b"\0binary\n")
        elif kind == "empty":
            path.touch()
        elif kind == "symlink":
            path.symlink_to(outside)
        else:
            os.mkfifo(path)
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            source = DiffSource(kind="worktree", worktree=str(repo))
            before = git(repo, "status", "--porcelain")
            summary = await asyncio.wait_for(client.call(
                "git/diffSummary", GitDiffParams(project_id=project, source=source), DiffSummaryResult,
            ), timeout=10)
            # Git omits FIFO entries; the file-detail request must still finish promptly.
            if kind != "fifo":
                assert [(row.path, row.change, row.insertions) for row in summary.files] == [
                    ("fresh", "added" if kind == "empty" else "binary", 0),
                ]
            else:
                assert summary.files == ()
            detail = await asyncio.wait_for(client.call(
                "git/diffFile", GitDiffFileParams(project_id=project, source=source, path="fresh"), DiffFileResult,
            ), timeout=10)
            assert detail.hunks == ()
            assert not detail.truncated
            assert outside.read_text(encoding="utf-8") == "private text must never be returned\n"
            assert git(repo, "rev-parse", "HEAD") == head
            assert git(repo, "status", "--porcelain") == before
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


@pytest.mark.parametrize("tracked", [False, True])
def test_file_diff_reports_byte_limit_then_recovers(tmp_path: Path, *, tracked: bool) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        head = commit(repo, "a.txt", "base\n", "root")
        path = repo / ("a.txt" if tracked else "fresh.txt")
        large = "large line\n" * 200_001
        path.write_text(large, encoding="utf-8")
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            source = DiffSource(kind="worktree", worktree=str(repo))
            ask = GitDiffFileParams(project_id=project, source=source, path=path.name)
            assert await client.error("git/diffFile", ask) == "DIFF_TOO_LARGE"
            assert path.read_text(encoding="utf-8") == large
            assert git(repo, "rev-parse", "HEAD") == head
            path.write_text("small\n", encoding="utf-8")
            detail = await client.call("git/diffFile", ask, DiffFileResult)
            assert not detail.truncated
            assert [line.text for hunk in detail.hunks for line in hunk.lines if line.kind == "add"] == ["small"]
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


@pytest.mark.parametrize("tracked", [False, True])
def test_diff_line_limits_and_invalid_request_do_not_change_files(tmp_path: Path, *, tracked: bool) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        head = commit(repo, "a.txt", "base\n", "root")
        path = repo / ("a.txt" if tracked else "fresh.txt")
        text = "".join(f"line {number}\n" for number in range(12))
        path.write_text(text, encoding="utf-8")
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            ask = GitDiffFileParams(
                project_id=project, source=DiffSource(kind="worktree", worktree=str(repo)), path=path.name,
            )
            assert await client.error("git/diffFile", msgspec.structs.replace(ask, max_lines=-1)) == "INVALID_PARAMS"
            for limit in (0, 3, 100):
                detail = await client.call(
                    "git/diffFile", msgspec.structs.replace(ask, max_lines=limit), DiffFileResult,
                )
                lines = [line for hunk in detail.hunks for line in hunk.lines]
                assert len(lines) <= limit
                assert detail.truncated is (limit < 12)
                if limit == 100:
                    assert [line.text for line in lines if line.kind == "add"] == text.splitlines()
            assert path.read_text(encoding="utf-8") == text
            assert git(repo, "rev-parse", "HEAD") == head
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_between_and_base_diffs_use_requested_revision(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        first = commit(repo, "a.txt", "first\n", "root")
        second = commit(repo, "a.txt", "second\n", "second")
        (repo / "a.txt").write_text("uncommitted\n", encoding="utf-8")
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            sources = (
                DiffSource(kind="between", oid=second, against=first),
                DiffSource(kind="base", worktree=str(repo), against=first),
                DiffSource(kind="base", worktree=str(repo)),
            )
            for source, expected in zip(sources, ("second", "uncommitted", "uncommitted"), strict=True):
                detail = await client.call(
                    "git/diffFile", GitDiffFileParams(project_id=project, source=source, path="a.txt"), DiffFileResult,
                )
                assert [line.text for hunk in detail.hunks for line in hunk.lines if line.kind == "add"] == [expected]
            missing = DiffSource(kind="between", oid="f" * 40, against=first)
            assert await client.error(
                "git/diffFile", GitDiffFileParams(project_id=project, source=missing, path="a.txt"),
            ) == "INTERNAL_ERROR"
            assert (repo / "a.txt").read_text(encoding="utf-8") == "uncommitted\n"
            assert git(repo, "rev-parse", "HEAD") == second
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_tab_graph_decorations_and_invalid_cursor(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        first = commit(repo, "a.txt", "base\n", "root")
        other = add_worktree(repo, tmp_path / "other", "unrelated")
        excluded = commit(other, "other.txt", "other\n", "unrelated")
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            tab = await client.call(
                "tab/open", msgspec.structs.replace(_open(project), workspace=WorkspaceMode.NEW_WORKTREE), TabView,
            )
            tree = Path(tab.worktree_path)
            head = commit(tree, "mine.txt", "mine\n", "mine")
            _ = git(repo, "tag", "v-test", head)
            _ = git(repo, "update-ref", "refs/remotes/origin/review", head)
            _ = git(tree, "checkout", "--detach", "-q", head)
            refs = git(repo, "show-ref")
            before = git(tree, "status", "--porcelain")
            graph = await client.call("git/graph", GitGraphParams(project_id=project, tab_id=tab.id), GitGraphResult)
            assert {node.oid for node in graph.nodes} == {first, head}
            assert excluded not in {node.oid for node in graph.nodes}
            node = next(node for node in graph.nodes if node.oid == head)
            assert {("v-test", "tag"), ("origin/review", "remote"), ("HEAD", "head")} <= {
                (ref.name, ref.kind) for ref in node.refs
            }
            assert await client.error(
                "git/graph", GitGraphParams(project_id=project, tab_id=TabId("missing")),
            ) == "TAB_NOT_FOUND"
            for cursor, error in ((graph.version + ":no-number", "INVALID_PARAMS"), ("old:1", "GIT_SNAPSHOT_STALE")):
                assert await client.error(
                    "git/graph", GitGraphParams(project_id=project, tab_id=tab.id, cursor=cursor),
                ) == error
            assert git(repo, "show-ref") == refs
            assert git(tree, "status", "--porcelain") == before
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_session_graph_and_state_exclude_other_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        first = commit(repo, "a.txt", "base\n", "root")
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            sessions = [await client.call(
                "workSession/create", WorkSessionCreateParams(project_id=project, title=title), WorkSessionView,
            ) for title in ("selected", "unrelated")]
            tabs = [await client.call(
                "tab/open", msgspec.structs.replace(_open(project), work_session_id=session.id), TabView,
            ) for session in sessions]
            heads = [commit(Path(tab.worktree_path), "mine.txt", f"{number}\n", f"work {number}")
                     for number, tab in enumerate(tabs)]
            refs = git(repo, "show-ref")
            for tab_filter in (None, tabs[0].id):
                graph = await client.call(
                    "git/graph", GitGraphParams(project_id=project, work_session_id=sessions[0].id, tab_id=tab_filter),
                    GitGraphResult,
                )
                assert {node.oid for node in graph.nodes} == {first, heads[0]}
            state = await client.call(
                "git/state", GitStateParams(project_id=project, work_session_id=sessions[0].id), GitStateResult,
            )
            assert [row.path for row in state.worktrees] == [sessions[0].worktree]
            assert {row.name for row in state.branches} == {"trunk", sessions[0].branch}
            assert git(repo, "show-ref") == refs
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


@pytest.mark.parametrize("remote", [False, True])
def test_deleted_default_branch_uses_available_base(tmp_path: Path, *, remote: bool) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        head = commit(repo, "a.txt", "base\n", "root")
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            _ = git(repo, "checkout", "-qb", "release")
            _ = git(repo, "branch", "-D", "trunk")
            if remote:
                _ = git(repo, "update-ref", "refs/remotes/origin/release", head)
                _ = git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/release")
            refs = git(repo, "show-ref")
            state = await client.call("git/state", GitStateParams(project_id=project), GitStateResult)
            assert state.base_branch == "release"
            assert state.base_oid == head
            assert state.worktrees[0].merge.status == "not_applicable"
            assert git(repo, "show-ref") == refs
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_separated_diff_hunks_and_binary_commit_preserve_objects(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        original = [f"line {number}\n" for number in range(50)]
        first = commit(repo, "a.txt", "".join(original), "root")
        (repo / "binary.dat").write_bytes(b"\0binary\n")
        _ = git(repo, "add", "binary.dat")
        _ = git(repo, "commit", "-qm", "binary")
        second = git(repo, "rev-parse", "HEAD")
        original[1] = "changed first\n"
        original[48] = "changed last\n"
        (repo / "a.txt").write_text("".join(original), encoding="utf-8")
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            binary = await client.call(
                "git/diffFile", GitDiffFileParams(
                    project_id=project, source=DiffSource(kind="commit", oid=second), path="binary.dat",
                ), DiffFileResult,
            )
            assert binary.hunks == ()
            assert not binary.truncated
            detail = await client.call(
                "git/diffFile", GitDiffFileParams(
                    project_id=project, source=DiffSource(kind="worktree", worktree=str(repo)), path="a.txt",
                ), DiffFileResult,
            )
            assert len(detail.hunks) == 2
            assert [line.text for hunk in detail.hunks for line in hunk.lines if line.kind == "add"] == [
                "changed first", "changed last",
            ]
            assert (repo / "a.txt").read_text(encoding="utf-8") == "".join(original)
            assert git(repo, "rev-parse", "HEAD") == second
            assert git(repo, "show", f"{first}:a.txt").splitlines() == [f"line {number}" for number in range(50)]
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_worker_snapshot_graph_keeps_recorded_identity_and_tab_filter(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "base\n", "root")
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            session = await client.call(
                "workSession/create", WorkSessionCreateParams(project_id=project, title="results"), WorkSessionView,
            )
            tabs = [await client.call(
                "tab/open", msgspec.structs.replace(_open(project), work_session_id=session.id, isolated=True), TabView,
            ) for _ in range(2)]
            results = []
            for number, tab in enumerate(tabs):
                _ = await client.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
                (Path(tab.worktree_path) / "result.txt").write_text(f"result {number}\n", encoding="utf-8")
                history = await client.call(
                    "workSession/result", WorkSessionResultParams(
                        project_id=project, work_session_id=session.id, tab_id=tab.id,
                    ), WorkSessionHistoryResult,
                )
                results.append(history.snapshots[-1])
            refs = git(repo, "show-ref")
            for number, tab in enumerate(tabs):
                graph = await client.call(
                    "git/graph", GitGraphParams(project_id=project, work_session_id=session.id, tab_id=tab.id),
                    GitGraphResult,
                )
                by_oid = {node.oid: node for node in graph.nodes}
                point = results[number]
                assert results[1 - number].oid not in by_oid
                node = by_oid[point.oid]
                assert node.kind == "checkpoint"
                assert node.tab_id == tab.id
                assert node.checkpoint_id == f"{session.id}-{point.sequence}"
                assert node.turn == point.sequence
                assert node.previous_snapshot == point.previous_oid
                assert git(repo, "show", f"{point.oid}:result.txt") == f"result {number}"
            all_history = await client.call("git/graph", GitGraphParams(project_id=project), GitGraphResult)
            assert {point.oid for point in results} <= {node.oid for node in all_history.nodes}
            assert any(ref.kind == "checkpoint" for node in all_history.nodes for ref in node.refs)
            assert git(repo, "show-ref") == refs
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_unknown_project_read_requests_do_not_resolve_arbitrary_worktree(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        head = commit(repo, "private.txt", "project-local text\n", "root")
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            missing = ProjectId("missing-project")
            source = DiffSource(kind="worktree", worktree=str(repo))
            requests = (
                ("git/state", GitStateParams(project_id=missing)),
                ("git/graph", GitGraphParams(project_id=missing)),
                ("git/diffSummary", GitDiffParams(project_id=missing, source=source)),
                ("git/diffFile", GitDiffFileParams(project_id=missing, source=source, path="private.txt")),
            )
            for method, params in requests:
                assert await client.error(method, params) == "PROJECT_NOT_FOUND"
            state = await client.call("git/state", GitStateParams(project_id=project), GitStateResult)
            assert state.base_oid == head
            assert git(repo, "rev-parse", "HEAD") == head
            assert git(repo, "status", "--porcelain") == ""
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())
