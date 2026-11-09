# PERF-7 follow-up: defer unmapped Git row content

Base: c4ac4bd7bb2cf41df89d2d0d0c9c72d0b0a2e27d, already published on main.
Candidate: the commit containing this report. No benchmark body, assertion,
threshold, renderer setting, query or 10,000-row workload has changed.

## Evidence and implementation

A separate profile of the base counted 9,702 row bindings: 8,240 were unmapped,
1,462 mapped. `_show_node` ran 9,702 times and took 0.606 s cumulatively;
format_date accounted for 0.280 s. That includes loading and scrolling, under
cProfile and a small mapping counter, not an uninstrumented frame budget.

Keep graph geometry available at bind for GTK size measurement. Save only the
latest pending row/state for an unmapped recycled widget and populate text,
tooltip description and badges on map. Already mapped widgets still update
immediately. Unbind clears pending references and hover state, so detached
bindings cannot later populate a reused widget. No global cache or collector
policy is added.

The new regression checks latest-binding-wins, graph geometry before mapping,
immediate mapped updates and discarded work after unbind. Existing real-core
Git tests exercise GTK mapping, selection, badges, refresh and pagination.

## Validation

- Candidate isolated: median 36.5 ms, 101 frames / 4 s, RSS408 MiB (entry362).
- Candidate full: 451 passed / 1 failed in185.68 s; median73.9 ms,53frames,
  RSS2148 MiB (entry2111). New regression passes. The only failure is the
  original 17.5 ms median performance assertion.
- Fixed-base full control: median79.0 ms; 449 passed / 2 failed in197.24s.
  Besides the scale assertion, a packaging-copy test calls git ls-files and
  fails because the git archive intentionally has no .git directory. This
  happens after the scale measurement; it is a limitation of the control
  harness, not a candidate regression. It is not counted as a green full suite.
- Ruff and strict basedpyright for the changed view pass.
- Separate candidate profile: `_show_node` 3,537 calls /0.278 s cumulative,
  versus 9,702 /0.606 s in the base profile. `_bind_row` cumulative time
  0.373 s versus0.855 s. About64% fewer content renders with almost the same
  number of bindings (9,732 versus9,702). Widget setup remains bounded (206).
  These are diagnostic profiles across loading and scrolling; they are not a
  whole-application speedup guarantee or acceptance timing.
- Screenshots: native real-core light/dark/narrow images in deferred-map-ui
  were opened and checked. No blank visible rows, stale badges, light patches
  or new clipping were found. The narrow inspector stacks below the graph.

A previous pre-merge full run reported65.0 ms. That is not the baseline of
this follow-up: the explicit fixed-base full control replays the exact c4ac4bd7
archive in a temporary directory, with the same observer and native virtualenv.
It never edits the candidate source. It naturally omits the new regression
introduced after that commit. Full suite runs are serial; ambient host load is
not controlled. The source archive has a different filesystem path and warm
cache state. Before/after wall times are observations, not a statistical latency
distribution or proof that all frame-time changes come from this optimization.

## Reproduction

Native UI Python: /home/example/Projects/sp_aconsole_prototype/.venv-ui/bin/python.
Use artifacts/perf7/runner.txt with --mode full|isolated, --label <unique>,
--profile for separate diagnostic runs, --checkout-ref <commit> for a temporary
fixed-commit archive, and --scale-only to select the unchanged scale file.
No parallel native tests were started during these performance runs.

## Surprises & Discoveries

- Most bound cache widgets had not yet been mapped, so they did unnecessary
  label and badge work before this change.
- The whole-suite failure persists and load-sensitive measurements vary
  considerably; the base control was repeated to avoid claiming a regression
  or success solely from an older pre-merge number.
- Used gtk-screenshot-review with the previously established private-Xvfb real-core
  fallback: hostrun snapshots target the main checkout and lack a display. The skill's docs/design/DESIGN.md is absent here;
  comparison with that mockup remains unavailable.

## Status

PERF-7 remains in progress. Default software GL still fails17.5 ms; no hardware
GPU result is available. Less row work is not equivalent to a green performance
gate. Further evidence about native layout/rendering cost is required before
claiming the frame-time requirement has been met.

## Recorded context

| Run | Renderer | Load before (1 min) | Rows |
| --- | --- | ---: | ---: |
| after-merge-profile | GskGLRenderer | 3.87 | 10000 |
| deferred-map-isolated | GskGLRenderer | 2.97 | 10000 |
| deferred-map-full | GskGLRenderer | 3.99 | 10000 |
| fixed-base-full | GskGLRenderer | 4.05 | 10000 |
| deferred-map-profile | GskGLRenderer | 2.99 | 10000 |

Raw observations, profile summaries, source hashes and function counts are
preserved in deferred-map-metrics.json and the labelled reports. The scale test
SHA-256 matches the base commit exactly.
