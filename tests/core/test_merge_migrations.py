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
            session.add(Plan(id=plan_id, project_id="p", title=plan_id, goal="keep",
                             status=PlanStatus.COMPLETED if plan_id == "plan-1" else PlanStatus.ACTIVE,
                             created_at=now, updated_at=now))
        session.flush()
        for plan_id in ("plan-1", "plan-623", "plan-636"):
            for key in ("CORE-0", "CORE-1"):
                session.add(PlanNode(id=f"{plan_id}-{key}", plan_id=plan_id, node_key=key, title=key,
                                     objective="keep", status=NodeStatus.PENDING, paths=[], acceptance=[],
                                     review_required=False, layer=0, priority=0, unmet_blockers=0, fence_seq=0,
                                     test_cmd="pytest", created_at=now, updated_at=now))
        session.flush()
        session.add(PlanEdge(src_node_id="plan-636-CORE-0", dst_node_id="plan-636-CORE-1", kind=EdgeKind.PARENT))
        session.add(Tab(id="t", project_id="p", agent_kind=AgentKind.CODEX, title="tab", tab_order=0,
                        last_status=TabStatus.IDLE, created_at=now, updated_at=now))
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
