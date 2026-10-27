"""Retention: event ring, quota, approvals, checkpoints, and closed tabs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from aconsole.core.domain.agent import AgentKind
from aconsole.core.domain.tab_status import TabStatus
from aconsole.core.infrastructure.persistence.cleanup import sweep
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.models import (
    Approval,
    ApprovalStatus,
    Checkpoint,
    EventLog,
    EventSource,
    Project,
    QuotaHistory,
    Setting,
    Tab,
)

if TYPE_CHECKING:
    from pathlib import Path

_NOW = datetime(2026, 10, 26, tzinfo=UTC)
_EVENTS = 2002
_KEEP = 5


def test_persistence_c_retention(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'store.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    with Session(engine) as session:
        _seed(session)
        session.commit()
    refs: list[str] = []
    with Session(engine) as session:
        result = sweep(session, now=_NOW, drop_ref=refs.append)
        session.commit()
    assert result.events_deleted == _EVENTS - 2000
    assert result.quota_deleted == 1
    assert result.approvals_deleted == 1
    assert result.checkpoints_deleted == 2
    assert result.tabs_deleted == 1
    assert result.refs == tuple(refs)
    assert "refs/aconsole/checkpoints/live/1" in refs
    assert "refs/aconsole/checkpoints/live/2" in refs
    assert "refs/aconsole/checkpoints/old/1" in refs
    with Session(engine) as session:
        live_events = session.scalar(select(func.count()).select_from(EventLog).where(EventLog.tab_id == "tab-live"))
        old_events = session.scalar(select(func.count()).select_from(EventLog).where(EventLog.tab_id == "tab-old"))
        quota = session.scalar(select(func.count()).select_from(QuotaHistory))
        approvals = session.scalars(select(Approval.summary)).all()
        turns = session.scalars(select(Checkpoint.turn).where(Checkpoint.tab_id == "tab-live")).all()
        tabs = session.scalars(select(Tab.id)).all()
    assert live_events == 2000
    assert old_events == 0
    assert quota == 1
    assert approvals == ["fresh"]
    assert sorted(turns) == [3, 4, 5, 6, 7]
    assert tabs == ["tab-live", "tab-recent"]


def _seed(session: Session) -> None:
    session.add(
        Project(
            id="01JB2Z00000000000000000001",
            name="demo",
            root_path="/home/sas/Projects/demo",
            languages=["python"],
            created_at=_NOW,
        )
    )
    session.add(Setting(key="checkpoints_keep", value={"keep": _KEEP}, rev=1))
    session.flush()
    session.add(_tab("tab-live", closed_at=None))
    session.add(_tab("tab-recent", closed_at=_NOW - timedelta(days=1)))
    session.add(_tab("tab-old", closed_at=_NOW - timedelta(days=31)))
    session.flush()
    moment = _NOW - timedelta(days=1)
    session.execute(
        insert(EventLog),
        [
            {
                "tab_id": "tab-live",
                "source": EventSource.CORE,
                "event_type": "tick",
                "payload": {"n": index},
                "at": moment,
            }
            for index in range(_EVENTS)
        ],
    )
    session.add(
        EventLog(tab_id="tab-old", source=EventSource.CORE, event_type="tick", payload={"n": 0}, at=moment)
    )
    session.add(
        QuotaHistory(
            account_id="claude:demo",
            window_key="5h",
            used_pct=1.0,
            observed_at=_NOW - timedelta(days=31),
        )
    )
    session.add(
        QuotaHistory(
            account_id="claude:demo",
            window_key="5h",
            used_pct=2.0,
            observed_at=_NOW - timedelta(days=1),
        )
    )
    session.add(_approval("old", _NOW - timedelta(days=91)))
    session.add(_approval("fresh", _NOW - timedelta(days=1)))
    for turn in range(1, 8):
        session.add(_checkpoint("tab-live", turn, f"refs/aconsole/checkpoints/live/{turn}"))
    session.add(_checkpoint("tab-old", 1, "refs/aconsole/checkpoints/old/1"))


def _tab(tab_id: str, *, closed_at: datetime | None) -> Tab:
    return Tab(
        id=tab_id,
        project_id="01JB2Z00000000000000000001",
        agent_kind=AgentKind.CLAUDE,
        title=tab_id,
        tab_order=0,
        last_status=TabStatus.IDLE,
        created_at=_NOW,
        updated_at=_NOW,
        closed_at=closed_at,
    )


def _approval(summary: str, requested_at: datetime) -> Approval:
    return Approval(
        id=f"01JB2Z0000000000000000{summary}",
        tab_id="tab-live",
        agent_session_id="sess",
        tool="bash",
        input_sha256="a" * 64,
        nonce="n" * 32,
        summary=summary,
        risk=[],
        status=ApprovalStatus.ALLOWED,
        requested_at=requested_at,
        expires_at=requested_at,
    )


def _checkpoint(tab_id: str, turn: int, ref: str) -> Checkpoint:
    return Checkpoint(
        id=f"{tab_id}-{turn}",
        tab_id=tab_id,
        turn=turn,
        git_ref=ref,
        tree_sha="b" * 64,
        label="snap",
        files_changed=0,
        insertions=0,
        deletions=0,
        created_at=_NOW,
    )
