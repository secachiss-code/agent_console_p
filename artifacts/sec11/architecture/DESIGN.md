# SEC-14: Git execution boundary and approved hook protocol

Status: design complete; implementation and security acceptance **not performed**.
Base: `a5bb65bd1b834c21f50116700aebabde3b630799`.
SEC-11 and SEC-13 remain failed. This document does not authorize deployment,
host policy changes, a new dependency, or additional hook permissions.

## Decision

Separate ordinary Git execution from hook execution. First validate a contained
Git worker with no ability to execute repository-selected programs. Then validate
a separate, authenticated hook broker. Keep both disabled in the product until
their combined compatibility and security matrix passes independently.

Do not promote either previous laboratory launcher. Re-listing configuration,
hashing it again, or binding only the config file cannot be the security boundary.
Do not grant Git permission to run `/bin/sh`, Python, or an arbitrary interpreter
in order to restore hooks: a late filter would gain that same permission.

This is a proposed architecture, not a claim that seccomp, namespaces, or an
unimplemented broker already provide the properties below.

## What is known at the base

| Evidence | Observed result | Consequence |
| --- | --- | --- |
| `runner.py:172`, `_spawn` | Enumerates driver names, then executes another Git process | Configuration can change between these processes |
| Original `race_probe.py` | Product executes a newly introduced clean filter | Keep this unchanged probe as a release blocker |
| `exec-boundary/guarded-after.xml` | 29 pass, 1 fail; approved pre-commit cannot execute | Blanket exec denial alone is incompatible |
| `config-namespace/adversarial-final.xml` | 4 pass, 4 fail; replace/include fail in ordinary and linked repositories | Immutable bytes on a file bind do not protect subsequent pathname resolution |
| `operations.py:145`, `merge_hooks` | Two hooks run directly through `_run_hook`, outside `GitRunner` | Protecting only the Git spawn misses a separate host execution surface |
| `git_merge.py:404`, `_run` | A target without a worktree uses merge hooks, commit-tree, then old-OID update-ref | Broker must support this path as well as normal commits |

These results are inherited evidence, not fresh benchmark or host executions.
The evidence manifest pins exact local source and JUnit bytes for review.

`GitCall.hooks=True` currently means an explicit commit/merge write, not approval
of an immutable script. `_hook` checks a pathname and `_run_hook` later executes
that pathname. The protocol proposed here must not confuse those mechanisms with
a content-bound approval. This is a code observation; a new hook replacement
exploit was not executed in SEC-14.

## Trust and authority

The adversary controls worktree files, attributes, local/common/worktree config,
include files, hook files and their dependencies, and concurrent rename/symlink
changes. Treat every discovery result and every worker reply as untrusted data.
An application mutex does not lock out another agent or arbitrary Git process.

The trusted computing base comprises the installed core/broker, policy and
launcher, pinned runtime image, kernel enforcement, and authenticated UI approval
channel. None may be loaded from the writable checkout. The agent must not be
able to change the broker database, runtime, policy, or executable by editing a
repository. This requires deployment work; the current checkout cannot establish
that property by writing another Python launcher.

The core alone requests operations and consumes one-shot grants. The worker and
hook processes have no UI credentials, core socket, hostrun socket, approval
database, or endpoint capable of asking the host to execute an arbitrary argv.
A public socket protected only by the shared host UID is insufficient.

The first supported hooks run inside a project sandbox. A request to run them
with unrestricted host/home/network access is a separate product/security
decision and is outside this design. Existing user-requested commits do not
constitute consent to introduce new host permissions.

## Git worker contract

1. Resolve project/worktree identities from trusted registrations. Open and pin
   their directory identities through trusted code. Validate common and per-
   worktree metadata before granting access. Reject arbitrary `GIT_DIR`, object
   alternates, symlink escapes, or external gitdirs unless explicitly registered.
   Resolution must remain confined during use, not just at a preceding realpath.
2. Start a fresh contained process with a minimal environment and closed inherited
   descriptors. Only registered project roots, required Git metadata, immutable
   runtime and private scratch are visible. Reads have no write grant; writes
   have only the necessary project grant. No host home, user runtime directory,
   desktop bus, host sockets, network, or host process namespace is exposed.
   Object alternates and linked worktrees require explicit additional grants;
   never mount all of the host parent directory to make discovery convenient.
