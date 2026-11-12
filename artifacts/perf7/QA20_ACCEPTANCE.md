# PERF-7 disposition after native test isolation

**The existing GPU scale acceptance now passes in isolation and in the full
UI collection.** Candidate: `8df4cd73421e4ecf9bfbe2e4b7d8c50470dcf4dd`.
This disposition supersedes the previous failed acceptance for current source;
all earlier FAIL results remain FAIL and their artifacts are unchanged.

Candidate runs were made immediately before its commit. Every changed helper,
new regression and original benchmark hash recorded in
artifacts/qa20/validation.json was compared with the committed Git blobs and
matches exactly. Product code/styles remain those already reviewed in PERF-7.
QA-19 and QA-20 correct cross-test window and watchdog ownership; they do not
weaken the benchmark or artificially shorten the history, duration or frame count.

## Results and baseline

| Standard native candidate | Full UI | Isolated Git |
| --- | ---: | ---: |
| Scale median | 17.2 ms | 17.1 ms |
| Frames / 4 s | 169 | 168 |
| RSS at entry / finish | 574 / 585 MiB | 247 / 259 MiB |
| p95 / worst | 36.2 / 51.9 ms | 35.2 / 129.7 ms |
| Original scale assertions | PASS | PASS |
| Entire collection | 468 PASS / 11 FAIL | 5 PASS / 0 FAIL |

Both standard runs use GTK 4.14.5, GskNglRenderer and Intel ADL-N accelerated GL,
with no renderer override and no Python source-callback wrappers. The established
passive measurement/lifecycle observers are retained. All original assertions
execute: 10000 rows, four seconds, median <=17.5 ms, minimum 30 frames,
RSS growth <=150 MiB. Host load and renderer are preserved in validation.json
and raw qa20-full / qa20-isolated JSONL and glxinfo files. Load averages at scale
entry are 4.21/3.35/2.59 (full) and 4.40/3.67/2.76 (isolated).

Fixed earlier baseline/candidate evidence is preserved in GRID_HISTORY.md and
grid-validation.json: archived ListView base `b4755a38` has isolated 33.4 ms and
full 50.0 ms, while the retained one-column grid's full run is still 31.1 ms.
LIFECYCLE.md records the later passive full-suite baseline at `11aec835`:
32.8 ms with 61 retained windows. QA-19 then removes accumulated test windows
but its accelerated full candidate still fails at 17.9 ms. That QA-19 GPU run
predates its final lazy-helper/spike refinement, as its report states; it is
not relabelled as exact-byte evidence of the final QA-19 commit.

The final retained grid, page batching, redraw reuse and earlier row-binding
changes keep their previous behavioral and visual evidence. GRID_HISTORY.md
records native keyboard/selection/inspector checks, virtualization/paging,
badges/tooltips and light/dark/narrow screenshot review. All those cases pass
in the current full collection; no new view/style/layout change requires
replacement screenshots. The previously documented grid accessibility role
change remains; no manual screen-reader claim is added.

## Limits preserved

The full suite is **not green**: its eleven failures are the existing desktop
notification, six narrow geometry, three image dimensions and dirty-preferences
cases. QA-4 owns that separate acceptance. The prior private-Xvfb functional
PASS is not a run of current QA-20 bytes, and software-renderer performance
has not been reaccepted here. No CI, package-install, security or release gate
is closed by this report.

An additional diagnostic full run wrapping Python GLib callbacks fails scale
at 17.6 ms (467 passed / 12 failed). That failure is retained and is not used as
acceptance timing. It verifies the watchdog fix: 40 late test callbacks become
zero, while twelve late product badge callbacks remain for MEM-2. It is not
evidence of a causal performance gain. Task/loop retention also remains under
MEM-2.

The standard passing margin is small, p95 still misses one refresh interval,
and ambient host load is uncontrolled. These runs satisfy the specified test
on this hardware; they do not promise a stable worst-case frame budget under
arbitrary load or isolate the contribution of any one optimization. Baseline
archive packaging limitations and all older failed trials stay documented in
their original reports.

Reproduction and complete failures/source hashes are in
artifacts/qa20/REPORT.md, validate.py and validation.json. The unchanged scale
test remains tests/gui/front/test_git_scale_ui.py.
