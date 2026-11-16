# QA-3 — human direction gate

Tier 3 is available only through UI/CLI RPC `plan/directionGate`; it is absent
from MCP. Commands come from the frozen L0 `gate_profile.direction` argv list.
The default runs `sh tools/check.sh` and the full tests with the UI environment.
Execution uses the selected tab's container and the integrated project root.
Agents cannot close L0. Human completion requires terminal L1 children and a
fresh green `direction` receipt. Vault status edits cannot bypass that guard.

Receipts bind the checkout tree, direction contract, plan revision, child
contracts/statuses, command argv and observed tool versions. SQL persistence
rechecks frozen revision and children. Receipt and human audit commit together;
checkout edits invalidate the receipt. Gate execution runs outside the board
lock. The durable audit retains the execution tab and UI PID for rechecking after
restart. Resource checks require at least five open tabs, core RSS <=120 MiB and
UI RSS <=150 MiB. Cache-read share is reported as a metric, not a pass guarantee.
`usage_log.py` reports uncached/cached input separately; their sum is the denominator.

Human invocation (then inspect the result before closing the L0):

```sh
PYTHONPATH=src .venv-core/bin/python -m aconsole.devcli direction-gate \
  --socket /path/to/core.sock --plan PLAN_ID --node L0_NODE_ID \
  --tab EXECUTION_TAB_ID --ui-pid UI_PID
```

`hostrun gate CORE-7` and the stop hook accept H-DAG keys. Existing task IDs and
malformed-input rejections remain covered.

Validation: 345 tests passed, 1 skipped across Tier 3, hostrun, domain, wire, fake
core and contracts. After adding version fingerprints, the affected seven Tier 3
tests passed again. Source typecheck and 11 contract checks pass; six architecture
contracts and raw SQL guard pass. Changed application code passes Ruff. The
hostrun actions file retains unrelated pre-existing Ruff findings; its task-key
change is covered by the hostrun suite. External docs checks are skipped because
docs/contracts is not linked.

## Measured acceptance limits

- `qa3-counts.json`: an isolated real PlanBoard freeze kept 29 vertices: six L0,
  23 L1, zero L2 vertices. The live user's plan was not frozen or closed.
- `qa3-usage.json`: existing CLI logs for 2026-11-16, 266 usage events, cache-read
  share 99.09%. This is an observed session aggregate, not a controlled H-DAG
  retry experiment or a promise about future CLI prompt caching.
- `qa3-rss.json`: the candidate native GTK app with five demo tabs measured
  194.08 MiB. This exceeds the 150 MiB UI target. Its fake core is deliberately
  not presented as a production-core memory benchmark.
- The already-running production core, PID 2891, measured 214.35–223.98 MiB,
  above the 120 MiB target. It had not been restarted onto this worktree, so this
  is a production baseline, not proof of the candidate core's memory use.

These measurements do not constitute a green live Tier 3 acceptance run. The
new gate correctly refuses green receipts when RSS or tab-count requirements
fail. No L0 direction was closed. Memory acceptance remains outstanding for
human direction review; publishing the implementation does not waive it.
