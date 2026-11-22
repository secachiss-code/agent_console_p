"""Versioned concept documents. One revision chain per key. This revision is the only Alembic head."""

import sqlalchemy as sa
from alembic import op

revision = "0014_concept_revisions"
down_revision = "0013_note_fts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _ = op.create_table(
        "concept_documents",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("key", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_concept_documents_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_concept_documents")),
        sa.UniqueConstraint("project_id", "key", name=op.f("uq_concept_documents_project_id")),
    )
    op.create_index("ix_concept_documents_project_id", "concept_documents", ["project_id"])
    _ = op.create_table(
        "concept_revisions",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("document_id", sa.String(length=26), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["concept_documents.id"],
            name=op.f("fk_concept_revisions_document_id_concept_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_concept_revisions")),
        sa.UniqueConstraint("document_id", "revision", name=op.f("uq_concept_revisions_document_id")),
    )
    op.create_index("ix_concept_revisions_document_id", "concept_revisions", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_concept_revisions_document_id", table_name="concept_revisions")
    op.drop_table("concept_revisions")
    op.drop_index("ix_concept_documents_project_id", table_name="concept_documents")
    op.drop_table("concept_documents")
