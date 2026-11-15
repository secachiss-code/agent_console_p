"""Persist H-DAG contracts, frozen baselines and the human decision inbox."""

import sqlalchemy as sa
from alembic import op

revision = "0010_plan_hdag"
down_revision = "0009_merge_approval_fork"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("phase", sa.String(16), sa.CheckConstraint("phase IN ('draft','frozen','completed')", name="phase"),
                  nullable=False, server_default="draft"),
        sa.Column("revision_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("frozen_at", sa.String(32)),
        sa.Column("baseline_nodes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gross_added", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gates_sha256", sa.String(64)),
    ):
        op.add_column("plans", column)
    for column in (
        sa.Column("level", sa.Integer(), sa.CheckConstraint("level IN (0, 1)", name="level"),
                  nullable=False, server_default="1"),
        sa.Column("pipeline", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_attempts", sa.Integer(),
                  sa.CheckConstraint("max_attempts BETWEEN 1 AND 4", name="max_attempts"),
                  nullable=False, server_default="4"),
        sa.Column("contract_sha256", sa.String(64)),
        sa.Column("invariants", sa.Text()),
        sa.Column("gate_profile", sa.JSON()),
        sa.Column("blast_radius", sa.JSON()),
        sa.Column("baseline_children", sa.Integer(), nullable=False, server_default="0"),
    ):
        op.add_column("plan_nodes", column)
    op.add_column("tabs", sa.Column("mode", sa.String(16),
                                    sa.CheckConstraint("mode IN ('plan','execute')", name="mode"),
                                    nullable=False, server_default="execute"))
    op.add_column("tabs", sa.Column("model", sa.String(64)))
    op.create_table(
        "plan_inbox",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("plan_id", sa.String(26), sa.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.String(26), sa.ForeignKey("plan_nodes.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(16),
                  sa.CheckConstraint("kind IN ('backlog','replan','human','add','split','reopen')", name="inbox_kind"),
                  nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("tab_id", sa.String(26), sa.ForeignKey("tabs.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(16),
                  sa.CheckConstraint("status IN ('open','applied','rejected')", name="inbox_status"),
                  nullable=False, server_default="open"),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("decided_at", sa.String(32)),
        sa.Column("decided_by", sa.String(64)),
    )
    op.create_index("ix_plan_inbox_plan_id", "plan_inbox", ["plan_id"])
    op.execute("""UPDATE plans SET
        phase = CASE WHEN status = 'completed' THEN 'completed' ELSE 'frozen' END,
        frozen_at = updated_at,
        baseline_nodes = (SELECT count(*) FROM plan_nodes WHERE plan_id = plans.id),
        gross_added = (SELECT count(*) FROM plan_nodes WHERE plan_id = plans.id)
    """)
    op.execute("""UPDATE plan_nodes SET level = 0
        WHERE plan_id = 'plan-636' AND node_key IN ('CORE-0','DB-0','SEC-0','QA-0','APR-0','FE-0')""")
    op.execute("""UPDATE plan_nodes SET baseline_children =
        (SELECT count(*) FROM plan_edges WHERE src_node_id = plan_nodes.id AND kind = 'parent')
        WHERE level = 0""")


def downgrade() -> None:
    op.drop_table("plan_inbox")
    op.drop_column("tabs", "model")
    op.drop_column("tabs", "mode")
    for column in ("baseline_children", "blast_radius", "gate_profile", "invariants", "contract_sha256",
                   "max_attempts", "pipeline", "level"):
        op.drop_column("plan_nodes", column)
    for column in ("gates_sha256", "gross_added", "baseline_nodes", "frozen_at", "revision_no", "phase"):
        op.drop_column("plans", column)
