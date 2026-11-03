"""Keep what a fact says and what a handover packet lists, not only their titles."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_knowledge_details"
down_revision = "0002_plan_node_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("kb_nodes") as batch:
        batch.add_column(sa.Column("body", sa.Text(), nullable=True))
    with op.batch_alter_table("handovers") as batch:
        batch.add_column(sa.Column("packet", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("handovers") as batch:
        batch.drop_column("packet")
    with op.batch_alter_table("kb_nodes") as batch:
        batch.drop_column("body")
