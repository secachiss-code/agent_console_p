"""Knowledge and handovers live through a restart, and a write that fails is reported and leaves nothing behind."""

# pyright: reportPrivateUsage=false
# Existing integration tests inspect the board and share daemon fixtures.

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

    from sqlalchemy.orm import Session

    from aconsole.core.application.ports.knowledge_store import StoredFact


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


def fts_rows(session: Session, query: str | None = None) -> list[tuple[str, str, str]]:
    from sqlalchemy import String, column, select, table

    notes = table("note_fts", column("title", String), column("body", String), column("note_ref", String))
    statement = select(notes.c.title, notes.c.body, notes.c.note_ref)
    if query is not None:
        statement = statement.where(column("note_fts").match(query))
    return [(row[0], row[1], row[2]) for row in session.execute(statement)]


def _fact() -> StoredFact:
    from datetime import UTC, datetime

    from aconsole.core.application.ports.knowledge_store import StoredFact

    return StoredFact(
        id="fact-1",
        project_id="p",
        kind="fact",
        title="Кэш SQLite",
        body="Транзакция café",
        source="src/a.py#L1-2",
        code_refs=(),
        stale=False,
        note_ref="aconsole://note/fact-1",
        updated_at=datetime.now(UTC),
    )


def test_fact_fts_shares_transaction_and_deletion(tmp_path: Path) -> None:
    from sqlalchemy.orm import Session

    from aconsole.core.infrastructure.persistence.models import KbNode, Project

    url = f"sqlite:///{tmp_path / 'fts.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    fact = _fact()
    try:
        with Session(engine) as session:
            session.add(Project(id="p", name="P", root_path="/p", languages=[], created_at=fact.updated_at))
            session.commit()
            knowledge_repo.save_fact(session, fact, None)
            assert fts_rows(session, "транзакция AND cafe") == [(fact.title, fact.body, fact.note_ref)]
            session.rollback()
            assert session.get(KbNode, fact.id) is None
            assert fts_rows(session) == []
            knowledge_repo.save_fact(session, fact, None)
            session.commit()
            row = session.get(KbNode, fact.id)
            assert row is not None
            session.delete(row)
            session.flush()
            assert fts_rows(session) == []
            session.rollback()
            assert fts_rows(session) == [(fact.title, fact.body, fact.note_ref)]
            session.delete(session.get_one(Project, "p"))
            session.commit()
            assert fts_rows(session) == []
    finally:
        engine.dispose()


def test_published_note_fts_is_atomic_and_replaces_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    from aconsole.core.domain.vault_sync import Note
    from aconsole.core.infrastructure.persistence.repositories import vault_files
    from aconsole.core.infrastructure.persistence.repositories.vault_files import VaultFileDatabase

    writer = _writer(tmp_path)
    store = VaultFileDatabase(writer)
    note = Note(title="Опубликовано", objective="Цель")
    ref = "plans/plan-1/N-01.md"
    rows: list[list[tuple[str, str, str]]] = []
    from aconsole.core.infrastructure.persistence.search import replace_note

    original = replace_note

    def inspect_then_fail(session: Session, note_ref: str, title: str, body: str) -> None:
        from aconsole.core.infrastructure.persistence.models import VaultFile

        original(session, note_ref, title, body)
        session.flush()
        assert session.get(VaultFile, note_ref) is not None
        assert fts_rows(session) == [(title, body, note_ref)]
        message = "publication failed"
        raise ValueError(message)

    try:
        monkeypatch.setattr(vault_files, "replace_note", inspect_then_fail)
        with pytest.raises(ValueError, match="publication failed"):
            store.save_publication(ref, note, ("front", "body", "protected"), "Черновик")
        assert store.load() == {}
        _ = writer.call(lambda session: rows.append(fts_rows(session)))
        assert rows.pop() == []
        monkeypatch.setattr(vault_files, "replace_note", original)
        store.save_publication(ref, note, ("front", "body", "protected"), "Старый текст")
        store.save_publication(ref, note, ("front2", "body2", "protected2"), "Новый текст")
        _ = writer.call(lambda session: rows.append(fts_rows(session)))
        assert rows.pop() == [(note.title, "Новый текст", ref)]
        assert store.load()[ref] == note
        assert store.matches(ref, "front2", "body2")
        store.save_pending(ref, "Отложенный текст")
        _ = writer.call(lambda session: rows.append(fts_rows(session)))
        assert rows.pop() == [(note.title, "Новый текст", ref)]
    finally:
        writer.stop()


def test_vault_sync_indexes_published_body(tmp_path: Path) -> None:
    from aconsole.core.infrastructure.mcp.tools import NodeIn
    from aconsole.core.infrastructure.persistence.repositories.vault_files import VaultFileDatabase
    from aconsole.core.infrastructure.vault.sync import VaultSync

    writer = _writer(tmp_path)
    board = PlanBoard()
    context = TabContext(tab_id="t", project_id="p", worktree=None, agent="claude")
    plan = board.create(context, "Plan", "goal", [NodeIn(key="N-01", title="Заметка", objective="Содержимое")])
    sync = VaultSync(board, bases=VaultFileDatabase(writer))
    root = tmp_path / "vault"
    root.mkdir()
    rows: list[list[tuple[str, str, str]]] = []
    try:
        sync.use_root(root)
        sync.stop()
        sync.publish(plan.plan_id)
        sync.publish(plan.plan_id)
        _ = writer.call(lambda session: rows.append(fts_rows(session, "содержимое")))
        result = rows.pop()
        assert len(result) == 1
        title, body, ref = result[0]
        assert title == "Заметка"
        assert "Содержимое" in body
        assert ref == f"plans/{plan.plan_id}/N-01.md"
        path = root / ref
        _ = path.write_text(path.read_text() + "Чужой абзац\n")
        sync.publish(plan.plan_id)
        _ = writer.call(lambda session: rows.append(fts_rows(session)))
        assert rows.pop() == result
    finally:
        sync.stop()
        writer.stop()
