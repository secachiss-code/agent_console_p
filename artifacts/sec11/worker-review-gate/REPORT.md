# SEC-34: prepared first-worker review packet

Base `fea8c49c3604bde3e5fdb8fd2e2fa263970aec03` (SEC33). Artifact-only.
Disposition: **PREPARED FOR REVIEW; actual review NOT PERFORMED**.
Stage B authorization **absent**, Stage A **INCOMPLETE**, SEC11/13 **failed**.
No product/runtime/permissions/probe/routing changes or installed execution.

`contract.json` is the proposed finite gate. `source-index.json` pins current
product Python files, all tracked SEC29–33/architecture artifacts at the base,
unchanged original security probes, their experimental launcher/plugin and
relevant test dependencies. Hashes establish provenance, not fresh installed
results or independence. Existing SEC31 482-case, SEC32 312-case and SEC33 two
122-case results remain historical finite laboratory evidence.

## Decisions submitted for review

Only InspectCandidate and StageCandidatePaths on separate standalone SHA1/SHA256
detached/unborn imported candidates are proposed. No generic runner, caller argv,
environment/executable, hook phase, live worktree publication or fallback.
SEC30's eleven excluded families and SEC31 dependency refusals remain explicit.
The smaller SEC32 UTF-8 regular-file loose-object importer subset stays in force;
SEC33 synthetic opaque path/copy/unmerged parser records expand no import scope.

SEC33 adds fixed index/status observations beyond SEC30 command authority. This
packet proposes an **explicit v1 extension requiring actual review**: status
before/after staging and `ls-files --stage -z` before/after, only inside the
candidate. The independent native diff oracle stays outside worker authority.
Manifest/index identities should be computed by a bounded trusted supervisor.
SEC31 mutable config discovery must become validation of captured bytes; it is
neither an open config RPC nor confinement. No implementation follows from this
decision being written down.

The response preserves lossless hex-byte porcelain records, all staged paths
versus HEAD and a separate per-call index-entry delta, tree OID, actual raw index
SHA256 and new candidate digest. Retrying an old digest rejects after actual byte
changes. Raw index identity can vary between fresh native fixtures due stat cache.
Request/output bounds are 1MiB, 4096 records/paths, 4096 bytes/path and diagnostics,
8MiB wire response and aggregate process output. New depth 16 and strict framing,
duplicate-key/type/trailing-data checks are proposed installed requirements.
All allocation/capture/serialization must be bounded before accumulation, counting
discarded stderr too. SEC33 checks after capture and does not meet that requirement.

Preflight denials preserve state. Once mutation may have started, errors durably
quarantine the candidate and retain actual partial state, never promise rollback
or automatic retry. If bounded observations cannot establish current identity,
return null current digests with `identity_state=unknown`, explicitly retain last
known identity separately and require recovery. Overflow may omit observations;
it must not recycle stale identity as current or return partial success. Full
manifests belong in bounded retained supervisor evidence, outside the wire reply.
This is stricter future behavior than the SEC33 in-memory wrapper.

## Proposed immutable runtime and installed cases

The machine contract specifies a protected installed launcher/supervisor/policy
and complete pinned Git runtime image, sealed input FD, strict descriptor closure,
private non-propagating filesystem/PID boundary and no host files/sockets/network.
Inspect sees a read-only candidate. Stage receives only private index/object/lock
write authority; config/attributes/hooks/HEAD/refs/worktree stay read-only. If an
implementation cannot enforce that separation while preserving native lock/rename
semantics, it rejects rather than granting all `.git` write access.

Initial pinned execution must be distinguished from every subsequent exec across
FD reuse, execveat, forks, threads and supported ABIs. The exact native transition,
syscall/LSM policy and platform capability set remain unresolved implementation
choices requiring review and proof. There is no proposed shell/interpreter or
nested-Git exception. Namespace identity, config enumeration or an FD number alone
cannot establish this property. Missing immutable runtime, capability, durable
record store or any enforced limit fails startup before Git without fallback.

Proposed hard budgets: 30s including setup/response, 256MiB aggregate memory,
16 tasks, 8MiB aggregate streams, 64MiB each candidate/scratch, 16384 candidate
entries, 1s cancel grace before whole-cgroup kill/reap. Storage/entry limits are a
new narrower compatibility choice requiring explicit review/native positives.
The supervisor must have bounded traversal/encoding inside its resource budget.
Cancellation, EOF, timeout or crash must durably record ambiguity, terminate all
descendants and reconcile on restart before candidate reuse. B's isolated durable
quarantine is a prerequisite distinct from C's future one-shot hook grant store.
SEC32 cleanup and SEC33 in-memory quarantine establish neither property.

