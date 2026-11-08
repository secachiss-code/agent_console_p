"""Join approval-policy and pending-fork migration histories without rewriting either."""

revision = "0009_merge_approval_fork"
down_revision = ("0008_approval_policy", "0008_pending_session_fork")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
