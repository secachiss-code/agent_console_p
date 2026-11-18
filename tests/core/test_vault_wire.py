"""Plan notes on disk: written, edited by hand, and refused when the plan rules say no."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

from aconsole.contracts.app import (
    EmptyParams,
    NoteUriParams,
    NoteUriResult,
    ProjectAddParams,
    ProjectView,
    SettingsUpdateParams,
)
from aconsole.contracts.common import NodeStatus, NoteRef
from aconsole.contracts.settings import Settings, SettingsPatch
from aconsole.core.domain.plan import NodeStatus as DomainStatus
from aconsole.core.domain.plan import Plan
from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.repositories.vault_files import VaultFileDatabase
from aconsole.core.infrastructure.vault.canvas import AttemptLine, CanvasHistory
from aconsole.core.infrastructure.vault.sync import VaultSync
from aconsole.core.infrastructure.vault.writer import load_front, note_hashes, split_note
from tests.core.test_knowledge_store import _writer
from tests.core.test_wire import _connect, _daemon, _git_repo, _stop

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from aconsole.core.domain.plan import Node
    from aconsole.core.domain.vault_sync import Note

_CTX = TabContext(tab_id="t1", project_id="p1", worktree=None, agent="claude")


def _board() -> tuple[PlanBoard, str]:
    board = PlanBoard()
    created = board.create(
        _CTX,
        "Plan",
        "goal",
        [
            NodeIn(key="N-01", title="First", objective="do one", acceptance=["ok"]),
            NodeIn(key="N-02", title="Second", objective="do two", depends_on=["N-01"]),
        ],
    )
    return board, created.plan_id


def _front(path: Path) -> dict[str, object]:
    front, _body = split_note(path.read_text(encoding="utf-8"))
    values = load_front(front)
    assert values is not None
    return values


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    _ = path.write_text(text.replace(old, new), encoding="utf-8")


def _sync(board: PlanBoard, root: Path) -> VaultSync:
    sync = VaultSync(board)
    sync.use_root(root)
    return sync


def test_notes_are_written_and_a_second_publish_changes_nothing(tmp_path: Path) -> None:
    board, plan_id = _board()
    sync = _sync(board, tmp_path)
    try:
        sync.publish(plan_id)
        path = tmp_path / "plans" / plan_id / "N-02.md"
        assert _front(path)["depends_on"] == ["[[N-01]]"]
        assert "## Цель" in path.read_text(encoding="utf-8")
        before = path.stat().st_mtime_ns
        sync.publish(plan_id)
        assert path.stat().st_mtime_ns == before
    finally:
        sync.stop()


def test_an_edited_title_and_status_reach_the_board_and_the_body_stays(tmp_path: Path) -> None:
    board, plan_id = _board()
    sync = _sync(board, tmp_path)
    try:
        sync.publish(plan_id)
        path = tmp_path / "plans" / plan_id / "N-01.md"
        _edit(path, "title: First", "title: Renamed")
        _edit(path, "status: pending", "status: blocked")
        _edit(path, "## Заметки", "## Заметки\n\nмоя заметка")  # noqa: RUF001 -- the note's heading
        sync.publish(plan_id)
        view = board.view(plan_id)
        assert view is not None
        first = next(node for node in view.nodes if node.key == "N-01")
        assert first.title == "Renamed"
        assert first.status is NodeStatus.BLOCKED
        assert "моя заметка" in path.read_text(encoding="utf-8")
        assert _front(path)["status"] == "blocked"
    finally:
        sync.stop()


def test_an_illegal_status_is_written_back(tmp_path: Path) -> None:
    board, plan_id = _board()
    sync = _sync(board, tmp_path)
    try:
        sync.publish(plan_id)
        path = tmp_path / "plans" / plan_id / "N-02.md"
        _edit(path, "status: pending", "status: done")
        sync.publish(plan_id)
        assert _front(path)["status"] == DomainStatus.PENDING.value
    finally:
        sync.stop()


def test_a_cycle_in_depends_on_is_refused(tmp_path: Path) -> None:
    board, plan_id = _board()
    sync = _sync(board, tmp_path)
    try:
        sync.publish(plan_id)
        path = tmp_path / "plans" / plan_id / "N-01.md"
        _edit(path, "depends_on: []", "depends_on: ['[[N-02]]']")
        sync.publish(plan_id)
        assert _front(path)["depends_on"] == []
    finally:
        sync.stop()


def test_a_missing_vault_is_not_created_and_a_ref_cannot_leave_it(tmp_path: Path) -> None:
    board, plan_id = _board()
    sync = VaultSync(board)
    sync.use_root(tmp_path / "absent")
    assert sync.root is None
    sync.publish(plan_id)
    assert not (tmp_path / "absent").exists()
    (tmp_path / "vault").mkdir()
    (tmp_path / "secret.md").write_text("x", encoding="utf-8")
    sync.use_root(tmp_path / "vault")
    try:
        assert sync.resolve("../secret.md") is None
    finally:
        sync.stop()


def test_the_daemon_writes_notes_when_a_vault_is_chosen_and_keeps_edits_across_a_restart(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    vault = tmp_path / "vault"
    vault.mkdir()

    async def wait_for(path: Path) -> None:
        for _ in range(200):
            if await asyncio.to_thread(path.is_file):
                return
            await asyncio.sleep(0.02)
        message = f"{path} did not appear"
        raise AssertionError(message)

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        assert not (await client.snapshot()).vault_ok
        ctx = TabContext(tab_id="t1", project_id=str(project.id), worktree=None, agent="claude")
        created = daemon._plans.create(ctx, "Plan", "goal", [NodeIn(key="N-01", title="First", objective="one")])
        current = await client.call("settings/get", EmptyParams(), Settings)
        chosen = await client.call(
            "settings/update",
            SettingsUpdateParams(patch=SettingsPatch(vault_path=str(vault)), rev=current.rev),
            Settings,
        )
        assert chosen.vault_path == str(vault)
        note = vault / "plans" / created.plan_id / "N-01.md"
        await wait_for(note)
        snapshot = await client.snapshot()
        assert snapshot.vault_ok
        ref = snapshot.plans[0].nodes[0].note
        assert ref == NoteRef(f"plans/{created.plan_id}/N-01.md")
        uri = await client.call("knowledge/noteUri", NoteUriParams(note=ref), NoteUriResult)
        assert uri.uri == note.as_uri()
        assert await client.error("knowledge/noteUri", NoteUriParams(note=NoteRef("../x.md"))) == "NOTE_NOT_FOUND"
        await _stop(daemon, task, client)

        # An edit made while the core is down still counts: the synced base survived in the database.
        await asyncio.to_thread(_edit, note, "title: First", "title: Edited offline")
        again = _daemon(tmp_path, serve_ingest=False)
        restarted = asyncio.create_task(again.serve())
        reader = await _connect(again.socket)
        for _ in range(200):
            plans = (await reader.snapshot()).plans
            if plans and plans[0].nodes[0].title == "Edited offline":
                break
            await asyncio.sleep(0.02)
        assert (await reader.snapshot()).plans[0].nodes[0].title == "Edited offline"
        await _stop(again, restarted, reader)

    asyncio.run(scenario())


def test_note_projection_reads_the_graph_linearly(monkeypatch: pytest.MonkeyPatch) -> None:
    count = 200
    board = PlanBoard()
    created = board.create(
        _CTX,
        "Large projection",
        "goal",
        [
            NodeIn(key=f"N-{i}", title=f"Node {i}", objective="work", depends_on=[] if i == 0 else [f"N-{i - 1}"])
            for i in range(count)
        ],
    )
    original = Plan.get
    reads = 0

    def counted(plan: Plan, key: str) -> Node:
        nonlocal reads
        reads += 1
        return original(plan, key)

    monkeypatch.setattr(Plan, "get", counted)
    notes = board.node_notes(created.plan_id)
    assert len(notes) == count
    assert reads <= 3 * count, "note projection must not traverse the entire graph once per note"
    keys = frozenset(f"N-{i}" for i in range(count))
    edges = tuple((f"N-{i}", f"N-{i - 1}") for i in range(1, count))
    states = tuple((f"N-{i}", "pending") for i in range(count))
    for index, projected in enumerate(notes):
        assert projected.key == projected.owners.node_key == f"N-{index}"
        assert projected.owners.known_keys == keys
        assert projected.owners.edges == edges
        assert projected.owners.blocker_status == states
        assert not projected.owners.leased_by_other
        assert projected.note.depends_on == (() if index == 0 else (f"N-{index - 1}",))


def test_projected_graph_is_fresh_and_previous_notes_keep_their_state() -> None:
    board, plan_id = _board()
    before = board.node_notes(plan_id)
    first_id = board.get(_CTX, plan_id, detailed=True).nodes[0].node_id
    lease = board.claim(_CTX, first_id)
    during = board.node_notes(plan_id)
    assert during[0].owners.leased_by_other
    assert not during[1].owners.leased_by_other
    assert during[0].note.status == "in_progress"
    assert during[1].owners.blocker_status == (("N-01", "in_progress"), ("N-02", "pending"))
    _ = board.update(_CTX, first_id, lease.fence, "failed", "test refusal", None)
    after = board.node_notes(plan_id)
    assert not after[0].owners.leased_by_other
    assert after[1].owners.blocker_status == (("N-01", "failed"), ("N-02", "pending"))
    assert before[0].note.status == "pending"
    assert before[1].owners.blocker_status == (("N-01", "pending"), ("N-02", "pending"))
    assert during[0].note.status == "in_progress"
    assert during[1].owners.blocker_status == (("N-01", "in_progress"), ("N-02", "pending"))


def test_durable_echo_survives_restart_and_does_not_parse_unchanged_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aconsole.core.infrastructure.vault import sync as sync_module

    board, plan_id = _board()
    writer = _writer(tmp_path)
    sync = VaultSync(board, bases=VaultFileDatabase(writer))
    sync.use_root(tmp_path)
    sync.stop()  # Deliver watcher events explicitly, without timing races.
    sync.publish(plan_id)
    ref = f"plans/{plan_id}/N-01.md"
    path = tmp_path / ref
    before = path.stat().st_mtime_ns
    text = path.read_bytes().decode()
    writer.stop()

    writer = _writer(tmp_path)
    store = VaultFileDatabase(writer)
    restarted = VaultSync(board, bases=store)
    restarted.use_root(tmp_path)
    restarted.stop()
    heard: list[str] = []
    restarted.on_edit = heard.append
    parsed = sync_module._parsed
    parsed_fronts: list[str] = []

    def tracked(front: str) -> Note | None:
        parsed_fronts.append(front)
        return parsed(front)

    monkeypatch.setattr(sync_module, "_parsed", tracked)
    try:
        assert len(restarted._writer.echo) == 0
        assert store.matches(ref, *note_hashes(text))
        restarted._heard(path)
        restarted.publish(plan_id)
        assert heard == []
        assert parsed_fronts == []
        assert path.stat().st_mtime_ns == before

        _edit(path, "title: First", "title: Edited offline")
        restarted._heard(path)
        assert heard == [plan_id]
        restarted.publish(plan_id)
        assert len(parsed_fronts) == 1
        assert board.node_notes(plan_id)[0].note.title == "Edited offline"

        # Body-only changes must also miss the digest.
        _edit(path, "## Заметки", "## Заметки\n\nUser paragraph")
        restarted._heard(path)
        restarted.publish(plan_id)
        assert len(heard) == 2
        assert len(parsed_fronts) == 2
        assert "User paragraph" in path.read_text()
    finally:
        restarted.stop()
        writer.stop()


def test_durable_echo_still_publishes_changed_board_state(tmp_path: Path) -> None:
    board, plan_id = _board()
    writer = _writer(tmp_path)
    store = VaultFileDatabase(writer)
    sync = VaultSync(board, bases=store)
    sync.use_root(tmp_path)
    sync.stop()
    try:
        sync.publish(plan_id)
        notes = board.node_notes(plan_id)
        board.apply_note(plan_id, "N-01", replace(notes[0].note, status="blocked"))
        sync.publish(plan_id)
        ref = f"plans/{plan_id}/N-01.md"
        assert _front(tmp_path / ref)["status"] == "blocked"
        assert store.matches(ref, *note_hashes((tmp_path / ref).read_bytes().decode()))
        # A legacy base without a successful byte publication is not an echo.
        store.save(ref, board.node_notes(plan_id)[0].note)
        assert not store.matches(ref, *note_hashes((tmp_path / ref).read_bytes().decode()))
    finally:
        sync.stop()
        writer.stop()


def test_attempt_publication_updates_the_durable_body_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    board, plan_id = _board()
    writer = _writer(tmp_path)
    store = VaultFileDatabase(writer)
    sync = VaultSync(board, bases=store)
    sync.use_root(tmp_path)
    sync.stop()
    line = AttemptLine(key="N-01", attempt_no=1, status="failed", verdict="retry", head_block="hash")

    def attempts(_history: CanvasHistory, _plan: str) -> tuple[AttemptLine, ...]:
        return (line,)

    monkeypatch.setattr(CanvasHistory, "attempts", attempts)
    sync.use_history(CanvasHistory(writer=writer))
    try:
        sync.publish(plan_id)
        ref = f"plans/{plan_id}/N-01.md"
        text = (tmp_path / ref).read_bytes().decode()
        assert line.render() in text
        assert store.matches(ref, *note_hashes(text))
        sync.publish(plan_id)
        assert (tmp_path / ref).read_bytes().decode().count(line.render()) == 1
        assert store.matches(ref, *note_hashes(text))
    finally:
        sync.stop()
        writer.stop()
