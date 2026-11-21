"""Frozen scope repair is explicit, versioned, atomic and never revives old receipts."""

from dataclasses import replace
from datetime import UTC, datetime
lazy from pathlib import Path

import pytest
lazy from sqlalchemy.orm import Session

from aconsole.core.application.services.plan_lifecycle import freeze_plan, repair_plan_paths
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext
from tests.core.test_plan_crud import _Memory
from tests.gate_profiles import MICRO_PROFILE
lazy from aconsole.core.application.ports.plan_store import StoredPlan

_AT = datetime(2026, 11, 18, tzinfo=UTC)


def _saved() -> StoredPlan:
    board = PlanBoard(now=lambda: _AT)
    ctx = TabContext(tab_id="planner", project_id="p", worktree=None, agent="codex", mode="plan")
    made = board.create(
        ctx,
        "Frozen scopes",
        "Goal",
        [
            NodeIn(
                key="IDX-0",
                title="Index direction",
                objective="Index",
                level=0,
                paths=["tools/rsgraph", "src/**"],
                invariants="Stable",
                acceptance=["all modules"],
                gate_profile=MICRO_PROFILE,
            ),
            NodeIn(
                key="IDX-3",
                title="Rust modules",
                objective="Extract",
                parent="IDX-0",
                pipeline=True,
                paths=["tools/rsgraph"],
                acceptance=["real imports"],
                test_cmd="pytest",
            ),
            NodeIn(
                key="IDX-1",
                title="Merged graphs",
                objective="Merge",
                parent="IDX-0",
                pipeline=True,
                paths=["src/a.py"],
                acceptance=["all languages"],
                test_cmd="pytest",
            ),
        ],
    )
    saved = freeze_plan(board.export_plan(made.plan_id), {}, at=_AT, graph_present=False)
    return replace(saved, nodes=tuple(replace(n, status="done") if n.key == "IDX-1" else n for n in saved.nodes))


def _repair(saved: StoredPlan) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    paths = {"IDX-0": ("tools/rsgraph/**", "src/**"), "IDX-3": ("tools/rsgraph/**",)}
    hashes = {n.key: n.contract_sha256 for n in saved.nodes if n.key in paths and n.contract_sha256 is not None}
    return paths, hashes


def test_repair_binds_new_scopes_without_changing_completed_siblings_or_topology() -> None:
    before = _saved()
    paths, hashes = _repair(before)
    after = repair_plan_paths(before, paths, hashes, at=_AT)
    assert after.nodes[2] == before.nodes[2]
    assert after.nodes[1].contract_sha256 != before.nodes[1].contract_sha256
    assert after.topology.phase == "frozen"
    assert after.topology.revision_no == before.topology.revision_no + 1
    assert after.topology.baseline_nodes == before.topology.baseline_nodes
    assert after.topology.gross_added == before.topology.gross_added
    assert [n.id for n in after.nodes] == [n.id for n in before.nodes]


@pytest.mark.parametrize("case", ["stale", "lease", "completed", "outside", "missing_parent", "empty"])
def test_invalid_repair_never_changes_the_original(case: str) -> None:
    before = _saved()
    paths, hashes = _repair(before)
    if case == "stale":
        hashes["IDX-3"] = "0" * 64
    elif case == "lease":
        before = replace(before, nodes=(replace(before.nodes[0], lease_id="held"), *before.nodes[1:]))
    elif case == "completed":
        before = replace(before, nodes=(before.nodes[0], replace(before.nodes[1], status="done"), before.nodes[2]))
    elif case == "outside":
        paths["IDX-3"] = ("../outside",)
    elif case == "missing_parent":
        del paths["IDX-0"]
        del hashes["IDX-0"]
    else:
        paths["IDX-3"] = ()
    with pytest.raises(DomainError):
        repair_plan_paths(before, paths, hashes, at=_AT)


def test_public_save_requires_reason_and_restores_repaired_contract_after_reload() -> None:
    saved = _saved()
    store = _Memory()
    store.save(saved)
    board = PlanBoard(now=lambda: _AT)
    board.use_store(store)
    paths, hashes = _repair(saved)
    with pytest.raises(DomainError):
        board.save_document("p", saved.title, saved.goal, saved.id, None, repair_paths=paths, repair_contracts=hashes)
    assert board.export_plan(saved.id) == saved
    board.save_document(
        "p",
        saved.title,
        saved.goal,
        saved.id,
        None,
        repair_paths=paths,
        repair_contracts=hashes,
        repair_note="Directory glob omitted at planning",
    )
    again = PlanBoard(now=lambda: _AT)
    again.use_store(store)
    assert again.export_plan(saved.id) == board.export_plan(saved.id)


def test_scope_repair_public_ipc_updates_snapshot_and_persists_human_audit(tmp_path: Path) -> None:
    import asyncio

    from aconsole.contracts.app import PlanSaveParams, PlanView, ProjectAddParams, ProjectView
    from aconsole.contracts.common import PlanId
    from aconsole.core.infrastructure.persistence.models import PlanInbox
    from aconsole.core.infrastructure.persistence.repositories.plans import PlanDatabase
    from tests.core.test_wire import _connect, _daemon, _git_repo, _stop

    async def scenario() -> None:
        repo = _git_repo(tmp_path / "repo")
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            daemon._plans._now = lambda: _AT
            writer = daemon._writer
            assert writer is not None
            saved = replace(_saved(), project_id=str(project.id))
            database = PlanDatabase(writer)
            database.save(saved)
            daemon._plans.use_store(database)
            paths, hashes = _repair(saved)
            result = await client.call(
                "plan/save",
                PlanSaveParams(
                    project_id=project.id,
                    title=saved.title,
                    goal=saved.goal,
                    plan_id=PlanId(saved.id),
                    repair_paths=paths,
                    repair_contracts=hashes,
                    repair_note="Fix omitted directory glob",
                ),
                PlanView,
            )
            assert result.nodes[1].status.value == "pending"
            snapshot = await client.snapshot()
            assert next(p for p in snapshot.plans if p.id == result.id) == result

            def audit(session: Session) -> list[dict[str, object]]:
                rows = session.query(PlanInbox).filter_by(plan_id=saved.id, kind="human").all()
                return [row.payload for row in rows if row.payload.get("decision") == "repair_paths"]

            records = writer.call(audit)
            assert len(records) == 1
            assert records[0]["decision"] == "repair_paths"
            assert "Fix omitted directory glob" in records[0]["decision_note"]
            reloaded = PlanBoard(now=lambda: _AT)
            reloaded.use_store(database)
            assert reloaded.export_plan(saved.id) == daemon._plans.export_plan(saved.id)
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())
