# PERF-7: native pipeline investigation and failed acceptance

Base: `173408c2cf5e4077ea55f9e6169306f43ba7c6d6`. Candidate is the commit
containing this report; source hashes are in native-metrics.json.

**Verdict: FAIL.** The unchanged 17.5 ms assertion still fails with the real Intel
GPU as well as software GL. Per project acceptance decision kb-127, the node
must be marked failed rather than done or indefinitely verifying. The tested
small fixes below do not constitute completion of the frame-time requirement.

## Retained implementation

- Append each graph page with one ListStore.splice notification instead of one
  notification per commit. The paging test preserves the original 450-row and
  unique-key assertions and additionally verifies atomic page publication.
- GraphCell retains its existing snapshot when layout, point kind and dirty state
  are equal. It still updates the current row reference. Changes to geometry,
  color, checkpoint/dirty flags redraw as before. A regression checks this.

No global render-node cache, Gtk.Inscription replacement, tooltip redesign,
renderer override, display reconfiguration or threshold change is retained.

## Hardware and sampling

The host has an Intel ADL-N GPU: desktop glxinfo reports Accelerated: yes,
with GskNglRenderer used by the tested GTK process. XRandR reports a 60 Hz
internal display and a 100 Hz external display; GTK reports 16667 us refresh.
Thus a 30 Hz monitor is not an explanation for the observed 33 ms cadence.
The Xvfb control uses llvmpipe and GskGLRenderer. Environment, RSS, host load,
actual GTK version, phases and raw test output are retained under labelled files.

perf/sysprof/valgrind/gdb were not installed. Approved apt downloads were extracted
only into .aconsole-dev/perf7-gdb; no system packages or kernel settings were
changed. The local GDB version is 15.1. A separate diagnostic run interrupts only
the debugger-owned UI child during the scroll marker, verifies PID start-time
before signalling, and captures main-thread stacks without argument values.
The successful v3 capture has 98 samples; v1/v2 produced no stacks and are
preserved as failed instrumentation attempts, not performance evidence.

Sampling pauses the process, uses a regular 40 ms interval and contains stripped
GTK frames. It is not a latency benchmark or unbiased CPU-time distribution.
Stack-family counts overlap: Gtk appears in 66 samples, GObject signal emission
in 54, PyGI signal marshalling in 20, and poll in 33. These observations locate
remaining native/dispatch work but do not identify one complete causal hot path.

Frame-clock callbacks separately observe end-of-update/layout/paint boundaries.
They include GTK handlers between signals and do not isolate a single C function.
For the software control the observed median intervals were about 10.5 ms before
end-of-update, 3.6 ms to end-of-layout, 17.4 ms to end-of-paint. A desktop prototype
observed about 11.4/2.7/6.1 ms, total about 21.2 ms. Pairwise medians need not sum
to the overall median. The prototype timing is not a result for retained code.

## Experiments rejected

Gtk.Inscription fixed-size text prototypes and a bounded shared Gsk.RenderNode
cache did not produce a meaningful median improvement on this desktop; results
remained about 33.3–33.5 ms. They were removed. Source snapshots of the rejected
variants are included for context, but intermediate variants were not commits.
Their logs must not be described as validation of the final retained source.

A diagnostic run with the normal accessibility backend instead of GTK_A11Y=test
also failed at 33.4 ms. That does not establish test-backend overhead as the cause.
The normal full-suite harness still uses the test backend for accessibility
assertions. No assertion was skipped or relaxed to obtain a PASS.

## Final validation

- Full native GTK suite in private Xvfb: **452 passed, 1 failed**, 171.76 s.
  The only failure is test_scrolling_a_long_history: median 49.9 ms versus 17.5 ms.
- Final accelerated desktop scale file: **1 passed, 1 failed**; median 33.4 ms,
  121 frames/4 s, p95 34.8 ms,RSS 268 MiB(entry 250 MiB). Assertions unchanged.
- Full accelerated suite was not run after the isolated performance assertion
  failed; no accelerated full-suite PASS is claimed.
- Ruff and strict basedpyright for changed views pass. The benchmark file hash
  exactly matches the base commit.

## Surprises & Discoveries / screenshots

The prior assumption that only a software-renderer environment was available
was corrected by the explicit read-only desktop probe. GPU availability and the
remaining failed hardware benchmark are now recorded rather than left unknown.

Applied gtk-screenshot-review using the established private-Xvfb real-core
fallback, because hostrun snapshots target the shared main checkout and previously
failed display initialization. Opened native-final-ui/git-light.png,git-dark.png
and git-narrow.png. Rows, badges, graph strokes and selection remain visible;
no new clipping or light patches were found; the narrow inspector stacks below
the graph. The generic §§2–3 and 8 of the shared checkout's
/home/sas/Projects/sp_aconsole_prototype/docs/design/DESIGN.md were consulted.
They are not a specific mockup for the later Git graph; no exact Git-mockup
conformance or new contrast measurement is claimed. Drawing code and style
values are unchanged by the retained fixes.

## Reproduction and limits

artifacts/perf7/runner.txt supports --desktop for the inherited X11 display and
--phases for diagnostic phase recording. Default runs keep private Xvfb and
GTK_A11Y=test. --a11y-default is only a diagnostic child-process option.
--native-stack requires the separately approved GDB packages extracted by
--mode tools --prepare-gdb. It must not be used for acceptance timing.
Native runs were serial; ambient desktop load was not controlled. These results
are observations on this host, not a statistical regression proof or universal
hardware bound. More focused native profiling or an architectural list change
with preserved keyboard/selection/accessibility semantics is still needed.

The final retained source was also tested separately on the accelerated desktop.
The normal-backend diagnostic and rejected prototypes are labelled explicitly
in native-metrics.json and do not count as final acceptance.
