# SEC-33: typed candidate results and actual failure state

Base `3600c68ed4f98edddcd54f48a1808413fae83202` (SEC-32). Artifact-only lab.
Stage A **INCOMPLETE**, SEC-11/13 **failed**, security acceptance **NOT RUN**.
No product, installed runtime, permissions, probes, routing or live publication.

`model.py` defines bounded lossless porcelain-v2 records (headers, ordinary,
rename/copy source and destination, unmerged, untracked/ignored). Literal paths
and header values use hex bytes, with exact native byte reconstruction. Unknown,
malformed, unsupported submodule/mode, incomplete rename, wrong object width,
path/record/output overflows reject explicitly. SHA1/SHA256 widths are selected
by the trusted fixture, not requests. Importer still supports its smaller UTF-8
path subset; synthetic invalid-UTF-8 response controls do not expand import scope.
Limits: 1 MiB per parsed output, 4096 records, 4096 bytes per relative path,
4096 diagnostic bytes; 1 MiB JSON request and 8 MiB serialized response envelope.
Envelope overflow quarantines with identities, explicit observations_omitted and
recovery_required instead of a partial success. These are finite response limits, NOT subprocess stream,
resource, process, memory, descendant or cancellation enforcement.

Inspect returns typed status, snapshot and raw index identities. Stage returns
status, tree OID, index SHA256 and new snapshot digest; old digest rejects reuse.
`changed_paths_hex` means all staged paths versus HEAD, including both sides of
renames; `index_changed_paths_hex` means index entry differences from immediately
before this call. The two meanings are explicit. Native `diff --cached
--name-only -z --no-renames --no-ext-diff --no-textconv` independently checks the
first; native index entries check the second. Added fixed `ls-files` and status
observations are LAB ONLY and are not an expansion of SEC30 worker authority.
No generic runner, raw argv, environment or public fault injection is exposed.

SEC31 whole-candidate preflight is reused to reject helpers/hooks before staging.
Its status observation is a lab convenience; preflight listing is not isolation.
The wrapper is an executable specification, not an installed request endpoint.
On real Git failure, the candidate is quarantined in this wrapper, response
retains before/after manifest, actual changed entries, current snapshot/index,
bounded stderr and exit. A trusted harness creates index.lock after successful
add: real write-tree fails while the newly staged index remains. No rollback is
claimed. A separate real missing-path add failure also has execution phase.
Quarantine is in-memory only; durable recovery is unresolved. Captured manifests
are included for finite fixtures within the envelope; overflow explicitly omits
observations and requires recovery. Allocation happens before the wire check.
Timeout handling marks recovery_required; termination/descendant proof is unrun.

`run.py` creates fresh imported byte bundles and separate native/protected copies
for SHA1/SHA256 detached/unborn. Controls cover add/modify/delete/rename, leading
dash, pathspec-looking, spaces, tabs, newlines, quotes and Unicode literal names;
source bundles/refs/HEAD/files/ignored/untracked and other candidates stay intact.
Fresh-limit controls lower the parser bound after real add and the wire bound
after real mutation; both reject with actual candidate identities. Wrong trusted
format width rejects detached status before add and unborn tree output after add.
A native ignored-path refusal produces >4096 diagnostic bytes and proves truncation.
Restaging an existing path proves all staged paths differ from per-call index delta.
Synthetic parser controls include ordinary/rename/copy/unmerged, opaque non-UTF8
path bytes and limits at/above boundaries. These supplement native ordinary and
rename records; synthetic copy/conflict bytes are not native conflict acceptance.
No extra helper/hook census or new native helper transform claim; original SEC31
positive controls remain pinned in upstream evidence.

Reproduce: `python3 artifacts/sec11/candidate-result-contract/run.py > /tmp/sec33.json`
then `python3 artifacts/sec11/candidate-result-contract/verify.py /tmp/sec33.json`.
Verifier checks finite matrix, native parity, lossless records, partial failure,
quarantine, source/contract/upstream hashes and actual base product blobs. Semantic
negative controls reject evidence corruption/promotion. This is local consistency,
not independent acceptance. Final matrix has 122 cases (4 native parity, 4 restage,
4 partial write-tree failures, 4 add failures, 4 diagnostic bounds, 12 response
failures, 24 helper/hook refusals, 4 preservation, 14 parser positive and 48 parser
negative). Ten semantic evidence corruptions reject. Saved validation contains
exact counts and hashes. Compilation and focused Ruff E9/F63/F7/F82/F401/F811 pass.
Full all-rule Ruff has 118 annotation/complexity/style/import/print findings in
`lint-all.txt`; full lint does not pass and no config was relaxed. Two fresh
122-case runs each pass and are retained as results.json/repeat-results.json.
Their raw index and snapshot hashes naturally differ (native index stat cache
contains fresh fixture timestamps/inodes). Semantic/native parity is checked
within each run; cross-run raw-byte equality is not asserted. Re-verifying the
same saved evidence produces byte-identical validation output.

Remaining Stage A: broader excluded Git/hook/lifecycle compatibility and a reviewed
worker implementation; no complete Stage A claim. Stage B separately requires a
reviewed gate, immutable installed runtime, unchanged SEC11/13 attacks with native
positive controls, executable/FD/ABI/descendant attacks, canaries/sockets and resource
bounds. C needs trusted acquisition and durable one-shot grants; D needs retained
candidate review, crash/cancel recovery and publication CAS; E needs coordinated
routing and independent installed acceptance. No installed probe, UI, CI, coverage,
performance or full product suite ran. Inherited sandbox daemon stall is unresolved.
No push authorization.
