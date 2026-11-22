"""Project analysis trust.

The flags live beside ``projects`` so a downgrade that drops this table still lets the
current ``Project`` model insert a row into the older ``projects`` shape.
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_analysis_trust"
down_revision = "0013_note_fts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _ = op.create_table(
        "project_analysis_trust",
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("analysis_trusted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("analysis_trusted IN (0, 1)"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_analysis_trust_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_project_analysis_trust")),
    )


def downgrade() -> None:
    op.drop_table("project_analysis_trust")
