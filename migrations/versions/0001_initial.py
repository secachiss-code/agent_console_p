"""Initial schema from DATA_MODEL §3, written out table by table.

The DDL is frozen here (generated once from the models by Alembic autogenerate), so a later model change cannot
change what this revision creates or drops.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from aconsole.core.infrastructure.persistence import types as ptypes
from aconsole.core.infrastructure.persistence.engine import foreign_keys

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    foreign_keys(bind, enabled=False)
    _create()
    foreign_keys(bind, enabled=True)


def downgrade() -> None:
    bind = op.get_bind()
    foreign_keys(bind, enabled=False)
    _drop()
    foreign_keys(bind, enabled=True)


def _create() -> None:  # noqa: PLR0915 -- generated DDL, one statement per table and index
    _ = op.create_table(
        "agent_accounts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column(
            "agent_kind",
            sa.Enum("claude", "codex", "opencode", "grok", "cursor", name="agentkind", native_enum=False, length=24),
            nullable=False,
        ),
        sa.Column("plan_label", sa.String(length=32), nullable=False),
        sa.Column("container_name", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_accounts")),
    )
    _ = op.create_table(
        "frecency",
        sa.Column("item_id", sa.String(length=256), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("last_used", ptypes.UtcDateTime(length=32), nullable=False),
        sa.Column("uses", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_frecency")),
    )
    _ = op.create_table(
        "projects",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("root_path", sa.String(length=4096), nullable=False),
        sa.Column("default_branch", sa.String(length=255), nullable=False),
        sa.Column("vault_folder", sa.String(length=1024), nullable=True),
        sa.Column("languages", ptypes.JsonList(), nullable=False),
        sa.Column("created_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("root_path", name=op.f("uq_projects_root_path")),
    )
    _ = op.create_table(
        "quota_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("window_key", sa.String(length=8), nullable=False),
        sa.Column("used_pct", sa.Float(), nullable=False),
        sa.Column("observed_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quota_history")),
    )
    with op.batch_alter_table("quota_history", schema=None) as batch_op:
        batch_op.create_index("ix_quota_hist", ["account_id", "window_key", "observed_at"], unique=False)

    _ = op.create_table(
        "settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", ptypes.JsonObject(), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_settings")),
    )
    _ = op.create_table(
        "vault_files",
        sa.Column("note_ref", sa.String(length=1024), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("owner", sa.String(length=8), nullable=False),
        sa.Column("kb_id", sa.String(length=64), nullable=True),
        sa.Column("plan_node_id", sa.String(length=26), nullable=True),
        sa.Column("frontmatter_hash", sa.String(length=64), nullable=True),
        sa.Column("body_hash", sa.String(length=64), nullable=True),
        sa.Column("base_frontmatter", ptypes.JsonObject(), nullable=True),
        sa.Column("mtime_ns", sa.Integer(), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("inode", sa.Integer(), nullable=True),
        sa.Column("last_writer", sa.String(length=8), nullable=False),
        sa.Column("pending_write", ptypes.JsonObject(), nullable=True),
        sa.Column("synced_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.PrimaryKeyConstraint("note_ref", name=op.f("pk_vault_files")),
    )
    _ = op.create_table(
        "iterations",
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("dag_state_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest_note_ref", sa.String(length=1024), nullable=False),
        sa.Column("type_check_passed", sa.Boolean(create_constraint=True), nullable=False),
        sa.Column("created_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_iterations_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("project_id", "number", name=op.f("pk_iterations")),
    )
    _ = op.create_table(
        "kb_nodes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column(
            "node_type",
            sa.Enum(
                "fact", "decision", "requirement", "module", "unresolved", name="kbtype", native_enum=False, length=24
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("note_ref", sa.String(length=1024), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=True),
        sa.Column("source", sa.String(length=2048), nullable=True),
        sa.Column("verified_commit", sa.String(length=64), nullable=True),
        sa.Column("is_stale", sa.Boolean(create_constraint=True), nullable=False),
        sa.Column("updated_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_kb_nodes_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kb_nodes")),
        sa.UniqueConstraint("note_ref", name=op.f("uq_kb_nodes_note_ref")),
    )
    with op.batch_alter_table("kb_nodes", schema=None) as batch_op:
        batch_op.create_index("ix_kb_nodes_project_type", ["project_id", "node_type"], unique=False)

    _ = op.create_table(
        "plans",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "completed", "archived", name="planstatus", native_enum=False, length=24),
            nullable=False,
        ),
        sa.Column("canvas_note_ref", sa.String(length=1024), nullable=True),
        sa.Column("created_by_tab", sa.String(length=26), nullable=True),
        sa.Column("created_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.Column("updated_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_tab"], ["tabs.id"], name="fk_plans_created_by_tab_tabs", ondelete="SET NULL", use_alter=True
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_plans_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plans")),
        sa.UniqueConstraint("project_id", "title", name=op.f("uq_plans_project_id")),
    )
    with op.batch_alter_table("plans", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_plans_project_id"), ["project_id"], unique=False)

    _ = op.create_table(
        "quota_current",
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("window_key", sa.String(length=8), nullable=False),
        sa.Column("used_pct", sa.Float(), nullable=True),
        sa.Column("resets_at", ptypes.UtcDateTime(length=32), nullable=True),
        sa.Column("estimated", sa.Boolean(create_constraint=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("observed_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["agent_accounts.id"],
            name=op.f("fk_quota_current_account_id_agent_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", "window_key", name=op.f("pk_quota_current")),
    )
    _ = op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column(
            "mode",
            sa.Enum(
                "new_worktree", "main_checkout", "existing_worktree", name="workspacemode", native_enum=False, length=24
            ),
            nullable=False,
        ),
        sa.Column("path", sa.String(length=4096), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("base_branch", sa.String(length=255), nullable=True),
        sa.Column("base_commit", sa.String(length=64), nullable=True),
        sa.Column("locked", sa.Boolean(create_constraint=True), nullable=False),
        sa.Column("dirty_cached", sa.Boolean(create_constraint=True), nullable=False),
        sa.Column("created_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.Column("removed_at", ptypes.UtcDateTime(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_workspaces_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        sa.UniqueConstraint("path", name=op.f("uq_workspaces_path")),
    )
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_workspaces_project_id"), ["project_id"], unique=False)

    _ = op.create_table(
        "kb_edges",
        sa.Column("src_id", sa.String(length=64), nullable=False),
        sa.Column("dst_id", sa.String(length=64), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "imports",
                "uses_type",
                "inherits",
                "project_ref",
                "code_ref",
                "depends_on",
                "supersedes",
                "links",
                name="kbedgekind",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["dst_id"], ["kb_nodes.id"], name=op.f("fk_kb_edges_dst_id_kb_nodes"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["src_id"], ["kb_nodes.id"], name=op.f("fk_kb_edges_src_id_kb_nodes"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("src_id", "dst_id", "kind", name=op.f("pk_kb_edges")),
    )
    with op.batch_alter_table("kb_edges", schema=None) as batch_op:
        batch_op.create_index("ix_kb_edges_dst", ["dst_id", "src_id", "kind"], unique=False)

    _ = op.create_table(
        "plan_nodes",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("plan_id", sa.String(length=26), nullable=False),
        sa.Column("node_key", sa.String(length=12), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "in_progress",
                "verifying",
                "blocked",
                "done",
                "failed",
                "cancelled",
                name="nodestatus",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column(
            "owner_agent",
            sa.Enum("claude", "codex", "opencode", "grok", "cursor", name="agentkind", native_enum=False, length=24),
            nullable=True,
        ),
        sa.Column("paths", ptypes.JsonList(), nullable=False),
        sa.Column("acceptance", ptypes.JsonList(), nullable=False),
        sa.Column("test_cmd", sa.String(length=1000), nullable=True),
        sa.Column("review_required", sa.Boolean(create_constraint=True), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("note_ref", sa.String(length=1024), nullable=True),
        sa.Column("layer", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Float(), nullable=False),
        sa.Column("unmet_blockers", sa.Integer(), nullable=False),
        sa.Column("fence_seq", sa.Integer(), nullable=False),
        sa.Column("created_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.Column("updated_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["plans.id"], name=op.f("fk_plan_nodes_plan_id_plans"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_nodes")),
        sa.UniqueConstraint("plan_id", "node_key", name=op.f("uq_plan_nodes_plan_id")),
    )
    with op.batch_alter_table("plan_nodes", schema=None) as batch_op:
        batch_op.create_index("ix_nodes_plan_status", ["plan_id", "status"], unique=False)

    _ = op.create_table(
        "tabs",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("workspace_id", sa.String(length=26), nullable=True),
        sa.Column(
            "agent_kind",
            sa.Enum("claude", "codex", "opencode", "grok", "cursor", name="agentkind", native_enum=False, length=24),
            nullable=False,
        ),
        sa.Column("account_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("tab_order", sa.Integer(), nullable=False),
        sa.Column(
            "last_status",
            sa.Enum(
                "starting",
                "idle",
                "working",
                "waiting_approval",
                "waiting_input",
                "error",
                "exited",
                "unknown",
                name="tabstatus",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("status_detail", sa.String(length=200), nullable=True),
        sa.Column("plan_node_id", sa.String(length=26), nullable=True),
        sa.Column("context_used_pct", sa.Float(), nullable=True),
        sa.Column("session_cost_usd", sa.Float(), nullable=True),
        sa.Column("agent_session_id", sa.String(length=64), nullable=True),
        sa.Column("closed_at", ptypes.UtcDateTime(length=32), nullable=True),
        sa.Column("created_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.Column("updated_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["agent_accounts.id"], name=op.f("fk_tabs_account_id_agent_accounts")),
        sa.ForeignKeyConstraint(
            ["plan_node_id"],
            ["plan_nodes.id"],
            name="fk_tabs_plan_node_id_plan_nodes",
            ondelete="SET NULL",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_tabs_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_tabs_workspace_id_workspaces"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tabs")),
    )
    with op.batch_alter_table("tabs", schema=None) as batch_op:
        batch_op.create_index("ix_tabs_project_order", ["project_id", "tab_order"], unique=False)

    _ = op.create_table(
        "agent_sessions",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("tab_id", sa.String(length=26), nullable=False),
        sa.Column("agent_session_id", sa.String(length=64), nullable=False),
        sa.Column("transcript_path", sa.String(length=4096), nullable=True),
        sa.Column("started_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.Column("ended_at", ptypes.UtcDateTime(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["tab_id"], ["tabs.id"], name=op.f("fk_agent_sessions_tab_id_tabs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_sessions")),
    )
    with op.batch_alter_table("agent_sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_sessions_agent_session_id"), ["agent_session_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_agent_sessions_tab_id"), ["tab_id"], unique=False)

    _ = op.create_table(
        "approval_rules",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("tab_id", sa.String(length=26), nullable=False),
        sa.Column("tool", sa.String(length=128), nullable=False),
        sa.Column("command_prefix", sa.String(length=1000), nullable=True),
        sa.Column("created_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["tab_id"], ["tabs.id"], name=op.f("fk_approval_rules_tab_id_tabs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_rules")),
    )
    with op.batch_alter_table("approval_rules", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_approval_rules_tab_id"), ["tab_id"], unique=False)

    _ = op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("tab_id", sa.String(length=26), nullable=False),
        sa.Column("agent_session_id", sa.String(length=64), nullable=False),
        sa.Column("tool", sa.String(length=128), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column("risk", ptypes.JsonList(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "allowed",
                "denied",
                "expired_to_native",
                "cancelled",
                name="approvalstatus",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("decided_by", sa.String(length=16), nullable=True),
        sa.Column("requested_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.Column("decided_at", ptypes.UtcDateTime(length=32), nullable=True),
        sa.Column("expires_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.ForeignKeyConstraint(["tab_id"], ["tabs.id"], name=op.f("fk_approvals_tab_id_tabs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
    )
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_approvals_tab_id"), ["tab_id"], unique=False)

    _ = op.create_table(
        "canvas_positions",
        sa.Column("plan_id", sa.String(length=26), nullable=False),
        sa.Column("node_id", sa.String(length=26), nullable=False),
        sa.Column("x", sa.Integer(), nullable=False),
        sa.Column("y", sa.Integer(), nullable=False),
        sa.Column("user_moved", sa.Boolean(create_constraint=True), nullable=False),
        sa.Column("updated_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["node_id"], ["plan_nodes.id"], name=op.f("fk_canvas_positions_node_id_plan_nodes"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["plans.id"], name=op.f("fk_canvas_positions_plan_id_plans"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("plan_id", "node_id", name=op.f("pk_canvas_positions")),
    )
    _ = op.create_table(
        "checkpoints",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("tab_id", sa.String(length=26), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("git_ref", sa.String(length=255), nullable=False),
        sa.Column("tree_sha", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("files_changed", sa.Integer(), nullable=False),
        sa.Column("insertions", sa.Integer(), nullable=False),
        sa.Column("deletions", sa.Integer(), nullable=False),
        sa.Column("created_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.ForeignKeyConstraint(["tab_id"], ["tabs.id"], name=op.f("fk_checkpoints_tab_id_tabs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checkpoints")),
        sa.UniqueConstraint("tab_id", "turn", name=op.f("uq_checkpoints_tab_id")),
    )
    _ = op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tab_id", sa.String(length=26), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "claude_hook",
                "claude_statusline",
                "codex_hook",
                "codex_notify",
                "sidecar",
                "core",
                name="eventsource",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", ptypes.JsonObject(), nullable=False),
        sa.Column("at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.ForeignKeyConstraint(["tab_id"], ["tabs.id"], name=op.f("fk_events_tab_id_tabs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.create_index("ix_events_tab_id", ["tab_id", "id"], unique=False)

    _ = op.create_table(
        "handovers",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("source_tab_id", sa.String(length=26), nullable=True),
        sa.Column(
            "target_agent",
            sa.Enum("claude", "codex", "opencode", "grok", "cursor", name="agentkind", native_enum=False, length=24),
            nullable=True,
        ),
        sa.Column("plan_node_id", sa.String(length=26), nullable=True),
        sa.Column("summary_markdown", sa.Text(), nullable=False),
        sa.Column("note_ref", sa.String(length=1024), nullable=True),
        sa.Column("created_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_node_id"], ["plan_nodes.id"], name=op.f("fk_handovers_plan_node_id_plan_nodes"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_handovers_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_tab_id"], ["tabs.id"], name=op.f("fk_handovers_source_tab_id_tabs"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_handovers")),
    )
    _ = op.create_table(
        "leases",
        sa.Column("node_id", sa.String(length=26), nullable=False),
        sa.Column("lease_id", sa.String(length=26), nullable=False),
        sa.Column("tab_id", sa.String(length=26), nullable=False),
        sa.Column("fence", sa.Integer(), nullable=False),
        sa.Column("acquired_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.Column("renewed_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.Column("expires_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["node_id"], ["plan_nodes.id"], name=op.f("fk_leases_node_id_plan_nodes"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tab_id"], ["tabs.id"], name=op.f("fk_leases_tab_id_tabs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("node_id", name=op.f("pk_leases")),
        sa.UniqueConstraint("lease_id", name=op.f("uq_leases_lease_id")),
    )
    _ = op.create_table(
        "node_artifacts",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("node_id", sa.String(length=26), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("commit", "file", "test_report", "url", "note", name="artifactkind", native_enum=False, length=24),
            nullable=False,
        ),
        sa.Column("ref", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("created_at", ptypes.UtcDateTime(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["node_id"], ["plan_nodes.id"], name=op.f("fk_node_artifacts_node_id_plan_nodes"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_node_artifacts")),
        sa.UniqueConstraint("node_id", "kind", "ref", name=op.f("uq_node_artifacts_node_id")),
    )
    with op.batch_alter_table("node_artifacts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_node_artifacts_node_id"), ["node_id"], unique=False)

    _ = op.create_table(
        "plan_edges",
        sa.Column("src_node_id", sa.String(length=26), nullable=False),
        sa.Column("dst_node_id", sa.String(length=26), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("blocks", "parent", "related", name="edgekind", native_enum=False, length=24),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dst_node_id"], ["plan_nodes.id"], name=op.f("fk_plan_edges_dst_node_id_plan_nodes"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["src_node_id"], ["plan_nodes.id"], name=op.f("fk_plan_edges_src_node_id_plan_nodes"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("src_node_id", "dst_node_id", "kind", name=op.f("pk_plan_edges")),
    )
    with op.batch_alter_table("plan_edges", schema=None) as batch_op:
        batch_op.create_index("ix_edges_dst", ["dst_node_id", "kind"], unique=False)


def _drop() -> None:  # noqa: PLR0915 -- generated DDL, one statement per table and index
    with op.batch_alter_table("plan_edges", schema=None) as batch_op:
        batch_op.drop_index("ix_edges_dst")

    op.drop_table("plan_edges")
    with op.batch_alter_table("node_artifacts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_node_artifacts_node_id"))

    op.drop_table("node_artifacts")
    op.drop_table("leases")
    op.drop_table("handovers")
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_index("ix_events_tab_id")

    op.drop_table("events")
    op.drop_table("checkpoints")
    op.drop_table("canvas_positions")
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_approvals_tab_id"))

    op.drop_table("approvals")
    with op.batch_alter_table("approval_rules", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_approval_rules_tab_id"))

    op.drop_table("approval_rules")
    with op.batch_alter_table("agent_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_sessions_tab_id"))
        batch_op.drop_index(batch_op.f("ix_agent_sessions_agent_session_id"))

    op.drop_table("agent_sessions")
    with op.batch_alter_table("tabs", schema=None) as batch_op:
        batch_op.drop_index("ix_tabs_project_order")

    op.drop_table("tabs")
    with op.batch_alter_table("plan_nodes", schema=None) as batch_op:
        batch_op.drop_index("ix_nodes_plan_status")

    op.drop_table("plan_nodes")
    with op.batch_alter_table("kb_edges", schema=None) as batch_op:
        batch_op.drop_index("ix_kb_edges_dst")

    op.drop_table("kb_edges")
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workspaces_project_id"))

    op.drop_table("workspaces")
    op.drop_table("quota_current")
    with op.batch_alter_table("plans", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_plans_project_id"))

    op.drop_table("plans")
    with op.batch_alter_table("kb_nodes", schema=None) as batch_op:
        batch_op.drop_index("ix_kb_nodes_project_type")

    op.drop_table("kb_nodes")
    op.drop_table("iterations")
    op.drop_table("vault_files")
    op.drop_table("settings")
    with op.batch_alter_table("quota_history", schema=None) as batch_op:
        batch_op.drop_index("ix_quota_hist")

    op.drop_table("quota_history")
    op.drop_table("projects")
    op.drop_table("frecency")
    op.drop_table("agent_accounts")
