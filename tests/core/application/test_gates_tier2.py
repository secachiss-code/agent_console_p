"""Blast-gate policy and real SQLite/Git integration around the bound container runner."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, override
from unittest.mock import patch

import pytest
from mcp import Client

from aconsole.core.application.services.gates import BlastGate, BlastProfile
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.mcp.server import build_server
from aconsole.core.infrastructure.mcp.tools import _ToolError
from aconsole.core.infrastructure.pipeline import PipelineStorage
from tests.core.application.test_gates_tier1 import _REQUEST as _MICRO_REQUEST
from tests.core.application.test_gates_tier1 import _Harness
from tests.core.test_mcp_pipeline import _git
from tests.core.test_mcp_pipeline import pipeline as pipeline  # noqa: PLC0414 -- shared integration fixture
from tests.core.test_mcp_tools import _error

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from aconsole.core.application.ports.gate_runner import GateResult
    from aconsole.core.infrastructure.mcp.tools import PlanBoard, TabContext, _Meta, _Stored
    from tests.core.test_mcp_pipeline import _Harness as PipelineHarness
    from tests.core.test_mcp_pipeline import _Runner

_PROFILE = BlastProfile(test_cmd="pytest tests/module_test.py", tests=("pytest",))
_REQUEST = replace(_MICRO_REQUEST, pipeline=replace(_MICRO_REQUEST.pipeline, step=None, seq=4))


class _BlastHarness(_Harness):
    @override
    def run(self, argv: tuple[str, ...], *, tier: int) -> GateResult:
        assert tier == 2
        return super().run(argv, tier=1)

    def blast(self) -> BlastGate:
        return BlastGate(bind_runner=self.bind, capture=self.capture, cache=self, audit=self)


def test_depth_order_and_cap_cache_and_read_only_probe() -> None:
    h = _BlastHarness()
    request = replace(_REQUEST, test_files=tuple(f"tests/t{i:02}.py" for i in range(40, 0, -1)))
    assert h.blast().run(request, _PROFILE, check_only=True) is None
    assert h.calls == []
    first = h.blast().run(request, _PROFILE)
    assert first is not None
    assert first.receipt.passed
    assert h.calls == [("pytest", "tests/module_test.py"), ("pytest", *request.test_files[:30])]
    h.calls.clear()
    cached = h.blast().run(request, _PROFILE, check_only=True)
    assert cached is not None
    assert cached.cached
    assert h.calls == []
    h.state = replace(h.state, state_hash="edited")
    assert h.blast().run(request, _PROFILE, check_only=True) is None


@pytest.mark.parametrize("attempt", [1, 4])
def test_failed_blast_consumes_attempt_and_replays_once(attempt: int) -> None:
    h = _BlastHarness()
    h.fail = "pytest"
    request = replace(_REQUEST, pipeline=replace(_REQUEST.pipeline, attempt=attempt))
    result = h.blast().run(request, _PROFILE)
    assert result is not None
    assert not result.receipt.passed
    assert result.exhausted == (attempt == 4)
    assert result.pipeline.attempt == (attempt + 1 if attempt < 4 else 4)
    again = h.blast().run(request, _PROFILE)
    assert again is not None
    assert again.cached
    assert len(h.audit) == 1
    assert len(h.calls) == 1


@pytest.mark.parametrize("path", ["../escape.py", "-option", "/absolute.py"])
def test_test_paths_cannot_be_options_or_escape(path: str) -> None:
    with pytest.raises(DomainError, match="NODE_CONTRACT"):
        _ = _BlastHarness().blast().run(replace(_REQUEST, test_files=(path,)), _PROFILE)


def _complete(h: PipelineHarness, number: int = 1) -> None:
    service = h.board.pipeline
    assert service is not None
    _ = service.begin(h.actor, h.node, h.fence)
    if number == 1:
        _ = service.submit(h.actor, h.node, h.fence, "spec", ())
    _ = (h.repo / "src/a.py").write_text(f"VALUE = {number + 1}\n")
    for step in ("patch", "test", "verify"):
        _ = service.submit(h.actor, h.node, h.fence, step, ())


def test_fresh_receipt_required_for_agent_and_human_completion(pipeline: PipelineHarness) -> None:
    h = pipeline
    _complete(h)
    with pytest.raises(DomainError, match="GATE_RECEIPT_REQUIRED"):
        _ = h.board.set_node_status(h.node, "done", "Reviewed")
    service = h.board.pipeline
    assert service is not None
    result = service.gate_run(h.actor, h.node, h.fence, "blast")
    assert result["passed"]
    assert PipelineStorage(h.writer).history(h.node)[-1].status == "passed"
    calls = tuple(h.runner.calls)
    assert service.gate_run(h.actor, h.node, h.fence, "blast")["cached"]
    assert tuple(h.runner.calls) == calls
    original = (h.repo / "src/a.py").read_text()
    _ = (h.repo / "src/a.py").write_text("VALUE = 999\n")
    with pytest.raises(DomainError, match="GATE_RECEIPT_REQUIRED"):
        _ = h.board.set_node_status(h.node, "done", "Reviewed")
    with pytest.raises(_ToolError, match="GATE_RECEIPT_REQUIRED"):
        _ = h.board.update(h.context(), h.node, h.fence, "done", None, "Checked")
    _ = (h.repo / "src/a.py").write_text(original)
    assert h.board.update(h.context(), h.node, h.fence, "done", None, "Checked").status == "done"


def test_verifying_runs_blast_but_stale_fence_does_no_work(pipeline: PipelineHarness) -> None:
    h = pipeline
    _complete(h)
    h.runner.calls.clear()
    with pytest.raises(_ToolError, match="LEASE_STALE_FENCE"):
        _ = h.board.update(h.context(), h.node, h.fence + 1, "verifying", None, "Checked")
    assert h.runner.calls == []
    assert h.board.update(h.context(), h.node, h.fence, "verifying", None, "Checked").status == "verifying"
    assert h.runner.calls[0] == ("pytest", "tests/test_a.py")
    assert h.board.set_node_status(h.node, "done", "Reviewed").status.value == "done"


def test_merge_runs_gate_and_durable_receipt_survives_cache_loss(pipeline: PipelineHarness) -> None:
    h = pipeline
    _complete(h)
    h.board.record_merge(node_id=h.node, tab_id="t", oid=_git(h.repo, "rev-parse", "HEAD"))
    node = h.board.export_plan(h.plan).nodes[1]
    assert node.status == "verifying"
    assert node.lease_id is None
    assert PipelineStorage(h.writer).history(h.node)[-1].status == "passed"
    h.restart()
    assert h.board.set_node_status(h.node, "done", "Reviewed merged changes").status.value == "done"


def test_four_blast_failures_block_without_fifth_attempt(pipeline: PipelineHarness) -> None:
    h = pipeline
    original = type(h.runner).run

    def fail_blast(runner: _Runner, argv: tuple[str, ...], *, tier: int) -> GateResult:
        result = original(runner, argv, tier=tier)
        return replace(result, returncode=1, output="blast failed") if tier == 2 else result

    with patch.object(type(h.runner), "run", fail_blast):
        for number in range(1, 5):
            _complete(h, number)
            assert h.board.pipeline is not None
            result = h.board.pipeline.gate_run(h.actor, h.node, h.fence, "blast")
            assert not result["passed"]
            assert result["exhausted"] == (number == 4)
    attempts = PipelineStorage(h.writer).history(h.node)
    assert len(attempts) == 4
    assert all(attempt.status == "failed" for attempt in attempts)
    assert h.board.export_plan(h.plan).nodes[1].status == "blocked"
    assert h.board.inbox(h.plan)[0].body == "ATTEMPTS_EXHAUSTED"


def test_commit_attachment_resolves_real_commit_and_refuses_unknown(pipeline: PipelineHarness) -> None:
    h = pipeline

    async def scenario() -> None:
        async with Client(build_server(h.board, h.context())) as client:
            args: dict[str, object] = {"node_id": h.node, "fence": h.fence, "kind": "commit", "ref": "missing"}
            refused = await client.call_tool("node_attach", args)
            assert _error(refused)["reason"] == "NODE_CONTRACT"
            args["ref"] = "HEAD"
            assert not (await client.call_tool("node_attach", args)).is_error

    asyncio.run(scenario())
    assert h.board.export_plan(h.plan).nodes[1].artifacts[0].ref == _git(h.repo, "rev-parse", "HEAD")


def test_cte_selects_three_hops_by_depth_and_caps_at_thirty(pipeline: PipelineHarness) -> None:
    from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex
    from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, GraphEdge, GraphNode
    from aconsole.core.infrastructure.codegraph.runner import ExtractorRunner
    from aconsole.core.infrastructure.persistence.repositories.code_graph import CodeGraphDatabase
    from aconsole.core.infrastructure.pipeline import PipelineGit

    h = pipeline
    _complete(h)
    attempt = PipelineStorage(h.writer).history(h.node)[-1]
    paths = (
        "src/a.py",
        "src/b.py",
        "src/c.py",
        "src/d.py",
        "tests/near.py",
        "tests/mid.py",
        "tests/far.py",
        "tests/out.py",
    )
    nodes = tuple(GraphNode(id=path, path=path, name=path, kind="module") for path in paths)
    edges = tuple(
        GraphEdge(source=source, target=target, kind="imports")
        for source, target in (
            ("src/b.py", "src/a.py"),
            ("src/c.py", "src/b.py"),
            ("src/d.py", "src/c.py"),
            ("tests/near.py", "src/a.py"),
            ("tests/mid.py", "src/b.py"),
            ("tests/far.py", "src/c.py"),
            ("tests/out.py", "src/d.py"),
        )
    )
    graph = ExtractorGraph(version=1, nodes=nodes, edges=edges)
    index = CodeGraphIndex(ExtractorRunner(), lambda _p: h.repo, store=CodeGraphDatabase(h.writer))
    with patch.object(CodeGraphIndex, "_build", return_value=(graph, ())):
        assert PipelineGit(index).blast_tests(h.actor, attempt) == ("tests/near.py", "tests/mid.py", "tests/far.py")
    direct = tuple(f"tests/t{i:02}.py" for i in range(35))
    graph = ExtractorGraph(
        version=1,
        nodes=(*nodes, *(GraphNode(id=path, path=path, name=path, kind="module") for path in direct)),
        edges=(*edges, *(GraphEdge(source=path, target="src/a.py", kind="imports") for path in direct)),
    )
    index = CodeGraphIndex(ExtractorRunner(), lambda _p: h.repo, store=CodeGraphDatabase(h.writer))
    with patch.object(CodeGraphIndex, "_build", return_value=(graph, ())):
        chosen = PipelineGit(index).blast_tests(h.actor, attempt)
    assert len(chosen) == 30
    assert chosen == ("tests/near.py", *direct[:29])


def test_changes_during_blast_cannot_produce_green_receipt() -> None:
    h = _BlastHarness()
    original = h.run

    def moving(argv: tuple[str, ...], *, tier: int) -> GateResult:
        result = original(argv, tier=tier)
        h.state = replace(h.state, state_hash="modified-during-tests")
        return result

    with patch.object(h, "run", side_effect=moving):
        result = h.blast().run(_REQUEST, _PROFILE)
    assert result is not None
    assert not result.receipt.passed
    assert "Workspace changed" in result.receipt.compact_verdict
    assert h.blast().run(_REQUEST, _PROFILE, check_only=True) is None


def test_blast_writer_rechecks_lease_after_runner_returns(pipeline: PipelineHarness) -> None:
    from sqlalchemy import select

    from aconsole.core.infrastructure.persistence.models import GateReceipt, Lease

    h = pipeline
    _complete(h)
    original = type(h.runner).run

    def revoke(session: Session) -> None:
        lease = session.get(Lease, h.node)
        assert lease is not None
        session.delete(lease)

    def interrupted(runner: _Runner, argv: tuple[str, ...], *, tier: int) -> GateResult:
        result = original(runner, argv, tier=tier)
        if tier == 2:
            _ = h.writer.call(revoke)
        return result

    assert h.board.pipeline is not None
    with patch.object(type(h.runner), "run", interrupted), pytest.raises(DomainError, match="LEASE_EXPIRED"):
        _ = h.board.pipeline.gate_run(h.actor, h.node, h.fence, "blast")
    storage = PipelineStorage(h.writer)
    assert storage.history(h.node)[-1].status == "running"
    assert (
        h.writer.call(
            lambda session: session.scalar(
                select(GateReceipt.receipt_hash).where(
                    GateReceipt.node_id == h.node,
                    GateReceipt.tier == "blast",
                )
            )
        )
        is None
    )


def test_running_gate_does_not_hold_global_board_lock(pipeline: PipelineHarness) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    h = pipeline
    _complete(h)
    assert h.board.pipeline is not None
    entered, release, queued = Event(), Event(), Event()
    original_run = type(h.runner).run
    original_scope = type(h.board)._verify_scope

    def held(runner: _Runner, argv: tuple[str, ...], *, tier: int) -> GateResult:
        if tier == 2:
            entered.set()
            assert release.wait(10)
        return original_run(runner, argv, tier=tier)

    def scoped(board: PlanBoard, ctx: TabContext, stored: _Stored, meta: _Meta) -> None:
        original_scope(board, ctx, stored, meta)
        if entered.is_set():
            queued.set()

    with (
        patch.object(type(h.runner), "run", held),
        patch.object(type(h.board), "_verify_scope", scoped),
        ThreadPoolExecutor(max_workers=3) as pool,
    ):
        gate = pool.submit(h.board.pipeline.gate_run, h.actor, h.node, h.fence, "blast")
        try:
            assert entered.wait(10)
            update = pool.submit(h.board.update, h.context(), h.node, h.fence, "verifying", None, "Checked")
            assert queued.wait(10)
            view = pool.submit(h.board.view, h.plan).result(timeout=5)
            assert view is not None
        finally:
            release.set()
        assert gate.result(timeout=10)["passed"]
        assert update.result(timeout=10).status == "verifying"
