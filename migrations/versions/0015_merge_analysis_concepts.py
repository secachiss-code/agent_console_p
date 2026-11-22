"""Join the project analysis trust and concept revision migration histories."""

revision = "0015_merge_analysis_concepts"
down_revision = ("0014_analysis_trust", "0014_concept_revisions")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
