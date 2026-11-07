"""Keep a native CLI fork distinct from a resume across core restarts."""

import sqlalchemy as sa
from alembic import op

revision = "0008_pending_session_fork"
down_revision = "0007_git_ref_deletions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tabs", sa.Column("fork_pending", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("tabs", "fork_pending")
