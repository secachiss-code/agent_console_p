"""Project approval-policy override. Null inherits the global choice stored in settings."""

import sqlalchemy as sa
from alembic import op

revision = "0008_approval_policy"
down_revision = "0007_git_ref_deletions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("approval_policy", sa.String(length=16), nullable=True))
    op.add_column("projects", sa.Column("approval_dangerous", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "approval_dangerous")
    op.drop_column("projects", "approval_policy")
