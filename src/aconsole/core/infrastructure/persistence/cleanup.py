"""Retention from DATA_MODEL §6. Worktree removal stays with the close rules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import ColumnElement, CursorResult, and_, delete, select
from sqlalchemy.orm import Session, aliased

from aconsole.core.domain.checkpoints import check_keep
from aconsole.core.infrastructure.persistence.models import (
    Approval,
    Checkpoint,
    EventLog,
    GateReceipt,
    GitRefDeletion,
    Project,
    QuotaHistory,
    Setting,
    Tab,
    WorkSessionSnapshotRow,
)

_EVENTS_PER_TAB = 2000
_QUOTA_DAYS = 30
_APPROVAL_DAYS = 90
_CLOSED_TAB_DAYS = 30
_DEFAULT_KEEP = 100
_KEEP_KEY = "checkpoints_keep"
_APP_KEY = "app"
_PORTION = 5000

type _IdTable = type[EventLog | QuotaHistory | Approval]


@dataclass(frozen=True, kw_only=True)
class SweepResult:
    events_deleted: int
    quota_deleted: int
    approvals_deleted: int
    checkpoints_deleted: int
    tabs_deleted: int
    refs: tuple[str, ...]
    more: bool = False
    receipts_deleted: int = 0
    gate_log_refs: tuple[str, ...] = ()


def sweep(session: Session, *, now: datetime, drop_ref: Callable[[str], None]) -> SweepResult:
    """Drop expired rows. ``drop_ref`` receives each removed checkpoint git ref."""
    if now.tzinfo is None:
        message = "cleanup clock must be timezone-aware"
        raise ValueError(message)
    refs: list[str] = []

    def dropped(ref: str) -> None:
        _queue_ref(session, ref)
        refs.append(ref)
        drop_ref(ref)

    events = _trim_events(session)
    quota = _trim_quota(session, now)
    approvals = _trim_approvals(session, now)
    checkpoints = _trim_checkpoints(session, _keep(session), dropped)
    tabs = _trim_closed_tabs(session, now, dropped)
    receipts, gate_logs = trim_gate_receipts(session, now)
    session.flush()
    return SweepResult(
        events_deleted=events,
        quota_deleted=quota,
        approvals_deleted=approvals,
        checkpoints_deleted=checkpoints,
        tabs_deleted=tabs,
        refs=tuple(refs),
        more=_PORTION in {quota, approvals, receipts} or events >= _PORTION,
        receipts_deleted=receipts,
        gate_log_refs=gate_logs,
    )


def _keep(session: Session) -> int:
    row = session.get(Setting, _KEEP_KEY)
    if row is not None:
        raw = row.value.get("keep")
        return check_keep(raw) if isinstance(raw, int) else _DEFAULT_KEEP
    # The Settings document holds what the user chose.
    app = session.get(Setting, _APP_KEY)
    chosen = app.value.get(_KEEP_KEY) if app is not None else None
    return check_keep(chosen) if isinstance(chosen, int) else _DEFAULT_KEEP


def _delete_portion(session: Session, table: _IdTable, condition: ColumnElement[bool]) -> int:
    """Delete by condition, at most one portion: a long delete would hold up every other write."""
    chosen = select(table.id).where(condition).limit(_PORTION)
    result = session.execute(delete(table).where(table.id.in_(chosen)).execution_options(synchronize_session=False))
    return result.rowcount if isinstance(result, CursorResult) else 0


def _trim_events(session: Session) -> int:
    deleted = 0
    for tab_id in session.scalars(select(EventLog.tab_id).distinct()).all():
        boundary = (
            select(EventLog.id)
            .where(EventLog.tab_id == tab_id)
            .order_by(EventLog.id.desc())
            .offset(_EVENTS_PER_TAB - 1)
            .limit(1)
            .scalar_subquery()
        )
        deleted += _delete_portion(session, EventLog, and_(EventLog.tab_id == tab_id, EventLog.id < boundary))
    return deleted


def _trim_quota(session: Session, now: datetime) -> int:
    return _delete_portion(session, QuotaHistory, QuotaHistory.observed_at < now - timedelta(days=_QUOTA_DAYS))


def _trim_approvals(session: Session, now: datetime) -> int:
    return _delete_portion(session, Approval, Approval.requested_at < now - timedelta(days=_APPROVAL_DAYS))


def _trim_checkpoints(session: Session, keep: int, dropped: Callable[[str], None]) -> int:
    tab_ids = session.scalars(select(Checkpoint.tab_id).distinct()).all()
    deleted = 0
    for tab_id in tab_ids:
        rows = session.scalars(
            select(Checkpoint).where(
                Checkpoint.tab_id == tab_id,
                ~Checkpoint.git_ref.in_(select(WorkSessionSnapshotRow.ref)),
            ).order_by(Checkpoint.turn.desc())
        ).all()
        for row in rows[keep:]:
            dropped(row.git_ref)
            session.delete(row)
            deleted += 1
    return deleted


def _trim_closed_tabs(session: Session, now: datetime, dropped: Callable[[str], None]) -> int:
    cutoff = now - timedelta(days=_CLOSED_TAB_DAYS)
    tabs = session.scalars(select(Tab).where(
        Tab.closed_at.is_not(None), Tab.closed_at < cutoff, Tab.work_session_id.is_(None),
    )).all()
    for tab in tabs:
        refs = session.scalars(select(Checkpoint.git_ref).where(Checkpoint.tab_id == tab.id)).all()
        for ref in refs:
            dropped(ref)
        session.delete(tab)
    return len(tabs)



def _queue_ref(session: Session, ref: str) -> None:
    """Store the repository before cascade deletion removes its tab/workspace context."""
    if not ref.startswith("refs/aconsole/checkpoints/"):
        return
    repo = session.scalar(select(Project.root_path).join(Tab, Tab.project_id == Project.id).join(
        Checkpoint, Checkpoint.tab_id == Tab.id,
    ).where(Checkpoint.git_ref == ref).limit(1))
    if repo is not None and session.get(GitRefDeletion, (repo, ref)) is None:
        session.add(GitRefDeletion(repo_path=repo, git_ref=ref))



def trim_gate_receipts(session: Session, now: datetime) -> tuple[int, tuple[str, ...]]:
    """Remove old receipts, preserving the newest per (node, tier), including ties.

    Return unreferenced logs for deletion only after the writer has committed.
    Attempts and blocks remain durable audit history.
    """
    if now.tzinfo is None:
        message = "cleanup clock must be timezone-aware"
        raise ValueError(message)
    newer = aliased(GateReceipt)
    has_newer = (
        select(newer.receipt_hash)
        .where(
            newer.node_id == GateReceipt.node_id,
            newer.tier == GateReceipt.tier,
            (newer.created_at > GateReceipt.created_at)
            | ((newer.created_at == GateReceipt.created_at) & (newer.receipt_hash > GateReceipt.receipt_hash)),
        )
        .exists()
    )
    chosen = (
        select(GateReceipt.receipt_hash)
        .where(
            GateReceipt.created_at < now - timedelta(days=30),
            has_newer,
        )
        .limit(_PORTION)
    )
    removed = (
        session.execute(
            delete(GateReceipt)
            .where(GateReceipt.receipt_hash.in_(chosen))
            .returning(
                GateReceipt.log_ref,
            )
        )
        .scalars()
        .all()
    )
    candidates = {ref for ref in removed if ref is not None}
    retained = set(session.scalars(select(GateReceipt.log_ref).where(GateReceipt.log_ref.in_(candidates))))
    return len(removed), tuple(sorted(candidates - retained))


def remove_gate_logs(log_refs: tuple[str, ...], log_root: Path) -> None:
    """Unlink committed, unreferenced gate logs confined to the configured directory."""
    root = log_root.resolve()
    for ref in log_refs:
        path = Path(ref)
        if path.suffix == ".log" and path.resolve().parent == root:
            path.unlink(missing_ok=True)