3. Install child-execution denial before handling repository-controlled input.
   The launcher must distinguish initial execution of pinned Git from every
   subsequent exec, across supported ABIs, threads and forked descendants.
   A reusable file-descriptor number is not proof of executable identity. The
   earlier execveat filter is therefore evidence for a hypothesis, not production
   enforcement. Unknown kernel/ABI/runtime or setup failure rejects the operation.
4. Disable native hooks and retain existing config/env/diff safeguards as defense
   in depth. Dynamic filters, merge drivers, signing helpers and configured
   commands cannot acquire exceptions through config or command-line arguments.
5. Use structured, command-specific requests. The trusted adapter builds argv;
   callers cannot supply extra `-c`, aliases, hook paths, shell snippets or runtime
   executable paths. Required external Git helpers must first be identified by
   the compatibility census below; no silent fallback to the host is permitted.
6. Enforce process-tree termination, wall time, memory, process count and streamed
   output limits. Current capture_output followed by a byte check is not a hard
   streaming memory bound. A descendant must not survive cancellation or retain a
   capability when the operation returns.

The filesystem and IPC boundary remains necessary even with exec denial: a Git
process handles hostile data and has filesystem syscalls without spawning a
helper. Seccomp alone is explicitly not a sandbox in the kernel documentation.
The exact enforcement mechanism (runtime isolation plus syscall/LSM policy) is
an implementation milestone, not selected or proven by this document. Required
capabilities must be probed, with fail-closed handling for missing ones.

## Hook broker protocol

Use separate typed requests: `PrepareOperation`, `ApproveHookBundle`,
`RunHookPhase`, `PublishOperation`, `CancelOperation`. No generic host exec RPC.
An untrusted Git child cannot initiate or approve one of these requests.

Prepare creates a durable, random operation ID and resolves project, worktree,
actor, target ref/old OID, candidate tree/index, message, mode and allowed phases.
Capture hooks from `$GIT_COMMON_DIR/hooks`; reject configuration-selected paths.
Copy script bytes into a broker-owned immutable bundle before approval. Include
an explicit manifest of interpreters/runtime, helper scripts, project inputs,
arguments, working directory and access grants. Pin script dependencies or run
them only against the captured project snapshot. Hashing the entrypoint alone
does not authorize mutable sourced files, `$PATH` helpers, symlinks or imports.
Reject unsupported dependency capture instead of executing live host paths.

The approval binds the canonical bundle digest and operation ID to the project,
worktree, actor, policy version, runtime identity, expected repository state,
phase set, access grants and expiry. Consumption is atomic and persisted before
execution. Concurrent/replayed requests cannot spend it twice. An idempotency
key may retrieve the existing result; it cannot create a second execution.
Changing any bound input requires a new preparation and approval. Do not use
the approval broker's old session/tool/input deduplication as a shortcut.

Each phase runs from the captured bundle in a fresh project sandbox, with a
bounded temporary index/message and no broker credentials. Scripts can launch
their declared tools inside that sandbox; this permission is never added to the
ordinary Git worker. A hook's nested Git also stays inside the sandbox, including
any driver it starts. Log bounded output, exit status and observed changes. Only
typed results and validated artifacts cross back to the core. No path supplied
by the hook becomes a host write target.

For initial implementation the broker is **not** a transparent replacement for
all Git hooks. The compatibility census must record phase ordering, arguments,
environment, index/message edits, post-success hooks and failure behavior for
each operation. Unsupported installed hooks block the operation with a specific
diagnostic; they are never silently skipped. The documented pre-commit,
prepare-commit-msg, commit-msg and post-commit behavior is a starting reference,
not an exhaustive list for merge, checkout, rebase or ref transactions.

## Transactions and hook mutations

Do not run a pre-commit outside Git and then call ordinary commit with native
hooks still enabled. That either doubles execution or reopens the escape.
Do not substitute commit-tree for commit until differential tests prove required
message, author/committer, empty/root/amend, index and hook semantics.

Prepare on a detached candidate snapshot. Hooks can change its files/index/message;
collect those changes and present any changed candidate for a new confirmation
before publication. Use a new grant for another hook execution. A successful
message-editing hook must affect the final commit message, including the current
target-without-worktree merge path. Never silently discard a hook's staged edits
or copy arbitrary hook paths back over live files.

