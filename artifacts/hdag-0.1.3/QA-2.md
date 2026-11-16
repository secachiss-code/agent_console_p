# QA-2 — Tier 2 blast verification

BlastGate runs the frozen module's test_cmd as argv, followed by the direction's
trusted test runner with dependent test files. No command is supplied by an agent
and no shell is involved. Selection uses the persisted code graph's recursive CTE,
three hops, nearest depth first and a global cap of 30 test paths. A missing graph
still runs the explicit module test command. Unsafe test path arguments are refused.

The completed L2 chain must have a fresh micro receipt. Blast receipts are bound
to the captured tree, module contract, direction profile, tool versions and argv.
Both human and agent completion require a matching green blast receipt; editing
the workspace invalidates it. Completion checks do not rerun tests. A green durable
receipt survives loss of the disposable cache. Tests that change the workspace
cannot produce a green receipt for their new contents.

Entering verifying runs Tier 2. Merge keeps pipeline nodes verifying, releases the
worker lease, tries Tier 2 and emits attention for human review. The original
attempt's tab and retained fence authorize post-merge verification; the Writer
rechecks that ownership or the active lease before persisting. Gate execution
runs outside the global board lock. Successful Tier 2 marks the attempt passed;
a failure records its receipt and consumes the attempt in one SQL transaction.
Four failures block the node and create the durable replan request. Commit
attachments must resolve to a commit in the actual worktree.

Validation uses real Git, SQLite, CTE and MCP with a recording container runner.
It covers depth/cap selection, state edits, cache loss/restart, agent and human
completion, automatic verifying/merge gates, failed budgets, commit resolution,
unsafe paths, writes during tests and lease revocation during execution. Existing
container-runner tests cover real process execution; this report does not claim a
new live CLI or Tier 3 acceptance run.

Checks: broad regression 192 passed, 1 skipped; after removing gate execution
from the board lock, the affected regression passed 63 tests. A separate concurrent
gate/update/read test also passed. Source and new-test typechecks, Ruff, raw SQL
guard and all six import contracts pass; check_contracts passes 11 checks
(two external documentation checks skipped because docs/contracts is not linked).
