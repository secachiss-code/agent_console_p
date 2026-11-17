"""Persist file and interface identities for indexed modules."""

import sqlalchemy as sa
from alembic import op

revision = "0012_code_file_hashes"
down_revision = "0011_node_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("kb_nodes") as batch:
        batch.add_column(sa.Column("language", sa.String(16), nullable=True))
        batch.add_column(sa.Column("body_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("interface_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("kb_nodes") as batch:
        batch.drop_column("interface_hash")
        batch.drop_column("body_hash")
        batch.drop_column("language")
