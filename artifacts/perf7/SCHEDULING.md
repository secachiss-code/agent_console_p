# PERF-7: suite scheduling investigation, 2026-11-12

Product base: `1afffa7760c4e6dd7d4ad783a7e7bfd8b31ea6fb`.
No product change or performance acceptance is claimed by this analysis.

`analyze_scheduling.py` derives a summary from the existing raw observer JSONL,
including source hashes, GC overlap and available GTK phase samples. Reproduce:

```
python3 artifacts/perf7/analyze_scheduling.py grid-styled grid-history grid-full resume-full-phases > artifacts/perf7/scheduling-analysis.json
```

The optional positional arguments select other run labels in this directory.
The observer has its own tick callback. Its selected intervals end at a tick
with 10,000 loaded rows and positive scroll; their first interval can straddle
the start of scrolling. These are not the exact samples from the original
benchmark callback. Therefore their medians must not replace acceptance results.

| Archived run | Original benchmark median | Observer wall median | Observer main-thread CPU median | Overlapping GC wall time |
| --- | ---: | ---: | ---: | ---: |
| Grid, isolated | 17.3 ms | 17.91 ms | 12.36 ms | 0.161 ms |
| Grid, Git tests | 18.8 ms | 19.46 ms | 14.33 ms | 0.121 ms |
| Grid, full UI | 31.1 ms | 29.95 ms | 16.44 ms | 0.172 ms |

Each window overlaps just one recorded generation-zero collection, including
its observer callbacks. These recorded Python GC pauses cannot account for
repeated frame-budget overruns in these three runs. This does not measure GTK
object disposal, native allocation or GPU resource reclamation.

The recorded cgroup `nr_throttled` and `throttled_usec` counters stay zero.
`cpu.max` was unavailable (`null`); the process's cgroup path was not resolved
by the earlier observer. This is no proof that host scheduling is unconstrained.

Observer main-thread CPU summed over the scroll window is 2.413, 2.553 and
2.470 seconds respectively. Higher per-frame CPU in the full suite is not
higher total main-thread CPU over the four-second traversal. The number of
frames differs (162/156/145) and each frame skips a different number of rows.
Consequently these data alone cannot attribute the result to slower row binding,
a leaked window or one native GTK function. Off-thread waiting and scheduling
remain possible contributors; subtracting medians would not isolate either.

The old isolated run recorded phases; the old history and full runs did not.
That prevents a direct per-phase suite comparison from those archives. A fresh
full run with `--phases` supplies the missing suite-side measurements.

## Fresh accelerated full-suite observation

The unchanged product on Intel ADL-N / GTK 4.14.5 / `GskNglRenderer` again
fails the original scale assertion: **30.2 ms median, 154 frames / 4 seconds**,
p95 36.8 ms, worst 49.7 ms; RSS 678 MiB (entry 666 MiB). Original workload,
renderer selection and assertions are unchanged. The assertion on memory growth
is after the failed frame assertion, so it is not an executed PASS.

The observer's median main-thread CPU interval is 17.35 ms (2.635 s total).
One recorded GC pause overlaps its window, 0.323 ms. Host load before the check
is 2.29 / 1.58 / 0.85, versus 2.96 / 2.55 / 2.49 in the old isolated run.
Thus the new failure does not require higher load-average numbers than the
old passing isolated run. This does not control CPU frequency or host activity.

| Observer phase interval | Old isolated median | Fresh full median |
| --- | ---: | ---: |
| before-paint -> end of update | 5.67 ms | 6.60 ms |
| end of update -> end of layout | 0.74 ms | 0.76 ms |
| end of layout -> end of paint | 4.99 ms | 6.45 ms |
| end of paint -> after-paint | 0.07 ms | 0.10 ms |
| after-paint -> next before-paint | 7.09 ms | 8.33 ms |

Phase callbacks bracket GTK work, and callback order can include repeated
layout signals. Pairwise medians do not add up to a frame median. The samples
were taken on separate days, with uncontrolled desktop activity. They narrow
the next investigation toward both native update and layout/render work;
they do not identify an individual native function or establish a cause.

No view, style, layout, workload or acceptance assertion was edited, so there
is no new screenshot claim. The existing observer cleanup regression passes
its four success/failure/phase subcases. Ruff passes for the analysis script
with INP001 excluded because this is a standalone artifact, like measure.py.

## Full-suite result and next work

Fresh full run: **468 passed, 6 failed, 416 warnings, 170.70 seconds**.
Failures are the opening-window approval notification, three DES3 image-size
baselines, the scale budget and the dirty-preferences close wait. These match
the six failure cases from the prior candidate full run. The additional passing
case is the already committed native Git keyboard regression, which that older
full run predated. The dirty-preferences failure now has a second full-suite
observation; the earlier isolated PASS does not establish its resolution.

The original scale test and the product view/graph source match `1afffa77` byte
for byte, as do the runner and measurement plugin. The only retained authored
changes are this report, the offline analysis script and evidence. No new
performance optimization is claimed. PERF-7 remains failed.

Next product investigation should sample native update/layout stacks specifically
in the full-suite context and inspect mapped-window/main-context lifecycle at
entry. Window retention is still a hypothesis, not a demonstrated root cause.
A product fix must still pass the original isolated and full-suite workloads
and receive the required visual and behavioral checks. The security review,
installed integration and remote-CI gates remain unchanged.
