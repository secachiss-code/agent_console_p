# MEM-2: release application loops and disposed-window Git timers

Base: `8e1aa930`. This node owns lifecycle cleanup in UI bootstrap and MainWindow.
It changes no style, layout, benchmark, workload or performance assertion.

## Reproduction and cause

`validate.py` exports the original source/tests from the base commit into a
temporary directory and adds the final three regression cases. All three fail
on that source: successful and failing application runs leave open loops, and
the window still owns its Git timer after unrealize. This is an actual native
Gio/GTK reproduction, not a mock of the GLib event loop.

PyGObject 3.58.1's GLibEventLoop context manager unregisters the loop/context;
its exit does not close the loop. A coroutine can call application.quit() just
before returning, stopping dispatch before spawn_task's discard callback runs.
The global task set then keeps that finished task and its open loop alive.
Pending tasks also lose the opportunity to finish asynchronous cancellation
cleanup. This establishes retention across repeated run_gtk calls; it does not
establish unbounded growth during one continuously running application.

MainWindow unrealize already marks terminal ownership disposed, but did not
remove `_git_badge_timer`. Later tab-model notifications could also start that
timer again because `_tab_list_changed` did not inspect disposal state. The
previous full diagnostic observed two surviving timers with twelve late calls.

## Change and focused validation

`asyncio.Runner(loop_factory=GLibEventLoop)` now owns each run_gtk loop. GLib's
context still surrounds app.run. After it exits, Runner cancels and awaits this
loop's pending tasks, finalizes async generators and its default executor, then
closes the loop. This also runs if app.run raises. It does not enumerate or
cancel tasks belonging to other loops, or remove unrelated GLib sources.

Window disposal removes its recorded Git source and clears its ID. The model
callback refuses new work after disposal; a late timer dispatch returns
SOURCE_REMOVE. Active-window timer behavior remains unchanged.

The final native focused run passes 13 tests. The new bootstrap cases each run
an actual Gio.Application three times, including injected run failures. They
check closed loops, release of completed tasks, asynchronous cancellation
finally blocks for tracked and directly created tasks, and async-generator
finalization. A task on a separate loop and an unrelated GLib source survive.
The window regression forces dispatch of the actual owned timer, observes a
Git watch request, checks that active model notifications retain a single
timer, then verifies source removal and no restart or new request after
unrealize/realize and subsequent model notifications. Repeated disposal leaves
the unrelated source alive.

## Native suite, performance and visual review

All three candidate runs use Intel ADL-N accelerated GL, GskNglRenderer and
GTK 4.14.5 on the live X11 desktop, without a renderer/workload override.

| Candidate run | Passed / failed | Scale median | Frames / 4 s | Entry / final RSS MiB |
| --- | ---: | ---: | ---: | ---: |
| Full, additional source wrappers | 471 / 11 | 17.4 ms PASS | 164 | 564 / 575 |
| Full, no source wrappers | 470 / 12 | 29.8 ms FAIL | 161 | 566 / 577 |
| Isolated, no source wrappers | 4 / 1 | 17.5 ms FAIL | 162 | 247 / 260 |

The isolated median is rounded in the diagnostic string; the unrounded value
is 17.514313499759737 ms and fails the original <=17.5 ms assertion.
All workloads still contain 10000 rows,
scroll for four seconds and keep the original frame and memory assertions.
The standard failures remain failures. This node does not establish a current
performance PASS or a causal performance improvement from resource cleanup.

The full source observer records zero late callbacks, zero remaining sources
and zero `_TASKS` entries both before scale and at the end. Every observed test
setup/teardown boundary has zero tracked sources/tasks. Previously there were
two surviving product sources, twelve late product callbacks and 146 retained
tasks before scale. No global cleanup fixture or source cancellation sweep was
introduced. All three candidate runs also record zero GTK toplevel windows at
every observed setup/teardown boundary.

The diagnostic full run retains exactly the eleven known desktop failures:
opening-window notification, six narrow geometry cases, three DES3 image
dimensions, and dirty-preferences close wait. The standard full run has those
eleven plus scale; the isolated run fails scale only. Full logs and exact lists
are retained in validation.json. The previous QA-20/PERF-7 acceptance concerns
older bytes and does not turn these new performance failures into passes.

A subsequent, explicitly comparative standard isolated run exports original
`8e1aa930` with runner.txt's `--checkout-ref` option. It also fails scale:
4 passed / 1 failed, 157 frames, median 18.363698999564804 ms, RSS 259 MiB
(entry 247), 17.28 s. This shows failure on the original code in the current
environment; it neither identifies the source of timing variation nor proves
that MEM-2 improves performance. The original base SHA is retained in
mem2-base-isolated-checkout.txt. No extra candidate reruns were used to select
a passing measurement.

Strict basedpyright for both modified product files and both new regression
files reports zero errors/warnings. Ruff and git diff --check for source,
tests and authored reports/scripts pass. Raw pytest logs/XML deliberately keep
their original whitespace and fail an unfiltered diff whitespace check.
A preliminary
attempt to type-check the existing B5a test module also reported pre-existing
missing annotations/optional model accesses; the final regressions are separate
files, and that module is unchanged. A container-only GTK attempt could not load
libgtk; final native checks run through distrobox-host-exec. Direct host-spawn
returned 127 and was not used as evidence of successful testing.

Applied gtk-screenshot-review to the main window with the existing fake-core
snapshot CLI in a private, temporary Xvfb. This keeps this worktree's source
and configuration isolated instead of using hostrun's shared-checkout target.
Read the shared checkout's docs/design/DESIGN.md §§2–3 and 8. Opened all three
images in ui/: light, dark and narrow. Requested sizes are 1280x800 / 700x800;
actual captured content is 1270x790 / 690x790 because of the existing shadow
convention. At that narrower width the header, terminal and approval actions
remain visible without text clipping. Russian messages are resolved, status
shapes remain distinguishable and dark mode has no light patches. No visual
change was required. Screenshot rendering warnings concern private Xvfb;
the performance runs above use the separate accelerated desktop.

### Surprises & Discoveries

- Current standard performance checks fail despite the source-wrapped run
  passing. Resource ownership acceptance is separate from performance acceptance;
  no renderer, threshold or workload is changed to obtain a PASS.
- The snapshot CLI captures content ten pixels smaller than its requested
  dimensions, an existing helper convention recorded explicitly above.

## Reproduce

Run with the native UI interpreter from this worktree:

```
artifacts/mem2/validate.py
python3 artifacts/mem2/analyze.py
```

The validator refuses to overwrite the full-run XML labels. Reproduction needs
fresh labels or a separate worktree. Its nonzero full-suite subprocess exits
remain recorded in exit-codes.json. Source/lifecycle events are losslessly
compressed by the analyzer, which also checks unchanged benchmark bytes.
The separate comparative run uses the same native interpreter:

```
artifacts/perf7/runner.txt --desktop --mode isolated --checkout-ref 8e1aa930 --label mem2-base-isolated --lifecycle
```
