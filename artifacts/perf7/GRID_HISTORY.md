# PERF-7: one-column native grid for Git history

Base: `b4755a38d44a2c0158d808868104c7a8d15bd138`.
**Partial optimization retained; performance acceptance remains FAIL.**

The history now uses Gtk.GridView constrained to one column, retaining its
Gio.ListStore, Gtk.SingleSelection, recycled row factory and row contents.
CSS targets the grid's `child` node instead of ListView's `row`, preserving
zero item padding and the existing 36 px minimum height. Graph drawing,
badges, date formatting, tooltips and accessible descriptions are unchanged.
This changes the native container's accessibility semantics from list to grid;
keyboard behavior is tested, but a manual screen-reader session is not claimed.

The hypothesis was to reduce native list allocation/recycling overhead.
These measurements support retaining the alternate container as an incremental
improvement on this host; they do not isolate a particular GTK function or prove
a statistically established speedup. Ambient desktop load was not controlled.
All native workloads ran sequentially on Intel ADL-N with GTK 4.14.5 and
GskNglRenderer, with no renderer override or incomplete-row diagnostic.

| Run | Median ms | Frames / 4 s | p95 ms | RSS MiB (entry) | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| Original ListView, isolated scale | 33.4 | 118 | 36.5 | 269 (250) | FAIL |
| Grid, before CSS selector adjustment | 17.1 | 177 | 34.7 | 267 (250) | PASS |
| Grid, final CSS, isolated | 17.3 | 162 | 35.4 | 274 (250) | PASS |
| Grid, all Git UI tests | 18.8 | 156 | 37.3 | 320 (295) | FAIL |
| Grid, full UI suite | 31.1 | 145 | 37.9 | 672 (661) | FAIL |
| Original ListView, full UI suite | 50.0 | 79 | 67.0 | 684 (670) | FAIL |

Only the final-CSS isolated run validates the retained source in isolation.
The earlier grid run uses the prior CSS and is explicitly a separate variant.
The unchanged 10,000-row/four-second test still enforces median <=17.5 ms,
minimum 30 frames and growth <=150 MiB. On failed runs its growth assertion
occurs after the failed frame assertion and is not an executed PASS. Observed
memory growth and measured frame counts are in the original scale reports.

The isolated pass is close to the threshold. The history-only and full-suite
failures preclude closing PERF-7; no universal latency guarantee is claimed.
The larger suite's high entry RSS and scheduling history are observations,
not proof that retained windows, GC, accessibility or one callback causes it.

## Behavior and visual checks

All Git UI checks other than the scale budget pass (26 passed, 1 failed).
The new native-input regression sends real X11 Down/Up/End/Home events and
checks selection and inspector synchronization. Existing checks cover paging,
refresh selection, stage/commit/merge, facts, tooltips, deferred labels,
accessible tooltip descriptions and graph redraw. No test assertion was relaxed.
The runner's new `--mode history` runs every `test_git*.py` in the selected
checkout, including the new keyboard regression.

Ruff and strict basedpyright pass for the changed view and new regression.
The observer-cleanup stdlib test passes its four success/failure/phase subcases.
The original scale-test bytes equal the base commit. Source and evidence hashes,
renderer, host load and test dispositions are recorded in grid-validation.json.

## Full-suite limits and Surprises & Discoveries

The candidate full run has **467 passed, 6 failed**. The archived base has
**466 passed, 7 failed**. Both fail the scale budget, the opening-window
approval notification case and all three DES3 visual baselines (actual images
are ten pixels smaller than the expected dimensions on this desktop). The
archived base additionally fails two packaging tests because the exported
source directory has no `.git` for their `git ls-files` call. Those extra
failures limit comparison of the full suites; no packaging regression is fixed
or claimed here.

Only the candidate full run fails the dirty-preferences close test, on a GTK
wait. An isolated candidate rerun of that exact test passes (1 passed,
473 deselected), selected with `PYTEST_ADDOPTS=-k
test_closing_dirty_preferences_can_cancel_or_discard`. The failure did not
reproduce in that rerun; its suite-dependent cause is unresolved. Neither full
suite is reported green.

Applied `gtk-screenshot-review` with the established real-core snapshot helper
rather than hostrun's shared-checkout/fake-core path. Read the generic layout,
tokens and accessibility guidance in the shared checkout's
`/home/sas/Projects/sp_aconsole_prototype/docs/design/DESIGN.md` §§2–3 and 8;
it is not an exact mockup of this later Git view.

Opened all six light/dark/narrow images from `grid-reviewed-ui` (desktop) and
`grid-private-reviewed-ui` (private Xvfb). The desktop helper produces actual
1290x810 / 710x810 images because its fixed shadow compensation differs from
the active window manager. The private images are 1270x790 / 700x790. These
actual sizes are recorded, not asserted to be exactly 1280x800 / 700x800.
At the actual 700 px width, the inspector stacks below the graph, its controls
and diff remain visible and no new text clipping was observed. Light and dark
views retain graph lanes, point shapes, selection and status pills; no light
patch appears in dark mode. Row shape drawing and style values are unchanged.
Dates in generated commits and localized relative labels vary with capture
time. No new contrast measurement or exact mockup conformance is claimed.

## Reproduction

Use the host UI interpreter with `artifacts/perf7/runner.txt`:

```
--desktop --scale-only --label allocation-control --phases   # base product
--desktop --label grid-styled --phases                      # retained product
--desktop --mode history --label grid-history
--desktop --mode full --label grid-full
--desktop --mode full --checkout-ref b4755a38d44a2c0158d808868104c7a8d15bd138 --label grid-baseline-full
--desktop --mode screenshots --label grid-reviewed
--mode screenshots --label grid-private-reviewed
```

The control used base source bytes in the working tree. The full control uses
an archived pinned checkout. Both full suites omit the subsequently added
keyboard regression; it is validated by the separate history run.
The fixture's expected unavailable-Podman diagnostics remain in the raw output;
they do not prevent the Git benchmark from producing its scale report.

## Remaining work

PERF-7 remains failed. Investigate suite-dependent native update/layout costs
using an unchanged workload, preserving the actual failed acceptance results.
Do not repeat rejected texture/Picture/static-text/primitive drawing variants
as untested fixes. Security Stage A/B/C/D/E, independent SEC34 review, remote CI
and installed integration gates are unaffected by this partial UI optimization.
