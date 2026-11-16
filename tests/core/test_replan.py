"""REPLAN_INTERRUPT is durable and human review cannot be impersonated by an agent."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
# Integration fixtures deliberately exercise the MCP and daemon internals.
import asyncio
from typing import TYPE_CHECKING
from unittest.mock import patch

import msgspec
import pytest
from mcp import Client

from aconsole.contracts.app import PlanDecideParams, PlanIdParams, PlanInboxResult, PlanView
from aconsole.contracts.common import PlanId
from aconsole.core.application.ports.plan_store import StoreWriteError
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.knowledge import KnowledgeService
from aconsole.core.infrastructure.mcp.server import build_server
from aconsole.core.infrastructure.mcp.tools import _ToolError
from aconsole.core.infrastructure.persistence.repositories.plans import PlanDatabase
from tests.core.test_mcp_pipeline import pipeline as pipeline  # noqa: PLC0414 -- shared pytest fixture
from tests.core.test_mcp_tools import _call, _error

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

    from aconsole.core.application.ports.plan_store import StoredPlan
    from tests.core.test_mcp_pipeline import _Harness


def _escalate(h: _Harness) -> str:
    return h.board.escalate(h.actor, h.node, h.fence, "contract", "Need a revised acceptance criterion")


def test_escalation_replay_restart_and_human_decision(pipeline: _Harness) -> None:
    h = pipeline
    events: list[tuple[str, str, str, str]] = []

    def attention(plan: str, node: str, kind: str, reason: str) -> None:
        events.append((plan, node, kind, reason))

    h.board.on_attention = attention
    before = h.board.export_plan(h.plan)
    request_id = _escalate(h)
    assert _escalate(h) == request_id
    assert len(events) == 1
    assert events[0][:3] == (h.plan, h.node, "replan")
    assert len(h.board.export_plan(h.plan).nodes) == len(before.nodes)
    assert h.board.export_plan(h.plan).nodes[1].status == "blocked"
    assert len(h.board.inbox(h.plan)) == 1
    h.restart()
    assert h.board.inbox(h.plan)[0].id == request_id
    with pytest.raises(DomainError, match="PLAN_ROLE"):
        _ = h.board.decide(h.plan, request_id, "unblock", "Reviewed", decided_by="agent")
    with pytest.raises(DomainError, match="NODE_CONTRACT"):
        _ = h.board.decide(h.plan, request_id, "unblock", " ")
    view = h.board.decide(h.plan, request_id, "unblock", "Reviewed")
    assert view.nodes[1].status.value == "pending"
    assert h.board.inbox(h.plan) == ()
    assert h.board.decide(h.plan, request_id, "unblock", "Reviewed") == view
    h.restart()
    entry = PlanDatabase(h.writer).load_change(request_id)
    assert entry is not None
    assert (entry.status, entry.decided_by, entry.payload["decision_note"]) == ("applied", "human", "Reviewed")
    assert h.board.export_plan(h.plan).nodes[1].status == "pending"


@pytest.mark.parametrize(("decision", "status"), [("cancel", "cancelled"), ("reject", "blocked")])
def test_cancel_or_reject(pipeline: _Harness, decision: str, status: str) -> None:
    h = pipeline
    request_id = _escalate(h)
    view = h.board.decide(h.plan, request_id, decision, "Reviewed")
    assert view.nodes[1].status.value == status
    assert h.board.inbox(h.plan) == ()
    with pytest.raises(DomainError, match="NODE_CONTRACT"):
        _ = h.board.decide(h.plan, request_id, "unblock", "Changed my mind")


def test_writer_failure_rolls_back_node_and_inbox(pipeline: _Harness) -> None:
    h = pipeline
    before = h.board.export_plan(h.plan)
    with (
        patch.object(PlanDatabase, "save_attention", side_effect=StoreWriteError("disk full")),
        pytest.raises(_ToolError),
    ):
        _ = _escalate(h)
    assert h.board.export_plan(h.plan) == before
    assert h.board.inbox(h.plan) == ()
    request_id = _escalate(h)
    blocked = h.board.export_plan(h.plan)
    with (
        patch.object(PlanDatabase, "save_decision", side_effect=StoreWriteError("disk full")),
        pytest.raises(_ToolError),
    ):
        _ = h.board.decide(h.plan, request_id, "unblock", "Reviewed")
    assert h.board.export_plan(h.plan) == blocked
    h.restart()
    assert h.board.inbox(h.plan)[0].id == request_id
    assert h.board.export_plan(h.plan).nodes == blocked.nodes


def test_mcp_escalation_rejects_agent_unblock(pipeline: _Harness) -> None:
    h = pipeline

    async def scenario() -> None:
        async with Client(build_server(h.board, h.context())) as client:
            args: dict[str, object] = {
                "node_id": h.node,
                "fence": h.fence,
                "kind": "contract",
                "reason": "Review acceptance",
            }
            result = await _call(client, "node_escalate", args)
            assert result["reason"] == "REPLAN_INTERRUPT"
            assert await _call(client, "node_escalate", args) == result
            denied = await client.call_tool("node_update", {"node_id": h.node, "status": "pending"})
            assert denied.is_error is True
            assert _error(denied)["reason"] == "NODE_TRANSITION_INVALID"
            assert not {tool.name for tool in (await client.list_tools()).tools} & {"plan_decide", "plan_inbox"}

    asyncio.run(scenario())


def test_review_rpc_dispatch_and_spoof_rejection(pipeline: _Harness) -> None:
    h = pipeline
    request_id = _escalate(h)
    # Review dispatch requires neither a vault nor a graph; these paths only access the board.
    service = object.__new__(KnowledgeService)
    service.board = h.board
    result = service.dispatch("plan/inbox", msgspec.json.encode(PlanIdParams(plan_id=PlanId(h.plan))))
    assert isinstance(result, PlanInboxResult)
    assert [entry.id for entry in result.entries] == [request_id]
    spoof = {"plan_id": h.plan, "request_id": request_id, "decision": "unblock", "note": "ok", "decided_by": "agent"}
    with pytest.raises(msgspec.ValidationError, match="unknown field"):
        _ = service.dispatch("plan/decide", msgspec.json.encode(spoof))
    result = service.dispatch(
        "plan/decide",
        msgspec.json.encode(
            PlanDecideParams(
                plan_id=PlanId(h.plan),
                request_id=request_id,
                decision="unblock",
                note="Human review",
            )
        ),
    )
    assert isinstance(result, PlanView)
    assert result.nodes[1].status.value == "pending"


def test_human_review_over_real_ipc_socket(tmp_path: Path) -> None:
    from aconsole.contracts.app import ProjectAddParams, ProjectView
    from aconsole.core.infrastructure.mcp.tools import NodeIn, TabContext
    from tests.core.test_wire import _connect, _daemon, _git_repo, _stop

    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        ctx = TabContext(tab_id="executor", project_id=str(project.id), worktree=repo, agent="codex")
        board = daemon._plans
        made = board.create(
            ctx,
            "Review",
            "Human decision",
            [
                NodeIn(
                    key="C-1",
                    title="Module",
                    objective="Ship",
                    paths=["**"],
                )
            ],
        )
        node_id = made.nodes[0].node_id
        claimed = board.claim(ctx, node_id)
        _ = board.update(ctx, node_id, claimed.fence, "blocked", "Acceptance needs review", None)
        wanted = PlanIdParams(plan_id=PlanId(made.plan_id))
        inbox = await client.call("plan/inbox", wanted, PlanInboxResult)
        assert len(inbox.entries) == 1
        decision = PlanDecideParams(
            plan_id=wanted.plan_id, request_id=inbox.entries[0].id, decision="unblock", note="Acceptance clarified"
        )
        view = await client.call("plan/decide", decision, PlanView)
        assert view.nodes[0].status.value == "pending"
        await _stop(daemon, task, client)
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        view = await client.call("plan/get", wanted, PlanView)
        assert view.nodes[0].status.value == "pending"
        assert (await client.call("plan/inbox", wanted, PlanInboxResult)).entries == ()
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_sql_transaction_rolls_back_partial_graph_write(pipeline: _Harness) -> None:
    from aconsole.core.infrastructure.persistence.repositories import plans

    h = pipeline
    original = plans.save_plan

    def interrupted(session: Session, plan: StoredPlan) -> None:
        original(session, plan)
        message = "transaction interrupted after graph flush"
        raise StoreWriteError(message)

    with patch.object(plans, "save_plan", side_effect=interrupted), pytest.raises(_ToolError):
        _ = _escalate(h)
    h.restart()
    assert h.board.export_plan(h.plan).nodes[1].status == "in_progress"
    assert h.board.inbox(h.plan) == ()
