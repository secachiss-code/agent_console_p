# PERF-7: retained test windows and diagnostic control, 2026-11-12

Product base: `11aec835a7247c1e7203f779b598aa9a564cc2ce`. Product code,
the original scale test and all assertions are unchanged by this investigation.
PERF-7 remains failed: the passing scale result below requires a diagnostic
intervention that is not yet a retained, validated test fixture.

## Observation and control

`lifecycle.py` inventories GTK toplevels before setup and after fixture teardown.
The default mode stores only scalar observations and does not retain windows,
iterate GLib, run garbage collection, close windows or change callbacks.
The optional `--destroy-windows` control destroys toplevels after each teardown
report. It deliberately changes cross-test resource retention and is diagnostic.
Neither mode changes the scale test's 10,000 rows, four-second traversal,
17.5 ms median, 150 MiB growth limit or minimum 30 frames.

| Full accelerated suite | Passive observation | Diagnostic destruction |
| --- | ---: | ---: |
| GTK windows before scale | 61 | 0 |
| Mapped windows before scale | 52 | 0 |
| Process threads before scale | 125 | 11 |
| File descriptors before scale | 227 | 227 |
| Scale median | 32.8 ms | 16.9 ms |
| Scale frames / 4 s | 124 | 180 |
| Scale p95 / worst | 47.4 / 88.1 ms | 35.2 / 50.6 ms |
| RSS at scale entry / finish | 660 / 685 MiB | 574 / 586 MiB |
| Passed / failed | 468 / 6 | 463 / 11 |
| Suite duration | 209.58 s | 141.13 s |

Both processes use GTK 4.14.5, Intel ADL-N accelerated GL and
`GskNglRenderer`, GTK_A11Y=test, and the same full test collection. Optional
phase recording is enabled in both. They ran serially on the live X11 desktop.
Load averages before scale were 3.16/2.33/1.52 and 4.36/3.36/2.13. Desktop
activity and CPU frequency are uncontrolled; this is not a randomized estimate.

The first run ends with 83 GTK toplevels. The 52 mapped windows before scale
belong to nine application IDs, including 9 GitPaneTest, 16 ApprovalTest,
10 B10ViewsTest and 9 AConsole windows. The test helper in
`tests/gui/front/test_git_pane.py` quits its application without destroying the
pane window and leaves its timeout source registered. Other helpers also
retain windows. The control's teardown snapshots return to zero toplevels
throughout all 474 tests, including failed tests.

This is direct evidence of cross-test window accumulation and a controlled
intervention associated with lower scale latency. It does not attribute the
improvement to one GTK function or show that every retained native resource
has been released: file descriptors are unchanged, and RSS remains substantial.
The diagnostic scale test passes all original assertions, including memory
growth, but the full suite is not green and no product performance acceptance
is claimed. Native C-stack sampling was not performed: the previously extracted
GDB packages are no longer present and no profiler is installed on the host.

## Additional failures and next work

The passive run reproduces the six existing failures: opening-window approval,
three DES3 image sizes, scale budget, and dirty-preferences close wait.
The control removes the scale failure but adds six narrow-window failures in
B0/B1. Their teardown observations show **1920x1080** AConsole windows despite
700x800 snapshot requests. That directly establishes wrong test geometry;
whether the desktop window manager or another lifecycle interaction produces
it still requires investigation. The other five failures remain. Do not replace
reviewed images or weaken assertions to accept these results.

QA-19 tracks permanent test-owned window cleanup, preservation of preexisting
windows, failure-path verification, and full/isolated reruns. A cleanup fixture
must also deal honestly with the newly exposed geometry failures. No UI views,
styles or layout were edited; no new screenshot or security acceptance applies.

## Reproduction

Use the host UI interpreter from the worktree:

```
artifacts/perf7/runner.txt --desktop --mode full --label lifecycle-full --lifecycle --phases
artifacts/perf7/runner.txt --desktop --mode full --label lifecycle-destroy-full --lifecycle --destroy-windows --phases
python3 artifacts/perf7/analyze_lifecycle.py lifecycle-full lifecycle-destroy-full
```

The baseline loaded the observer/runner before the optional destruction branch
was added; the subsequently added branch is disabled in its command. Both runs
used unchanged product and benchmark bytes. The committed lifecycle logs use
lossless gzip; the analyzer accepts either plain or compressed JSONL and records
hashes of the stored evidence. Original renderer output, phase JSONL, scale
reports, pytest logs and JUnit are retained. Ruff, Python compilation and the
existing observer cleanup regression pass. Security integration, independent
review, remote CI and release gates remain unresolved.
