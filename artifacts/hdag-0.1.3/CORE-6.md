# CORE-6 — assembled L2 pipeline

PipelineService implements begin, submit, reset and micro gate execution. PlanBoard
delegates application policy; separate adapters provide real Git side refs,
Writer-backed attempts/blocks/receipts and disposable scratch/prefix/gate caches.
Bootstrap binds the actual tab's container, user, workspace, model and native
session. Verification argv comes from the frozen direction profile. Tool version
probes also execute through the bound container runner.

Blocks and their chain heads are durable. Concurrent duplicate begin/submit
requests create one attempt and one block. Old fences are rejected before work
and again in the Writer transaction, including lease reclamation after request
authorization. Cache loss and database reopen resume the same chain. Four failed
micro gates block the node; no fifth attempt starts. Normalization belongs to the
captured delta. The attempt remains running after micro success until Tier 2
accepts it; failure at that later gate can still consume it correctly.

Content-addressed side refs preserve earlier verified snapshots. Reset restores
only files matched by the module contract, removes new scope files and preserves
other changes, HEAD and the user's index. A merged pipeline node remains verifying
without an expiring lease; it emits plan_attention and survives restart without
requeueing. Both agent and human completion require a blast receipt. The blast
implementation is the next QA-2 node and currently refuses completion.

Planning tabs can request a spec but cannot submit/reset/run gates. Attention
events are capability-gated by hdag.v1 for old clients. Exported MCP schemas and
contract goldens are regenerated. ContextShaper checks the entire reply budget
before marking a prefix delivered.

Validation: 213 tests passed, 1 skipped across MCP pipeline/tools/auth, Claude HTTP,
legacy checked completion, durable attempts, context shaping, Tier 1, domain plan
and contracts. Tests use real Git, SQLite, cache and MCP with a recording container
runner; they do not claim a live CLI end-to-end run. Separate existing Tier 1 and
container-runner suites retain execution coverage. Full source typecheck and
contract checker: 11 checks passed. Ruff, raw SQL check and all six import-linter
architecture contracts pass. docs/contracts is not linked, so its two checks are
skipped. Live CLI, Tier 2/3 and performance acceptance remain in later nodes.
