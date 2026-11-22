"""Add-only graph assembly keeps ids, attempts, and a receipt that replays exactly."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from aconsole.contracts.common import NodeStatus
from aconsole.core.domain.plan_admission import assembly_node_id
from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.models import NodeAttempt, Project
from aconsole.core.infrastructure.persistence.repositories import plans as plans_repo
from aconsole.core.infrastructure.persistence.repositories.plans import PlanDatabase
from aconsole.core.infrastructure.persistence.writer import Writer

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


_NOW = datetime(2026, 11, 22, tzinfo=UTC)


def _writer(tmp_path: Path) -> Writer:
    url = f"sqlite:///{tmp_path / 'state.db'}"
    migrate(url, "head")
    writer = Writer(write_engine(url))
    writer.start()
    return writer


def _board(writer: Writer) -> tuple[PlanBoard, TabContext, str]:
    def seed(session: Session) -> None:
        session.add(Project(id="p", root_path="/r", name="r", languages=[], created_at=_NOW))

    _ = writer.call(seed)
    board = PlanBoard(now=lambda: _NOW)
    board.use_store(PlanDatabase(writer))
    ctx = TabContext(tab_id="t1", project_id="p", worktree=None, agent="claude")
    created = board.create(ctx, "Plan", "goal", [NodeIn(key="N-01", title="One", objective="do")])
    return board, ctx, created.plan_id


def _keys(board: PlanBoard, plan_id: str) -> set[str]:
    view = board.view(plan_id)
    assert view is not None
    return {node.key for node in view.nodes}


def _offer(key: str, *, objective: str = "do", depends_on: list[str] | None = None) -> NodeIn:
    return NodeIn(key=key, title=f"Node {key}", objective=objective, depends_on=depends_on or [])


def test_assembly_refuses_before_write(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    try:
        board, ctx, plan_id = _board(writer)
        original = board.view(plan_id)
        assert original is not None
        node_id = str(original.nodes[0].id)
        vault: list[object] = []
        board.on_changed = vault.append

        with pytest.raises(Exception, match="PLAN_CYCLE"):
            _ = board.commit_graph(
                ctx, plan_id, "unused", [_offer("N-02", depends_on=["N-03"]), _offer("N-03", depends_on=["N-02"])]
            )
        with pytest.raises(Exception, match="NODE_CONTRACT"):
            _ = board.commit_graph(ctx, plan_id, "unused", [_offer("N-01", objective="changed")])
        assert vault == []
        _ = board.claim(ctx, node_id)
        held = len(vault)
        with pytest.raises(Exception, match="LEASE_HELD"):
            _ = board.preview_graph(ctx, plan_id, [_offer("N-02")])
        with pytest.raises(Exception, match="LEASE_HELD"):
            _ = board.add_nodes(ctx, plan_id, [_offer("N-02")])
        assert len(vault) == held
        _ = board.set_node_status(node_id, "done", "shipped")
        _ = board.freeze(plan_id, {}, graph_present=False)
        frozen = len(vault)
        with pytest.raises(Exception, match="PLAN_FROZEN"):
            _ = board.commit_graph(ctx, plan_id, "unused", [_offer("N-02")])
        assert len(vault) == frozen

        assert _keys(board, plan_id) == {"N-01"}
        kept = board.view(plan_id)
        assert kept is not None
        assert kept.nodes[0].status is NodeStatus.DONE
        assert str(kept.nodes[0].id) == node_id
    finally:
        writer.stop()


def test_conflict_cas_replay_and_restart(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    try:
        board, ctx, plan_id = _board(writer)
        original = board.view(plan_id)
        assert original is not None
        node_id = str(original.nodes[0].id)

        def remember(session: Session) -> None:
            session.add(
                NodeAttempt(
                    id="attempt-1",
                    node_id=node_id,
                    attempt_no=1,
                    status="failed",
                    base_oid="b" * 40,
                    compact_verdict="kept",
                    started_at=_NOW,
                )
            )

        _ = writer.call(remember)
        first = board.preview_graph(ctx, plan_id, [_offer("N-02")])
        second = board.preview_graph(ctx, plan_id, [_offer("N-03")])
        added = board.commit_graph(ctx, plan_id, first.token, [_offer("N-02")])
        with pytest.raises(Exception, match="GRAPH_TOKEN_STALE"):
            _ = board.commit_graph(ctx, plan_id, second.token, [_offer("N-03")])
        assert _keys(board, plan_id) == {"N-01", "N-02"}
        again = board.commit_graph(ctx, plan_id, first.token, [_offer("N-02")])
        assert again.node_ids == added.node_ids
        assert assembly_node_id(plan_id, "N-02") == dict(added.node_ids)["N-02"]

        restarted = PlanBoard(now=lambda: _NOW)
        restarted.use_store(PlanDatabase(writer))
        replay = restarted.commit_graph(ctx, plan_id, first.token, [_offer("N-02")])
        assert replay.node_ids == added.node_ids
        kept = restarted.view(plan_id)
        assert kept is not None
        assert str(next(node.id for node in kept.nodes if node.key == "N-01")) == node_id
        found: list[str] = []

        def read(session: Session) -> None:
            rows = session.scalars(select(NodeAttempt.compact_verdict).where(NodeAttempt.node_id == node_id)).all()
            found.extend([verdict for verdict in rows if verdict is not None])

        _ = writer.call(read)
        assert found == ["kept"]
    finally:
        writer.stop()


def test_a_failed_assembly_changes_nothing_and_retries_the_same_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = _writer(tmp_path)
    try:
        board, ctx, plan_id = _board(writer)
        original = board.view(plan_id)
        assert original is not None
        node_id = str(original.nodes[0].id)

        def remember(session: Session) -> None:
            session.add(
                NodeAttempt(
                    id="attempt-1",
                    node_id=node_id,
                    attempt_no=1,
                    status="failed",
                    base_oid="b" * 40,
                    started_at=_NOW,
                )
            )

        _ = writer.call(remember)
        vault: list[object] = []
        board.on_changed = vault.append
        offer = [_offer("N-02")]
        preview = board.preview_graph(ctx, plan_id, offer)

        def broken(*_args: object) -> None:
            statement = "INSERT"
            cause = ValueError(statement)
            raise OperationalError(statement, {}, cause)

        monkeypatch.setattr(plans_repo, "save_plan", broken)
        with pytest.raises(Exception, match="INTERNAL_ERROR"):
            _ = board.commit_graph(ctx, plan_id, preview.token, offer)
        assert _keys(board, plan_id) == {"N-01"}
        assert vault == []
        counts: list[int] = []

        def count(session: Session) -> None:
            counts.append(len(session.scalars(select(NodeAttempt.id).where(NodeAttempt.node_id == node_id)).all()))

        _ = writer.call(count)
        assert counts == [1]
        monkeypatch.undo()
        receipt = board.commit_graph(ctx, plan_id, preview.token, offer)
        assert dict(receipt.node_ids)["N-02"] == assembly_node_id(plan_id, "N-02")
        assert len(vault) == 1
    finally:
        writer.stop()
