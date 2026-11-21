"""Expected public MCP capabilities, including the H-DAG pipeline tools."""

ALL_TOOLS = frozenset({
    "plan_create", "plan_add_nodes", "plan_save", "plan_delete", "plan_get", "plan_ready", "node_claim",
    "step_begin", "step_submit", "step_reset", "gate_run", "node_escalate", "plan_graph", "plan_node_get",
    "index_overview", "index_find", "index_neighbors", "index_read", "index_task_context", "index_lint",
    "node_update", "node_attach", "ctx_get", "kb_add", "kb_impact", "handover", "plan_list",
})
EXECUTOR_TOOLS = ALL_TOOLS - {"plan_create", "plan_add_nodes", "plan_save", "plan_delete"}
