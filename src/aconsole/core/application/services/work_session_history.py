"""Journal before taking a snapshot; recover its ref if the core stopped before acknowledging it."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from aconsole.contracts.common import TabId
from aconsole.contracts.work_sessions import WorkSessionHistoryResult, WorkSessionSnapshotView
from aconsole.core.application.ports.checkpoint_store import CheckpointStore, CheckpointStoreError
from aconsole.core.application.ports.git_repo import GitCommandError, GitReader
from aconsole.core.application.ports.plan_store import StoreWriteError
from aconsole.core.application.ports.work_session_store import WorkSessionHistoryStore
from aconsole.core.application.services.git_ops import Tokens
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.work_session import WorkSession, WorkSessionSnapshot


class WorkSessionHistory:
    def __init__(self, snapshots: CheckpointStore, reader: GitReader, now: Callable[[], datetime]) -> None:
        self._snapshots: CheckpointStore = snapshots
        self._reader: GitReader = reader
        self._now: Callable[[], datetime] = now
        self._store: WorkSessionHistoryStore | None = None
        self._records: dict[tuple[str, int], WorkSessionSnapshot] = {}
        self.tokens: Tokens[tuple[str, int, str]] = Tokens(now)
        self.actor_context: Callable[[str], tuple[str, str, str | None]] = lambda actor: (actor, "", None)

    def use_store(self, store: WorkSessionHistoryStore) -> None:
        self._store = store
        self._records = {(row.work_session_id, row.sequence): row for row in store.load()}

    def recover(self, session: WorkSession, repository: Path | None = None) -> None:
        pending = [row for row in self.records(session.id) if row.status in {"pending", "legacy"}]
        if not pending:
            return
        refs = {ref.name: ref.oid for ref in self._reader.refs(repository or Path(session.worktree))}
        previous: dict[str, str] = {}
        for row in pending:
            oid = refs.get(row.ref)
            self._save(
                replace(
                    row,
                    oid=oid,
                    previous_oid=previous.get(row.worktree) if row.status == "legacy" else row.previous_oid,
                    status="finished" if oid else "interrupted",
                )
            )
            if oid:
                previous[row.worktree] = oid

    def records(self, session_id: str) -> tuple[WorkSessionSnapshot, ...]:
        return tuple(
            sorted(
                (row for row in tuple(self._records.values()) if row.work_session_id == session_id),
                key=lambda row: row.sequence,
            )
        )

    def view(self, session_id: str) -> WorkSessionHistoryResult:
        return WorkSessionHistoryResult(
            snapshots=tuple(
                WorkSessionSnapshotView(
                    sequence=row.sequence,
                    kind=row.kind,
                    actor_tab_id=None if row.actor_tab_id is None else TabId(row.actor_tab_id),
                    worktree=row.worktree,
                    ref=row.ref,
                    head_oid=row.head_oid,
                    oid=row.oid,
                    previous_oid=row.previous_oid,
                    label=row.label,
                    status=row.status,
                    created_at=row.created_at,
                    actor_title=row.actor_title,
                    agent=row.agent,
                    plan_node=row.plan_node,
                )
                for row in self.records(session_id)
            )
        )

    def capture(
        self,
        session: WorkSession,
        actor: str | None,
        kind: str,
        label: str,
        *,
        worktree: str | None = None,
    ) -> WorkSessionSnapshot:
        return self.complete(self.prepare(session, actor, kind, label, worktree=worktree))

    def prepare(
        self,
        session: WorkSession,
        actor: str | None,
        kind: str,
        label: str,
        *,
        worktree: str | None = None,
    ) -> WorkSessionSnapshot:
        records = self.records(session.id)
        sequence = 1 if not records else records[-1].sequence + 1
        path = session.worktree if worktree is None else worktree
        previous = next((row.oid for row in reversed(records) if row.oid and row.worktree == path), None)
        pending = WorkSessionSnapshot(
            work_session_id=session.id,
            sequence=sequence,
            kind=kind,
            actor_tab_id=actor,
            worktree=path,
            ref=f"refs/aconsole/sessions/{session.id}/checkpoints/{sequence}",
            head_oid=None,
            oid=None,
            tree=None,
            previous_oid=previous,
            label=label[:200],
            status="pending",
            created_at=self._now(),
        )
        if actor is not None:
            title, agent, node = self.actor_context(actor)
            pending = replace(pending, actor_title=title, agent=agent, plan_node=node)
        self._save(pending)
        return pending

    def complete(self, pending: WorkSessionSnapshot) -> WorkSessionSnapshot:
        path = Path(pending.worktree)
        try:
            captured = self._snapshots.capture(path, pending.ref, pending.label)
            oid = next((row.oid for row in self._reader.refs(path) if row.name == pending.ref), None)
        except (CheckpointStoreError, GitCommandError) as exc:
            raise DomainError(code=3020, name="GIT_COMMAND_FAILED", retryable=True) from exc
        if oid is None:
            raise DomainError(code=3005, name="CHECKPOINT_NOT_FOUND", retryable=False)
        finished = replace(
            pending, oid=oid, head_oid=self._reader.parent_of(path, oid), tree=captured.tree, status="finished"
        )
        self._save(finished)
        return finished

    def require(self, session_id: str, sequence: int) -> WorkSessionSnapshot:
        found = self._records.get((session_id, sequence))
        if found is None or found.oid is None or found.status != "finished":
            raise DomainError(code=3005, name="CHECKPOINT_NOT_FOUND", retryable=False)
        return found

    def materialize(self, session: WorkSession, sequence: int) -> None:
        target = self.require(session.id, sequence)
        self._snapshots.materialize(Path(session.worktree), target.ref)

    def _save(self, record: WorkSessionSnapshot) -> None:
        if self._store is not None:
            try:
                self._store.save(record)
            except (StoreWriteError, OSError, TimeoutError) as exc:
                raise DomainError(code=3032, name="DATABASE_TRANSACTION_FAILED", retryable=True) from exc
        self._records[(record.work_session_id, record.sequence)] = record
