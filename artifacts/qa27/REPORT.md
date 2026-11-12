# QA-27: read-only Git polling without optional index locks

Base: `4b7a426c8e06e81e742fe430a2062efbbb52ca7e`.

The three read-only `status --porcelain` calls in `test_git_actions.py`
now use Git's global `--no-optional-locks`. In particular, observing abort
completion cannot refresh the index while the core is aborting rebase.
Clean-status, restored-HEAD, stale-commit assertions and deadlines are
preserved. Product code, retries, lock cleanup and reviewed PNGs are unchanged.

QA26's original failure remains in `artifacts/qa26/desktop-full/pytest.log`:
Git explicitly refused abort because the linked worktree index lock existed.
Its actual owner was not recorded. This change removes a known source of
optional lock contention; it does not retrospectively identify that owner or
prove every future lock collision impossible.

## Real Git controls

`python3 artifacts/qa27/controls.py` runs disposable SHA1 and SHA256 linked
worktrees with real Git. Each verifies:

- After changing only a tracked file's stat data, read-only status leaves the
  index byte-identical; ordinary status refreshes its index bytes.
- Rebase reaches a real conflict. The fixture exclusively creates its own
  `index.lock`; read-only status still reports the conflict. Abort refuses
  with an index-lock diagnostic and preserves conflict HEAD/index/file bytes.
- Releasing only that fixture-owned lock permits abort, restoring the original
  worktree HEAD, clean status and contents, with no rebase state left behind.

Raw commands, return codes and diagnostics are in `controls.json`. These
finite controls establish the locking mechanism and refusal/release behavior,
without modifying the product or deleting any live worktree locks.

## Native verification

The targeted original `test_git_actions.py` passes all **8 tests** on the
real desktop in **19.69 seconds**, including abort and stale commit refusal.
The full private Xvfb run passes **533 tests**, with **1 failure** in
**148.39 seconds**: the unchanged scale test records 10,000 rows, 161 frames
in 4 seconds, median **24.4 ms**, p95 30.2 ms, worst 55.9 ms, RSS 742 MiB
versus 723 MiB at measurement start. The median exceeds 17.5 ms; the later
memory assertion is not reached. No tests are skipped. Git actions pass.

The full desktop run passes **525 tests**, with **9 failures**, no skips, in
**153.67 seconds**. All nine failures are the same six B0/B1 geometry checks
and three DES3 image comparisons seen in QA26. They pass on private Xvfb;
no baselines were changed. Abort and stale commit refusal pass in both full
runs. The original desktop scale test also passes all frame and memory
assertions this time. A passing run does not emit its numeric scale report
without an explicit report environment variable, which was not set here;
no new median or RSS number is claimed. This differs from QA26's failed
full-desktop scale and leaves reproducibility and its cause unestablished.
Exact case names are in `validation.json` and the raw JUnit/log.

Reproduction uses the existing host UI virtualenv and extracted QA25 Xvfb:

```
python tools/native_ui_tests.py --mode desktop --timeout 120 --output artifacts/qa27/targeted-desktop tests/gui/front/test_git_actions.py
python tools/native_ui_tests.py --mode geometry --xvfb /tmp/aconsole-qa25-xvfb/host-package/usr/bin/Xvfb --timeout 300 --output artifacts/qa27/full-private tests/gui tests/ui tests/smoke
python tools/native_ui_tests.py --mode desktop --timeout 300 --output artifacts/qa27/full-desktop tests/gui tests/ui tests/smoke
python3 artifacts/qa27/verify.py
```

Run directories refuse overwrite. Each `run.json` records the exact command,
display, renderer override and exit code. Runs are sequential without coverage.
No renderer observer is injected in QA27; QA26's renderer evidence is retained
without claiming a new renderer measurement. Initial sandbox host invocation
returned 127 without creating evidence; native tests used escalated host runs.
Final Ruff formatting of the polling predicate after desktop launch adds only
parentheses; semantics and assertions match the tested expression.

The verifier checks every tracked source/test and QA26 evidence file against
the base, permitting only the exact three status options and comment/wrapping.
It recomputes JUnit outcomes and evidence hashes. Ruff checks the changed test,
artifact scripts pass E/F/I lint and compilation; no strict artifact typing
claim. Existing GTK/GI deprecation warnings remain in raw logs.

QA-4 stays failed pending full performance and required CI/build/install
acceptance. SEC-11/13 stay failed; independent SEC34/GATE1 review and Stage B
authority are unchanged. No UI view/style/layout change requires screenshots.
