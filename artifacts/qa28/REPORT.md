# QA-28: finite desktop full/isolated scale comparison

Base HEAD: `b61c6edd8251600a947e66535de48a06cdf8489f`.
Exactly one full desktop run followed by one isolated desktop scale run.
No repeat-to-pass, product change, assertion/workload change, settings write,
renderer override, baseline update or additional frame callback was introduced.

| Observation | Full desktop | Isolated desktop |
| --- | --- | --- |
| Pytest | 525 passed, 9 failed | 1 passed |
| Duration (pytest summary) | 149.87 s | 13.19 s |
| Original scale | PASS | PASS |
| Rows / measurement | 10,000 / 4 s | 10,000 / 4 s |
| Frames | 182 | 159 |
| Median / limit | 17.0 / 17.5 ms | 17.5 / 17.5 ms |
| p95 / worst | 35.5 / 54.1 ms | 35.5 / 101.3 ms |
| RSS start / final (rounded test report) | 605 / 616 MiB | 198 / 262 MiB |
| Window / pane allocation | 1280 x 800 | 1280 x 800 |
| List / scroller allocation | 620 x 754 | 620 x 766 |
| Mapped / total GTK toplevels | 1 / 1 | 1 / 1 |
| Retained bootstrap tasks | 1 | 1 |
| Sampled active GLib sources | 13 | 9 |

No skips or errors. The nine full-suite failures are exactly QA27's six
B0/B1 desktop geometry and three DES3 baseline failures. Git abort and stale
commit cases pass. The isolated median is rounded to one decimal in the
original report; PASS comes from the original unrounded assertion, not from
inferring acceptance from the displayed value. Both original memory-growth
assertions pass; absolute full-process RSS has no acceptance threshold here.

Both runs use GTK 4.14.5, the actual `GskNglRenderer`, display `:0`, and
Mutter(Gala). Each run separately records `glxinfo -B`: Intel ADL-N,
Mesa 25.2.8, direct rendering and acceleration reported. This establishes the
available GL environment and GTK renderer, not per-frame GPU timing.

## Passive observation and limits

The artifact-only pytest plugin delegates to the original `_on_pane` and
`_settled`. Immediately after the original wait first observes all 10,000
rows, it takes one synchronous scalar snapshot, before the test records its
loading completion and starts scrolling. It performs no event-loop drain,
GC collection, wait, workload adjustment, source removal or frame callback.
The scan and scalar gathering take 67.3 / 68.9 ms, excluding JSON writing.
This observer has a cost and may affect the pre-scroll state; its results do
not establish timing equivalence with unobserved QA26/27 runs.

Sources are sampled through `GLib.MainContext.default().find_source_by_id`
for IDs 1 through 65,536. GLib exposes no public enumeration API; sources
outside that range or on another context are unobserved. No source allocation
or callback is wrapped. Extra sources in the full sample are one
`GFileMonitorSource` and three GTK text-line-display cache callbacks. Their
names do not establish ownership, dispatch during scrolling or a leak.
The bootstrap task snapshot covers `_TASKS`, not all Python/GTK native tasks;
its only entry in each run is the active pane test coroutine.

The same observed window allocation and renderer accompany both current
passes. The list is 12 pixels shorter after the full suite. Full-process RSS
is higher before scale as well as after it; no claim that the extra RSS is
live leaked objects or caused the former failure follows from these numbers.
One snapshot cannot establish allocation stability over all measured frames.
The earlier 31.4 ms failure remains unexplained, and these two near-budget
passes do not establish reproducibility. No causal finding or performance
fix is claimed.

## Reproduction and integrity

Use the existing host UI interpreter, sequentially, from the repository:

```
/home/sas/Projects/sp_aconsole_prototype/.venv-ui/bin/python artifacts/qa28/desktop.py --full
/home/sas/Projects/sp_aconsole_prototype/.venv-ui/bin/python artifacts/qa28/desktop.py
python3 artifacts/qa28/verify.py
```

The host interpreter is invoked via escalated `distrobox-host-exec` from this
container. Initial sandbox invocation returned 127 and produced no run data.
Output directories refuse overwrite. `environment.json` pins the tested HEAD
and relevant inherited environment and stores raw GL/WM observations;
`run.json` records exact pytest command, effective backend/renderer and exit
status. The runner explicitly sets `GDK_BACKEND=x11`, `GTK_A11Y=test`, and
source/plugin `PYTHONPATH`, clears Wayland/Broadway overrides, and supplies
the original `ACONSOLE_SCALE_REPORT`. Log, JUnit, original numeric report and
observation JSON are retained for each process.

The verifier recomputes outcomes, compares source/tests/tools and all QA26/27
evidence to the base, and hashes these artifacts. Scripts pass compilation
and Ruff E/F/I. After execution only import spacing/comment and line wrapping
were adjusted to satisfy lint; no observer/runner semantics changed.
No view/style/layout change requires screenshot review.

QA-4 remains failed: prior private geometry evidence remains separate from
GPU results, and CI/build/install acceptance is outstanding. SEC-11/13,
independent SEC34/GATE1 review and Stage B authority are unchanged.
