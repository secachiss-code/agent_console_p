"""Task sessions retain existing workspaces without executing Git or moving refs."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

from aconsole.core.infrastructure.persistence import types as ptypes

revision = "0005_work_sessions"
down_revision = "0004_git_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = op.create_table(
        "work_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(26), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("worktree", sa.String(4096), nullable=False),
        sa.Column("workspace_removed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("branch", sa.String(255), nullable=True),
        sa.Column("base_branch", sa.String(255), nullable=True),
        sa.Column("workspace_mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.Column("updated_at", ptypes.UtcDateTime(length=32), nullable=False),
    )
    op.create_index("ix_work_sessions_project_id", "work_sessions", ["project_id"])
    op.create_index(
        "ix_work_sessions_workspace", "work_sessions", ["worktree"], unique=True,
        sqlite_where=sa.column("workspace_removed") == sa.false(),
    )
    for name in ("tabs", "git_operations"):
        op.add_column(name, sa.Column("work_session_id", sa.String(32), nullable=True))
        op.create_index(f"ix_{name}_work_session_id", name, ["work_session_id"])
    _backfill(table)


def _backfill(target: sa.Table) -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    workspaces = sa.Table("workspaces", metadata, autoload_with=bind)
    tabs = sa.Table("tabs", metadata, autoload_with=bind)
    operations = sa.Table("git_operations", metadata, autoload_with=bind)
    identifiers: dict[tuple[str, str], str] = {}
    for row in bind.execute(sa.select(workspaces).order_by(workspaces.c.created_at, workspaces.c.id)).mappings():
        if row["removed_at"] is not None:
            continue
        path = str(Path(row["path"]).resolve())
        key = (row["project_id"], path)
        session_id = identifiers.get(key)
        if session_id is None:
            session_id = hashlib.sha256(f"{key[0]}\0{path}".encode()).hexdigest()[:32]
            identifiers[key] = session_id
            bind.execute(
                target.insert().values(
                    id=session_id,
                    project_id=key[0],
                    title=(row["branch"] or Path(path).name or "Workspace")[:120],
                    worktree=path,
                    branch=row["branch"],
                    base_branch=row["base_branch"],
                    workspace_mode=row["mode"],
                    status="active",
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        bind.execute(tabs.update().where(tabs.c.workspace_id == row["id"]).values(work_session_id=session_id))
        bind.execute(
            operations.update()
            .where(
                operations.c.project_id == key[0],
                operations.c.worktree == row["path"],
            )
            .values(work_session_id=session_id)
        )
    # Source-tab linkage also identifies merges that targeted a different checkout.
    for row in bind.execute(sa.select(tabs.c.id, tabs.c.work_session_id)).mappings():
        if row["work_session_id"] is not None:
            bind.execute(
                operations.update()
                .where(
                    operations.c.tab_id == row["id"],
                    operations.c.work_session_id.is_(None),
                )
                .values(work_session_id=row["work_session_id"])
            )


def downgrade() -> None:
    for name in ("git_operations", "tabs"):
        op.drop_index(f"ix_{name}_work_session_id", table_name=name)
        op.drop_column(name, "work_session_id")
    op.drop_index("ix_work_sessions_project_id", table_name="work_sessions")
    op.drop_table("work_sessions")
