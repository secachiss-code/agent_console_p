"""Expected public MCP capabilities, including the H-DAG pipeline tools."""

ALL_TOOLS = frozenset({
    "plan_create", "plan_add_nodes", "plan_get", "plan_ready", "node_claim",
    "step_begin", "step_submit", "step_reset", "gate_run", "node_escalate",
    "node_update", "node_attach", "ctx_get", "kb_add", "kb_impact", "handover", "plan_list",
})
EXECUTOR_TOOLS = ALL_TOOLS - {"plan_create", "plan_add_nodes"}
