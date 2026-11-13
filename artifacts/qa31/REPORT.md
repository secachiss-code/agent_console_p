# QA-31: remaining QA-4 evidence

Base: `035af5801ae9a9d31367f99af3c84804dabbffdb`. Inventory only; no product,
test, workflow, reviewed PNG or prior evidence changed. QA-4 remains failed.

## Acceptance map

| QA-4 criterion | Current implementation / command | Existing measured evidence | Remaining evidence |
| --- | --- | --- | --- |
| CI builds and installs deb | `.github/workflows/check.yml`, Build and install Debian package: UI Python runs `packaging/build_deb.py`; `sudo apt-get install -y ./dist/aconsole_*_amd64.deb`; from `/tmp`, clean-environment `/usr/bin/aconsole --help`, `/usr/bin/aconsoled --help`, `/opt/aconsole/{core,ui}/bin/python` GIL/import assertions | Historical CI #43 on `b9530a82`: build/install step succeeded; raw `../qa4-recheck/ci43-result.log`. QA17 local build exit 0 in 55.81 s; extracted payload 4/4 checks succeeded | Current commit not available via GitHub commit lookup (422); no current remote CI acceptance. QA17 extraction did not run apt/dpkg, maintainer scripts or installed absolute launchers. No current-source build/install evidence |
| Coverage threshold applies | Workflow core and UI collect branch coverage separately with `--cov-fail-under=0`; combine then `coverage report --precision=2`; `pyproject.toml` sets `fail_under=90`, branch=true, source=`src/aconsole` | QA17 full core 2176 passed/1 skipped and UI 507 passed, combined **90.01828457446808%**, actual coverage gate exit 0. Earlier CI #42 failed below 90%, proving the threshold can reject a run | QA17 snapshot differs in **five Python files**, plus one CSS file by Git diff. Its coverage is historical, not current. QA30 has no coverage collection. Need fresh full current-source combined coverage; do not combine old and new product versions |
| One golden set | `tools/check_contracts.py::check_golden`; `tests/contracts/test_contracts_golden.py::test_contracts_golden_bytes`; both read `tests/fixtures/contracts/golden` | Tracked file inventory has exactly that one golden directory. Golden test compares exact struct names and deterministic bytes. QA17 full contracts passed | No separate current remote result; contract code, fixtures and tests unchanged since QA17. No second set found; no new focused run justified solely by this inventory |
| Long temporary paths work | `tests/socket_paths.py` shortens only AF_UNIX transport/runtime paths; `tests/test_socket_paths.py` performs real bind/connect while preserving deep data paths | QA4 recheck `long-before.xml`: 1 failure; `long-after.xml`: 13 passed; `long-core.xml`: 1982 passed/1 skipped. Reproduction uses `/tmp/ac-qa4-long-` plus 96 `x` characters | Historical full long-path pass; helpers/conftest/guards unchanged since QA17. Not a current full-suite result. Next full core can deliberately use a long basetemp to refresh this without a redundant additional run |
| Tests do not touch actual HOME | `tests/conftest.py::pytest_configure` isolates HOME and XDG config/cache/data before collection; fixture isolates XDG state; socket root mode 0700. `test_home_is_private_before_collection_and_in_child_processes` checks import-time path and child inheritance | Same 13-case focused result, full long-path run and QA17 full suite; no helper/conftest change since QA17 | This establishes implemented isolation and tested inheritance, not a complete audit of every absolute-path write. Recheck in next full core, rather than claiming universal non-interference from static reading |

Workflow steps use `${{ !cancelled() }}` (not `always()`); core failure does not
hide UI, combine or package step, while cancellation still skips them. Step
configuration is not evidence that any particular remote step ran successfully.
The workflow intentionally deselects the original scrolling test; its separate
GPU evidence remains QA30 and is not repeated here.

Other defects named in the QA-4 objective are mapped too:

| Original issue | Current guard / existing result | Limit |
| --- | --- | --- |
| Ownership without main / spaced paths | `tools/check_ownership.py`, `tests/test_build_guards.py`; missing main fails, complete NUL-separated paths tested; QA4 focused 13 passed | `tools/check.sh` invokes ownership only with HOSTRUN_CONTAINER. QA17 check lacked that variable; CMT-1 is not accepted by that check |
| Untracked app modules omitted from deb | `packaging/build_deb.py::_copy_app` rejects untracked app/migration files, copies NUL-separated tracked paths; build guard tests cover omissions and spaces | Guard tests do not replace installing the actual package |
| Tools/tests outside linters | `tools/check.sh` runs Ruff on repository root; `pyproject.toml` retains explicit exclusions including hostrun and its tests; QA17 check PASS | Do not describe this as linting every file without exclusions. No fresh full lint/type/layer check in QA31 |
| Breaking comparison against HEAD | `tools/check_contracts.py::_contract_base`; workflow fetch-depth=0 and event base SHA; build guards test committed breaking change and missing explicit baseline | Missing/unavailable baseline returns an error. Existing code and tests unchanged; current remote execution absent |
| Version divergence | `tests/test_package.py::test_version_and_roots_import` compares package/project/packaging version 0.1.1; QA17 package metadata 0.1.1/amd64 | Current-input guard inventory unchanged; installed current-source package not measured |

