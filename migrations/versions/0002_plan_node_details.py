"""Keep what an agent wrote on a plan node: its boundaries, output format, and note."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_plan_node_details"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_COLUMNS = ("boundaries", "output_format", "agent_note")


def upgrade() -> None:
    with op.batch_alter_table("plan_nodes") as batch:
        for name in _COLUMNS:
            batch.add_column(sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("plan_nodes") as batch:
        for name in reversed(_COLUMNS):
            batch.drop_column(name)
