"""A plan write is confirmed after the commit; one that fails is reported and leaves nothing behind."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import OperationalError

from aconsole.contracts.app import ProjectAddParams, ProjectView
from aconsole.contracts.common import NodeStatus
from aconsole.core.infrastructure.mcp import tools
from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.repositories import plans as plans_repo
from aconsole.core.infrastructure.persistence.repositories.plans import PlanDatabase
from aconsole.core.infrastructure.persistence.writer import Writer
from tests.core.test_wire import _connect, _daemon, _git_repo, _stop

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


_STATEMENT = "INSERT"


def _broken(*_args: object) -> None:
    raise OperationalError(_STATEMENT, {}, ValueError())


def _writer(tmp_path: Path) -> Writer:
    url = f"sqlite:///{tmp_path / 'state.db'}"
    migrate(url, "head")
    writer = Writer(write_engine(url))
    writer.start()
    return writer


def test_a_plan_write_that_fails_is_reported_and_undone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo(tmp_path / "repo")

    async def project_id() -> str:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        board: PlanBoard = daemon._plans
        ctx = TabContext(tab_id="t1", project_id=str(project.id), worktree=None, agent="claude")
        created = board.create(ctx, "Plan", "goal", [NodeIn(key="N-01", title="One", objective="do")])
        node = created.nodes[0].node_id

        monkeypatch.setattr(plans_repo, "save_plan", _broken)
        with pytest.raises(tools._ToolError):
            _ = board.set_node_status(node, "cancelled", None)
        view = board.views()[0]
        assert view.nodes[0].status is NodeStatus.PENDING
        monkeypatch.undo()

        moved = board.set_node_status(node, "cancelled", None)
        assert moved.status is NodeStatus.CANCELLED
        await _stop(daemon, task, client)
        return str(project.id)

    _ = asyncio.run(project_id())


def test_the_first_save_of_a_plan_that_fails_leaves_no_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _writer(tmp_path)
    try:
        board = PlanBoard()
        board.use_store(PlanDatabase(writer))
        monkeypatch.setattr(plans_repo, "save_plan", _broken)
        ctx = TabContext(tab_id="t1", project_id="p", worktree=None, agent="claude")
        with pytest.raises(tools._ToolError):
            _ = board.create(ctx, "Plan", "goal", [NodeIn(key="N-01", title="One", objective="do")])
        assert board.views() == ()
    finally:
        writer.stop()


def test_one_bad_job_does_not_take_its_batch_with_it(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    done: list[str] = []

    def good(_session: Session) -> None:
        done.append("good")

    try:
        writer.post(good)
        writer.post(_broken)
        writer.post(good)
        _ = writer.call(good)
    finally:
        writer.stop()
    assert done.count("good") >= 3