## Provenance and freshness

`inventory.json` hashes raw JUnit, build/smoke and coverage records and compares
all 280 recorded QA17 snapshot inputs against current files. The changed Python
files are Git operations, UI bootstrap, GitPane, preferences and window. Git
also identifies `src/aconsole/ui/style/style.css`; the QA17 snapshot does not
hash CSS. The builder, workflow, pyproject, lock, contracts, golden fixtures,
conftest, socket helpers and build guards have no Git diff from QA17.

The existing 72,820,138-byte deb has SHA256
`e93e88fb79cedb51fc150343705ece60180766c3da1b06b8ecfaf4bfcb614c46`, exactly
the QA17 package-smoke hash. It is an old payload, not a newly built current
package. No rebuild is necessary to establish that fact.

QA25 private geometry: 31 passed. QA26 full private GUI: 533 passed, 1 failed
(original scale). QA30 desktop: 527 passed, 9 geometry/visual failures; isolated
scale 1 passed. Those results retain their distinct source/environment scopes;
they are not a single all-green current CI or combined coverage run.

`ci-lookup.json` preserves connected GitHub tool responses: current commit
lookup returns 422, combined statuses empty, PR-triggered workflow query empty.
The workflow query only includes its first page and PR-triggered runs; empty
statuses alone are not an exhaustive Actions query. No push/rerun was performed.

## Isolated installation feasibility

Container discovery: Debian 13, uid1000, no Podman/bwrap/proot on PATH and no
Podman socket at the two standard checked paths. `apt-get` and `dpkg-deb`
presence grants neither root authority nor a safe target root. Do not install
the application into this persistent agent container or the working host.

`host-environment.json` records one explicitly approved bounded host probe:
elementary OS 8 / Ubuntu noble, uid1000, Podman 4.9.3 rootless with overlay;
`podman info` and image listing exit 0. Four images exist; none has an
`ubuntu:24.04` name. This tag check does not exclude an equivalent differently
named image. Image/container creation and package installation were not tested.
`unshare --user --map-root-user --mount true` exits 1 at uid_map; a direct
unshare/chroot route therefore is not established. bwrap exists but is untested.
Podman info success does not prove that a new container can start.

A viable next install experiment is a **new disposable rootless Podman
Ubuntu 24.04 container**, with no host HOME, runtime socket, worktree or
Podman socket mounted. Copy a newly built deb through Podman copy; resolve its
dependencies and install **inside that container**, then execute the workflow's
absolute launcher and GIL/import assertions from `/tmp`. Retain image digest,
dependency transaction, maintainer-script output, exit codes, package/source
hashes and container cleanup result. It may require an image pull and package
network access. No existing container should be modified or removed. Extraction
or dpkg `--root` with host-visible absolute maintainer-script paths is not a
substitute. Even successful local isolated installation remains local evidence,
separate from the explicit remote CI acceptance criterion.

## Concrete next action

First refresh full local core and UI **branch coverage on one frozen source**,
sequentially, preserving failures and raw data. Use the workflow's exact test
lists and existing single scrolling deselection. Give core a long basetemp;
use the explicit owned-Xvfb runner for UI, with no desktop/GPU repetition or
new exclusions. Run real combine/report regardless of suite failures, recording
the unchanged 90% threshold and scope. Bound each run and refuse overwriting
prior evidence. Only then select any coverage repair or rebuild/install task
from the actual result; a failed UI case must remain visible even if coverage
passes. This closes the newly identified freshness gap before another packaging
check could be mistaken for acceptance of old source.

Remote CI still requires making an agreed current revision available remotely
and observing its actual workflow. QA31 does not push or publish it. Independent
SEC34/GATE1 review, SEC11/13 failures, Stage B authority and other plan gates
are unchanged.

## Reproduce

`/usr/bin/python3 artifacts/qa31/inventory.py` regenerates inventory only.
`host_probe.py` runs with the host interpreter under sandbox approval and refuses
to overwrite its evidence. It lists availability, probes a disposable empty
namespace, reads Podman info/images and starts no containers. GitHub responses
are a retained lookup, not simulated by the verifier. `verify.py` validates
hashes, counts, source differences and the bounded host probe results without
rerunning tests, namespaces, Podman or network operations.

Final validation: full Ruff for `artifacts/qa31`, evidence verifier and
`git diff --check` pass. The initial full Ruff check found missing annotations
and intentional evidence-script diagnostics; annotations and documented local
noqa reasons were added before the final check. No repository lint settings
were changed. No fresh full application test, coverage or install PASS is claimed.
