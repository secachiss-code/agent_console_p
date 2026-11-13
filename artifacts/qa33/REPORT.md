# QA-33: retained fresh core/UI branch datasets

Frozen product HEAD: `591258e9573cb1bb542658eb50fe51efddfa6d5e`.
One bounded sequential pair, with no retries: core **2181 passed, 1 skipped,
1 deselected** in 468.76s; private-Xvfb UI **535 passed, 1 deselected** in
160.27s. Both pytest exits are 0. Real combine and CLI report exits are 0.
Combined branch coverage is **90.10800806607553%** (CLI **90.11%**), with
unchanged branch=true, source=`src/aconsole`, fail_under=90.

QA33 acceptance passes. QA32 remains failed for its original dataset retention
loss. This run was justified by that measured loss, not by retrying a failed
score. The small difference from QA32's 90.0803845% is an observed result;
no causal claim is made. Product code, test assertions, workflow, exclusions,
reviewed images and desktop settings were unchanged. No GPU pair was repeated.
Remote CI, package installation, QA4, security and Stage B acceptance are unchanged.

## Preservation and provenance

`run.py` archives each completed pytest dataset immediately into
`datasets/core.sqlite` and `datasets/ui.sqlite`, outside the `.coverage` prefix,
and records both original and copied SHA256 values plus archive timestamps
before combine. Archiving is attempted regardless of pytest exit. Actual
`coverage combine --keep` then combines the fresh inputs; actual
`coverage report --keep-combined --precision=2` enforces the existing threshold
and retains discovered inputs. Both independent archives remain byte-identical
after these commands. The merged `.coverage` is also retained.

`results.json` records exact commands, HEAD, environment, start/end times, source
hashes and input hashes including the executed runner, workflow, pyproject and
lock. Core ends before UI starts, UI ends before combine starts, and each
archive is timestamped before combine. All Python/CSS files under src/tests/tools
were hashed before and after the run. `verify.py` checks those hashes against
the current files. `launch.txt` records the exact approved host invocation.
No runner formatting or changes were made after launch.

The workflow test lists and sole scrolling deselection were preserved.
Core used a basetemp over 100 characters long. UI used the existing owned
private-display helper and QA32's extracted host-compatible Xvfb binary;
its executable SHA256 is recorded. Xvfb was started with authentication and
TCP disabled, and its owned process group was cleaned up by the helper.
The runner bounds each command at 1200s and cleans up test process groups.
No package was downloaded or installed for QA33.

`coverage_probe.py` loads the merged dataset through the coverage API without
CLI auto-combine, retaining the exact total/configuration and API report.
`verify.py` opens each archive read-only, checks SQLite integrity and branch
metadata, and verifies the merged database's files and contextual branch arcs
are **exactly the union of both independent archives**. This checks the data,
in addition to existence and hashes. `evidence.json` hashes the retained result,
JUnit, raw logs, runner, verifier, archives and combined database. The duplicate
working `.coverage.core/ui` files need not be committed: recorded original
hashes are checked against the committed independent archives.

Recheck without rerunning suites:

```
python3 artifacts/qa33/verify.py
```

## Remaining QA4 action

Current local coverage no longer has a freshness or dataset retention gap.
The next concrete task is to rebuild the Debian package from the current frozen
source and test an actual install in a new disposable rootless Ubuntu 24.04
Podman container. Preserve the source/package hashes, image digest, dependency
transaction, maintainer-script output, absolute launcher/GIL/import checks and
cleanup result. Mount no host HOME, worktree, runtime or Podman socket into the
container; transfer the package with Podman copy. Do not install it on the
working host or existing agent container. Image pull/dependency access may
require explicit sandbox approval. A local install does not establish remote CI
acceptance; publishing a revision is separate from this task.
