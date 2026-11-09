# PERF-6: palette ranking in the complete UI suite

Baseline: `94a76538f9aaa5a990fe9a7e55ac98ed5821c489`. The original ranker is preserved in
`baseline-rank.txt`; `baseline.json` records its SHA-256. Candidate: the PERF-6
commit containing this report. Native runs use the same host UI virtualenv,
private Xvfb, clean temporary XDG directories and serial pytest execution.

## Confirmed cause

The original ranker passes in isolation (4.132 and 4.319 ms) but fails in the full
suite (145.538 ms). During that call, generation-2 garbage collection takes
141.562 ms, about 97% of wall time. Thread CPU time is 145.428 ms, which also
rules out descheduling as the main explanation for this observed spike.
The collector finds 459 objects and no uncollectable objects. The full process
has a peak RSS of 2,689,688 KiB, compared with about 80,000 KiB in isolation.

The old implementation allocates tracked ranking tuples plus separate sliced
tuple sort keys. Those temporary allocations can trigger collection of the
large, pre-existing process heap. This explains the observed ranking failure;
it does not identify which preceding GUI tests retained that heap, nor prove a
memory leak. GC counts in JSON are sampled at test setup/teardown, rather than
immediately around the function.

## Change

Use private, ordered `msgspec.Struct` ranking records with `gc=False`, sort them
directly, and normalize aliases in one loop without a temporary tuple/generator.
An explicit input ordinal preserves stable order for equal keys and prevents
comparison of opaque `PaletteItem` values. Matching priorities remain unchanged.

Only these transient records are excluded from cyclic GC. They contain scalars
and a frozen, immutable `PaletteItem`, cannot refer back to themselves, and never
escape the call. Reference counting releases them normally; a regression test
checks that item references are released. The process collector and its thresholds
remain unchanged. Future changes to these fields must preserve the acyclic
ownership invariant.

## Validation

The existing `test_rank_2000_under_16ms` body, data and 16 ms assertion are
unchanged. `measure.py` wraps that function and observes wall/thread/process CPU
and `gc.callbacks`; it never forces collection or changes GC policy. JSON is
written after the test. Instrumentation adds small timing/allocation overhead.

- Baseline isolated: 41 passed in each of two runs.
- Baseline full: 438 passed, 2 failed (PERF-5 frame time and PERF-6 rank time).
- Candidate isolated: 44 passed; ranking 3.153 ms, no collection inside the call.
- Candidate full: 442 passed, 1 failed in 173.61 s; ranking 3.163 ms, no
  collection inside the call. The remaining failure is PERF-5
  (`test_scrolling_a_long_history`). Peak RSS is 2,680,688 KiB, still similar
  to baseline: this change does not claim to reduce the retained GUI heap.
- Differential comparison against the saved original: 14,400 combinations,
  identical result object identity and order, including duplicate IDs, Unicode,
  alias precedence, cutoffs, limits and score hints (`differential.json`).
- Ruff and strict basedpyright for the ranker pass; `git diff --check` passes.
- Added regression coverage for equal-key stability, a later exact alias taking
  precedence over an earlier prefix, and item reference release.

| Run | Rank wall time | GC inside rank | Suite result |
| --- | ---: | --- | --- |
| Baseline isolated 1 | 4.132 ms | two generation-0 collections | 41 passed |
| Baseline isolated 2 | 4.319 ms | two generation-0 collections | 41 passed |
| Baseline full | 145.538 ms | generation 2: 141.562 ms; generation 0: 0.106 ms | 438 passed, 2 failed |
| Candidate isolated | 3.153 ms | none | 44 passed |
| Candidate full | 3.163 ms | none | 442 passed, 1 failed |

Baseline and candidate use identical suite paths; candidate adds three regression
cases (440 total becomes 443). The instrumented call's CPU time in the candidate
full run is 3.162 ms. No other native test run was launched concurrently with the
candidate full measurement. This is a before/after reproduction, not a statistical
latency distribution.

## Reproduction

Run on the native host, from the intended checkout, with GTK/VTE dependencies:

```sh
/home/example/Projects/sp_aconsole_prototype/.venv-ui/bin/python artifacts/perf6/runner.txt --mode isolated --label new-isolated
/home/example/Projects/sp_aconsole_prototype/.venv-ui/bin/python artifacts/perf6/runner.txt --mode full --label new-full
```

The runner downloads/extracts Xvfb into a temporary directory and cleans up its
server. It writes `<label>.log`, `<label>.xml` and `<label>.jsonl` here. Baseline
measurements were made before editing production code, on the baseline commit.
The differential script can be run with the UI Python against the current tree:

```sh
/home/example/Projects/sp_aconsole_prototype/.venv-ui/bin/python artifacts/perf6/compare.txt
```

These measurements validate the existing 2,000-item workload on this host; they
are not a bound for arbitrary query sizes, competing system load or GUI latency.
Removing temporary GC pressure from ranking does not remove the cost of later
global collection elsewhere in a long-lived process.
