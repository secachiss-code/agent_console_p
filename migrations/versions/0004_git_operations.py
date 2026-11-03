"""A journal of merges and rebases, so a restart can say what happened to a branch."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from aconsole.core.infrastructure.persistence import types as ptypes

revision = "0004_git_operations"
down_revision = "0003_knowledge_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _ = op.create_table(
        "git_operations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("mode", sa.String(24), nullable=True),
        sa.Column("source", sa.String(512), nullable=False),
        sa.Column("target", sa.String(512), nullable=False),
        sa.Column("source_oid", sa.String(64), nullable=False),
        sa.Column("target_oid", sa.String(64), nullable=False),
        sa.Column("result_oid", sa.String(64), nullable=True),
        sa.Column("expected_tree", sa.String(64), nullable=True),
        sa.Column("worktree", sa.Text(), nullable=True),
        sa.Column("tab_id", sa.String(64), nullable=True),
        sa.Column("plan_node", sa.String(64), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("follow_up_pending", sa.Boolean(), nullable=False),
        sa.Column("created_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.Column("updated_at", ptypes.UtcDateTime(length=32), nullable=False),
    )
    op.create_index("ix_git_operations_open", "git_operations", ["status"])


def downgrade() -> None:
    op.drop_index("ix_git_operations_open", table_name="git_operations")
    op.drop_table("git_operations")
