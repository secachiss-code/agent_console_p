# AConsole 0.1.3(б)

Package version: `0.1.3+b`. Base: `b932ee21` (0.1.3+a).

## Reproduction and cause

Read-only audit of the live database: plan-900 is frozen; all 23 nodes are pending,
all contract hashes exist, and all 17 modules have pipeline=true. All six direction
profiles contain only direction/environment and omit every micro command.
`PlanBoard.contract` passes freeze/hash checks, then `micro_profile` rejects
`_argv(None)` with a bare NODE_CONTRACT. Old freeze checks only profile truthiness.
See audit.json. The earlier diagnosis of an unfrozen plan was incorrect.

## Changes

One domain validator checks complete argv during planning, freezing, approved
plan changes and execution. Nested and complete flat profiles work. Errors name
the direction and command. Existing invalid frozen plans are diagnosed without
changing their contracts or creating a lease. MCP reports phase, contract hash,
pipeline mode and contract_error. Execution readiness includes profile validity.

No live database writes, installation, daemon restart or plan-900 contract edits
are performed by this release build. Existing invalid profiles still need explicit
correction through planning; this build does not invent verification commands.

## Verification

- Ruff: all changed Python files pass.
- Basedpyright: changed production modules pass, 0 errors/warnings.
- Targeted profile/plan CRUD/package regression: 48 passed.
- Scope guard regression with properly frozen fixtures: 37 passed.
- Recovery/upgrade/fake-core diagnostic rerun: 25 passed.
- Gate environment/runner rerun without inherited PYTHON_GIL=0: 21 passed, 1 skipped.
- Packaged runtime extracted to /tmp imports 0.1.3+b and reproduces the exact
  field-specific rejection for the original bad profile.
- Initial expanded run was stopped after approximately half the collection.
  Its nine failures were all system-Python Podman stubs rejecting inherited
  PYTHON_GIL=0; the gate/environment rerun above resolves them. The free-threaded
  core has GIL disabled by default without that environment override.
- Remaining core/contracts/gate/hostrun run: 1284 collected across three subprocesses.
  Shard 1: 427 passed, one 5 ms event-loop tick failure under concurrent load.
  Shard 2: 428 passed. Shard 3: 426 passed, one outdated readiness fixture failure,
  one skipped. The old persistence fixture now supplies a complete profile and
  freezes before asserting readiness; its full file passes (5 tests).
  The tick test passes alone with its original threshold unchanged.
  See regression-shards.json for the original per-shard statuses and resolutions.
- Final package-to-worktree comparison: all six changed production modules
  match byte-for-byte; Ruff and git diff --check remain clean.

The broad verification was split and interrupted for diagnosis; this report does
not claim a single uninterrupted all-green full-suite invocation.

Package: dist/aconsole_0.1.3+b_amd64.deb (approximately 65 MiB).
SHA-256: effb6473915dedb5793a3d1b7fc32cdeb5667fb652a949686e65f040a21c002e

The initial failure was not a missing freeze or a blocked status. Changing
readiness alone did not repair it; the complete-profile admission/diagnostic
changes are the substantive fix in this version.
