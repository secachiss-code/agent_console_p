"""Both independently deployed 0008 revisions upgrade to the merged head."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

import pytest
from sqlalchemy import inspect

from aconsole.core.infrastructure.persistence.engine import migrate, write_engine


@pytest.mark.parametrize("revision", ["0008_approval_policy", "0008_pending_session_fork"])
def test_upgrade_from_either_branch_keeps_both_features(tmp_path: Path, revision: str) -> None:
    url = f"sqlite:///{tmp_path / 'branch.db'}"
    migrate(url, revision)
    migrate(url, "head")
    engine = write_engine(url)
    try:
        schema = inspect(engine)
        assert {"approval_policy", "approval_dangerous"} <= {
            column["name"] for column in schema.get_columns("projects")
        }
        assert "fork_pending" in {column["name"] for column in schema.get_columns("tabs")}
    finally:
        engine.dispose()


def test_hdag_upgrade_backfills_legacy_data_and_round_trips(tmp_path: Path) -> None:  # noqa: PLR0915 -- seed and verify both sides of the migration
    from datetime import UTC, datetime

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    from aconsole.core.domain.agent import AgentKind
    from aconsole.core.domain.plan import NodeStatus
    from aconsole.core.domain.tab_status import TabStatus
    from aconsole.core.infrastructure.persistence.engine import MIGRATIONS
    from aconsole.core.infrastructure.persistence.models import (
        EdgeKind,
        Plan,
        PlanEdge,
        PlanNode,
        PlanStatus,
        Project,
        Tab,
    )

    url = f"sqlite:///{tmp_path / 'hdag.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add(Project(id="p", name="P", root_path="/p", languages=[], created_at=now))
        session.flush()
        for plan_id in ("plan-1", "plan-623", "plan-636"):
            session.add(
                Plan(
                    id=plan_id,
                    project_id="p",
                    title=plan_id,
                    goal="keep",
                    status=PlanStatus.COMPLETED if plan_id == "plan-1" else PlanStatus.ACTIVE,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.flush()
        for plan_id in ("plan-1", "plan-623", "plan-636"):
            for key in ("CORE-0", "CORE-1"):
                session.add(
                    PlanNode(
                        id=f"{plan_id}-{key}",
                        plan_id=plan_id,
                        node_key=key,
                        title=key,
                        objective="keep",
                        status=NodeStatus.PENDING,
                        paths=[],
                        acceptance=[],
                        review_required=False,
                        layer=0,
                        priority=0,
                        unmet_blockers=0,
                        fence_seq=0,
                        test_cmd="pytest",
                        created_at=now,
                        updated_at=now,
                    )
                )
        session.flush()
        session.add(PlanEdge(src_node_id="plan-636-CORE-0", dst_node_id="plan-636-CORE-1", kind=EdgeKind.PARENT))
        session.add(
            Tab(
                id="t",
                project_id="p",
                agent_kind=AgentKind.CODEX,
                title="tab",
                tab_order=0,
                last_status=TabStatus.IDLE,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    engine.dispose()
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url)
    command.downgrade(cfg, "0009_merge_approval_fork")
    migrate(url, "head")
    engine = write_engine(url)
    try:
        with Session(engine) as session:
            plans = {plan.id: plan for plan in session.scalars(select(Plan))}
            assert plans["plan-1"].phase == "completed"
            assert plans["plan-636"].phase == "frozen"
            assert all(plan.baseline_nodes == 2 and plan.gross_added == 2 for plan in plans.values())
            nodes = {node.id: node for node in session.scalars(select(PlanNode))}
            assert nodes["plan-636-CORE-0"].level == 0
            assert nodes["plan-636-CORE-0"].baseline_children == 1
            assert nodes["plan-623-CORE-0"].level == 1
            assert nodes["plan-1-CORE-0"].level == 1
            assert all(not node.pipeline and node.test_cmd == "pytest" for node in nodes.values())
            tab = session.get(Tab, "t")
            assert tab is not None
            assert tab.mode == "execute"
            assert tab.model is None
            for row, attribute, invalid in (
                (plans["plan-636"], "phase", "invalid"),
                (nodes["plan-636-CORE-0"], "level", 2),
                (nodes["plan-636-CORE-0"], "max_attempts", 5),
                (tab, "mode", "invalid"),
            ):
                with pytest.raises(IntegrityError), session.begin_nested():
                    _flush_invalid(session, row, attribute, invalid)
    finally:
        engine.dispose()


def _flush_invalid(session: Session, row: object, attribute: str, value: object) -> None:
    setattr(row, attribute, value)
    session.flush()


def test_code_file_hashes_round_trip_keeps_module_identity_and_fact_edges(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from aconsole.core.infrastructure.persistence.engine import MIGRATIONS
    from aconsole.core.infrastructure.persistence.models import KbEdge, KbEdgeKind, KbNode, KbType, Project

    url = f"sqlite:///{tmp_path / 'hashes.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    now = datetime.now(UTC)
    try:
        with Session(engine) as session:
            session.add(Project(id="p", name="P", root_path="/p", languages=[], created_at=now))
            session.flush()
            session.add_all(
                [
                    KbNode(
                        id="module",
                        project_id="p",
                        node_type=KbType.MODULE,
                        title="x.py",
                        source="x.py",
                        language="python",
                        body_hash="a" * 64,
                        interface_hash="b" * 64,
                        is_stale=False,
                        updated_at=now,
                    ),
                    KbNode(
                        id="fact",
                        project_id="p",
                        node_type=KbType.FACT,
                        title="Keep",
                        body="Keep body",
                        is_stale=False,
                        updated_at=now,
                    ),
                ]
            )
            session.flush()
            session.add(KbEdge(src_id="fact", dst_id="module", kind=KbEdgeKind.CODE_REF, origin="fact"))
            session.commit()
    finally:
        engine.dispose()
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url)
    command.downgrade(cfg, "0011_node_attempts")
    engine = write_engine(url)
    try:
        assert not {"language", "body_hash", "interface_hash"} & {
            column["name"] for column in inspect(engine).get_columns("kb_nodes")
        }
        with Session(engine) as session:
            assert set(session.scalars(select(KbNode.id))) == {"fact", "module"}
            assert session.scalar(select(KbNode.body).where(KbNode.id == "fact")) == "Keep body"
            assert session.scalar(select(KbEdge.dst_id)) == "module"
    finally:
        engine.dispose()
    migrate(url, "head")
    engine = write_engine(url)
    try:
        with Session(engine) as session:
            rows = session.scalars(select(KbNode)).all()
            assert len(rows) == 2
            assert all(
                node.language is None and node.body_hash is None and node.interface_hash is None for node in rows
            )
            edge = session.scalar(select(KbEdge))
            assert edge is not None
            assert edge.kind is KbEdgeKind.CODE_REF
            assert edge.origin == "fact"
    finally:
        engine.dispose()


def test_note_fts_upgrade_and_downgrade_preserve_source_rows(tmp_path: Path) -> None:  # noqa: PLR0915 -- verify both sides of the migration
    from datetime import UTC, datetime

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from aconsole.core.infrastructure.persistence.engine import MIGRATIONS
    from aconsole.core.infrastructure.persistence.models import KbNode, KbType, Project
    from aconsole.core.infrastructure.persistence.search import replace_note
    from tests.core.test_knowledge_store import fts_rows

    url = f"sqlite:///{tmp_path / 'note-fts.db'}"
    migrate(url, "0012_code_file_hashes")
    engine = write_engine(url)
    now = datetime.now(UTC)
    try:
        with Session(engine) as session:
            session.add(Project(id="p", name="P", root_path="/p", languages=[], created_at=now))
            session.flush()
            session.add(
                KbNode(
                    id="fact",
                    project_id="p",
                    node_type=KbType.FACT,
                    title="До миграции",
                    body="Тело",
                    note_ref="fact-ref",
                    is_stale=False,
                    updated_at=now,
                )
            )
            session.commit()
    finally:
        engine.dispose()
    migrate(url, "head")
    engine = write_engine(url)
    try:
        assert "note_fts" in inspect(engine).get_table_names()
        with Session(engine) as session:
            replace_note(session, "fact-ref", "До миграции", "Тело")
            session.commit()
            assert fts_rows(session, "тело") == [("До миграции", "Тело", "fact-ref")]
    finally:
        engine.dispose()
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url)
    command.downgrade(cfg, "0012_code_file_hashes")
    engine = write_engine(url)
    try:
        assert not any(name.startswith("note_fts") for name in inspect(engine).get_table_names())
        with Session(engine) as session:
            assert session.scalar(select(KbNode.body)) == "Тело"
    finally:
        engine.dispose()
    migrate(url, "head")
    engine = write_engine(url)
    try:
        with Session(engine) as session:
            assert fts_rows(session) == []
            assert session.scalar(select(KbNode.title)) == "До миграции"
    finally:
        engine.dispose()
