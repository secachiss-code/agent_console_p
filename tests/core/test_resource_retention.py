"""Bounded resource maintenance preserves task history and survives process restarts."""
from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from aconsole.core.infrastructure.agents.usage_log import UsageLog, claude_events, codex_events
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from tests.core.git_helpers import commit, git, new_repo

if TYPE_CHECKING:
    import pytest


_AT = datetime(2026, 11, 6, tzinfo=UTC)


def _claude(identity: str, output: int) -> bytes:
    return json.dumps({"type": "assistant", "timestamp": _AT.isoformat(), "requestId": identity,
                       "message": {"id": identity, "model": "claude-test",
                                   "usage": {"output_tokens": output}}}).encode()


def test_usage_append_reads_only_new_bytes_and_handles_partial_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "session.jsonl"
    initial = b"\n".join(_claude(str(index), 1) for index in range(500)) + b"\n"
    path.write_bytes(initial)
    reads = []
    original = Path.open

    class Counted(io.BufferedReader):
        def read(self, size: int = -1, /) -> bytes:
            data = super().read(size)
            reads.append(len(data))
            return data

        def readline(self, size: int = -1, /) -> bytes:
            data = super().readline(size)
            reads.append(len(data))
            return data

    def observe(file: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
        if file == path and mode == "rb":
            return Counted(io.FileIO(file, "r"))
        return original(file, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observe)
    log = UsageLog(lambda: tmp_path, claude_events)
    assert log.models(_AT)[0].output_tokens == 500
    reads.clear()
    assert log.models(_AT)[0].output_tokens == 500
    assert sum(reads) == 0
    addition = _claude("new", 7) + b"\n"
    with original(path, "ab") as stream:
        _ = stream.write(addition[:20])
    assert log.models(_AT)[0].output_tokens == 500
    with original(path, "ab") as stream:
        _ = stream.write(addition[20:])
    assert log.models(_AT)[0].output_tokens == 507
    assert sum(reads) <= len(addition)


def test_codex_append_keeps_model_and_duplicate_total_state(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    context = {"type": "turn_context", "payload": {"model": "codex-test"}}

    def tokens(total: int) -> bytes:
        return json.dumps({"type": "event_msg", "timestamp": _AT.isoformat(), "payload": {
            "type": "token_count", "info": {"total_token_usage": {"total_tokens": total},
            "last_token_usage": {"input_tokens": 5, "output_tokens": 2}}}}).encode()

    path.write_bytes(json.dumps(context).encode() + b"\n" + tokens(7) + b"\n")
    log = UsageLog(lambda: tmp_path, codex_events)
    assert log.models(_AT)[0].input_tokens == 5
    with path.open("ab") as stream:
        _ = stream.write(tokens(7) + b"\n" + tokens(14))
    usage = log.models(_AT)[0]
    assert usage.model == "codex-test"
    assert usage.input_tokens == 10
    assert usage.output_tokens == 4
    with path.open("ab") as stream:
        _ = stream.write(b"\n")
    assert log.models(_AT)[0] == usage
    path.rename(tmp_path / "old.log")
    path.write_bytes(json.dumps(context).encode() + b"\n" + tokens(7) + b"\n")
    assert log.models(_AT)[0].input_tokens == 5
    path.write_bytes(b"")
    assert log.models(_AT) == ()


def test_daemon_logging_rotates_during_one_process(tmp_path: Path) -> None:
    from aconsole.core.infrastructure.runtime_logging import configure_logging

    root = logging.getLogger()
    level = root.level
    target = tmp_path / "private/core.log"
    handler = configure_logging(target, max_bytes=512, backups=2)
    try:
        assert configure_logging(target, max_bytes=512, backups=2) is handler
        logger = logging.getLogger("aconsole.core.resource-test")
        for number in range(30):
            logger.warning("entry-%d %s", number, "x" * 160)
        logger.warning("final-marker")
        handler.flush()
        files = list(target.parent.glob("core.log*"))
        assert len(files) <= 3
        assert all(file.stat().st_size <= 512 for file in files)
        assert "final-marker" in target.read_text(encoding="utf-8")
        assert target.stat().st_mode & 0o777 == 0o600
    finally:
        root.removeHandler(handler)
        handler.close()
        root.setLevel(level)


def test_wal_repeated_batches_compact_without_losing_rows(tmp_path: Path) -> None:
    from aconsole.core.infrastructure.persistence.engine import checkpoint_wal
    from aconsole.core.infrastructure.persistence.models import Setting

    database = tmp_path / "database.db"
    url = f"sqlite:///{database}"
    migrate(url, "head")
    engine = write_engine(url)
    try:
        for batch in range(5):
            with Session(engine) as session:
                for index in range(30):
                    session.add(Setting(key=f"{batch}-{index}", value={"data": "x" * 8192}, rev=1))
                session.commit()
            result = checkpoint_wal(engine, truncate=True)
            assert not result.busy
            assert database.with_name(database.name + "-wal").stat().st_size == 0
        with Session(engine) as session:
            assert session.get(Setting, "4-29") is not None
    finally:
        engine.dispose()


def test_evicted_ref_deletions_survive_restart_and_preserve_session_refs(tmp_path: Path) -> None:
    from aconsole.core.infrastructure.git.retention import drain_ref_deletions
    from aconsole.core.infrastructure.persistence.cleanup import sweep
    from aconsole.core.infrastructure.persistence.models import Project, WorkSessionRow, WorkSessionSnapshotRow
    from aconsole.core.infrastructure.persistence.repositories.ref_deletions import RefDeletionQueue
    from aconsole.core.infrastructure.persistence.writer import Writer
    from tests.core.test_persistence_c import _NOW, _seed

    repo = new_repo(tmp_path / "repo")
    head = commit(repo, "f", "data\n", "seed")
    url = f"sqlite:///{tmp_path / 'database.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    with Session(engine) as session:
        _seed(session)
        project = session.get(Project, "01JB2Z00000000000000000001")
        assert project is not None
        project.root_path = str(repo)
        session.add(WorkSessionRow(id="task", project_id=project.id, title="keep history", worktree=str(repo),
                    workspace_mode="main_checkout", status="archived", created_at=_NOW, updated_at=_NOW))
        session.flush()
        session.add(WorkSessionSnapshotRow(work_session_id="task", sequence=1, kind="legacy_checkpoint",
                    worktree=str(repo), ref="refs/aconsole/checkpoints/live/1", label="keep", status="finished",
                    oid=head, created_at=_NOW))
        session.commit()
    refs = [f"refs/aconsole/checkpoints/live/{number}" for number in range(1, 8)]
    refs += ["refs/aconsole/checkpoints/old/1", "refs/aconsole/sessions/task/checkpoints/1"]
    for ref in refs:
        _ = git(repo, "update-ref", ref, head)
    with Session(engine) as session:
        swept = sweep(session, now=_NOW, drop_ref=lambda _ref: None)
        session.commit()
    assert swept.refs
    engine.dispose()
    engine = write_engine(url)
    writer = Writer(engine)
    writer.start()
    try:
        queue = RefDeletionQueue(writer)
        assert queue.pending()
        deleted = drain_ref_deletions(queue)
        assert set(deleted) == set(swept.refs)
        assert queue.pending() == ()
        remaining = git(repo, "for-each-ref", "--format=%(refname)").splitlines()
        assert "refs/aconsole/checkpoints/live/1" in remaining
        assert "refs/aconsole/sessions/task/checkpoints/1" in remaining
        assert not set(swept.refs) & set(remaining)
    finally:
        writer.stop()
        engine.dispose()



def test_immediate_retention_keeps_a_ref_owned_by_session_history(tmp_path: Path) -> None:
    from aconsole.core.application.services.checkpoints import CheckpointService
    from aconsole.core.infrastructure.git.checkpoints import GitCheckpoints

    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "f", "base\n", "seed")
    service = CheckpointService(GitCheckpoints(), keep=5)
    protected = "refs/aconsole/checkpoints/tab/1"
    service.protected_ref = lambda ref: ref == protected
    for number in range(7):
        _ = service.on_user_prompt(repo, "tab", str(number), _AT)
    remaining = git(repo, "for-each-ref", "--format=%(refname)").splitlines()
    assert protected in remaining
    assert "refs/aconsole/checkpoints/tab/2" not in remaining
    assert len(service.list("tab")) == 5


def test_failed_ref_deletion_retries_and_never_deletes_a_user_branch(tmp_path: Path) -> None:
    from aconsole.core.infrastructure.git.retention import drain_ref_deletions
    from aconsole.core.infrastructure.persistence.models import GitRefDeletion
    from aconsole.core.infrastructure.persistence.repositories.ref_deletions import RefDeletionQueue
    from aconsole.core.infrastructure.persistence.writer import Writer

    repo = new_repo(tmp_path / "repo")
    head = commit(repo, "f", "data\n", "seed")
    ref = "refs/aconsole/checkpoints/retired/1"
    _ = git(repo, "update-ref", ref, head)
    url = f"sqlite:///{tmp_path / 'retry.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    with Session(engine) as session:
        session.add(GitRefDeletion(repo_path=str(repo), git_ref=ref))
        session.add(GitRefDeletion(repo_path=str(repo), git_ref="refs/heads/trunk"))
        session.commit()
    writer = Writer(engine)
    writer.start()
    try:
        queue = RefDeletionQueue(writer)

        def unavailable(_repo: Path, _ref: str) -> None:
            message = "temporary repository failure"
            raise OSError(message)

        assert drain_ref_deletions(queue, unavailable) == ()
        assert len(queue.pending()) == 2
        assert git(repo, "rev-parse", ref) == head
        assert drain_ref_deletions(queue) == (ref,)
        assert len(queue.pending()) == 1
        assert git(repo, "rev-parse", "refs/heads/trunk") == head
    finally:
        writer.stop()
        engine.dispose()



def test_real_entrypoint_configures_logging_without_requiring_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aconsole.core import __main__ as entry

    configured = []
    monkeypatch.setattr(entry, "configure_logging", lambda: configured.append(True))
    monkeypatch.setattr(entry, "serve_daemon", lambda _socket: object())

    async def serve(_daemon: object) -> int:
        return 0

    monkeypatch.setattr(entry, "run_with_signals", serve)
    assert entry.main(["--serve", "--socket", str(tmp_path / "core.sock"), "--allow-gil"]) == 0
    assert configured == [True]



def test_serving_daemon_drains_ref_jobs_restored_from_database(tmp_path: Path) -> None:
    from sqlalchemy import select

    from aconsole.core.infrastructure.persistence.models import GitRefDeletion
    from tests.core.test_wire import _connect, _daemon, _stop

    repo = new_repo(tmp_path / "repo")
    head = commit(repo, "f", "data\n", "seed")
    ref = "refs/aconsole/checkpoints/retired/1"
    _ = git(repo, "update-ref", ref, head)
    url = f"sqlite:///{tmp_path / 'aconsole.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    with Session(engine) as session:
        session.add(GitRefDeletion(repo_path=str(repo), git_ref=ref))
        session.commit()
    engine.dispose()

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        daemon.sweep_every_s = 0.02
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            _ = await client.snapshot()
            for _attempt in range(100):
                refs = await asyncio.to_thread(git, repo, "for-each-ref", "--format=%(refname)")
                if ref not in refs.splitlines():
                    break
                await asyncio.sleep(0.02)
            assert ref not in refs.splitlines()
        finally:
            await _stop(daemon, serving, client)

    asyncio.run(scenario())
    engine = write_engine(url)
    try:
        with Session(engine) as session:
            assert session.scalars(select(GitRefDeletion)).all() == []
    finally:
        engine.dispose()
