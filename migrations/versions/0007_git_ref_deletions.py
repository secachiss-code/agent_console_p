"""Durable outbox for expired legacy checkpoint refs."""

import sqlalchemy as sa
from alembic import op

revision = "0007_git_ref_deletions"
down_revision = "0006_session_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "git_ref_deletions",
        sa.Column("repo_path", sa.String(4096), primary_key=True),
        sa.Column("git_ref", sa.String(255), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("git_ref_deletions")