For a target ref without a checked-out worktree, preserve old-OID compare-and-swap
and the existing durable operation journal. Revalidate the expected tree and OID
before publication. A stale target fails; it must not be force-updated. Persist
the actual produced OID before follow-up. On restart, reconcile against real refs
as the existing merge recovery does; never rerun a consumed hook automatically.

Publishing a checked-out worktree is a separate hard problem. A ref CAS plus an
application mutex cannot atomically replace an index and files against arbitrary
external writes. The first broker prototype must therefore operate on isolated
candidate repositories and must not publish into a live worktree. The product
adapter stays disabled until a reviewed publication/recovery protocol preserves
concurrent edits, ignored/untracked files, index state and dirty hook changes.
This is an explicit implementation blocker, not a claim of solved atomicity.

For a post-success hook failure, record the completed Git mutation and hook
failure separately. Do not report that the ref was rolled back or retry a commit.
On timeout/cancel, terminate descendants, retain recovery metadata, revoke grants
and report the actual publication state. An ambiguous outcome requires recovery,
not a second automatic execution or a success response.

## Implementation sequence and independent acceptance

| Stage | Concrete output | Gate before the next stage |
| --- | --- | --- |
| A: census | Machine-readable list of runner callers/commands and direct `_run_hook`; differential traces of commit, merge, squash, fast-forward and no-worktree merge hooks | Existing hook refusals, message edits, index edits, recovery and all actually invoked hook phases accounted for |
| B: isolated worker | Installed immutable launcher/runtime; capability checks; structured command adapter; no product routing | Original race and post-ready replace/include/worktree/inplace attacks pass unchanged; executable/FD/ABI/descendant attacks denied; external canaries and host sockets inaccessible |
| C: isolated broker | Private persistent grant store; immutable bundles; authenticated requests; sandboxed phase runner | Script/dependency/runtime/path replacement, stale state, cross-tab/project, replay, crash and cancellation tests; positive approved hook controls pass |
| D: publication | Reviewed commit/merge adapters and journal transitions; crash-injection harness | Differential results against ordinary Git; staged/unstaged/untracked/ignored files preserved; late external changes refused; exactly-once grant semantics after restart |
| E: product routing | Route GitRunner and direct merge hooks together; remove host bypass | Full related core/UI/contract checks and independent installed host-boundary acceptance; no weakened assertions |

Every stage needs a separate implementation node and commit with measured
evidence. A passing design or prototype cannot close SEC-11. No new implementation
nodes are claimed ready by this document: stage A is the next executable task.

The security matrix must include ordinary and linked worktrees, empty/bare/SHA256
repositories where supported, config symlinks, hostile gitdir/commondir/alternates,
attribute races, unknown driver names, global/system/includes/worktree configs,
hook fallback chains, indirect sourced files, forged replies, output exhaustion,
setup failure and unsupported architecture. A denied-helper result may be a
safe diagnostic, but must not silently stage unfiltered content or resolve a
required filter/custom merge driver as success. Existing configuration-dependent
semantics need an explicit compatibility decision before product routing.

Acceptance must retain the original race bytes and marker-absence assertions,
plus positive controls showing attacks execute without the boundary. Verify
approved hooks execute exactly once and refusing hooks leave refs/index/files
as specified. Namespace identity alone does not prove containment; use external
read/write/socket/process canaries and repeat against the installed host path.
An agent-editable report cannot substitute for the independent GATE-1 authority.

## References and limits

Local evidence: [original race](../race_probe.py),
[exec experiment report](../exec-boundary/REPORT.md),
[namespace experiment report](../config-namespace/REPORT.md),
[source/evidence manifest](evidence.json).

Primary references consulted on 2026-11-10:
[Git hook contracts](https://git-scm.com/docs/githooks),
[kernel seccomp documentation](https://www.kernel.org/doc/html/latest/userspace-api/seccomp_filter.html),
[kernel Landlock documentation](https://www.kernel.org/doc/html/latest/userspace-api/landlock.html).
These explain interface semantics and design constraints, not installed support
or acceptance of the proposed implementation. Landlock is a possible additional
layer; its availability and necessary rights on the target have not been tested.

SEC-14 changes documentation and evidence indexing only. No product source,
existing assertions, launcher, host service, policy or approval behavior changed.
No new full suite, coverage, GPU, CI, package-install or security PASS is claimed.
