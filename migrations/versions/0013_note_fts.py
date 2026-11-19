"""Index facts and published notes with FTS5 unicode61."""

from alembic import op

from aconsole.core.infrastructure.persistence.search import create_index, drop_index

revision = "0013_note_fts"
down_revision = "0012_code_file_hashes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_index(op.get_bind())


def downgrade() -> None:
    drop_index(op.get_bind())
