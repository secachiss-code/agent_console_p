"""Plan notes on disk: written, edited by hand, and refused when the plan rules say no."""

from __future__ import annotations

import asyncio
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
from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext
from aconsole.core.infrastructure.vault.sync import VaultSync
from aconsole.core.infrastructure.vault.writer import load_front, split_note
from tests.core.test_wire import _connect, _daemon, _git_repo, _stop

if TYPE_CHECKING:
    from pathlib import Path

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
        _edit(path, "## Заметки", "## Заметки\n\nмоя заметка")
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
