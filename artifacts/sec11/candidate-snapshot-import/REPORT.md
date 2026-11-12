# SEC-32: agreed-bytes candidate snapshot import

Laboratory only, based on SEC-31 `b94c9da9c58db8b5623f42ee79f279b01b03a276`.
Stage A remains **INCOMPLETE**. SEC-11 and SEC-13 remain **failed**.
No product, installed worker, permissions, original probes or routing changes.

The importer accepts immutable Python bytes and a separately supplied agreed SHA256,
not a live repository path, archive, filesystem tree or caller registry. The JSON
manifest carries every directory/file path, kind and mode, exact file hex bytes,
per-file SHA256, root mode and Git object format. Its outer digest covers all
fields. Duplicate JSON keys/paths, unexpected fields, traversal, metadata aliases,
missing parents, file/directory collisions, links and special entry kinds fail.
No archive extraction or source copying occurs. The fixture-only bytes producer in
run.py reads exclusively fresh disposable repositories; it asserts regular files
have no hardlinks. Physical source link topology cannot be represented by this
format; explicit symlink/hardlink entries are rejected. ACLs, xattrs and timestamps
are outside this format. There is no live capture API or claim of race-safe capture.

The accepted subset is standalone SHA1/SHA256 detached/unborn with loose objects,
ordinary index entries, refs/logs, attributes/ignore files and hook bytes/modes.
Pack files, alternates, external gitdirs, includes, custom hook paths, submodules,
sparse/split index metadata and unknown metadata/config dependencies are refused.
Fixed config parsing does not follow includes; known filter.demo/merge.demo helper
declarations are preserved as inert metadata and subsequently refused by the SEC31
adapter. This does not claim to capture or support their external programs.
The importer never runs hooks/helpers; fixed fsck and index inspection validate
objects/refs/index only after config rejection. Arbitrary executable hooks can be
preserved, then cause specific adapter refusal, including before InspectCandidate.
Nonexecutable hooks roundtrip unchanged in positive controls.

Imports use a private fresh directory under a trusted lab parent, exact exclusive
file creation and chmod, full materialized manifest equality, Git completeness
checks, then rename and registry insertion. Existing destinations, including
symlinks, are refused. Handled failures and injected interruptions remove the
partial directory and never register it; retry starts fresh. This single-process
lab does not establish a hostile-parent race boundary, no-replace atomic rename,
fsync durability, SIGKILL cleanup, read-only mounts or recovery across restart.
Uncaught process death can leave unregistered temporary data. Failures after
candidate execution require discarding it; there is no copy-back/publication API.

Finite tests cover each SHA format/origin, exact pre-operation manifest identity
(including raw index, loose objects, config, attributes, ignore, hooks, HEAD/refs),
then native status bytes, tree OIDs and logical index entries after literal staging.
Files, HEAD and refs remain equal and the source remains unchanged. Native and
candidate index stat caches are not expected to be byte-identical after staging.
Each invalid import records the unchanged bundle/source/protected candidate,
registry and parent contents. Byte/mode/missing-file tampering with the old agreed
digest fails; independently agreed malformed bundles exercise structural and
Git validation. Existing IDs/symlink targets, three interruption points and clean
retry are checked. Imported helpers/hooks must retain exact bytes/modes and fail
both typed requests without candidate execution. Required clean/smudge and custom
merge native success/refusal controls reuse the exact SEC31 runner implementation;
no hook census is added. Their partial failure state is recorded without assuming
rollback.

Reproduce inside the container:

```sh
python3 artifacts/sec11/candidate-snapshot-import/run.py > /tmp/sec32-results.json
python3 artifacts/sec11/candidate-snapshot-import/verify.py /tmp/sec32-results.json
python3 artifacts/sec11/candidate-snapshot-import/verify.py
```

The saved JSON/verifier bind the SEC30 contract, SEC31 sources, current/base product
hashes, this artifact code and report, matrix identities and observed outcomes.
Semantic negative controls detect evidence promotion, removed cases, status/tree
changes, mutation on rejection and false helper/hook success. This agent-editable
ledger is local integrity evidence, not independent acceptance or a trusted allowlist.

Remaining gates: Stage A explicit reconciliation; B immutable installed runtime,
unchanged SEC11/13 probes and positive controls, FD/ABI, descendant cancellation,
canaries, read-only mounts and resource/stream limits; C authenticated one-shot
grants and trustworthy snapshot acquisition; D crash/cancel/journal recovery and
live publication; E coordinated routing and independent installed acceptance.
No full product/UI/CI/install/performance/security gate is claimed here.

Validation: two fresh runs of the final matrix (312 cases each), eight semantic
negative controls, repeated saved-evidence verification, 279 unchanged product
Python hashes against SEC31/current/base Git blobs, unchanged SEC30/31 verifiers,
Python compilation, focused Ruff E9/F63/F7/F82/F401/F811 and staged whitespace.
Strict repository-wide-rule Ruff on these standalone scripts reports 116 findings
(type annotations, complexity/style and import/print conventions); it is not a
full lint PASS. The initial `python3 -m ruff` could not find Ruff in that interpreter;
focused lint used the existing project .venv-core/bin/ruff. No dependencies were
installed, and no lint configuration or acceptance gate was relaxed.
