"""Human plan/save and plan/delete: one write, then the plan is gone from memory and disk."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.contracts.app import (
    EmptyResult,
    PlanIdParams,
    PlanNodeSpec,
    PlanSaveParams,
    PlanView,
    ProjectAddParams,
    ProjectView,
)
from aconsole.contracts.common import PlanId, ProjectId
from aconsole.contracts.events import PlanRemoved, PlanUpdated
from aconsole.contracts.ipc import EventEnvelope
from aconsole.core.application.services.coalescer import Coalescer
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
from aconsole.core.infrastructure.mcp.tools import PlanBoard, TabContext
from aconsole.ui.client.store import CoreStore
from tests.core.test_wire import _connect, _daemon, _git_repo, _stop

if TYPE_CHECKING:
    from pathlib import Path

    from aconsole.core.application.ports.plan_store import StoredPlan


def _direction() -> PlanNodeSpec:
    return PlanNodeSpec(
        key="D-0",
        title="Direction",
        objective="Hold the scope",
        level=0,
        paths=("src/**",),
        acceptance=("scope held",),
        invariants="pure",
        gate_profile=msgspec.Raw(json.dumps({"tests": ["pytest"]}).encode()),
    )


def _module(key: str = "M-1") -> PlanNodeSpec:
    return PlanNodeSpec(
        key=key,
        title=f"Module {key}",
        objective="Ship the module",
        parent="D-0",
        paths=("src/a.py",),
        acceptance=("tested",),
        test_cmd="pytest",
    )


class _Memory:
    def __init__(self) -> None:
        self.plans: dict[str, StoredPlan] = {}

    def load(self) -> tuple[StoredPlan, ...]:
        return tuple(self.plans.values())

    def save(self, plan: StoredPlan) -> None:
        self.plans[plan.id] = plan

    def delete(self, plan_id: str) -> None:
        self.plans.pop(plan_id, None)


def _board() -> tuple[PlanBoard, _Memory]:
    board = PlanBoard()
    store = _Memory()
    board.use_store(store)
    return board, store


def test_save_is_idempotent_and_replaces_only_a_draft() -> None:
    board, store = _board()
    first = board.save_document("proj", "Board", "goal", None, (_direction(), _module()))
    again = board.save_document("proj", "Board", "goal", None, None)
    assert again.id == first.id
    assert len(store.plans) == 1
    renamed = board.save_document("proj", "Renamed", "next", str(first.id), (_direction(), _module("M-2")))
    assert renamed.title == "Renamed"
    assert [node.key for node in renamed.nodes] == ["D-0", "M-2"]
    assert {node.key for node in store.plans[str(first.id)].nodes} == {"D-0", "M-2"}
    board.freeze(str(first.id), {}, graph_present=False)
    with pytest.raises(DomainError, match="PLAN_FROZEN"):
        board.save_document("proj", "Renamed", "later", str(first.id), None)
    same = board.save_document("proj", "Renamed", "next", str(first.id), None)
    assert same.phase == "frozen"


def test_a_lease_keeps_the_plan() -> None:
    board, _store = _board()
    made = board.save_document("proj", "Board", "goal", None, (_direction(), _module()))
    module = next(node for node in made.nodes if node.key == "M-1")
    ctx = TabContext(tab_id="tab-1", project_id="proj", worktree=None, agent="codex", mode="execute")
    board.claim(ctx, str(module.id))
    with pytest.raises(DomainError, match="LEASE_HELD"):
        board.delete_plan(str(made.id))
    with pytest.raises(DomainError, match="LEASE_HELD"):
        board.save_document("proj", "Board", "goal", str(made.id), (_direction(),))


def test_delete_removes_the_plan_from_the_store() -> None:
    board, store = _board()
    made = board.save_document("proj", "Board", "goal", None, (_direction(),))
    board.delete_plan(str(made.id))
    assert store.plans == {}
    assert board.view(str(made.id)) is None
    with pytest.raises(DomainError, match="PLAN_NOT_FOUND"):
        board.delete_plan(str(made.id))


def test_removal_wins_over_a_pending_update() -> None:
    plan = demo_snapshot().plans[0]
    coalescer = Coalescer()
    assert coalescer.push(PlanUpdated(plan=plan)) is None
    assert isinstance(coalescer.push(PlanRemoved(plan_id=plan.id)), PlanRemoved)
    assert coalescer.flush() == ()
    store = CoreStore()
    snapshot = demo_snapshot()
    store.apply_snapshot(snapshot, 0)
    assert store.apply(EventEnvelope(seq=1, at=plan.updated_at, event=PlanRemoved(plan_id=plan.id)))
    assert store.snapshot is not None
    assert plan.id not in {item.id for item in store.snapshot.plans}


def test_plan_save_and_delete_survive_a_restart(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        saved = await client.call(
            "plan/save",
            PlanSaveParams(
                project_id=ProjectId(str(project.id)),
                title="Board",
                goal="Ship it",
                nodes=(_direction(), _module()),
            ),
            PlanView,
        )
        assert saved.status == "active"
        assert saved.phase == "draft"
        assert {node.key for node in saved.nodes} == {"D-0", "M-1"}
        assert any(plan.id == saved.id for plan in (await client.snapshot()).plans)
        replaced = await client.call(
            "plan/save",
            PlanSaveParams(
                project_id=ProjectId(str(project.id)),
                title="Board renamed",
                goal="Ship it",
                plan_id=PlanId(str(saved.id)),
                nodes=(_direction(), _module("M-2")),
            ),
            PlanView,
        )
        assert replaced.id == saved.id
        assert {node.key for node in replaced.nodes} == {"D-0", "M-2"}
        frozen = await client.call("plan/freeze", PlanIdParams(plan_id=saved.id), PlanView)
        assert frozen.phase == "frozen"
        assert (
            await client.error(
                "plan/save",
                PlanSaveParams(
                    project_id=ProjectId(str(project.id)),
                    title="Nope",
                    plan_id=saved.id,
                ),
            )
            == "PLAN_FROZEN"
        )
        await client.call("plan/delete", PlanIdParams(plan_id=saved.id), EmptyResult)
        assert await client.error("plan/get", PlanIdParams(plan_id=saved.id)) == "PLAN_NOT_FOUND"
        assert all(plan.id != saved.id for plan in (await client.snapshot()).plans)
        await _stop(daemon, task, client)

        again = _daemon(tmp_path, serve_ingest=False)
        restarted = asyncio.create_task(again.serve())
        second = await _connect(again.socket)
        assert all(plan.id != saved.id for plan in (await second.snapshot()).plans)
        await _stop(again, restarted, second)

    asyncio.run(scenario())
