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
        "handover",
        "Read or write this tab handover.",
        "knowledge",
        "action, goal?, done?, decisions?, open_questions?, next_steps?, failing_checks?",
        '{"action":"read"}',
    ),
)
TOOL_INFO = {tool.name: tool for tool in TOOL_CATALOG}
