"""Project boundaries, impact traversal, and durable MCP knowledge failures."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

import msgspec
import pytest
from mcp import Client
from sqlalchemy.exc import OperationalError

from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.mcp.server import build_server
from aconsole.core.infrastructure.mcp.tools import CtxResult, Impact, PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.engine import write_engine
from aconsole.core.infrastructure.persistence.models import KbNode, KbType, Tab
from aconsole.core.infrastructure.persistence.repositories import knowledge as knowledge_repo
from aconsole.core.infrastructure.persistence.repositories.knowledge import KnowledgeDatabase
from aconsole.core.infrastructure.persistence.repositories.tabs import TabDatabase
from aconsole.core.infrastructure.persistence.search import replace_note
from aconsole.core.infrastructure.persistence.startup import prepare_database, sqlite_url
from aconsole.core.infrastructure.persistence.writer import Writer
from tests.core.test_mcp_tools import _call, _error, _node, _node_id  # pyright: ignore[reportPrivateUsage]
from tests.core.test_store_recovery import _WHEN, _tab  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from sqlalchemy.orm import Session


_HERE = TabContext(tab_id="tab-one", project_id="project", worktree=None, agent="claude")
_ELSEWHERE = replace(_HERE, tab_id="other-tab", project_id="other-project")


def _client(board: PlanBoard, ctx: TabContext = _HERE) -> Client:
    return Client(build_server(board, ctx))


async def _context(client: Client, **arguments: object) -> CtxResult:
    return CtxResult.model_validate(await _call(client, "ctx_get", arguments))


async def _decision(client: Client, title: str, **extra: object) -> str:
    result = await _call(
        client,
        "kb_add",
        {
            "type": "decision",
            "title": title,
            "body": "Preserve data",
            "source": "adr",
            **extra,
        },
    )
    identity = result["kb_id"]
    assert isinstance(identity, str)
    return identity


@pytest.mark.parametrize("source", ["src/a.py", "/absolute/source#L1-2", "../outside#L1-2", "missing#L1-2"])
def test_invalid_fact_source_does_not_publish_knowledge(tmp_path: Path, source: str) -> None:
    async def scenario() -> None:
        board = PlanBoard()
        async with _client(board, replace(_HERE, worktree=tmp_path)) as client:
            result = await client.call_tool(
                "kb_add",
                {
                    "type": "fact",
                    "title": "Unsafe source",
                    "body": "Body",
                    "source": source,
                },
            )
            assert _error(result)["reason"] == "PATH_NOT_ALLOWED"
            assert (await _call(client, "ctx_get", {}))["items"] == []

    asyncio.run(scenario())


def test_valid_local_fact_source_is_visible_without_a_git_checkout(tmp_path: Path) -> None:
    _ = (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")

    async def scenario() -> None:
        async with _client(PlanBoard(), replace(_HERE, worktree=tmp_path)) as client:
            added = await _call(
                client,
                "kb_add",
                {
                    "type": "fact",
                    "title": "Source",
                    "body": "One line",
                    "source": "source.py#L1-1",
                },
            )
            found = await _context(client)
            assert [item.id for item in found.items] == [added["kb_id"]]
            assert found.verified_commit is None

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["supersede", "handover", "impact", "node_context", "plan"])
def test_foreign_project_ids_are_rejected_without_changing_local_state(operation: str) -> None:
    async def scenario() -> None:
        board = PlanBoard()
        async with _client(board, _ELSEWHERE) as foreign:
            fact = await _decision(foreign, "Private")
            plan = await _call(foreign, "plan_create", {"title": "Private", "goal": "Ship", "nodes": [_node("A-1")]})
        async with _client(board) as client:
            _ = await _decision(client, "Local")
            before = await _call(client, "ctx_get", {})
            calls = {
                "supersede": (
                    "kb_add",
                    {"type": "decision", "title": "Replacement", "body": "B", "source": "adr", "supersedes": fact},
                    "NOTE_NOT_FOUND",
                ),
                "handover": ("handover", {"action": "write", "goal": "Ship", "decisions": [fact]}, "NOTE_NOT_FOUND"),
                "impact": ("kb_impact", {"target": fact}, "IMPACT_TARGET_UNKNOWN"),
                "node_context": ("ctx_get", {"node_id": _node_id(plan, "A-1")}, "NODE_NOT_FOUND"),
                "plan": ("plan_ready", {"plan_id": plan["plan_id"]}, "PLAN_NOT_FOUND"),
            }
            name, arguments, expected = calls[operation]
            assert _error(await client.call_tool(name, arguments))["reason"] == expected
            assert await _call(client, "ctx_get", {}) == before
            assert (await _call(client, "handover", {"action": "read"})).get("handover_id") is None

    asyncio.run(scenario())


def test_node_context_path_filter_query_override_stale_order_and_limit() -> None:
    async def scenario() -> None:
        async with _client(PlanBoard()) as client:
            plan = await _call(
                client, "plan_create", {"title": "Plan", "goal": "Ship", "nodes": [_node("A-1", paths=["src/a.py"])]}
            )
            old = await _decision(client, "A old", code_refs=["src/a.py"])
            current = await _decision(client, "Z current", code_refs=["src/a.py"], supersedes=old)
            unrelated = await _decision(client, "B unrelated", code_refs=["src/b.py"])
            node_id = _node_id(plan, "A-1")
            found = await _context(client, node_id=node_id)
            assert [(item.id, item.stale) for item in found.items] == [(current, False), (old, True)]
            limited = await _context(client, node_id=node_id, max_items=1)
            assert limited.truncated is True
            assert [item.id for item in limited.items] == [current]
            queried = await _context(client, node_id=node_id, query="UNRELATED")
            assert [item.id for item in queried.items] == [unrelated]
            assert (await _call(client, "ctx_get", {"query": "absent"}))["items"] == []

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [1, 2, 10])
def test_impact_cycles_deduplicate_and_report_truncation(limit: int) -> None:
    async def scenario() -> None:
        board = PlanBoard()
        async with _client(board, _ELSEWHERE) as foreign:
            private = await _decision(foreign, "Private", code_refs=["shared.py"])
        async with _client(board) as client:
            first = await _decision(client, "First", code_refs=["shared.py", "shared.py"])
            second = await _decision(client, "Second", code_refs=["shared.py"])
            impact = Impact.model_validate(
                await _call(client, "kb_impact", {"target": "First", "hops": 3, "limit": limit})
            )
            assert impact.layers == [["shared.py"]] + ([[second]] if limit > 1 else [])
            assert impact.truncated is (limit == 1)
            assert bool(impact.canvas_note) is (limit == 1)
            flattened = [item for layer in impact.layers for item in layer]
            assert first not in flattened
            assert private not in flattened
            filtered = await _call(client, "kb_impact", {"target": first, "kinds": ["fact"]})
            assert filtered["layers"] == []

    asyncio.run(scenario())


class _UnavailableGraph:
    def resolve(self, project_id: str, target: str) -> str | None:
        _ = (project_id, target)
        raise DomainError(code=5007, name="IMPACT_TARGET_UNKNOWN", retryable=True)

    def dependents(self, project_id: str, kinds: frozenset[str] | None = None) -> dict[str, tuple[str, ...]]:
        _ = (project_id, kinds)
        pytest.fail("resolve already failed")


def test_codegraph_failure_keeps_knowledge_impact_available() -> None:
    async def scenario() -> None:
        board = PlanBoard()
        board.code_graph = _UnavailableGraph()
        async with _client(board) as client:
            fact = await _decision(client, "Known", code_refs=["source.py"])
            result = await _call(client, "kb_impact", {"target": fact})
            assert result["layers"] == [["source.py"]]
            assert (
                _error(await client.call_tool("kb_impact", {"target": "missing"}))["reason"] == "IMPACT_TARGET_UNKNOWN"
            )

    asyncio.run(scenario())


def test_implicit_ready_keeps_claimed_plan_and_otherwise_chooses_latest_local_plan() -> None:
    async def scenario() -> None:
        board = PlanBoard()
        async with _client(board) as client:
            assert _error(await client.call_tool("plan_ready", {}))["reason"] == "PLAN_NOT_FOUND"
            first = await _call(client, "plan_create", {"title": "First", "goal": "Ship", "nodes": [_node("A-1")]})
            claimed = await _call(client, "node_claim", {"node_id": _node_id(first, "A-1")})
            _ = await _call(client, "plan_create", {"title": "Second", "goal": "Ship", "nodes": [_node("B-1")]})
            async with _client(board, _ELSEWHERE) as foreign:
                _ = await _call(foreign, "plan_create", {"title": "Foreign", "goal": "Ship", "nodes": [_node("C-1")]})
            assert (await _call(client, "plan_ready", {}))["nodes"] == []
            _ = await _call(
                client,
                "node_update",
                {"node_id": _node_id(first, "A-1"), "fence": claimed["fence"], "status": "done", "evidence": "Checked"},
            )
            ready = await _call(client, "plan_ready", {})
            nodes = msgspec.convert(ready["nodes"], list[dict[str, object]])
            assert [node["key"] for node in nodes] == ["B-1"]

    asyncio.run(scenario())


@contextmanager
def _database(path: Path) -> Generator[tuple[PlanBoard, Writer]]:
    engine = write_engine(sqlite_url(path))
    writer = Writer(engine)
    writer.start()
    try:
        board = PlanBoard()
        board.use_knowledge(KnowledgeDatabase(writer))
        yield board, writer
    finally:
        writer.stop()
        engine.dispose()


def _fail_write(*_args: object) -> None:
    statement = "INSERT"
    raise OperationalError(statement, {}, ValueError("disk unavailable"))


def test_ctx_query_ranks_project_facts_and_appends_only_unindexed_substring_hits(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _ = prepare_database(path, now=_WHEN)

    def seed_legacy(session: Session) -> None:
        session.add(
            KbNode(
                id="kb-1",
                project_id=_HERE.project_id,
                node_type=KbType.FACT,
                title='A legacy cacheline "',
                note_ref="aconsole://note/kb-1",
                body="Imported fact",
                is_stale=False,
                updated_at=_WHEN,
            )
        )
        replace_note(session, "aconsole://note/vault-only", "Cache", "cache cache cache cache")

    with _database(path) as (_, writer):
        tabs = TabDatabase(writer)
        tabs.save(_tab())
        tabs.save(replace(_tab("other-tab"), project_id=_ELSEWHERE.project_id, project_root="/other"))
        _ = writer.call(seed_legacy)

    async def scenario(board: PlanBoard) -> None:
        async with _client(board, _ELSEWHERE) as foreign:
            _ = await _decision(foreign, "Cache", body="cache cache cache cache")

        async with _client(board) as client:

            async def search(query: str, max_items: int = 6) -> tuple[list[str], bool]:
                result = await _context(client, query=query, max_items=max_items)
                return [item.id for item in result.items], result.truncated

            weak = await _decision(client, "B weak", body="Some guidance for handling a cache safely.")
            strong = await _decision(client, "Z strong", body="cache cache cache")
            indexed_substring = await _decision(client, "C cacheline", body="Unrelated")

            expected = [strong, weak, "kb-1"]
            assert await search("CACHE") == (expected, False)
            for limit in (1, 2, 3):
                assert await search("cache", limit) == (expected[:limit], limit < len(expected))

            assert await search('"handling a" AND cach*') == ([weak], False)
            for query in ['"', "cache OR", "unknown:cache"]:
                assert await search(query) == ([], False)
            assert await search("") == (["kb-1", weak, indexed_substring, strong], False)

    with _database(path) as (board, _):
        asyncio.run(scenario(board))


@pytest.mark.parametrize("operation", ["supersede", "handover"])
def test_failed_write_preserves_previous_knowledge_and_handover_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    path = tmp_path / "state.db"
    _ = prepare_database(path, now=_WHEN)
    with _database(path) as (board, writer):
        TabDatabase(writer).save(_tab())

        async def scenario() -> tuple[str, dict[str, object]]:
            async with _client(board) as client:
                fact = await _decision(client, "Original", code_refs=["src/a.py"])
                _ = await _call(client, "handover", {"action": "write", "goal": "Original", "decisions": [fact]})
                before = await _call(client, "handover", {"action": "read"})
                with monkeypatch.context() as patch:
                    patch.setattr(
                        knowledge_repo, "save_fact" if operation == "supersede" else "save_handover", _fail_write
                    )
                    name, args = (
                        (
                            "kb_add",
                            {"type": "decision", "title": "Lost", "body": "B", "source": "adr", "supersedes": fact},
                        )
                        if operation == "supersede"
                        else ("handover", {"action": "write", "goal": "Lost"})
                    )
                    assert _error(await client.call_tool(name, args))["reason"] == "INTERNAL_ERROR"
                items = (await _context(client)).items
                assert [(item.id, item.stale) for item in items] == [(fact, False)]
                assert await _call(client, "handover", {"action": "read"}) == before
                return fact, before

        fact, packet = asyncio.run(scenario())
    with _database(path) as (restarted, _):

        async def verify() -> None:
            async with _client(restarted) as client:
                assert await _call(client, "handover", {"action": "read"}) == packet
                assert [item.id for item in (await _context(client)).items] == [fact]
                fresh = await _decision(client, "Retry", supersedes=fact)
                assert fresh != fact

        asyncio.run(verify())


@pytest.mark.parametrize("delete_source_tab", [False, True])
def test_restarted_project_keeps_its_facts_and_does_not_adopt_an_orphan_handover(
    tmp_path: Path,
    *,
    delete_source_tab: bool,
) -> None:
    path = tmp_path / "state.db"
    _ = prepare_database(path, now=_WHEN)
    with _database(path) as (board, writer):
        tabs = TabDatabase(writer)
        tabs.save(_tab())
        tabs.save(
            replace(
                _tab("other-tab"),
                project_id="other-project",
                project_root="/other",
                worktree="/other/wt",
            )
        )

        async def seed() -> tuple[str, str]:
            async with _client(board) as client:
                fact = await _decision(client, "Own", code_refs=["shared.py"])
                _ = await _call(client, "handover", {"action": "write", "goal": "Own goal", "decisions": [fact]})
            async with _client(board, _ELSEWHERE) as foreign:
                other = await _decision(foreign, "Other", code_refs=["shared.py"])
                _ = await _call(foreign, "handover", {"action": "write", "goal": "Other goal", "decisions": [other]})
            return fact, other

        own, other = asyncio.run(seed())
        if delete_source_tab:

            def remove_tab(session: Session) -> None:
                row = session.get(Tab, _HERE.tab_id)
                assert row is not None
                session.delete(row)

            _ = writer.call(remove_tab)
    with _database(path) as (restarted, _):

        async def verify() -> None:
            for ctx, identity, goal in [(_HERE, own, "Own goal"), (_ELSEWHERE, other, "Other goal")]:
                async with _client(restarted, ctx) as client:
                    found = await _context(client)
                    assert [item.id for item in found.items] == [identity]
                    handover = await _call(client, "handover", {"action": "read"})
                    if delete_source_tab and ctx == _HERE:
                        assert handover.get("handover_id") is None
                    else:
                        assert handover["goal"] == goal
                        assert handover["decisions"] == [identity]

        asyncio.run(verify())
