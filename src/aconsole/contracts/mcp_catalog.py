"""Tool information shared by MCP discovery and the desktop reference."""

from dataclasses import dataclass
from typing import Literal

type ToolMode = Literal["plan", "execute"]
type ToolGroup = Literal["plan", "node", "step", "knowledge"]


@dataclass(frozen=True, slots=True)
class ToolInfo:
    name: str
    description: str
    group: ToolGroup
    arguments: str
    example: str
    mode: ToolMode | None = None


TOOL_CATALOG = (
    ToolInfo(
        "plan_create",
        "Create a plan. Same title returns the same plan.",
        "plan",
        "title, goal, nodes?",
        '{"title":"Worktree graph","goal":"Keep the graph current"}',
        "plan",
    ),
    ToolInfo(
        "plan_add_nodes",
        "Add nodes. Existing keys stay.",
        "plan",
        "plan_id, nodes",
        '{"plan_id":"plan-1","nodes":[{"key":"IDX-1","title":"Index","objective":"Index imports"}]}',
        "plan",
    ),
    ToolInfo(
        "plan_save",
        "Replace one draft plan.",
        "plan",
        "title, goal?, plan_id?, nodes?, repair_gate_profiles?, repair_paths?, repair_contracts?, repair_note?",
        '{"plan_id":"plan-1","title":"Worktree graph","goal":"Index imports"}',
        "plan",
    ),
    ToolInfo("plan_delete", "Delete one plan.", "plan", "plan_id", '{"plan_id":"plan-1"}', "plan"),
    ToolInfo(
        "plan_get",
        "Read a plan. Pass since for changes only.",
        "plan",
        "plan_id, response_format?, since?",
        '{"plan_id":"plan-1","response_format":"concise"}',
    ),
    ToolInfo("plan_ready", "List claimable nodes.", "plan", "plan_id?, limit?", '{"plan_id":"plan-1","limit":3}'),
    ToolInfo(
        "plan_graph", "Read plan overview or a neighborhood by node ID. Reuse arguments with cursor.", "plan",
        "plan_id, mode?, anchor?, depth?, edge_kinds?, limit?, cursor?",
        '{"plan_id":"plan-1","mode":"local","anchor":"node-1"}',
    ),
    ToolInfo(
        "plan_node_get", "Read a node and selected sections. Reuse arguments with cursor.", "node",
        "node_id, sections?, limit?, cursor?", '{"node_id":"node-1","sections":["contract","relations"]}',
    ),
    ToolInfo("plan_list", "List plans.", "plan", "status?", '{"status":"active"}'),
    ToolInfo("node_claim", "Take a node lease. Returns fence.", "node", "node_id", '{"node_id":"node-1"}'),
    ToolInfo(
        "node_update",
        "Set status or heartbeat.",
        "node",
        "node_id, fence?, status?, note?, evidence?",
        '{"node_id":"node-1","fence":1}',
    ),
    ToolInfo(
        "node_attach",
        "Attach one artifact.",
        "node",
        "node_id, fence, kind, ref, title?",
        '{"node_id":"node-1","fence":1,"kind":"test_report","ref":"reports/tests.txt"}',
    ),
    ToolInfo(
        "node_escalate",
        "Ask a human. Adds no node.",
        "node",
        "node_id, fence, reason, kind?",
        '{"node_id":"node-1","fence":1,"reason":"Which compatibility contract applies?"}',
        "execute",
    ),
    ToolInfo(
        "step_begin",
        "Resume one L2 step.",
        "step",
        "node_id, fence, full_context?",
        '{"node_id":"node-1","fence":1,"full_context":true}',
    ),
    ToolInfo(
        "step_submit",
        "Record one L2 step.",
        "step",
        "node_id, fence, step, affected_symbols?, note?, patch?",
        '{"node_id":"node-1","fence":1,"step":"spec"}',
        "execute",
    ),
    ToolInfo(
        "step_reset",
        "Restore files of one step.",
        "step",
        "node_id, fence",
        '{"node_id":"node-1","fence":1}',
        "execute",
    ),
    ToolInfo(
        "gate_run",
        "Run micro or blast once.",
        "step",
        "node_id, fence, tier",
        '{"node_id":"node-1","fence":1,"tier":"micro"}',
        "execute",
    ),
    ToolInfo(
        "ctx_get",
        "Read short knowledge.",
        "knowledge",
        "node_id?, query?, max_items?, response_format?",
        '{"query":"worktree graph","max_items":3}',
    ),
    ToolInfo(
        "kb_add",
        "Record one fact, decision, or requirement.",
        "knowledge",
        "type, title, body, source, code_refs?, supersedes?",
        '{"type":"fact","title":"Module dependency","body":"App imports store.","source":"app.py#L1-L2"}',
    ),
    ToolInfo(
        "kb_impact",
        "List neighbors of one target.",
        "knowledge",
        "target, hops?, limit?, kinds?",
        '{"target":"src/store.py","hops":3,"limit":5}',
    ),
    ToolInfo(
        "index_overview", "Read the worktree inventory by directory.", "knowledge",
        "scope?, depth?, limit?, cursor?", '{"scope":"src","depth":1}',
    ),
    ToolInfo(
        "index_find", "Find paths, symbols and related tests in the current worktree.", "knowledge",
        "query, intent?, scope?, kinds?, limit?, cursor?", '{"query":"plan_ready","intent":"change"}',
    ),
    ToolInfo(
        "index_neighbors", "Read incoming or outgoing module links for up to eight targets.", "knowledge",
        "targets, direction?, edge_kinds?, depth?, limit?, cursor?", '{"targets":["src/store.py"],"direction":"in"}',
    ),
    ToolInfo(
        "index_read", "Read specific ranges from the authorized worktree snapshot.", "knowledge",
        "refs, expected_revision?, max_bytes?", '{"refs":[{"path":"src/store.py","start_line":1,"end_line":20}]}',
    ),
    ToolInfo(
        "index_task_context", "Read the task contract and relevant worktree paths. Reuse a known digest.", "knowledge",
        "node_id, intent?, since_digest?, limit?", '{"node_id":"node-1","intent":"change"}',
    ),
    ToolInfo(
        "analyzer_list", "List managed Python analyzer extensions for this worktree.", "knowledge", "", "{}",
    ),
    ToolInfo(
        "analyzer_diagnostics", "Read managed extension diagnostics for selected Python files.", "knowledge",
        "analyzer, targets, expected_revision?", '{"analyzer":"ruff","targets":["src/example.py"]}',
    ),
    ToolInfo(
        "index_lint", "Run read-only Ruff on changed Python files, a task scope, or selected paths.", "knowledge",
        "scope?, node_id?, targets?, expected_revision?, limit?, cursor?", '{"scope":"node","node_id":"node-1"}',
    ),
    ToolInfo(
        "handover",
        "Read or write this tab handover.",
        "knowledge",
        "action, goal?, done?, decisions?, open_questions?, next_steps?, failing_checks?",
        '{"action":"read"}',
    ),
)
TOOL_INFO = {tool.name: tool for tool in TOOL_CATALOG}
