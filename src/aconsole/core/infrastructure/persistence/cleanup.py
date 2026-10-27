"""Retention from DATA_MODEL §6. Worktree removal stays with the close rules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from aconsole.core.domain.checkpoints import check_keep
from aconsole.core.infrastructure.persistence.models import (
    Approval,
    Checkpoint,
    EventLog,
    QuotaHistory,
    Setting,
    Tab,
)

_EVENTS_PER_TAB = 2000
_QUOTA_DAYS = 30
_APPROVAL_DAYS = 90
_CLOSED_TAB_DAYS = 30
_DEFAULT_KEEP = 5
_KEEP_KEY = "checkpoints_keep"


@dataclass(frozen=True, kw_only=True)
class SweepResult:
    events_deleted: int
    quota_deleted: int
    approvals_deleted: int
    checkpoints_deleted: int
    tabs_deleted: int
    refs: tuple[str, ...]


def sweep(session: Session, *, now: datetime, drop_ref: Callable[[str], None]) -> SweepResult:
    """Drop expired rows. ``drop_ref`` receives each removed checkpoint git ref."""
    if now.tzinfo is None:
        message = "cleanup clock must be timezone-aware"
        raise ValueError(message)
    refs: list[str] = []

    def dropped(ref: str) -> None:
        refs.append(ref)
        drop_ref(ref)

    events = _trim_events(session)
    quota = _trim_quota(session, now)
    approvals = _trim_approvals(session, now)
    checkpoints = _trim_checkpoints(session, _keep(session), dropped)
    tabs = _trim_closed_tabs(session, now, dropped)
    session.flush()
    return SweepResult(
        events_deleted=events,
        quota_deleted=quota,
        approvals_deleted=approvals,
        checkpoints_deleted=checkpoints,
        tabs_deleted=tabs,
        refs=tuple(refs),
    )


def _keep(session: Session) -> int:
    row = session.get(Setting, _KEEP_KEY)
    if row is None:
        return _DEFAULT_KEEP
    raw = row.value.get("keep")
    if not isinstance(raw, int):
        return _DEFAULT_KEEP
    return check_keep(raw)


def _trim_events(session: Session) -> int:
    tab_ids = session.scalars(select(EventLog.tab_id).distinct()).all()
    deleted = 0
    for tab_id in tab_ids:
        boundary = (
            select(EventLog.id)
            .where(EventLog.tab_id == tab_id)
            .order_by(EventLog.id.desc())
            .offset(_EVENTS_PER_TAB - 1)
            .limit(1)
            .scalar_subquery()
        )
        doomed = session.scalars(select(EventLog.id).where(EventLog.tab_id == tab_id, EventLog.id < boundary)).all()
        if doomed:
            _ = session.execute(delete(EventLog).where(EventLog.id.in_(doomed)))
        deleted += len(doomed)
    return deleted


def _trim_quota(session: Session, now: datetime) -> int:
    cutoff = now - timedelta(days=_QUOTA_DAYS)
    doomed = session.scalars(select(QuotaHistory.id).where(QuotaHistory.observed_at < cutoff)).all()
    if doomed:
        _ = session.execute(delete(QuotaHistory).where(QuotaHistory.id.in_(doomed)))
    return len(doomed)


def _trim_approvals(session: Session, now: datetime) -> int:
    cutoff = now - timedelta(days=_APPROVAL_DAYS)
    doomed = session.scalars(select(Approval.id).where(Approval.requested_at < cutoff)).all()
    if doomed:
        _ = session.execute(delete(Approval).where(Approval.id.in_(doomed)))
    return len(doomed)


def _trim_checkpoints(session: Session, keep: int, dropped: Callable[[str], None]) -> int:
    tab_ids = session.scalars(select(Checkpoint.tab_id).distinct()).all()
    deleted = 0
    for tab_id in tab_ids:
        rows = session.scalars(
            select(Checkpoint).where(Checkpoint.tab_id == tab_id).order_by(Checkpoint.turn.desc())
        ).all()
        for row in rows[keep:]:
            dropped(row.git_ref)
            session.delete(row)
            deleted += 1
    return deleted


def _trim_closed_tabs(session: Session, now: datetime, dropped: Callable[[str], None]) -> int:
    cutoff = now - timedelta(days=_CLOSED_TAB_DAYS)
    tabs = session.scalars(select(Tab).where(Tab.closed_at.is_not(None), Tab.closed_at < cutoff)).all()
    for tab in tabs:
        refs = session.scalars(select(Checkpoint.git_ref).where(Checkpoint.tab_id == tab.id)).all()
        for ref in refs:
            dropped(ref)
        session.delete(tab)
    return len(tabs)
