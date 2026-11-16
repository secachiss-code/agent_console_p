# APR-1 — durable human review

`node_escalate` blocks the active executor's node and writes one open `replan`
request in the same Writer transaction. Retrying the same request returns its
original ID. The graph gains no vertices. Exhausting four attempts and ordinary
`node_update(status="blocked")` use the same inbox/audit path. `plan_attention`
is emitted after persistence succeeds.

`plan/inbox` exposes open requests to UI/CLI. `plan/decide` accepts unblock,
cancel, reject and apply; apply delegates add/split proposals to APR-2's existing
change policy. Decision identity is fixed to human by the RPC implementation;
unknown input fields, including a forged `decided_by`, are rejected. Unblock
requires a nonempty note. Agent transitions from blocked/failed to pending are
forbidden by the domain. Human status changes and accepted vault status edits
also record a decision. Decisions, status changes and their audit metadata commit
atomically. Replaying an identical decision is safe; a different later decision
is refused. Payloads are transported as JSON text to retain arbitrary proposal
structure without introducing an untyped contract field.

The existing CORE-6 merge path retains verifying without an expiring lease and
emits attention; its restart and capability tests remain in the regression suite.
No human review decision was made against the live user's product plan.

Validation covers real SQLite, Git, MCP and a real daemon IPC socket. Tests
exercise escalation replay, agent refusal, mandatory notes, cancel/reject,
decision replay and restart, spoof rejection, failed writes and rollback after
the graph has been flushed inside the SQL transaction. UI presentation belongs
to FE-2. Tier 2 completion remains fail-closed until QA-2.

Regression: 156 tests passed (review, MCP pipeline/tools/retries, plan wire,
domain matrix, checked completion and contracts). Source typecheck, schema/golden
checks and IPC validation pass (11 contract checks; two external documentation
checks skipped because docs/contracts is not linked). All six architecture
contracts, Ruff and the raw SQL guard pass.
