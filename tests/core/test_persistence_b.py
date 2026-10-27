"""Writer, readers, unit of work, and a round-trip for every repository."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session, sessionmaker

from aconsole.core.domain.agent import AgentKind
from aconsole.core.domain.plan import NodeStatus
from aconsole.core.domain.tab_status import TabStatus
from aconsole.core.domain.workspace import WorkspaceMode
from aconsole.core.infrastructure.persistence.engine import migrate, read_engine, write_engine
from aconsole.core.infrastructure.persistence.models import EventLog, EventSource, Project
from aconsole.core.infrastructure.persistence.readers import Readers
from aconsole.core.infrastructure.persistence.repositories.catalog import (
    REPOSITORIES,
    AgentAccountRecord,
    AgentAccountRepository,
    AgentSessionRecord,
    AgentSessionRepository,
    ApprovalRecord,
    ApprovalRepository,
    ApprovalRuleRecord,
    ApprovalRuleRepository,
    ApprovalStatus,
    ArtifactKind,
    CanvasPositionRecord,
    CanvasPositionRepository,
    CheckpointRecord,
    CheckpointRepository,
    EdgeKind,
    EventLogRecord,
    EventLogRepository,
    FrecencyRecord,
    FrecencyRepository,
    HandoverRecord,
    HandoverRepository,
    IterationRecord,
    IterationRepository,
    KbEdgeKind,
    KbEdgeRecord,
    KbEdgeRepository,
    KbNodeRecord,
    KbNodeRepository,
    KbType,
    LeaseRecord,
    LeaseRepository,
    NodeArtifactRecord,
    NodeArtifactRepository,
    PlanEdgeRecord,
    PlanEdgeRepository,
    PlanNodeRecord,
    PlanNodeRepository,
    PlanRecord,
    PlanRepository,
    PlanStatus,
    ProjectRecord,
    ProjectRepository,
    QuotaCurrentRecord,
    QuotaCurrentRepository,
    QuotaHistoryRecord,
    QuotaHistoryRepository,
    SettingRecord,
    SettingRepository,
    TabRecord,
    TabRepository,
    VaultFileRecord,
    VaultFileRepository,
    WorkspaceRecord,
    WorkspaceRepository,
)
from aconsole.core.infrastructure.persistence.uow import UnitOfWork
from aconsole.core.infrastructure.persistence.writer import Writer

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sqlalchemy.engine import Engine

_WHEN = datetime(2026, 10, 26, tzinfo=UTC)
_EVENTS = 10_000
_CHUNK = 40


def _key(number: int) -> str:
    return f"01JB2Z{number:020d}"


_SCHEMA = threading.Lock()


def _prepare(path: Path) -> str:
    url = f"sqlite:///{path / 'store.db'}"
    # Alembic's SQLite reflection is not safe when several threads create schemas at once.
    with _SCHEMA:
        migrate(url, "head")
    return url


def test_persistence_b_repositories(tmp_path: Path) -> None:
    factory = sessionmaker(bind=write_engine(_prepare(tmp_path)), expire_on_commit=False)
    project, account, workspace, plan, node, other, tab = (_key(n) for n in range(1, 8))
    project_row = ProjectRecord(
        id=project,
        name="demo",
        root_path="/home/sas/Projects/demo",
        default_branch="main",
        vault_folder=None,
        languages=["python"],
        created_at=_WHEN,
    )
    account_row = AgentAccountRecord(
        id=account,
        agent_kind=AgentKind.CLAUDE,
        plan_label="Pro",
        container_name="agent-claude",
    )
    workspace_row = WorkspaceRecord(
        id=workspace,
        project_id=project,
        mode=WorkspaceMode.NEW_WORKTREE,
        path="/home/sas/Projects/demo-wt",
        branch="feature",
        base_branch="main",
        base_commit="abc",
        locked=False,
        dirty_cached=False,
        created_at=_WHEN,
        removed_at=None,
    )
    plan_row = PlanRecord(
        id=plan,
        project_id=project,
        title="plan",
        goal="ship",
        status=PlanStatus.ACTIVE,
        canvas_note_ref=None,
        created_by_tab=None,
        created_at=_WHEN,
        updated_at=_WHEN,
    )
    node_row = PlanNodeRecord(
        id=node,
        plan_id=plan,
        node_key="N-01",
        title="node",
        objective="do",
        status=NodeStatus.PENDING,
        owner_agent=AgentKind.CLAUDE,
        paths=["src"],
        acceptance=["tests"],
        test_cmd=None,
        review_required=False,
        evidence=None,
        note_ref=None,
        layer=0,
        priority=1.0,
        unmet_blockers=0,
        fence_seq=0,
        created_at=_WHEN,
        updated_at=_WHEN,
    )
    other_row = PlanNodeRecord(
        id=other,
        plan_id=plan,
        node_key="N-02",
        title="other",
        objective="wait",
        status=NodeStatus.PENDING,
        owner_agent=None,
        paths=[],
        acceptance=[],
        test_cmd=None,
        review_required=False,
        evidence=None,
        note_ref=None,
        layer=1,
        priority=0.0,
        unmet_blockers=1,
        fence_seq=0,
        created_at=_WHEN,
        updated_at=_WHEN,
    )
    tab_row = TabRecord(
        id=tab,
        project_id=project,
        workspace_id=workspace,
        agent_kind=AgentKind.CLAUDE,
        account_id=account,
        title="tab",
        tab_order=0,
        last_status=TabStatus.IDLE,
        status_detail=None,
        plan_node_id=node,
        context_used_pct=None,
        session_cost_usd=None,
        agent_session_id=None,
        closed_at=None,
        created_at=_WHEN,
        updated_at=_WHEN,
    )
    with UnitOfWork(factory) as uow:
        session = uow.session
        assert session is not None
        stored = [
            ProjectRepository().add(session, project_row),
            AgentAccountRepository().add(session, account_row),
            WorkspaceRepository().add(session, workspace_row),
            PlanRepository().add(session, plan_row),
            PlanNodeRepository().add(session, node_row),
            PlanNodeRepository().add(session, other_row),
            TabRepository().add(session, tab_row),
            AgentSessionRepository().add(
                session,
                AgentSessionRecord(
                    id=_key(8),
                    tab_id=tab,
                    agent_session_id="sess",
                    transcript_path=None,
                    started_at=_WHEN,
                    ended_at=None,
                ),
            ),
            EventLogRepository().add(
                session,
                EventLogRecord(tab_id=tab, source=EventSource.CORE, event_type="tick", payload={"n": 1}, at=_WHEN),
            ),
            QuotaCurrentRepository().add(
                session,
                QuotaCurrentRecord(
                    account_id=account,
                    window_key="5h",
                    used_pct=10.0,
                    resets_at=_WHEN,
                    estimated=False,
                    source="statusline",
                    observed_at=_WHEN,
                ),
            ),
            QuotaHistoryRepository().add(
                session,
                QuotaHistoryRecord(account_id=account, window_key="5h", used_pct=10.0, observed_at=_WHEN),
            ),
            ApprovalRepository().add(
                session,
                ApprovalRecord(
                    id=_key(9),
                    tab_id=tab,
                    agent_session_id="sess",
                    tool="bash",
                    input_sha256="a" * 64,
                    nonce="n" * 32,
                    summary="run",
                    risk=["shell"],
                    status=ApprovalStatus.PENDING,
                    decided_by=None,
                    requested_at=_WHEN,
                    decided_at=None,
                    expires_at=_WHEN,
                ),
            ),
            ApprovalRuleRepository().add(
                session,
                ApprovalRuleRecord(id=_key(10), tab_id=tab, tool="bash", command_prefix="git status", created_at=_WHEN),
            ),
            CheckpointRepository().add(
                session,
                CheckpointRecord(
                    id=_key(11),
                    tab_id=tab,
                    turn=1,
                    git_ref="refs/aconsole/checkpoints/tab/1",
                    tree_sha="b" * 64,
                    label="before",
                    files_changed=1,
                    insertions=2,
                    deletions=0,
                    created_at=_WHEN,
                ),
            ),
            PlanEdgeRepository().add(
                session,
                PlanEdgeRecord(src_node_id=node, dst_node_id=other, kind=EdgeKind.BLOCKS),
            ),
            LeaseRepository().add(
                session,
                LeaseRecord(
                    node_id=node,
                    lease_id=_key(12),
                    tab_id=tab,
                    fence=1,
                    acquired_at=_WHEN,
                    renewed_at=_WHEN,
                    expires_at=_WHEN,
                ),
            ),
            NodeArtifactRepository().add(
                session,
                NodeArtifactRecord(
                    id=_key(13),
                    node_id=node,
                    kind=ArtifactKind.NOTE,
                    ref="notes/a.md",
                    title=None,
                    created_at=_WHEN,
                ),
            ),
            CanvasPositionRepository().add(
                session,
                CanvasPositionRecord(plan_id=plan, node_id=node, x=1, y=2, user_moved=True, updated_at=_WHEN),
            ),
            KbNodeRepository().add(
                session,
                KbNodeRecord(
                    id="kb:root",
                    project_id=project,
                    node_type=KbType.FACT,
                    title="fact",
                    note_ref=None,
                    status=None,
                    source=None,
                    verified_commit=None,
                    is_stale=False,
                    updated_at=_WHEN,
                ),
            ),
            KbNodeRepository().add(
                session,
                KbNodeRecord(
                    id="kb:child",
                    project_id=project,
                    node_type=KbType.MODULE,
                    title="child",
                    note_ref="notes/child.md",
                    status=None,
                    source=None,
                    verified_commit=None,
                    is_stale=False,
                    updated_at=_WHEN,
                ),
            ),
            KbEdgeRepository().add(
                session,
                KbEdgeRecord(src_id="kb:root", dst_id="kb:child", kind=KbEdgeKind.LINKS, origin="vault"),
            ),
            VaultFileRepository().add(
                session,
                VaultFileRecord(
                    note_ref="notes/demo.md",
                    kind="note",
                    owner="app",
                    kb_id=None,
                    plan_node_id=None,
                    frontmatter_hash=None,
                    body_hash=None,
                    base_frontmatter=None,
                    mtime_ns=None,
                    size=None,
                    inode=None,
                    last_writer="app",
                    pending_write=None,
                    synced_at=_WHEN,
                ),
            ),
            HandoverRepository().add(
                session,
                HandoverRecord(
                    id=_key(14),
                    project_id=project,
                    source_tab_id=tab,
                    target_agent=AgentKind.CODEX,
                    plan_node_id=node,
                    summary_markdown="hand",
                    note_ref=None,
                    created_at=_WHEN,
                ),
            ),
            IterationRepository().add(
                session,
                IterationRecord(
                    project_id=project,
                    number=1,
                    dag_state_hash="c" * 64,
                    manifest_note_ref="notes/iter.md",
                    type_check_passed=True,
                    created_at=_WHEN,
                ),
            ),
            FrecencyRepository().add(
                session,
                FrecencyRecord(item_id="palette.open", score=1.5, last_used=_WHEN, uses=3),
            ),
            SettingRepository().add(session, SettingRecord(key="theme", value={"name": "dark"}, rev=1)),
        ]
        uow.commit()
    repos = [
        ProjectRepository(),
        AgentAccountRepository(),
        WorkspaceRepository(),
        PlanRepository(),
        PlanNodeRepository(),
        PlanNodeRepository(),
        TabRepository(),
        AgentSessionRepository(),
        EventLogRepository(),
        QuotaCurrentRepository(),
        QuotaHistoryRepository(),
        ApprovalRepository(),
        ApprovalRuleRepository(),
        CheckpointRepository(),
        PlanEdgeRepository(),
        LeaseRepository(),
        NodeArtifactRepository(),
        CanvasPositionRepository(),
        KbNodeRepository(),
        KbNodeRepository(),
        KbEdgeRepository(),
        VaultFileRepository(),
        HandoverRepository(),
        IterationRepository(),
        FrecencyRepository(),
        SettingRepository(),
    ]
    assert len(REPOSITORIES) == 24
    with factory() as reading:
        for repo, record in zip(repos, stored, strict=True):
            assert repo.one(reading, repo.identity(record)) == record


def test_persistence_b_writer_round_trip(tmp_path: Path) -> None:
    engine = write_engine(_prepare(tmp_path))
    writer = Writer(engine)
    writer.start()
    sample = ProjectRecord(
        id=_key(1),
        name="demo",
        root_path="/home/sas/Projects/demo",
        default_branch="main",
        vault_folder=None,
        languages=["python"],
        created_at=_WHEN,
    )
    try:

        def _save(session: Session) -> str:
            ProjectRepository().add(session, sample)
            return sample.id

        assert writer.call(_save) == sample.id
    finally:
        writer.stop()
    with Session(engine) as session:
        row = session.get(Project, sample.id)
    assert row is not None
    assert row.name == "demo"


def test_persistence_b_reader_is_thread_local(tmp_path: Path) -> None:
    readers = Readers(read_engine(_prepare(tmp_path)))
    first = readers.session()
    seen: list[int] = []

    def _other() -> None:
        seen.append(id(readers.session()))

    thread = threading.Thread(target=_other)
    thread.start()
    thread.join()
    assert id(readers.session()) == id(first)
    assert seen[0] != id(first)
    readers.close()


def test_persistence_b_event_rate(tmp_path: Path) -> None:
    count = asyncio.run(_event_rate(tmp_path))
    assert count == _EVENTS


async def _event_rate(path: Path) -> int:
    url = _prepare(path)
    engine = write_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    tab = _key(6)
    with UnitOfWork(factory) as uow:
        session = uow.session
        assert session is not None
        ProjectRepository().add(
            session,
            ProjectRecord(
                id=_key(1),
                name="demo",
                root_path="/home/sas/Projects/demo",
                default_branch="main",
                vault_folder=None,
                languages=[],
                created_at=_WHEN,
            ),
        )
        AgentAccountRepository().add(
            session,
            AgentAccountRecord(id="claude:demo", agent_kind=AgentKind.CLAUDE, plan_label="Pro", container_name="box"),
        )
        WorkspaceRepository().add(
            session,
            WorkspaceRecord(
                id=_key(2),
                project_id=_key(1),
                mode=WorkspaceMode.MAIN_CHECKOUT,
                path="/home/sas/Projects/demo",
                branch=None,
                base_branch=None,
                base_commit=None,
                locked=False,
                dirty_cached=False,
                created_at=_WHEN,
                removed_at=None,
            ),
        )
        PlanRepository().add(
            session,
            PlanRecord(
                id=_key(3),
                project_id=_key(1),
                title="plan",
                goal="ship",
                status=PlanStatus.ACTIVE,
                canvas_note_ref=None,
                created_by_tab=None,
                created_at=_WHEN,
                updated_at=_WHEN,
            ),
        )
        PlanNodeRepository().add(
            session,
            PlanNodeRecord(
                id=_key(4),
                plan_id=_key(3),
                node_key="N-01",
                title="node",
                objective="do",
                status=NodeStatus.PENDING,
                owner_agent=None,
                paths=[],
                acceptance=[],
                test_cmd=None,
                review_required=False,
                evidence=None,
                note_ref=None,
                layer=0,
                priority=1.0,
                unmet_blockers=0,
                fence_seq=0,
                created_at=_WHEN,
                updated_at=_WHEN,
            ),
        )
        TabRepository().add(
            session,
            TabRecord(
                id=tab,
                project_id=_key(1),
                workspace_id=_key(2),
                agent_kind=AgentKind.CLAUDE,
                account_id="claude:demo",
                title="tab",
                tab_order=0,
                last_status=TabStatus.IDLE,
                status_detail=None,
                plan_node_id=_key(4),
                context_used_pct=None,
                session_cost_usd=None,
                agent_session_id=None,
                closed_at=None,
                created_at=_WHEN,
                updated_at=_WHEN,
            ),
        )
        uow.commit()
    writer = Writer(engine)
    writer.start()
    lags: list[float] = []

    async def _probe(done: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        previous = loop.time()
        while not done.is_set():
            await asyncio.sleep(0)
            now = loop.time()
            lags.append(now - previous)
            previous = now

    def _insert(db: Session) -> int:
        moment = datetime.now(UTC)
        for start in range(0, _EVENTS, _CHUNK):
            db.execute(
                insert(EventLog),
                [
                    {
                        "tab_id": tab,
                        "source": EventSource.CORE,
                        "event_type": "tick",
                        "payload": {"n": start + offset},
                        "at": moment,
                    }
                    for offset in range(_CHUNK)
                ],
            )
            time.sleep(0)
        return _EVENTS

    done = asyncio.Event()
    probe = asyncio.create_task(_probe(done))
    started = time.perf_counter()
    try:
        count = await writer.submit(_insert)
    finally:
        done.set()
        await probe
        writer.stop()
    elapsed = time.perf_counter() - started
    assert count == _EVENTS
    assert elapsed < 1.0
    assert len(lags) > 100
    slow = [lag for lag in lags if lag >= 0.005]
    assert len(slow) / len(lags) < 0.01
    with Session(engine) as session:
        stored = session.scalar(select(func.count()).select_from(EventLog))
    assert stored == _EVENTS
    return int(stored)


_TICK_RATE = 10_000
_TICK_SPAN_S = 3.0
_TICK_WARMUP_S = 0.5
_TICK_LIMIT_S = 0.005
_TICK_BATCH = 100


def test_persistence_b_tick(tmp_path: Path) -> None:
    lags, stored = asyncio.run(_tick_lags(tmp_path))
    ordered = sorted(lags)
    worst = ordered[-1]
    middle = ordered[len(ordered) // 2]
    high = ordered[(len(ordered) * 99) // 100]
    assert stored == _TICK_RATE * int(_TICK_SPAN_S)
    assert worst < _TICK_LIMIT_S, f"p50={middle} p99={high} max={worst} n={len(ordered)}"


def _seed_tick(path: Path) -> tuple[Engine, str]:
    url = _prepare(path)
    engine = write_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    tab = _key(7)
    with UnitOfWork(factory) as uow:
        session = uow.session
        assert session is not None
        ProjectRepository().add(
            session,
            ProjectRecord(
                id=_key(1),
                name="demo",
                root_path="/home/sas/Projects/demo",
                default_branch="main",
                vault_folder=None,
                languages=[],
                created_at=_WHEN,
            ),
        )
        AgentAccountRepository().add(
            session,
            AgentAccountRecord(id="claude:demo", agent_kind=AgentKind.CLAUDE, plan_label="Pro", container_name="box"),
        )
        WorkspaceRepository().add(
            session,
            WorkspaceRecord(
                id=_key(2),
                project_id=_key(1),
                mode=WorkspaceMode.MAIN_CHECKOUT,
                path="/home/sas/Projects/demo",
                branch=None,
                base_branch=None,
                base_commit=None,
                locked=False,
                dirty_cached=False,
                created_at=_WHEN,
                removed_at=None,
            ),
        )
        PlanRepository().add(
            session,
            PlanRecord(
                id=_key(3),
                project_id=_key(1),
                title="plan",
                goal="ship",
                status=PlanStatus.ACTIVE,
                canvas_note_ref=None,
                created_by_tab=None,
                created_at=_WHEN,
                updated_at=_WHEN,
            ),
        )
        PlanNodeRepository().add(
            session,
            PlanNodeRecord(
                id=_key(4),
                plan_id=_key(3),
                node_key="N-01",
                title="node",
                objective="do",
                status=NodeStatus.PENDING,
                owner_agent=None,
                paths=[],
                acceptance=[],
                test_cmd=None,
                review_required=False,
                evidence=None,
                note_ref=None,
                layer=0,
                priority=1.0,
                unmet_blockers=0,
                fence_seq=0,
                created_at=_WHEN,
                updated_at=_WHEN,
            ),
        )
        TabRepository().add(
            session,
            TabRecord(
                id=tab,
                project_id=_key(1),
                workspace_id=_key(2),
                agent_kind=AgentKind.CLAUDE,
                account_id="claude:demo",
                title="tab",
                tab_order=0,
                last_status=TabStatus.IDLE,
                status_detail=None,
                plan_node_id=_key(4),
                context_used_pct=None,
                session_cost_usd=None,
                agent_session_id=None,
                closed_at=None,
                created_at=_WHEN,
                updated_at=_WHEN,
            ),
        )
        uow.commit()
    return engine, tab


def _tick_job(start: int, tab: str, moment: datetime) -> Callable[[Session], object]:
    def _insert(db: Session) -> int:
        db.execute(
            insert(EventLog),
            [
                {
                    "tab_id": tab,
                    "source": EventSource.CORE,
                    "event_type": "tick",
                    "payload": {"n": start + offset},
                    "at": moment,
                }
                for offset in range(_TICK_BATCH)
            ],
        )
        return start

    return _insert


async def _tick_lags(path: Path) -> tuple[list[float], int]:
    engine, tab = _seed_tick(path)
    writer = Writer(engine)
    writer.start()
    lags: list[float] = []
    try:
        await _measure_ticks(writer, tab, lags)
        _ = await writer.submit(lambda _db: None)
    finally:
        writer.stop()
    with Session(engine) as session:
        stored = session.scalar(select(func.count()).select_from(EventLog))
    return lags, int(stored)


async def _measure_ticks(writer: Writer, tab: str, lags: list[float]) -> None:
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + _TICK_SPAN_S
    warmup = started + _TICK_WARMUP_S
    moment = datetime.now(UTC)
    total = _TICK_RATE * int(_TICK_SPAN_S)
    step = _TICK_BATCH / _TICK_RATE

    async def _feed() -> None:
        next_at = loop.time()
        for start in range(0, total, _TICK_BATCH):
            _ = writer.submit(_tick_job(start, tab, moment))
            next_at += step
            delay = next_at - loop.time()
            await asyncio.sleep(max(0.0, delay))

    async def _probe() -> None:
        previous = loop.time()
        while previous < deadline:
            await asyncio.sleep(0)
            now = loop.time()
            if now >= warmup:
                lags.append(now - previous)
            previous = now

    await asyncio.gather(_feed(), _probe())
