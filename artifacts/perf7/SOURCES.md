# PERF-7: Python GLib sources and retained UI tasks

Product and test base: `9cba48ade3b43f69c9aab2acde07b7d1229c6b97`.
No product, benchmark, assertion, style or fixture changes in this investigation.

The optional `runner.txt --sources` plugin wraps Python GLib timeout/idle
registration. It preserves callback arguments and results, records scalar owner
labels and late dispatches, and prunes manually removed sources using the default
context. It does not cancel sources, iterate the loop or collect garbage. It also
reads scalar state from bootstrap._TASKS without retaining tasks. Registration via
native GTK, explicit GLib.Source.attach, and non-default contexts is not covered.
Instrumentation overhead makes these diagnostic runs, not performance acceptance.

The full accelerated desktop run has 465 passed / 11 failed in 137.87 seconds.
All failures are the previously recorded desktop functional failures. The scale
case passes its original assertions: 10000 rows, 166 frames/4s, median rounded
17.5 ms, RSS 592 MiB (entry 580). This does not close PERF-7: the previously failing
uninstrumented candidate needs acceptance without this additional observer, and
the complete desktop suite still fails. Ambient desktop load is uncontrolled.
GTK is 4.14.5, renderer GskNglRenderer, Intel ADL-N accelerated GL.

Despite zero GTK toplevels at every setup/teardown snapshot, **40 test watchdogs**
fire during subsequent tests: 16 ApprovalPanel, 11 GitPane, 10 ReviewPage and
three whole-window approval helpers. Four old GitPane watchdogs fire during
the scale test. Each app registers a bound `_expired` callback and discards the
source ID; successful application exit does not remove that timeout. QA-20 owns
the targeted correction and failure/timeout-path checks.

Two MainWindow._keep_git_badges timers also survive teardown and fire twelve
times under later tests, once during scale. Their owners are the connection-loss
recovery and disposed-window tests. Before scale _TASKS contains 146 entries;
at the end it contains 180, a mixture of completed and pending tasks. Loop-closed
is false for many retained entries, so this observation alone does not establish
why their callbacks remain pending. MEM-2 owns investigation and regression tests;
global cancellation is not an accepted fix.

No claim is made that these callbacks explain the remaining frame-time failure.
Their direct runtime and retained-object cost require separate controls.
Original logs/XML, renderer, phase data and losslessly compressed source/lifecycle
events are preserved. sources-analysis.json and sources-lifecycle-analysis.json
contain evidence hashes. A real-GLib smoke probe checked callback arguments,
one-shot removal, late attribution, manual-removal pruning and function restoration.
Ruff passes. Final source annotations and comparison style were cleaned up after
the run; no observer behavior changed. No new native screenshot review applies.

Reproduce with the host UI interpreter from this worktree:

```
artifacts/perf7/runner.txt --desktop --mode full --label sources-full --sources --lifecycle --phases
python3 artifacts/perf7/analyze_sources.py sources-full
python3 artifacts/perf7/analyze_lifecycle.py sources-full
```
