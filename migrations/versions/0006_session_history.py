"""Durable session snapshots retain actors even after a tab is pruned."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

from aconsole.core.infrastructure.persistence import types as ptypes

revision = "0006_session_history"
down_revision = "0005_work_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tabs", sa.Column("isolated", sa.Boolean(), nullable=False, server_default=sa.false()))
    table = op.create_table(
        "work_session_snapshots",
        sa.Column(
            "work_session_id", sa.String(32), sa.ForeignKey("work_sessions.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("actor_tab_id", sa.String(32), nullable=True),
        sa.Column("actor_title", sa.String(80), nullable=True),
        sa.Column("agent", sa.String(32), nullable=True),
        sa.Column("plan_node", sa.String(64), nullable=True),
        sa.Column("worktree", sa.String(4096), nullable=False),
        sa.Column("ref", sa.String(255), nullable=False),
        sa.Column("head_oid", sa.String(64), nullable=True),
        sa.Column("oid", sa.String(64), nullable=True),
        sa.Column("tree", sa.String(64), nullable=True),
        sa.Column("previous_oid", sa.String(64), nullable=True),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", ptypes.UtcDateTime(length=32), nullable=False),
    )
    _legacy_snapshots(table)


def _legacy_snapshots(target: sa.Table) -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    checkpoints = sa.Table("checkpoints", metadata, autoload_with=bind)
    tabs = sa.Table("tabs", metadata, autoload_with=bind)
    workspaces = sa.Table("workspaces", metadata, autoload_with=bind)
    query = sa.select(
        checkpoints, tabs.c.work_session_id, tabs.c.title, tabs.c.agent_kind, tabs.c.plan_node_id, workspaces.c.path,
    ).select_from(checkpoints.join(tabs, checkpoints.c.tab_id == tabs.c.id).join(
        workspaces, tabs.c.workspace_id == workspaces.c.id,
    )).where(tabs.c.work_session_id.is_not(None)).order_by(checkpoints.c.created_at, checkpoints.c.id)
    sequences: dict[str, int] = {}
    for row in bind.execute(query).mappings():
        session_id = row["work_session_id"]
        sequence = sequences.get(session_id, 0) + 1
        sequences[session_id] = sequence
        bind.execute(target.insert().values(
            work_session_id=session_id, sequence=sequence, kind="legacy_checkpoint", actor_tab_id=row["tab_id"],
            actor_title=row["title"], agent=row["agent_kind"], plan_node=row["plan_node_id"],
            worktree=str(Path(row["path"]).resolve()), ref=row["git_ref"], head_oid=None, oid=None,
            tree=row["tree_sha"], previous_oid=None, label=row["label"], status="legacy",
            created_at=datetime.fromisoformat(row["created_at"]),
        ))


def downgrade() -> None:
    op.drop_column("tabs", "isolated")
    op.drop_table("work_session_snapshots")
