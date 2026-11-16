"""Real Git, SQLite, disposable cache and MCP around a recording container runner."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
lazy from collections.abc import Iterator
lazy from pathlib import Path

import pytest
from mcp import Client
lazy from sqlalchemy import Engine

from aconsole.contracts.common import NodeId, PlanId
from aconsole.contracts.events import PlanAttention
from aconsole.core.application.ports.gate_runner import GateResult, GateRunnerUnavailableError
from aconsole.core.application.services.context_shaper import ContextShaper
from aconsole.core.application.services.pipeline import PipelineActor, PipelineContract, PipelineService
from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex
from aconsole.core.infrastructure.codegraph.runner import ExtractorRunner
from aconsole.core.infrastructure.ipc.ring import EventRing
from aconsole.core.infrastructure.ipc.session import Connection
from aconsole.core.infrastructure.mcp.server import build_server
from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.cache.store import CacheStore
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.repositories.attempts import AttemptDatabase
from aconsole.core.infrastructure.persistence.repositories.code_graph import CodeGraphDatabase
from aconsole.core.infrastructure.persistence.repositories.plans import PlanDatabase
from aconsole.core.infrastructure.persistence.repositories.tabs import TabDatabase
from aconsole.core.infrastructure.persistence.writer import Writer
from aconsole.core.infrastructure.pipeline import MicroRuntime, PipelineCache, PipelineGit, PipelineStorage
from tests.core.test_ipc_a import _init, _subscribe
from tests.core.test_mcp_tools import _call, _error
from tests.core.test_tabs_restore import _row
lazy from aconsole.core.domain.pipeline import StateBlock

_AT = datetime(2026, 11, 16, tzinfo=UTC)
_PROFILE = {
    "micro": {
        "format": ["ruff", "format"],
        "fix": ["ruff", "check", "--fix"],
        "lint": ["ruff", "check"],
        "types": ["basedpyright"],
        "tests": ["pytest"],
    }
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(("git", "-C", str(repo), *args), check=True, capture_output=True, text=True).stdout.strip()


@dataclass
class _Runner:
    repo: Path
    fail: bool = False
    unavailable: bool = False
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(self, argv: tuple[str, ...], *, tier: int) -> GateResult:
        assert tier in {1, 2}
        if self.unavailable:
            raise GateRunnerUnavailableError
        self.calls.append(argv)
        if argv[:2] == ("ruff", "format"):
            for name in argv[2:]:
                path = self.repo / name
                text = path.read_text()
                if not text.endswith("\n\n"):
                    path.write_text(text + "\n")
        failed = self.fail and argv[:2] == ("ruff", "check") and "--fix" not in argv
        return GateResult(
            argv=argv,
            returncode=1 if failed else 0,
            timed_out=False,
            output="lint failed" if failed else "tool version 1",
            log_path="recording-runner.log",
            duration_ms=1,
        )


@dataclass
class _Harness:
    repo: Path
    url: str
    engine: Engine
    writer: Writer
    board: PlanBoard
    actor: PipelineActor
    runner: _Runner
    node: str
    plan: str
    fence: int
    cache: CacheStore | None = None

    def attach(self) -> None:
        self.board.use_attempts(AttemptDatabase(self.writer))
        self.cache = CacheStore.open(None)
        cache = PipelineCache(self.cache)
        store = PipelineStorage(self.writer, now=lambda: _AT)
        graph = CodeGraphIndex(ExtractorRunner(), lambda _p: self.repo, store=CodeGraphDatabase(self.writer))
        workspace = PipelineGit(graph)
        runtime = MicroRuntime(store, cache, workspace, lambda _actor, _commands: self.runner)
        workspace.versions = runtime.versions
        self.board.pipeline = PipelineService(
            book=self.board,
            store=store,
            workspace=workspace,
            scratch=cache,
            shaper=ContextShaper(cache),
            micro=runtime.run,
            blast=runtime.blast,
        )
        self.board.pipeline_actor = lambda _tab: self.actor

    def context(self) -> TabContext:
        return TabContext(
            tab_id=self.actor.tab_id, project_id="p", worktree=self.repo, agent="codex", mode=self.actor.mode
        )

    def restart(self) -> None:
        assert self.cache is not None
        self.cache.close()
        self.writer.stop()
        self.engine.dispose()
        self.engine = write_engine(self.url)
        self.writer = Writer(self.engine)
        self.writer.start()
        self.board = PlanBoard(now=lambda: _AT, checked_completion=True)
        self.board.use_store(PlanDatabase(self.writer))
        self.attach()


@pytest.fixture
def pipeline(tmp_path: Path) -> Iterator[_Harness]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for name, body in (("src/a.py", "VALUE = 1\n"), ("tests/test_a.py", "from src.a import VALUE\n")):
        path = repo / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(body)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    url = f"sqlite:///{tmp_path / 'state.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    writer = Writer(engine)
    writer.start()
    TabDatabase(writer).save(
        replace(
            _row("t", title="Pipeline"),
            project_id="p",
            project_root=str(repo),
            worktree=str(repo),
            session=None,
            plan_node=None,
            workspace_mode="main_checkout",
        )
    )
    board = PlanBoard(now=lambda: _AT, checked_completion=True)
    board.use_store(PlanDatabase(writer))
    ctx = TabContext(tab_id="t", project_id="p", worktree=repo, agent="codex")
    created = board.create(
        ctx,
        "Pipeline",
        "Goal",
        [
            NodeIn(
                key="D-0",
                title="Direction",
                objective="Scope",
                level=0,
                paths=["src/**", "tests/**"],
                invariants="pure",
                gate_profile=_PROFILE,
            ),
            NodeIn(
                key="C-1",
                title="Contract",
                objective="Change",
                parent="D-0",
                pipeline=True,
                paths=["src/**", "tests/test_a.py"],
                acceptance=["green"],
                test_cmd="pytest tests/test_a.py",
            ),
        ],
    )
    board.freeze(created.plan_id, {"C-1": ()}, graph_present=True)
    node = created.nodes[1].node_id
    claim = board.claim(ctx, node)
    actor = PipelineActor(
        tab_id="t",
        project_id="p",
        worktree=repo,
        agent="codex",
        agent_session_id="native-session",
        container="recording-container",
    )
    harness = _Harness(
        repo=repo,
        url=url,
        engine=engine,
        writer=writer,
        board=board,
        actor=actor,
        runner=_Runner(repo),
        node=node,
        plan=created.plan_id,
        fence=claim.fence,
    )
    harness.attach()
    try:
        yield harness
    finally:
        if harness.cache is not None:
            harness.cache.close()
        harness.writer.stop()
        harness.engine.dispose()


def test_full_chain_replays_and_stale_fence_is_rejected(pipeline: _Harness) -> None:
    async def scenario() -> None:
        async with Client(build_server(pipeline.board, pipeline.context())) as client:
            params = {"node_id": pipeline.node, "fence": pipeline.fence}
            first = await _call(client, "step_begin", params)
            assert "direction_prefix" in first
            assert list(first)[-3:] == ["lease_id", "fence", "expires_at"]
            again = await _call(client, "step_begin", params)
            assert "direction_prefix" not in again
            assert again["prefix_sha256"] == first["prefix_sha256"]
            assert again["contract"] == first["contract"]
            for step in ("spec", "patch", "test", "verify"):
                if step == "patch":
                    (pipeline.repo / "src/a.py").write_text("VALUE = 2\n")
                if step == "test":
                    (pipeline.repo / "tests/test_a.py").write_text("from src.a import VALUE\nassert VALUE == 2\n")
                submitted = await _call(client, "step_submit", {**params, "step": step})
                calls = len(pipeline.runner.calls)
                assert await _call(client, "step_submit", {**params, "step": step}) == submitted
                assert len(pipeline.runner.calls) == calls
            assert submitted["next_step"] is None
            assert submitted["receipt_hash"]
            assert (pipeline.repo / "src/a.py").read_text().endswith("\n\n")
            gated = await _call(client, "gate_run", {**params, "tier": "micro"})
            assert gated["receipt_hash"] == submitted["receipt_hash"]
            stale = await client.call_tool("step_submit", {**params, "fence": 0, "step": "verify"})
            assert _error(stale)["reason"] == "LEASE_STALE_FENCE"
            refused = await client.call_tool(
                "node_update", {**params, "status": "done", "evidence": "agent says green"}
            )
            assert _error(refused)["reason"] == "GATE_RECEIPT_REQUIRED"
        history = PipelineStorage(pipeline.writer).history(pipeline.node)
        assert len(history) == 1
        assert history[0].status == "running", "Tier 2 still has to accept this attempt"
        blocks = PipelineStorage(pipeline.writer).blocks(history[0].id)
        assert [block.step_type for block in blocks] == ["spec", "patch", "test", "verify"]
        assert blocks[-1].delta_sha256 != blocks[-2].delta_sha256
        assert len(pipeline.board.export_plan(pipeline.plan).nodes) == 2
        assert _git(pipeline.repo, "diff", "--cached") == ""
        assert any(command[0] == "pytest" and "--version" not in command for command in pipeline.runner.calls)

    asyncio.run(scenario())


def test_cache_loss_and_core_restart_resume_durable_chain(pipeline: _Harness) -> None:
    params = {"node_id": pipeline.node, "fence": pipeline.fence}

    async def first() -> str:
        async with Client(build_server(pipeline.board, pipeline.context())) as client:
            await _call(client, "step_begin", params)
            return str((await _call(client, "step_submit", {**params, "step": "spec"}))["block_hash"])

    digest = asyncio.run(first())
    pipeline.restart()

    async def second() -> None:
        async with Client(build_server(pipeline.board, pipeline.context())) as client:
            begun = await _call(client, "step_begin", params)
            assert "direction_prefix" in begun
            replay = await _call(client, "step_submit", {**params, "step": "spec"})
            assert replay["block_hash"] == digest
            assert replay["attempt"] == 1
            assert replay["next_step"] == "patch"

    asyncio.run(second())
    assert len(PipelineStorage(pipeline.writer).history(pipeline.node)) == 1


@pytest.mark.parametrize("outside", ["outside.py", "tests/foreign.py"])
def test_outside_scope_never_advances_and_test_edit_requires_review(pipeline: _Harness, outside: str) -> None:
    async def scenario() -> None:
        async with Client(build_server(pipeline.board, pipeline.context())) as client:
            params = {"node_id": pipeline.node, "fence": pipeline.fence}
            await _call(client, "step_begin", params)
            (pipeline.repo / outside).write_text("outside\n")
            denied = await client.call_tool("step_submit", {**params, "step": "spec"})
            assert _error(denied)["reason"] == "SCOPE_VIOLATION"

    asyncio.run(scenario())
    record = PipelineStorage(pipeline.writer).history(pipeline.node)[0]
    assert not PipelineStorage(pipeline.writer).blocks(record.id)
    node = PlanDatabase(pipeline.writer).load()[0].nodes[1]
    assert node.review_required == outside.startswith("tests/")


def test_reset_restores_only_scope_and_preserves_green_snapshot(pipeline: _Harness) -> None:
    async def scenario() -> None:
        async with Client(build_server(pipeline.board, pipeline.context())) as client:
            params = {"node_id": pipeline.node, "fence": pipeline.fence}
            await _call(client, "step_begin", params)
            for step in ("spec", "patch", "test", "verify"):
                await _call(client, "step_submit", {**params, "step": step})
            good = (pipeline.repo / "src/a.py").read_text()
            (pipeline.repo / "src/a.py").write_text("changed\n")
            (pipeline.repo / "src/new.py").write_text("new\n")
            (pipeline.repo / "outside.txt").write_text("keep me\n")
            reset = await _call(client, "step_reset", params)
            assert (pipeline.repo / "src/a.py").read_text() == good
            assert not (pipeline.repo / "src/new.py").exists()
            assert (pipeline.repo / "outside.txt").read_text() == "keep me\n"
            assert _git(pipeline.repo, "show", str(reset["reset_to"]) + ":src/a.py") == good.strip()
            assert _git(pipeline.repo, "diff", "--cached") == ""

    asyncio.run(scenario())


def test_failed_micro_gate_consumes_durable_budget_without_fifth_attempt(pipeline: _Harness) -> None:
    pipeline.runner.fail = True
    attention: list[tuple[str, str, str, str]] = []
    pipeline.board.on_attention = lambda plan, node, kind, reason: attention.append((plan, node, kind, reason))

    async def scenario() -> None:
        async with Client(build_server(pipeline.board, pipeline.context())) as client:
            params = {"node_id": pipeline.node, "fence": pipeline.fence}
            for attempt in range(1, 5):
                await _call(client, "step_begin", params)
                if attempt == 1:
                    await _call(client, "step_submit", {**params, "step": "spec"})
                (pipeline.repo / "src/a.py").write_text(f"VALUE = {attempt + 1}\n")
                for step in ("patch", "test"):
                    await _call(client, "step_submit", {**params, "step": step})
                failed = await _call(client, "step_submit", {**params, "step": "verify"})
                assert failed["passed"] is False
                assert failed["exhausted"] == (attempt == 4)
            denied = await client.call_tool("step_begin", params)
            assert _error(denied)["reason"] == "ATTEMPTS_EXHAUSTED"

    asyncio.run(scenario())
    store = PipelineStorage(pipeline.writer)
    history = store.history(pipeline.node)
    assert len(history) == 4
    assert all(attempt.status == "failed" for attempt in history)
    for previous, following in pairwise(history):
        assert store.blocks(following.id)[0].prev_block_hash == previous.head_block
    state = pipeline.board.export_plan(pipeline.plan)
    assert state.nodes[1].status == "blocked"
    assert len(state.nodes) == 2
    assert attention == [(pipeline.plan, pipeline.node, "replan", "ATTEMPTS_EXHAUSTED")]
    inbox = pipeline.board.inbox(pipeline.plan)
    assert len(inbox) == 1
    assert inbox[0].body == "ATTEMPTS_EXHAUSTED"
    assert inbox[0].payload["kind"] == "attempts"
    assert "VALUE = 5" in (pipeline.repo / "src/a.py").read_text()


def test_pipeline_merge_waits_for_gate_without_expiring_or_requeueing(pipeline: _Harness) -> None:
    attention: list[tuple[str, str, str, str]] = []
    pipeline.board.on_attention = lambda plan, node, kind, reason: attention.append((plan, node, kind, reason))
    pipeline.board.record_merge(node_id=pipeline.node, tab_id="t", oid=_git(pipeline.repo, "rev-parse", "HEAD"))
    state = pipeline.board.export_plan(pipeline.plan)
    assert state.nodes[1].status == "verifying"
    assert state.nodes[1].lease_id is None
    assert len(attention) == 1
    pipeline.restart()
    pipeline.board._now = lambda: _AT + timedelta(days=7)
    assert pipeline.board.ready(pipeline.context(), pipeline.plan, 20).nodes == []
    assert pipeline.board.export_plan(pipeline.plan).nodes[1].status == "verifying"


def test_planning_tab_can_read_spec_but_cannot_submit_steps(pipeline: _Harness) -> None:
    pipeline.actor = replace(pipeline.actor, mode="plan")

    async def scenario() -> None:
        server = build_server(pipeline.board, pipeline.context())
        names = {tool.name for tool in await server.list_tools()}
        assert "step_begin" in names
        assert not names.intersection({"step_submit", "step_reset", "gate_run"})
        denied = await server.call_tool(
            "step_submit", {"node_id": pipeline.node, "fence": pipeline.fence, "step": "spec"}
        )
        assert "PLAN_ROLE" in str(denied)
        assert not PipelineStorage(pipeline.writer).history(pipeline.node)

    asyncio.run(scenario())


@pytest.mark.parametrize("capable", [False, True])
def test_attention_is_sent_only_to_clients_negotiating_hdag(*, capable: bool) -> None:
    ring = EventRing()
    connection = Connection(ring, host_namespace=True)
    capabilities = frozenset({"events.v1", "hdag.v1"}) if capable else frozenset({"events.v1"})
    connection.receive(_init(caps=capabilities))
    connection.receive(_subscribe(2, 0))
    event = PlanAttention(plan_id=PlanId("p"), node_id=NodeId("n"), kind="verify", reason="Gate required")
    frames = connection.deliver(ring.append(event, _AT))
    assert bool(frames) is capable


def test_unavailable_container_does_not_consume_an_attempt(pipeline: _Harness) -> None:
    async def scenario() -> None:
        async with Client(build_server(pipeline.board, pipeline.context())) as client:
            params = {"node_id": pipeline.node, "fence": pipeline.fence}
            await _call(client, "step_begin", params)
            pipeline.runner.unavailable = True
            unavailable = await client.call_tool("step_submit", {**params, "step": "spec"})
            assert _error(unavailable)["reason"] == "GATE_RUNNER_UNAVAILABLE"
            store = PipelineStorage(pipeline.writer)
            records = store.history(pipeline.node)
            assert len(records) == 1
            assert records[0].status == "running"
            assert not store.blocks(records[0].id)
            pipeline.runner.unavailable = False
            submitted = await _call(client, "step_submit", {**params, "step": "spec", "note": "Specification ready"})
            assert submitted["attempt"] == 1
            assert pipeline.board.export_plan(pipeline.plan).nodes[1].note == "Specification ready"

    asyncio.run(scenario())


def test_writer_rejects_lease_reclaimed_after_step_authorization(
    pipeline: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = PipelineStorage.append

    def racing_append(
        storage: PipelineStorage,
        attempt_id: str,
        block: StateBlock,
        *,
        actor: PipelineActor,
        contract: PipelineContract,
    ) -> str:
        pipeline.board.update(pipeline.context(), pipeline.node, pipeline.fence, "pending", None, None)
        pipeline.board.claim(pipeline.context(), pipeline.node)
        return original(storage, attempt_id, block, actor=actor, contract=contract)

    monkeypatch.setattr(PipelineStorage, "append", racing_append)

    async def scenario() -> None:
        async with Client(build_server(pipeline.board, pipeline.context())) as client:
            params = {"node_id": pipeline.node, "fence": pipeline.fence}
            await _call(client, "step_begin", params)
            denied = await client.call_tool("step_submit", {**params, "step": "spec"})
            assert _error(denied)["reason"] == "LEASE_STALE_FENCE"

    asyncio.run(scenario())
    store = PipelineStorage(pipeline.writer)
    assert not store.blocks(store.history(pipeline.node)[0].id)
    assert pipeline.board.export_plan(pipeline.plan).nodes[1].fence == pipeline.fence + 1


def test_parallel_replays_create_one_attempt_and_one_block(pipeline: _Harness) -> None:
    async def scenario() -> None:
        async with Client(build_server(pipeline.board, pipeline.context())) as client:
            params = {"node_id": pipeline.node, "fence": pipeline.fence}
            begins = await asyncio.gather(_call(client, "step_begin", params), _call(client, "step_begin", params))
            assert sum("direction_prefix" in begun for begun in begins) == 1
            submitted = await asyncio.gather(
                _call(client, "step_submit", {**params, "step": "spec"}),
                _call(client, "step_submit", {**params, "step": "spec"}),
            )
            assert submitted[0] == submitted[1]

    asyncio.run(scenario())
    store = PipelineStorage(pipeline.writer)
    assert len(store.history(pipeline.node)) == 1
    assert len(store.blocks(store.history(pipeline.node)[0].id)) == 1
