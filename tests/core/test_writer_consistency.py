"""Queue deadlines, shutdown and settings transactions preserve one durable outcome."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from aconsole.contracts.app import EmptyParams, SettingsUpdateParams
from aconsole.contracts.settings import Settings, SettingsPatch
from aconsole.core.application.services.tabs import TabService
from aconsole.core.domain.clock import FrozenClock
from aconsole.core.infrastructure.persistence import writer as writer_module
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.models import Setting
from aconsole.core.infrastructure.persistence.repositories.tabs import TabDatabase
from aconsole.core.infrastructure.persistence.writer import Writer
from tests.core.conftest import FakeTabPorts
from tests.core.test_wire import _connect, _daemon, _stop

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine


@pytest.fixture
def storage(tmp_path: Path) -> tuple[Writer, Engine]:
    url = f"sqlite:///{tmp_path / 'store.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    writer = Writer(engine)
    writer.start()
    try:
        yield writer, engine
    finally:
        writer.stop()
        writer._thread.join(timeout=2)
        engine.dispose()


def _put(session: Session, key: str) -> str:
    session.add(Setting(key=key, value={"saved": True}, rev=1))
    return key


def test_a_queued_call_that_times_out_never_writes_later(
    storage: tuple[Writer, Engine], monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer, engine = storage
    entered, release = threading.Event(), threading.Event()

    def block(_session: Session) -> None:
        entered.set()
        assert release.wait(2)

    writer.post(block)
    assert entered.wait(1)
    monkeypatch.setattr(writer_module, "_WAIT_S", 0.03)
    try:
        with pytest.raises(TimeoutError):
            writer.call(lambda session: _put(session, "expired"))
    finally:
        release.set()
    monkeypatch.setattr(writer_module, "_WAIT_S", 1.0)
    _ = writer.call(lambda _session: None)
    with Session(engine) as session:
        assert session.get(Setting, "expired") is None


def test_a_running_call_waits_for_its_committed_outcome(
    storage: tuple[Writer, Engine], monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer, engine = storage
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(writer_module, "_WAIT_S", 0.03)

    def work(session: Session) -> str:
        entered.set()
        assert release.wait(2)
        return _put(session, "running")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(writer.call, work)
        assert entered.wait(1)
        try:
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.08)
        finally:
            release.set()
        assert pending.result(timeout=1) == "running"
    with Session(engine) as session:
        assert session.get(Setting, "running") is not None


def test_stop_does_not_claim_success_or_accept_writes_while_the_thread_is_busy(
    storage: tuple[Writer, Engine], monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer, engine = storage
    entered, release = threading.Event(), threading.Event()

    def block(_session: Session) -> None:
        entered.set()
        assert release.wait(2)

    writer.post(block)
    assert entered.wait(1)
    writer.post(lambda session: _put(session, "first"))
    writer.post(lambda session: _put(session, "second"))
    monkeypatch.setattr(writer_module, "_WAIT_S", 0.03)
    try:
        with pytest.raises(TimeoutError):
            writer.stop()
        with pytest.raises(RuntimeError):
            writer.post(lambda session: _put(session, "too-late"))
    finally:
        release.set()
        writer._thread.join(timeout=2)
        writer.stop()
    with Session(engine) as session:
        assert set(session.scalars(select(Setting.key))) == {"first", "second"}


def test_cancellation_before_a_submitted_job_runs_skips_the_write(storage: tuple[Writer, Engine]) -> None:
    writer, engine = storage
    entered, release = threading.Event(), threading.Event()

    def block(_session: Session) -> None:
        entered.set()
        assert release.wait(2)

    writer.post(block)
    assert entered.wait(1)

    async def scenario() -> None:
        pending = writer.submit(lambda session: _put(session, "cancelled"))
        assert pending.cancel()
        await asyncio.sleep(0)
        release.set()
        _ = await asyncio.to_thread(writer.call, lambda _session: None)

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    with Session(engine) as session:
        assert session.get(Setting, "cancelled") is None


def _seed(session: Session) -> None:
    session.add(Setting(key="app", value={"active_project": "keep", "worktrees_dir": "/old"}, rev=0))


def _service(writer: Writer) -> TabService:
    _ = writer.call(_seed)
    service = TabService(FrozenClock(datetime(2026, 11, 5, tzinfo=UTC)), FakeTabPorts())
    service.use_store(TabDatabase(writer))
    return service


def _patch() -> SettingsUpdateParams:
    return SettingsUpdateParams(rev=0, patch=SettingsPatch(
        setup_completed=True, approvals_mode="native", approval_hold_seconds=60,
        theme="dark", worktrees_dir="/new",
    ))


def test_failed_settings_commit_leaves_database_memory_and_observers_unchanged(
    storage: tuple[Writer, Engine],
) -> None:
    writer, engine = storage
    service = _service(writer)
    before = service.current_settings()
    notifications = []
    service.on_settings_changed = notifications.append

    def refuse_dark(session: Session) -> None:
        if session.bind is engine:
            row = session.get(Setting, "app")
            if row is not None and row.value.get("theme") == "dark":
                message = "settings commit refused"
                raise RuntimeError(message)

    event.listen(Session, "before_commit", refuse_dark)
    try:
        with pytest.raises(RuntimeError, match="settings commit refused"):
            service.update_settings(_patch())
    finally:
        event.remove(Session, "before_commit", refuse_dark)
    assert service.current_settings() == before
    assert service._settings_rev == before.rev
    assert service._approvals_mode == before.approvals_mode
    assert service._setup_completed == before.setup_completed
    assert notifications == []
    with Session(engine) as session:
        row = session.get(Setting, "app")
        assert row is not None
        assert row.value == {"active_project": "keep", "worktrees_dir": "/old"}
        assert row.rev == 0


def test_settings_publish_one_transaction_with_all_fields(storage: tuple[Writer, Engine]) -> None:
    writer, engine = storage
    service = _service(writer)
    writes = []

    def count(session: Session, _context: object, _instances: object) -> None:
        if session.bind is engine and any(isinstance(row, Setting) for row in (*session.new, *session.dirty)):
            writes.append(1)

    event.listen(Session, "before_flush", count)
    try:
        updated = service.update_settings(_patch())
    finally:
        event.remove(Session, "before_flush", count)
    assert writes == [1]
    assert updated == service.current_settings()
    assert updated.worktrees_dir == "/new"
    assert updated.approvals_mode == "native"
    assert updated.theme == "dark"
    with Session(engine) as session:
        row = session.get(Setting, "app")
        assert row is not None
        assert row.value["active_project"] == "keep"
        assert row.rev == 1


def test_cancelled_job_waiting_inside_a_batch_is_not_executed(
    storage: tuple[Writer, Engine], monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer, engine = storage
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(writer_module, "_WINDOW_S", 0.2)

    def first(_session: Session) -> None:
        entered.set()
        assert release.wait(2)

    async def scenario() -> None:
        writer.post(first)
        pending = writer.submit(lambda session: _put(session, "later-in-batch"))
        assert await asyncio.to_thread(entered.wait, 1)
        assert pending.cancel()
        await asyncio.sleep(0)
        release.set()
        _ = await asyncio.to_thread(writer.call, lambda _session: None)

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    with Session(engine) as session:
        assert session.get(Setting, "later-in-batch") is None


def test_batch_retry_and_stop_commit_each_successful_write_once(storage: tuple[Writer, Engine]) -> None:
    writer, engine = storage

    def refuse(_session: Session) -> None:
        message = "bad job"
        raise ValueError(message)

    async def scenario() -> None:
        first = writer.submit(lambda session: _put(session, "one"))
        bad = writer.submit(refuse)
        second = writer.submit(lambda session: _put(session, "two"))
        results = await asyncio.gather(first, bad, second, return_exceptions=True)
        assert results[0] == "one"
        assert isinstance(results[1], ValueError)
        assert results[2] == "two"

    asyncio.run(scenario())
    writer.stop()
    assert writer.failed_jobs == 1
    with Session(engine) as session:
        assert set(session.scalars(select(Setting.key))) == {"one", "two"}


def test_a_closed_event_loop_does_not_kill_the_writer(storage: tuple[Writer, Engine]) -> None:
    writer, engine = storage
    entered, release = threading.Event(), threading.Event()

    def running(session: Session) -> str:
        entered.set()
        assert release.wait(2)
        return _put(session, "committed")

    async def scenario() -> None:
        pending = writer.submit(running)
        assert await asyncio.to_thread(entered.wait, 1)
        assert pending.cancel()

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    assert writer.call(lambda session: _put(session, "next")) == "next"
    with Session(engine) as session:
        assert set(session.scalars(select(Setting.key))) == {"committed", "next"}


def test_failed_settings_via_ipc_roll_back_and_the_connection_stays_usable(tmp_path: Path) -> None:
    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        before = await client.call("settings/get", EmptyParams(), Settings)
        engine = daemon._engine
        assert engine is not None

        def refuse(session: Session) -> None:
            if session.bind is engine:
                row = session.get(Setting, "app")
                if row is not None and row.value.get("theme") == "dark":
                    message = "settings fault injection"
                    raise RuntimeError(message)

        try:
            event.listen(Session, "before_commit", refuse)
            try:
                error = await asyncio.wait_for(client.error("settings/update", _patch()), timeout=3)
                assert error == "INTERNAL_ERROR"
            finally:
                event.remove(Session, "before_commit", refuse)
            assert await client.call("settings/get", EmptyParams(), Settings) == before
            assert daemon.tabs._approvals_mode == before.approvals_mode
            assert (await client.snapshot()).settings_rev == before.rev
            updated = await client.call("settings/update", _patch(), Settings)
            assert updated.rev == 1
            assert updated.approvals_mode == "native"
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_settings_worktree_and_language_choices_survive_an_ipc_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            patch = SettingsPatch(worktrees_dir="/chosen", codegraph_languages=frozenset({"python"}),
                                  approvals_mode="native", setup_completed=True, theme="dark")
            updated = await client.call("settings/update", SettingsUpdateParams(rev=0, patch=patch), Settings)
        finally:
            await _stop(daemon, task, client)
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            restored = await client.call("settings/get", EmptyParams(), Settings)
            assert restored == updated
            assert restored.worktrees_dir == "/chosen"
            assert restored.codegraph_languages == frozenset({"python"})
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())
