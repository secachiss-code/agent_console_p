"""Durable L2 attempts, canonical block chains and gate receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0011_node_attempts"
down_revision = "0010_plan_hdag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node_attempts",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("node_id", sa.String(26), sa.ForeignKey("plan_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("tab_id", sa.String(26)),
        sa.Column("agent_kind", sa.String(24)),
        sa.Column("model", sa.String(64)),
        sa.Column("base_oid", sa.String(64), nullable=False),
        sa.Column("head_block", sa.String(64)),
        sa.Column("diff_stat", sa.String(200)),
        sa.Column("compact_verdict", sa.String(500)),
        sa.Column("cost_usd", sa.Float()),
        sa.Column("started_at", sa.String(32), nullable=False),
        sa.Column("finished_at", sa.String(32)),
        sa.UniqueConstraint("node_id", "attempt_no"),
        sa.CheckConstraint("attempt_no BETWEEN 1 AND 4", name="attempt_no"),
        sa.CheckConstraint("status IN ('running','passed','failed','abandoned')", name="status"),
        sa.CheckConstraint("length(compact_verdict) <= 500", name="compact_verdict"),
    )
    op.create_table(
        "node_step_blocks",
        sa.Column("block_hash", sa.String(64), primary_key=True),
        sa.Column("attempt_id", sa.String(26), sa.ForeignKey("node_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(8), nullable=False),
        sa.Column("prev_block_hash", sa.String(64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.UniqueConstraint("attempt_id", "seq"),
        sa.CheckConstraint("seq BETWEEN 1 AND 4", name="seq"),
        sa.CheckConstraint("step_type IN ('spec','patch','test','verify')", name="step_type"),
    )
    op.create_table(
        "gate_receipts",
        sa.Column("receipt_hash", sa.String(64), primary_key=True),
        sa.Column("node_id", sa.String(26), sa.ForeignKey("plan_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tier", sa.String(12), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("argv_sha256", sa.String(64), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Boolean(create_constraint=True), nullable=False),
        sa.Column("compact_verdict", sa.String(500), nullable=False),
        sa.Column("log_ref", sa.String(1024)),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.CheckConstraint("tier IN ('micro','blast','direction')", name="tier"),
        sa.CheckConstraint("length(compact_verdict) <= 500", name="compact_verdict"),
        sa.CheckConstraint("duration_ms >= 0", name="duration_ms"),
    )
    op.create_index("ix_gate_receipts_node", "gate_receipts", ["node_id", "tier", "created_at"])


def downgrade() -> None:
    op.drop_table("gate_receipts")
    op.drop_table("node_step_blocks")
    op.drop_table("node_attempts")
