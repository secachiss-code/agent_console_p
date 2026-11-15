"""SQLAlchemy Core schema for disposable, versioned cache data."""

from sqlalchemy import CheckConstraint, Column, Index, Integer, MetaData, Table, Text

CACHE_SCHEMA = 1
metadata = MetaData()

direction_prefix_cache = Table(
    "direction_prefix_cache", metadata,
    Column("plan_id", Text, primary_key=True),
    Column("direction_key", Text, primary_key=True),
    Column("manifest_sha256", Text, nullable=False),
    Column("prefix_sha256", Text, nullable=False),
    Column("prefix_blob", Text, nullable=False),
    Column("byte_len", Integer, nullable=False),
    Column("updated_at", Integer, nullable=False),
)

ast_skeleton_cache = Table(
    "ast_skeleton_cache", metadata,
    Column("file_sha256", Text, primary_key=True),
    Column("extractor", Text, primary_key=True),
    Column("signatures", Text, nullable=False),
    Column("byte_len", Integer, nullable=False),
    Column("last_used_at", Integer, nullable=False),
)

node_scratchpad = Table(
    "node_scratchpad", metadata,
    Column("node_id", Text, primary_key=True),
    Column("tab_id", Text, nullable=False),
    Column("fence", Integer, nullable=False),
    Column("attempt_no", Integer, nullable=False),
    Column("step_type", Text, nullable=False),
    Column("head_block", Text, nullable=False),
    Column("remaining_items_json", Text, nullable=False),
    Column("compact_verdict", Text, nullable=True),
    Column("diff_stat", Text, nullable=True),
    Column("updated_at", Integer, nullable=False),
    CheckConstraint("length(compact_verdict) <= 500"),
)

verification_gate_cache = Table(
    "verification_gate_cache", metadata,
    Column("state_hash", Text, primary_key=True),
    Column("tier", Text, primary_key=True),
    Column("node_id", Text, nullable=False),
    Column("passed", Integer, nullable=False),
    Column("compact_verdict", Text, nullable=False),
    Column("receipt_hash", Text, nullable=False),
    Column("cached_at", Integer, nullable=False),
    CheckConstraint("length(compact_verdict) <= 500"),
)

session_file_tracker = Table(
    "session_file_tracker", metadata,
    Column("tab_id", Text, primary_key=True),
    Column("agent_session_id", Text, primary_key=True),
    Column("file_path", Text, primary_key=True),
    Column("file_sha256", Text, nullable=False),
    Column("turn_number", Integer, nullable=False),
)

_ = Index("ix_skeleton_lru", ast_skeleton_cache.c.last_used_at)
_ = Index("ix_gate_cache_node", verification_gate_cache.c.node_id)
