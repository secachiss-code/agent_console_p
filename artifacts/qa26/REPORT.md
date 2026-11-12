# QA-26: full native UI verification after QA-25

Base: `63cec43f8813972940a42e97a6ddeb65eb811d16`.
This is a verification node. Product code, tests, assertions and reviewed PNGs
are unchanged. The full target is `tests/gui tests/ui tests/smoke`, with no test
selection exclusions and no coverage instrumentation.

## Results

- Private Xvfb: **533 passed, 1 failed**, 534 cases, no skips, 148.13 seconds.
  The sole failure is the original scale frame budget: 10,000 rows, 165 frames
  in 4 seconds, median **23.7 ms**, p95 31.2 ms, worst 49.6 ms. RSS is 743 MiB,
  versus 725 MiB at measurement start. The memory assertion follows the failed
  frame assertion and did not execute. Functional passing cases include the
  original geometry/keyboard and reviewed DES3 image comparisons. This Xvfb run
  has no WM and does not establish accelerated performance acceptance.
- Isolated desktop scale: **1 passed**, 12.99 seconds. The original test checks
  10,000 rows, at least 30 frames, 4 seconds of scrolling, median <=17.5 ms and
  RSS growth <=150 MiB. Observed: 186 frames, median **16.9 ms**, p95 34.8 ms,
  worst 104.1 ms, RSS 262 MiB versus 198 MiB at measurement start. All original
  assertions execute successfully. There is no separate p95/worst assertion.
- Full desktop scale fails: 10,000 rows, 155 frames, median **31.4 ms**, p95
  35.2 ms, worst 44.8 ms; RSS 616 MiB versus 605 MiB at measurement start.
  Its memory assertion is not reached. Full-suite totals and exact failed
  case names are recorded in `validation.json` and the original JUnit/log.

Full desktop totals: **523 passed, 11 failed**, 534 cases, no skips, 162.99
seconds. Nine failures are geometry/image checks: six B0/B1 sidebar/header
checks and three DES3 comparisons (captured content 1270x790 vs 1280x800, or
690x790 vs 700x800). They pass on private Xvfb, consistently with the QA-21/25
environment split; this does not accept changed PNG baselines. The other two
failures are scale and the conflict-rebase abort case.

The rebase exception notes now explicitly capture Git refusing abort because
`repo/.git/worktrees/wt/index.lock` exists; HEAD remains at the conflicted
revision with `UU a.txt`. This is stronger evidence than QA-9, where the lock
collision was only a hypothesis and all diagnostic controls passed. The
original polling predicate itself runs ordinary `git status`, which may take
an optional index lock; product reads already disable those locks. This run
does not identify the process owning the colliding lock. Follow-up should
remove optional write locking from read-only test polling, preserve the same
clean-status/HEAD and stale-commit assertions, and verify abort lifecycle with
native Git rather than deleting locks or retrying product operations blindly.

Both desktop runs use the real `:0`, GTK 4.14.5, **GskNglRenderer**, with no
GSK_RENDERER override. The retained GLX evidence reports Intel ADL-N, direct
rendering and `Accelerated: yes`; the supporting WM window identifies
`Mutter(Gala)`. The suite and isolated run execute sequentially, after private
Xvfb completes. These are observations on the available desktop, not proof of
deterministic scheduling. Window allocation and full-suite retained resources
were not sampled by this small observer; these runs do not establish the cause
of the difference in frame medians.

## Reproduction and evidence

Use the host UI virtualenv interpreter from this worktree:

```
python tools/native_ui_tests.py --mode geometry --xvfb /tmp/aconsole-qa25-xvfb/host-package/usr/bin/Xvfb --timeout 300 --output artifacts/qa26/full-private tests/gui tests/ui tests/smoke
python artifacts/qa26/desktop.py
python artifacts/qa26/desktop.py --full
python artifacts/qa26/verify.py
```

The three run directories refuse overwrite; use a fresh output location for
another verification node. Xvfb is the already extracted host package from
QA-25; no packages are downloaded or installed here. `run.json` captures the
exact pytest command, display, overrides and return code. Raw logs and JUnit
are retained. `renderer.py` observes the actual scale window once before the
original measurement callback; it adds no frame callback and changes no
workload or assertion. The artifact scripts were formatted after the desktop
full run started; their final formatting does not change their behavior.

`verify.py` recomputes JUnit outcomes, checks every tracked source/test file
against the base commit (including approved PNG bytes), and hashes the raw
evidence and artifact scripts. Ruff E/F/I excluding E402/E501 and compilation
are checked for the artifact scripts; full strict typing is not claimed.

The initial sandbox host invocation returned 127 and created no evidence;
the native runs used approved escalated host interpreter commands. Existing
GI/GTK/VTE deprecations remain visible. There are no view/style/layout changes,
so no new screenshot design review is required.

## Remaining work

The private functional result and isolated GPU pass do not close **QA-4**:
full desktop geometry failures and the full-suite frame budget remain, along
with remote CI, build/install acceptance. A follow-up should record actual
scale window dimensions and retained resources in the full GPU suite before
assigning a cause or changing production code. Preserve the original workload
and limits. Notification-daemon D-Bus click acceptance is still not provided.
SEC-11/13 remain failed. Independent SEC-34 review, Stage B authorization and
GATE-1 independent acceptance are unchanged; this report supplies none of them.
