"""Knowledge and handovers live through a restart, and a write that fails is reported and leaves nothing behind."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import OperationalError

from aconsole.contracts.app import ProjectAddParams, ProjectView, TabView
from aconsole.core.infrastructure.mcp import tools
from aconsole.core.infrastructure.mcp.tools import PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.repositories import knowledge as knowledge_repo
from aconsole.core.infrastructure.persistence.repositories.knowledge import KnowledgeDatabase
from aconsole.core.infrastructure.persistence.writer import Writer
from tests.core.test_wire import _connect, _daemon, _git_repo, _open, _stop

if TYPE_CHECKING:
    from pathlib import Path


_STATEMENT = "INSERT"


def _broken(*_args: object) -> None:
    raise OperationalError(_STATEMENT, {}, ValueError())


def test_knowledge_and_handover_survive_a_restart(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        tab = await client.call("tab/open", _open(project.id), TabView)
        ctx = TabContext(tab_id=str(tab.id), project_id=str(project.id), worktree=None, agent="claude")
        board: PlanBoard = daemon._plans
        old = board.kb_add(ctx, "decision", "Use SQLite", "One file.", "adr-1", ["store.py"], None)
        refs = ["store.py", "wal.py"]
        new = board.kb_add(ctx, "decision", "Use SQLite WAL", "Readers stay free.", "adr-2", refs, old.kb_id)
        written = board._write_handover(ctx, "Finish", ["a"], [new.kb_id], ["q"], ["next"], ["ruff"])
        await _stop(daemon, task, client)

        again = _daemon(tmp_path, serve_ingest=False)
        restarted = asyncio.create_task(again.serve())
        second = await _connect(again.socket)
        board = again._plans
        facts = board._facts
        assert list(facts) == [old.kb_id, new.kb_id]
        assert facts[old.kb_id].stale
        assert not facts[new.kb_id].stale
        assert facts[new.kb_id].body == "Readers stay free."
        assert facts[new.kb_id].source == "adr-2"
        assert facts[new.kb_id].code_refs == ("store.py", "wal.py")
        assert board._target(str(project.id), "wal.py") == new.kb_id
        packet = board._read_handover(ctx)
        assert packet.handover_id == written.handover_id
        assert (packet.goal, packet.done, packet.decisions) == ("Finish", ["a"], [new.kb_id])
        assert (packet.open_questions, packet.next_steps, packet.failing_checks) == (["q"], ["next"], ["ruff"])
        fresh = board.kb_add(ctx, "decision", "Later", "", "adr-3", [], None)
        assert fresh.kb_id not in {old.kb_id, new.kb_id}
        await _stop(again, restarted, second)

    asyncio.run(scenario())


def _writer(tmp_path: Path) -> Writer:
    url = f"sqlite:///{tmp_path / 'state.db'}"
    migrate(url, "head")
    writer = Writer(write_engine(url))
    writer.start()
    return writer


def test_a_fact_that_fails_to_save_stays_out_of_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _writer(tmp_path)
    try:
        board = PlanBoard()
        board.use_knowledge(KnowledgeDatabase(writer))
        monkeypatch.setattr(knowledge_repo, "save_fact", _broken)
        ctx = TabContext(tab_id="t1", project_id="p", worktree=None, agent="claude")
        with pytest.raises(tools._ToolError):
            _ = board.kb_add(ctx, "decision", "T", "B", "s", [], None)
        assert board._facts == {}
    finally:
        writer.stop()
