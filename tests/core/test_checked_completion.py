"""Checked completion frees verified work without approving unfinished work."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from aconsole.contracts.app import (
    PlanIdParams,
    PlanView,
    ProjectAddParams,
    ProjectView,
    TabIdParams,
    TabView,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import PlanId
from aconsole.core.application.ports.plan_store import StoredPlan, StoreWriteError
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.plan import Move, NodeSpec, NodeStatus, Plan, Role
from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext
from tests.core.test_claude_mcp import _config, _content, _Mcp
from tests.core.test_mcp_tools import _Clock
from tests.core.test_wire import _connect, _daemon, _git_repo, _open, _stop

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("verify_first", [False, True])
@pytest.mark.parametrize("evidence", [None, "hostrun test core: PASS"])
def test_checked_completion_requires_fresh_evidence(*, verify_first: bool, evidence: str | None) -> None:
    clock = _Clock()
    plan = Plan(checked_completion=True)
    plan.add(NodeSpec(key="A-1", created_at=clock(), review_required=True, paths=("src/a.py",)))
    plan.add(NodeSpec(key="B-1", created_at=clock(), depends_on=("A-1",), paths=("src/a.py",)))
    claimed = plan.claim("A-1", tab_id="tab-a", at=clock())
    if verify_first:
        plan.transition(Move(
            key="A-1", to=NodeStatus.VERIFYING, role=Role.AGENT, at=clock(),
            tab_id="tab-a", fence=claimed.fence, evidence="build started",
        ))
    move = Move(
        key="A-1", to=NodeStatus.DONE, role=Role.AGENT, at=clock(),
        tab_id="tab-a", fence=claimed.fence, evidence=evidence,
    )
    if evidence is None:
        with pytest.raises(DomainError, match="NODE_EVIDENCE_REQUIRED"):
            plan.transition(move)
        assert not plan.ready("B-1")
    else:
        done = plan.transition(move)
        assert done.status is NodeStatus.DONE
        assert done.evidence == evidence
        assert done.tab_id is None
        assert plan.ready("B-1")


def test_checked_completion_expired_verification_returns_for_rechecking() -> None:
    clock = _Clock()
    plan = Plan(checked_completion=True)
    plan.add(NodeSpec(key="A-1", created_at=clock(), review_required=True))
    claimed = plan.claim("A-1", tab_id="tab-a", at=clock())
    plan.transition(Move(
        key="A-1", to=NodeStatus.VERIFYING, role=Role.AGENT, at=clock(),
        tab_id="tab-a", fence=claimed.fence, evidence="old result",
    ))
    clock.moment += timedelta(minutes=16)
    assert plan.expire_leases(clock()) == ("A-1",)
    assert plan.get("A-1").status is NodeStatus.PENDING
    assert plan.get("A-1").evidence == "old result"
    assert plan.get("A-1").tab_id is None
    again = plan.claim("A-1", tab_id="tab-b", at=clock())
    assert again.fence == claimed.fence + 1
    with pytest.raises(DomainError, match="LEASE_STALE_FENCE"):
        plan.transition(Move(
            key="A-1", to=NodeStatus.DONE, role=Role.AGENT, at=clock(),
            tab_id="tab-a", fence=claimed.fence, evidence="stale",
        ))


def test_explicit_manual_policy_keeps_review_and_its_lease() -> None:
    clock = _Clock()
    plan = Plan(checked_completion=False)
    plan.add(NodeSpec(key="A-1", created_at=clock(), review_required=True))
    claimed = plan.claim("A-1", tab_id="tab-a", at=clock())
    plan.transition(Move(
        key="A-1", to=NodeStatus.VERIFYING, role=Role.AGENT, at=clock(),
        fence=claimed.fence, evidence="tests pass",
    ))
    clock.moment += timedelta(minutes=16)
    assert plan.expire_leases(clock()) == ()
    with pytest.raises(DomainError, match="NODE_TRANSITION_INVALID"):
        plan.transition(Move(
            key="A-1", to=NodeStatus.DONE, role=Role.AGENT, at=clock(),
            fence=claimed.fence, evidence="tests pass",
        ))


class _Store:
    def __init__(self) -> None:
        self.saved: StoredPlan | None = None
        self.refuse = False

    def load(self) -> tuple[StoredPlan, ...]:
        return () if self.saved is None else (self.saved,)

    def save(self, plan: StoredPlan) -> None:
        if self.refuse:
            message = "disk refused"
            raise StoreWriteError(message)
        self.saved = plan


def _context(tab: str) -> TabContext:
    return TabContext(tab_id=tab, project_id="proj", worktree=None, agent="codex")


def test_checked_completion_restores_and_releases_old_verifying_paths() -> None:
    clock = _Clock()
    store = _Store()
    original = PlanBoard(now=clock)
    original.use_store(store)
    made = original.create(_context("tab-a"), "Restored", "goal", [
        NodeIn(key="A-1", title="First", objective="do", review_required=True, paths=["src/a.py"]),
        NodeIn(key="B-1", title="Second", objective="do", paths=["src/a.py"]),
    ])
    node_id = made.nodes[0].node_id
    claim = original.claim(_context("tab-a"), node_id)
    original.update(_context("tab-a"), node_id, claim.fence, "verifying", None, "old result")
    clock.moment += timedelta(minutes=16)
    checked = PlanBoard(now=clock, checked_completion=True)
    checked.use_store(store)
    ready = checked.ready(_context("tab-b"), made.plan_id, 20)
    assert {node.key for node in ready.nodes} == {"A-1", "B-1"}
    assert checked.get(_context("tab-b"), made.plan_id, detailed=True).counts["done"] == 0
    assert store.saved is not None
    assert store.saved.nodes[0].status == "pending"
    assert store.saved.nodes[0].evidence == "old result"


def test_checked_completion_survives_failed_write_rollback() -> None:
    clock = _Clock()
    store = _Store()
    board = PlanBoard(now=clock, checked_completion=True)
    board.use_store(store)
    made = board.create(_context("tab-a"), "Rollback", "goal", [
        NodeIn(key="A-1", title="First", objective="do", review_required=True),
    ])
    node_id = made.nodes[0].node_id
    claim = board.claim(_context("tab-a"), node_id)
    store.refuse = True
    with pytest.raises(Exception, match="disk refused"):
        board.update(_context("tab-a"), node_id, claim.fence, "done", None, "tests pass")
    assert board.views()[0].done == 0
    store.refuse = False
    done = board.update(_context("tab-a"), node_id, claim.fence, "done", None, "tests pass")
    assert done.status == "done"
    assert store.saved is not None
    assert store.saved.nodes[0].status == "done"


def test_daemon_checked_completion_updates_live_plan_count_and_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FAKE_HOME", str(home))
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:
        daemon = _daemon(tmp_path)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            tab = await client.call("tab/open", _open(project.id), TabView)
            spec = await client.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
            _path, config = _config(spec.argv)
            session = _Mcp(config, spec.env)
            await asyncio.to_thread(session.start)

            async def call(name: str, arguments: dict[str, object]) -> dict[str, object]:
                result = await asyncio.to_thread(session.request, "tools/call", {"name": name, "arguments": arguments})
                assert not result.get("isError", False), result
                return _content(result)

            made = await call("plan_create", {"title": "Checked", "goal": "goal", "nodes": [{
                "key": "A-1", "title": "First", "objective": "do", "review_required": True,
            }]})
            plan_id = made["plan_id"]
            assert isinstance(plan_id, str)
            nodes = made["nodes"]
            assert isinstance(nodes, list)
            assert isinstance(nodes[0], dict)
            node_id = nodes[0]["node_id"]
            claim = await call("node_claim", {"node_id": node_id})
            await call("node_update", {
                "node_id": node_id, "fence": claim["fence"], "status": "done", "evidence": "mandatory checks PASS",
            })
            view = await client.call("plan/get", PlanIdParams(plan_id=PlanId(plan_id)), PlanView)
            assert (view.done, view.total) == (1, 1)
            assert view.nodes[0].status.value == "done"
        finally:
            await _stop(daemon, task, client)
        again = _daemon(tmp_path, serve_ingest=False)
        second_task = asyncio.create_task(again.serve())
        second_client = await _connect(again.socket)
        try:
            kept = await second_client.call("plan/get", PlanIdParams(plan_id=PlanId(plan_id)), PlanView)
            assert (kept.done, kept.total) == (1, 1)
        finally:
            await _stop(again, second_task, second_client)

    asyncio.run(scenario())
