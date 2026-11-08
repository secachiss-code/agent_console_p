"""Palette navigation is served by the assembled core, not the fake server."""
from __future__ import annotations

import asyncio
import statistics
import threading
import time
from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.contracts.app import (
    CommandResult,
    EmptyResult,
    PaletteExecuteParams,
    PaletteItemsResult,
    PaletteQuery,
    ProjectAddParams,
    ProjectView,
    ResetRankingParams,
    TabView,
)
from aconsole.contracts.common import NoteRef, PaletteKind, ProjectId, TabId
from aconsole.core.application.services.palette import PaletteService
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
from aconsole.core.infrastructure.mcp.tools import NodeIn
from tests.core.test_plan_wire import _context
from tests.core.test_wire import _connect, _daemon, _git_repo, _open, _stop

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.performance
def test_real_core_palette_route_and_latency(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket, stream_limit=16 * 1024 * 1024)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            tab = await client.call("tab/open", _open(project.id), TabView)
            vault = tmp_path / "vault"
            vault.mkdir()
            daemon.vault.use_root(vault)
            daemon._plans.create(_context(project), "Palette plan", "Navigate", [
                NodeIn(key=f"N-{i}", title=f"Node {i}", objective="Work") for i in range(200)
            ])
            query = PaletteQuery(tab_id=tab.id, project_id=project.id)
            times: list[float] = []
            for _ in range(10):
                start = time.perf_counter()
                result = await client.call("palette/items", query, PaletteItemsResult)
                times.append(time.perf_counter() - start)
            assert max(times) < 0.1
            assert {item.kind for item in result.items} >= {
                PaletteKind.COMMAND, PaletteKind.TAB, PaletteKind.PLAN_NODE, PaletteKind.NOTE,
            }
            note_item = next(item for item in result.items if item.kind is PaletteKind.NOTE)
            assert note_item.subtitle is not None
            note_path = vault / note_item.subtitle
            for _ in range(200):
                if note_path.is_file():
                    break
                await asyncio.sleep(0.01)
            opened_note = await client.call("palette/execute", PaletteExecuteParams(
                item_id=note_item.id, tab_id=tab.id, project_id=project.id, argument=None,
            ), CommandResult)
            assert opened_note.open_uri == note_path.as_uri()
            target = next(item for item in result.items if item.kind is PaletteKind.TAB)
            executed = await client.call("palette/execute", PaletteExecuteParams(
                item_id=target.id, tab_id=tab.id, project_id=project.id, argument=None,
            ), CommandResult)
            assert executed.opened_tab == tab.id
            ranked = await client.call("palette/items", query, PaletteItemsResult)
            assert next(item for item in ranked.items if item.id == target.id).score_hint == 1
            await client.call("palette/resetRanking", ResetRankingParams(item_id=None), EmptyResult)
            reset = await client.call("palette/items", query, PaletteItemsResult)
            assert all(item.score_hint == 0 for item in reset.items)
            stale = await client.call("palette/execute", PaletteExecuteParams(
                item_id="tab:missing", tab_id=tab.id, project_id=project.id, argument=None,
            ), CommandResult)
            assert not stale.ok
            daemon._plans.create(_context(project), "Palette plan", "Navigate", [
                NodeIn(key="NEW-1", title="Fresh catalog entry", objective="Work"),
            ])
            changed = await client.call("palette/items", query, PaletteItemsResult)
            assert any(item.title == "NEW-1: Fresh catalog entry" for item in changed.items)
            print(  # noqa: T201 -- record real IPC latency for the acceptance report
                f"palette median={statistics.median(times)*1000:.2f}ms "
                f"max={max(times)*1000:.2f}ms items={len(result.items)}",
            )
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_catalog_contains_linked_notes_and_approvals_and_revalidates() -> None:
    snapshot = demo_snapshot()
    plan = snapshot.plans[0]
    note = NoteRef("plans/palette.md")
    node = msgspec.structs.replace(plan.nodes[0], note=note)
    snapshot = msgspec.structs.replace(snapshot, plans=(msgspec.structs.replace(plan, nodes=(node,)),), vault_ok=True)
    service = PaletteService(lambda: snapshot, lambda ref: f"file:///vault/{ref}")
    query = PaletteQuery(tab_id=None, project_id=None)
    result = service.dispatch("palette/items", msgspec.json.encode(query))
    assert isinstance(result, PaletteItemsResult)
    assert {item.kind for item in result.items} >= {PaletteKind.NOTE, PaletteKind.APPROVAL}
    executed = service.dispatch("palette/execute", msgspec.json.encode(PaletteExecuteParams(
        item_id=f"note:{note}", tab_id=None, project_id=None, argument=None,
    )))
    assert isinstance(executed, CommandResult)
    assert executed.open_uri == "file:///vault/plans/palette.md"
    snapshot = msgspec.structs.replace(snapshot, plans=())
    stale = service.dispatch("palette/execute", msgspec.json.encode(PaletteExecuteParams(
        item_id=f"note:{note}", tab_id=None, project_id=None, argument=None,
    )))
    assert isinstance(stale, CommandResult)
    assert not stale.ok
    with pytest.raises(ValueError, match="arguments"):
        service.dispatch("palette/execute", msgspec.json.encode(PaletteExecuteParams(
            item_id="anything", tab_id=None, project_id=None, argument="arbitrary code",
        )))



def test_palette_plan_cache_is_seeded_after_restart(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            daemon._plans.create(_context(project), "Restored palette", "Navigate", [
                NodeIn(key="R-1", title="Survives restart", objective="Work"),
            ])
        finally:
            await _stop(daemon, task, client)
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            result = await client.call(
                "palette/items", PaletteQuery(tab_id=None, project_id=project.id), PaletteItemsResult,
            )
            assert any(item.title == "R-1: Survives restart" for item in result.items)
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())



@pytest.mark.parametrize("query", [
    PaletteQuery(tab_id=TabId("missing-tab"), project_id=None),
    PaletteQuery(tab_id=None, project_id=ProjectId("missing-project")),
])
def test_palette_rejects_unknown_context(query: PaletteQuery) -> None:
    service = PaletteService(demo_snapshot, lambda ref: f"file:///vault/{ref}")
    with pytest.raises(ValueError, match="context"):
        service.dispatch("palette/items", msgspec.json.encode(query))


def test_palette_answers_while_the_database_writer_is_busy(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        entered = threading.Event()
        released = threading.Event()

        def hold(_session: object) -> None:
            entered.set()
            _ = released.wait(timeout=2)

        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            tab = await client.call("tab/open", _open(project.id), TabView)
            daemon._plans.create(_context(project), "Navigation", "Read without database IO", [
                NodeIn(key="N-1", title="Available", objective="Navigate"),
            ])
            writer = daemon._writer
            assert writer is not None
            writer.post(hold)
            assert await asyncio.to_thread(entered.wait, 2)
            result = await asyncio.wait_for(client.call(
                "palette/items", PaletteQuery(tab_id=tab.id, project_id=project.id), PaletteItemsResult,
            ), timeout=0.25)
            assert not released.is_set(), "navigation must complete while the writer is still blocked"
            assert {row.kind for row in result.items} >= {
                PaletteKind.COMMAND, PaletteKind.TAB, PaletteKind.PLAN_NODE,
            }
            assert any(row.title == "N-1: Available" for row in result.items)
        finally:
            released.set()
            await _stop(daemon, task, client)

    asyncio.run(scenario())
