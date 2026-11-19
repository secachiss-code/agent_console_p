"""Exact-byte note digests, unchanged mtimes and serialized in-memory echo pruning."""

from __future__ import annotations

import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event, current_thread
from typing import TYPE_CHECKING

import pytest
lazy from sqlalchemy.orm import Session

from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.models import NodeAttempt, Project, VaultFile
from aconsole.core.infrastructure.persistence.repositories.plans import PlanDatabase
from aconsole.core.infrastructure.persistence.repositories.vault_files import VaultFileDatabase
from aconsole.core.infrastructure.persistence.writer import Writer
from aconsole.core.infrastructure.vault.canvas import CanvasHistory
from aconsole.core.infrastructure.vault.sync import VaultSync
from aconsole.core.infrastructure.vault.writer import (
    EchoBook,
    UserSectionError,
    VaultWriter,
    append_under,
    note_hashes,
    render,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_SAMPLE = "tests/fixtures/vault/sample"
_WHEN = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.moment: datetime = _WHEN

    def __call__(self) -> datetime:
        return self.moment


def test_vault_writer_round_trip_is_byte_for_byte(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    _ = shutil.copytree(_SAMPLE, root)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        original = path.read_bytes()
        rendered = render(original.decode("utf-8"))
        assert rendered.encode("utf-8") == original


def test_vault_writer_repeat_does_not_change_mtime(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    _ = shutil.copyfile(f"{_SAMPLE}/sample/plans/sample-plan/N-01-vault-sample.md", source)
    clock = _Clock()
    writer = VaultWriter(now=clock)
    text = source.read_text(encoding="utf-8")
    os.utime(source, ns=(1_000_000_000, 1_000_000_000))
    before = source.stat().st_mtime_ns
    assert writer.write(source, render(text)) is False
    assert source.stat().st_mtime_ns == before
    updated = append_under(text, "Журнал", "- 2026-10-29 12:00 app: still here")
    assert "Sample task for the vault spike." in updated
    assert writer.write(source, updated) is True
    assert source.stat().st_mtime_ns != before
    assert writer.echo.is_echo(source, updated, clock.moment) is True
    clock.moment += timedelta(seconds=11)
    assert writer.echo.is_echo(source, updated, clock.moment) is False
    assert len(writer.echo) == 0


def test_vault_writer_refuses_a_user_section() -> None:
    with pytest.raises(UserSectionError):
        _ = append_under("# Title\n\n## Цель\nkeep\n", "Цель", "replaced")


def test_note_hashes_keep_front_body_and_line_endings_distinct() -> None:
    import hashlib

    text = "---\ntitle: First\n---\ncafé\n"
    front, body = note_hashes(text)
    assert front == hashlib.blake2b(b"title: First\n", digest_size=32).hexdigest()
    assert body == hashlib.blake2b("café\n".encode(), digest_size=32).hexdigest()
    assert note_hashes(text.replace("First", "Second")) == (note_hashes("---\ntitle: Second\n---\n")[0], body)
    assert note_hashes(text.replace("café", "Changed"))[0] == front
    assert note_hashes(text.replace("\n", "\r\n")) != (front, body)


def test_echo_prune_and_remember_are_serialized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    book = EchoBook()
    first, second = tmp_path / "first.md", tmp_path / "second.md"
    book.remember(first, "one", _WHEN)
    now = _WHEN + timedelta(seconds=11)
    entered, release, writing, written = Event(), Event(), Event(), Event()
    prune = EchoBook._prune  # pyright: ignore[reportPrivateUsage] -- pause the real pruning operation to verify lock ordering

    def paused_prune(self: EchoBook, at: datetime) -> None:
        if current_thread().name.startswith("echo-reader"):
            entered.set()
            assert release.wait(3)
        prune(self, at)

    def remember() -> None:
        writing.set()
        book.remember(second, "two", now)
        written.set()

    monkeypatch.setattr(EchoBook, "_prune", paused_prune)
    with (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="echo-reader") as reader,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="echo-writer") as writer,
    ):
        observed = reader.submit(book.is_echo, first, "one", now)
        assert entered.wait(3)
        saved = writer.submit(remember)
        try:
            assert writing.wait(3)
            assert not written.wait(0.1)
        finally:
            release.set()
        assert observed.result(timeout=3) is False
        saved.result(timeout=3)
    assert len(book) == 1
    assert book.is_echo(second, "two", now)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_protected_hash_ignores_attempts_but_keeps_neighbor_sections(newline: str) -> None:
    from aconsole.core.infrastructure.vault.writer import protected_body_hash, update_attempt

    body = "# Node\n\n## Заметки\nmy text\n"
    text = "---\ntitle: Node\n---\n" + body
    text = text.replace("\n", newline)
    with_attempt = update_attempt(text, 1, "- 1/4 · failed · retry · hash")
    assert protected_body_hash(with_attempt) == protected_body_hash(text)
    assert protected_body_hash(with_attempt.replace("retry", "changed")) == protected_body_hash(text)
    assert protected_body_hash(with_attempt.replace("my text", "my changed text")) != protected_body_hash(text)
    after = with_attempt + "## Other\nprotected tail\n"
    assert protected_body_hash(after) != protected_body_hash(text)
    assert protected_body_hash(after.replace("protected tail", "edit")) != protected_body_hash(after)


def test_attempt_headings_in_fenced_examples_are_protected() -> None:
    from aconsole.core.infrastructure.vault.writer import protected_body_hash, update_attempt

    text = "## Заметки\n```md\n## Попытки\nexample\n```\n"
    updated = update_attempt(text, 1, "- 1/4 · passed · ok · hash")
    assert updated.startswith(text)
    assert protected_body_hash(updated) == protected_body_hash(text)
    assert protected_body_hash(updated.replace("example", "edit")) != protected_body_hash(updated)


def test_writer_refuses_a_file_changed_since_read(tmp_path: Path) -> None:
    from aconsole.core.infrastructure.vault.writer import ConcurrentNoteEditError

    path = tmp_path / "note.md"
    writer = VaultWriter()
    assert writer.write(path, "original\n")
    _ = path.write_bytes(b"human edit\r\n")
    before = path.stat().st_mtime_ns
    with pytest.raises(ConcurrentNoteEditError):
        _ = writer.write(path, "app edit\n", expected="original\n")
    assert path.read_bytes() == b"human edit\r\n"
    assert path.stat().st_mtime_ns == before


@pytest.fixture
def protected_vault(tmp_path: Path) -> Iterator[tuple[PlanBoard, str, VaultSync, Writer, Path]]:
    url = f"sqlite:///{tmp_path / 'protected.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    writer = Writer(engine)
    writer.start()

    def seed(session: Session) -> None:
        session.add(Project(id="p", name="P", root_path="/p", languages=[], created_at=_WHEN))

    _ = writer.call(seed)
    board = PlanBoard()
    board.use_store(PlanDatabase(writer))
    ctx = TabContext(tab_id="t", project_id="p", worktree=None, agent="claude")
    plan = board.create(ctx, "Plan", "goal", [NodeIn(key="N-01", title="First", objective="keep")])
    root = tmp_path / "vault"
    root.mkdir()
    sync = VaultSync(board, bases=VaultFileDatabase(writer))
    sync.use_root(root)
    sync.stop()  # Watch notifications are not needed to exercise publication conflicts.
    sync.use_history(CanvasHistory(writer=writer))
    sync.publish(plan.plan_id)
    path = root / "plans" / plan.plan_id / "N-01.md"
    try:
        yield board, plan.plan_id, sync, writer, path
    finally:
        sync.stop()
        writer.stop()
        engine.dispose()


def _record_attempt(board: PlanBoard, plan_id: str, writer: Writer) -> None:
    def store(session: Session) -> None:
        session.add(
            NodeAttempt(
                id="attempt",
                node_id=board.node_notes(plan_id)[0].node_id,
                attempt_no=1,
                status="failed",
                base_oid="a" * 40,
                compact_verdict="retry",
                started_at=_WHEN,
            )
        )

    _ = writer.call(store)


def _publication(writer: Writer, ref: str) -> tuple[str | None, dict[str, object] | None]:
    found: list[tuple[str | None, dict[str, object] | None]] = []

    def read(session: Session) -> None:
        row = session.get(VaultFile, ref)
        assert row is not None
        found.append((row.body_hash, row.pending_write))

    _ = writer.call(read)
    return found[0]


@pytest.mark.parametrize("restart", [False, True])
def test_foreign_paragraph_defers_all_writes_and_preserves_durable_attempt(
    protected_vault: tuple[PlanBoard, str, VaultSync, Writer, Path],
    monkeypatch: pytest.MonkeyPatch,
    *,
    restart: bool,
) -> None:
    from dataclasses import replace

    from aconsole.core.infrastructure.vault import sync as sync_module
    from aconsole.core.infrastructure.vault.writer import update_attempt

    board, plan_id, sync, writer, path = protected_vault
    ref = f"plans/{plan_id}/N-01.md"
    baseline = path.read_bytes()
    old_hash, _pending = _publication(writer, ref)
    _record_attempt(board, plan_id, writer)
    board.apply_note(plan_id, "N-01", replace(board.node_notes(plan_id)[0].note, status="blocked"))
    foreign = baseline + "\nЛичный абзац\r\n".encode()
    _ = path.write_bytes(foreign)
    if restart:
        sync = VaultSync(board, bases=VaultFileDatabase(writer))
        sync.use_root(path.parents[2])
        sync.stop()
        sync.use_history(CanvasHistory(writer=writer))
    calls: list[int] = []
    original = update_attempt

    def update(text: str, number: int, line: str) -> str:
        calls.append(number)
        return original(text, number, line)

    monkeypatch.setattr(sync_module, "update_attempt", update)
    before = path.stat().st_mtime_ns
    for _ in range(2):
        sync.publish(plan_id)
        assert path.read_bytes() == foreign
        assert path.stat().st_mtime_ns == before
        body_hash, pending = _publication(writer, ref)
        assert body_hash == old_hash
        assert pending is not None
        assert "status: blocked" in str(pending["text"])
    assert calls == []
    history = CanvasHistory(writer=writer).attempts(plan_id)
    assert len(history) == 1
    assert history[0].verdict == "retry"
    # Removing the conflicting paragraph restores the last published baseline.
    _ = path.write_bytes(baseline)
    sync.publish(plan_id)
    assert calls == [1]
    text = path.read_bytes().decode()
    assert "status: blocked" in text
    assert history[0].render() in text
    assert _publication(writer, ref)[1] is None
    store = VaultFileDatabase(writer)
    assert store.matches(ref, *note_hashes(text))


def test_user_changes_inside_attempts_allow_publication(
    protected_vault: tuple[PlanBoard, str, VaultSync, Writer, Path],
) -> None:
    from aconsole.core.infrastructure.vault.writer import update_attempt

    board, plan_id, sync, writer, path = protected_vault
    _record_attempt(board, plan_id, writer)
    sync.publish(plan_id)
    text = path.read_bytes().decode()
    _ = path.write_bytes(update_attempt(text, 1, "- 1/4 · user edit").encode())
    sync.publish(plan_id)
    assert CanvasHistory(writer=writer).attempts(plan_id)[0].render() in path.read_bytes().decode()
    assert _publication(writer, f"plans/{plan_id}/N-01.md")[1] is None


def test_legacy_publication_baseline_allows_front_edits_but_rejects_foreign_body(
    protected_vault: tuple[PlanBoard, str, VaultSync, Writer, Path],
) -> None:
    board, plan_id, _sync, writer, path = protected_vault
    ref = f"plans/{plan_id}/N-01.md"
    baseline = path.read_bytes().decode()
    store = VaultFileDatabase(writer)
    # Simulate a successful publication made before protected-body hashes existed.
    store.save_written(ref, board.node_notes(plan_id)[0].note, *note_hashes(baseline))
    _ = path.write_bytes(baseline.replace("title: First", "title: Renamed").encode())
    sync = VaultSync(board, bases=VaultFileDatabase(writer))
    sync.use_root(path.parents[2])
    sync.stop()
    sync.publish(plan_id)
    assert board.node_notes(plan_id)[0].note.title == "Renamed"
    published = path.read_bytes().decode()
    assert store.matches(ref, *note_hashes(published))
    store.save_written(ref, board.node_notes(plan_id)[0].note, *note_hashes(published))
    foreign = published.encode() + b"\nhuman paragraph\n"
    _ = path.write_bytes(foreign)
    restarted = VaultSync(board, bases=VaultFileDatabase(writer))
    restarted.use_root(path.parents[2])
    restarted.stop()
    restarted.publish(plan_id)
    assert path.read_bytes() == foreign
    assert _publication(writer, ref)[1] is not None


def test_concurrent_front_edit_defers_attempts_until_the_next_merge(
    protected_vault: tuple[PlanBoard, str, VaultSync, Writer, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, plan_id, sync, writer, path = protected_vault
    _record_attempt(board, plan_id, writer)
    baseline = path.read_bytes().decode()
    original = VaultWriter.write
    injected: list[bool] = []

    def racing(self: VaultWriter, target: Path, text: str, *, expected: str | None = None) -> bool:
        if target == path and expected is not None and not injected:
            injected.append(True)
            _ = path.write_bytes(baseline.replace("title: First", "title: Concurrent").encode())
        return original(self, target, text, expected=expected)

    monkeypatch.setattr(VaultWriter, "write", racing)
    sync.publish(plan_id)
    text = path.read_bytes().decode()
    assert "title: Concurrent" in text
    assert "## Попытки" not in text
    assert _publication(writer, f"plans/{plan_id}/N-01.md")[1] is not None
    sync.publish(plan_id)
    assert board.node_notes(plan_id)[0].note.title == "Concurrent"
    assert CanvasHistory(writer=writer).attempts(plan_id)[0].render() in path.read_bytes().decode()
    assert _publication(writer, f"plans/{plan_id}/N-01.md")[1] is None
