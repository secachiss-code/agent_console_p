# SEC-31: executable candidate compatibility specification

Base: `e95dde8b26b8bb65845a335dea6f5819d3d16c7d` (SEC-30).
Laboratory only. Stage A remains **INCOMPLETE**; SEC-11/SEC-13 remain failed.
No installed runtime, product change, production routing, host permission change,
original probe edit or security acceptance is part of this node.

`adapter.py` implements the two typed requests in SEC-30 against fixture-owned
registrations. It rejects unknown operations/fields, stale digests, unsafe paths,
unsupported origin/dependencies, configured helpers and executable hooks before
candidate command execution. Literal paths are passed with `--literal-pathspecs`
and `--`. The only execution templates are status, add and write-tree.

The registry and snapshots are created by trusted, single-process lab fixtures.
They are **not** a snapshot importer or an API for arbitrary repositories.
The adapter conservatively rejects every symlink, include, nonessential config
key, diff/filter/merge attribute and custom hooksPath. This is narrower than a
general Git configuration parser. Identity covers bytes, entry kinds and modes,
including config, index, objects, hooks, attributes and ignore files. It does not
cover ACLs, xattrs, mount topology or a concurrent attacker. A second digest check
does not close the check/use race. Config listing and hooksPath suppression are
defense in depth, **not confinement**.

`run.py` builds fresh standalone SHA1/SHA256 detached/unborn originals and separate
native/candidate copies under TemporaryDirectory. T-positive compares exact status
bytes, staged tree OIDs and index entries; saves actual index hashes and tree
entries; checks files, HEAD and refs remain unchanged, including ignored/untracked
sentinels. Binary index files have filesystem-specific stat fields, so their hashes
are recorded individually rather than required to match across copies.

T-helper rejects required/optional/process/unused/config/attribute helpers.
Independent native controls invoke real required clean/smudge and custom merge
helpers with success and refusal, asserting transformed contents and actual
partial-write state. Native smudge refusal may remove the worktree file; merge
refusal leaves an unresolved index. There is no blanket rollback assertion.
T-hooks exercises every SEC-30 phase, unknown and executable sample/fallback files
against both requests, custom paths, and inert nonexecutable controls. A fresh
native command sequence records all eleven phases and a pre-commit refusal.
The installed pre-merge sample is also run natively with real git-sh-setup and
downstream pre-commit success/refusal controls, separately from adapter rejection.
T-input covers invalid paths/fields, stale/mode/attribute/ignore snapshots,
unsupported gitdirs/alternates/submodules/sparse/split indexes and include capture.
T-deny covers each excluded family, all eight lifecycle identifiers and concrete
root/amend/sign/no-checkout/FF/sequencer/checkpoint/removal/prune/runner variants.
These lifecycle requests are explicit denials, not executions of product callbacks.

Every denied fixture saves equal before/after candidate and original manifest
digests and an empty execution log. Preflight config reads are not counted as
candidate status/stage execution. Success on nonexecutable-hook fixtures is
separately classified. No old RPC result is relabeled as this adapter's acceptance.

Reproduce in the container, without installing dependencies:

```sh
python3 artifacts/sec11/candidate-contract-fixtures/run.py > /tmp/sec31-results.json
python3 artifacts/sec11/candidate-contract-fixtures/verify.py /tmp/sec31-results.json
python3 artifacts/sec11/candidate-contract-fixtures/verify.py
```

`results.json` retains the actual run and tool versions. `validation.json` is local
evidence integrity: exact SEC-30 contract digest, current/base product hashes,
artifact hashes, finite matrix and saved outcomes. Verifier negative controls
reject security promotion, lost cases, helper false success, altered trees,
mutated deny state and arbitrary execution templates. This agent-editable evidence
is neither an independent review nor a trusted runtime allowlist.

The lab uses subprocess capture and a timeout for convenience; it does not enforce
the proposed memory/process/streaming limits or prove descendant cancellation.
Failures after command execution require discarding the candidate; no live state
is ever copied back. Immutable snapshot import, installed containment and original
unchanged SEC-11/13 probes/positive controls (B), authenticated one-shot grants (C),
crash/cancel/publication recovery (D), coordinated routing and independent installed
acceptance (E) remain required. No product suites, UI, CI/install, performance,
host security or stalled daemon experiments run in this node.

Validation scope: two fresh finite runs (482 cases each), repeated saved-evidence
verification, six semantic negative controls, Python syntax, focused Ruff fatal
syntax/import rules and staged whitespace. A direct product-style all-rule Ruff
check of these standalone lab scripts reports annotation/complexity/style findings;
it is not a PASS. No lint configuration or product gate is relaxed here.
The adapter returns raw status bytes/tree/index identities; a product status parser,
changed-path response model, read-only mount and proposed resource limits remain
unimplemented. This executable specification tests the named compatibility subset.
