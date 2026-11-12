# QA-20: release native application watchdogs

Base: `44fc9f2b` (product unchanged from `9cba48ad`).
QA-20 corrects test-owned GLib sources; it changes no product view, style,
benchmark workload, assertions, reviewed images or global cleanup fixture.

## Change

Five watchdog registrations in GitPane, ReviewPage, ApprovalPanel and the two
whole-window approval helpers now use tests/gui/watchdog.py. Each source belongs
to the application shutdown signal. Normal or failed checks quit the app and
remove that source; actual expiration clears ownership before invoking the
original timeout callback, avoiding a second removal during shutdown. The
shutdown handler disconnects itself. No unrelated sources are enumerated,
cancelled or dispatched. Deadlines are unchanged and still start at the original
call sites. The helper imports GI lazily.

Three real Gio application cases exercise successful completion, an injected
check failure and a real 1 ms timeout. All three verify that the owned source
is gone and an unrelated source survives. They pass locally (0.15 s) and on the
host (0.08 s). The original exception is still reported, and expiration still
calls the original timeout callback.

## Native validation

All runs use Intel ADL-N accelerated GL, GskNglRenderer, GTK 4.14.5 and the live
X11 desktop. The runner retains its established passive measurement plugin;
only the explicitly labelled diagnostic run wraps GLib source callbacks.

| Candidate run | Passed / failed | Scale median | Frames / 4 s | Entry / final RSS MiB |
| --- | ---: | ---: | ---: | ---: |
| Full, additional source wrappers | 467 / 12 | 17.6 ms FAIL | 163 | 580 / 591 |
| Full, no source wrappers | 468 / 11 | 17.2 ms PASS | 169 | 574 / 585 |
| Isolated, no source wrappers | 5 / 0 | 17.1 ms PASS | 168 | 247 / 259 |

Durations: 138.26 s, 145.42 s, 18.37 s. Both standard scale cases execute and pass
all original assertions: 10000 rows, 4 seconds, median <=17.5 ms, at least 30
frames, memory growth <=150 MiB. The benchmark bytes match the base commit.
The diagnostic scale FAIL remains a FAIL; it is not acceptance evidence.

The full source observer records **zero late test callbacks**, versus 40 before
the correction. There are two surviving product badge timers with twelve late
dispatches; those are not cancelled by QA-20. Sources before scale fall from ten
to two. _TASKS still contains 146 entries before scale (maximum 182 in the run).
Zero GTK toplevels are observed at all setup/teardown boundaries in all three
runs. Original raw source and lifecycle events are losslessly compressed.
validation.json records source/evidence hashes and complete failure lists.

The eleven standard full-suite failures exactly match the preceding source
baseline's desktop failures: opening-window approval notification, six narrow
geometry assertions, three DES3 image sizes and dirty-preferences close wait.
The diagnostic run additionally fails scale. QA-20 does not declare the desktop,
QA-4, security integrations or release gates green. Functional private-Xvfb
evidence from QA-19 remains prior evidence, not a rerun of QA-20 bytes.

Ruff, git diff --check and strict basedpyright for the helper/new regression pass.
Basedpyright needs `--venvpath /home/sas/Projects/sp_aconsole_prototype`; the first
invocation without it failed dependency resolution and is not claimed as passing.
A fresh free-threaded interpreter with -Xgil=0 imports this helper without
importing gi or enabling the GIL. No view/layout edit requires new screenshots.

## Follow-up and limits

MEM-2 tracks product badge timers and task/loop ownership. A separate local
Gio-only probe runs run_gtk three times, each spawning one coroutine which
awaits asyncio.sleep(0), then quits its own app. _TASKS grows 1/2/3, all entries
are done and none of their loops is closed. Installed GLibEventLoop.__exit__
unregisters its policy/context without closing the loop; run_gtk creates a new
loop on every call. This establishes repeated-run retention, not unbounded
growth during a single normal application run. Do not globally cancel sources
or suppress callbacks to treat that separate problem.

The performance margin is small, ambient desktop load is uncontrolled and p95
still exceeds one refresh interval. These runs establish the existing test's
assertions, not a universal or stable worst-case frame-time guarantee. They do
not isolate a causal speedup from watchdog cleanup. PERF-7 disposition is
recorded separately after the QA-20 candidate is committed.

## Reproduce

With the host UI interpreter from this worktree, using fresh evidence labels:

```
artifacts/qa20/validate.py
python3 artifacts/qa20/analyze.py
```

The validation script deliberately refuses to overwrite existing labelled XML.
Its nonzero full-run exits represent the failures listed above. The analyzer
asserts absence of late test callbacks and unchanged benchmark bytes, without
reclassifying full-suite failures as passing.
