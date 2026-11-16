# APR-2 — durable human plan decisions

`plan/freeze` and `plan/applyChange` are UI/CLI RPCs, absent from MCP. Freeze
persists phase, revision, timestamp, baseline/growth counters, direction child
counts, gate hash, every contract hash and per-module blast radius. Radius uses
the persisted CTE and expands contract globs against graph paths. No graph keeps
NULL radius and returns NO_CODE_GRAPH; a present graph with missing radius is
rejected. Repeating freeze preserves revision/timestamp and writes one audit row.

Change application loads an existing inbox request, checks domain admission and
growth, then saves graph and human decision in one Writer transaction. Split
proof is resolved from kernel receipts for the request's node in the same plan;
an agent-supplied hash is insufficient. Replay works after restart. Failed proof
or growth leaves the request open and the graph unchanged. Frozen contracts also
reject edits through the vault adapter.

Application policy lives in plan_lifecycle.py and domain/plan_admission.py;
PlanBoard assembles adapters. Contract version is 1.5.0 with hdag.v1; package
version remains 0.1.2 until release. Fake results, typed UI facade, schemas and
golden examples are regenerated.

Validation: 56 tests across plan lifecycle/store/wire, MCP, Claude HTTP and
contracts passed; this includes fresh-core freeze/apply/restart with and without
a code graph. Additional negative cases cover NULL radius, frozen graph/vault
edits, monotonic growth after cancellation and forged split proof. Changed-source
Ruff and basedpyright pass; import-linter keeps all six architecture contracts;
raw SQL check passes. Full Tier 3 product acceptance remains in QA-3.

`tools/check_contracts.py`: 11 checks passed (including full source typecheck,
schema compatibility, both interpreters and real IPC method routing). Two
documentation checks were skipped because docs/contracts is not linked in this
checkout. CoreClient compatibility tests: 15 passed.

No live user plan was frozen or changed by these integration tests.