Nineteen named **NOT RUN** installed cases B01–B19 specify stimuli, expected
failure/state and native/valid positive controls: runtime replacement, startup
failure, sealed input/path attacks, FD leaks/reuse, mount boundaries, exec/ABI,
original SEC11/13 schedules, canaries/sockets, strict requests, native parity,
helper/hook refusal, streams, aggregate resources, actual partial failure,
cancel/crash/restart, forged replies, excluded operations and review authority.
Each must retain installed image/policy/source identity, exact stimulus, exit,
bounded streams, state/canary evidence and native positive-control result. A
complete independent installed verdict is future work, not this local verifier.

## Original probe integration is still a blocker

The unchanged SEC11 probe schedules a late filter through GitRunner._execute and
requires that its enumeration schedule actually ran. An immutable candidate copy
cannot turn an unreachable `swapped` assertion into a PASS. SEC13's unchanged
probe directly invokes its experimental launcher and uses ready/release after
the mount, then replace/include/worktree/inplace in ordinary and linked repos.
Running that old launcher is a baseline, not testing a new installed worker.

A separately reviewed installed harness/adapter must preserve the exact sources,
schedules, marker-absence and parent-namespace assertions, alongside unguarded
native controls proving actual marker execution. Keep the original red baseline.
If unchanged schedule integration is impossible, record NOT RUN/BLOCKED and seek
a concrete reviewed test integration decision, never skip/xfail, invert a security
assertion or silently replace the probe. Linked-origin refusal does not prove
linked-operation parity: the installed boundary still needs hostile linked input
and equivalent attack controls. No original probe was rerun in SEC34.

## Gate and remaining sequence

Actual review must bind this packet and source digests, proposed observation and
resource changes, complete launch/policy mechanism and unresolved probe integration.
Reviewer identity/disposition belongs to an independent trusted authority. A local
agent-editable packet or validation cannot supply approval or user authorization.
The node is preparation only; no Stage B implementation is authorized by its PASS.

1. Record actual review disposition and resolve implementation/probe blockers.
2. Separate B implementation node/commit, protected installation and independent
   installed B01–B19 acceptance; keep product routing disabled.
3. C: trustworthy consistent acquisition, immutable dependencies, authenticated
   project/tab/actor/state binding and durable atomic one-shot grants, tested
   against concurrent replay/restart. No automatic consumed-hook retry.
4. D: retain changed index/tree/message for new review/grant, crash/cancel
   reconciliation, publication old-OID CAS and preservation of concurrent live,
   ignored/untracked changes. Record completed publication and late failure apart.
5. E: coordinated GitRunner/direct merge-hook routing and independent installed
   GATE-1 plus related core/UI/CI/install acceptance before reconsidering SEC11/13.

Full Stage A compatibility remains incomplete for excluded operations and
hook/helper/lifecycle behavior. A reviewer may explicitly accept the narrow v1
scope for B without claiming full Stage A PASS. No further open-ended census is
part of SEC34. QA4/GATE1/PERF7/CI/install and inherited daemon stall remain open;
no new product/full-suite/UI/coverage/performance/security PASS or push authority.

## Reproduction and verification limits

Run `python3 artifacts/sec11/worker-review-gate/collect.py` to print the source
index, compare it before updating saved evidence. Run
`python3 artifacts/sec11/worker-review-gate/verify.py` for deterministic local
integrity. It checks current files against the saved index and actual base Git
blobs, unchanged SEC33 product hashes, exact operation/observation authority,
limits, FD/mount grants, failed/unrun status, original probes and all 19 cases.
Twenty-one semantic corruptions must reject unauthorized scope/review promotion,
lost native controls, limits/recovery changes and matrix/gate removal.

Prose requirements are proposed design, not an executable runtime policy. The
verifier checks enumerated structural constraints and hashes; it cannot prove
the design complete, enforce prose, authenticate its own files, approve itself,
or exercise any installed boundary. Compilation and focused fatal/import Ruff
checks pass and are local script checks only. SEC30/31/32/33 unchanged saved-data
verifiers pass, with outputs equal to their saved validation JSON; retained in
`upstream-reverification.json`. No native fixture or original probe rerun follows
from that check. Full all-rule Ruff has 26 annotation, namespace-package, style,
subprocess and print findings retained in `lint-all.txt`; no lint configuration
changes or full lint PASS is implied. Repeated local verification must reproduce
the same validation bytes.
