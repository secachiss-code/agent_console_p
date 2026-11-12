# PERF-7: graph isolation and observer cleanup

Product base: `89b4b3f24de8879265f09f2daa1ca083ba3cb742`.
Final product source and the original 10,000-row/four-second benchmark equal
this base. No graph prototype is retained. **PERF-7 remains failed.**

## Retained change

The optional phase observer reused `handle` for phase signal IDs and the list's
tick callback ID. Its `finally` block consequently tried to remove a tick
callback using the last phase signal ID. Use separate IDs for these two APIs.
This affects cleanup after measurement; it does not explain away the earlier
failed frame-time measurements. A widget's eventual disposal may also remove
callbacks; no historical full-suite contamination is asserted.

The new stdlib regression covers success and injected exceptions, each with
and without phase recording. It verifies all phase disconnections, the actual
tick removal, GC callback cleanup and final report writing. Against the old
observer, both phase-enabled cases fail; against the fixed observer all four
cases pass. Ruff passes. Strict basedpyright passes for the restored graph view.
The initial local type-check command lacked the UI environment; the corrected
command uses `--venvpath /home/sas/Projects/sp_aconsole_prototype`.

The runner also exposes explicit, scale-only diagnostic row probes. Probe runs
record their mode in JSON and deliberately render incomplete rows. An inherited
probe environment variable is cleared unless the CLI explicitly enables it.
The CLI rejects probes outside isolated scale runs. Probe results are never
product acceptance, including when pytest itself reports success.

## Native observations

All runs used the accelerated Intel ADL-N desktop, `GskNglRenderer` and
`GTK_A11Y=test`, with phase observations enabled. Controls use the working
tree's product bytes matching the base, rather than a separate archive checkout.
Native runs did not overlap; ambient host load was not controlled. These are
single-run observations, not statistical regression estimates.

| Run | Median ms | Frames / 4 s | RSS MiB (entry) | Result |
| --- | ---: | ---: | ---: | --- |
| Initial normal control | 33.5 | 112 | 269 (250) | 1 pass, 1 fail |
| Static title, diagnostic | 33.2 | 124 | 269 (250) | 1 pass, 1 fail |
| Static date, diagnostic | 33.5 | 120 | 269 (250) | 1 pass, 1 fail |
| Invisible graph, diagnostic | 16.9 | 187 | 262 (250) | 2 pass; **not acceptance** |
| Final normal control | 33.4 | 119 | 269 (250) | 1 pass, 1 fail |
| Solid straight lanes as rectangles | 33.5 | 116 | 271 (250) | 4 pass, 1 fail |
| Rectangles plus round-clipped filled nodes | 32.1 | 148 | 262 (250) | 4 pass, 1 fail |
| Local Cairo graph node | 33.6 | 114 | 271 (250) | 4 pass, 1 fail |

The static-text probes keep tooltip/accessibility updates and the rest of the
row binding. The invisible-graph probe keeps layout, binding and the widget,
but sets its opacity to zero. Thus it isolates graph snapshot/rendering work
approximately; it does not identify one GTK function or prove GPU path masks
are the sole cause. It also removes visual content required by the product.

Replacing only straight solid strokes with same-width rectangular fills did
not help. Replacing ordinary filled circular nodes with rounded clips gave an
insufficient improvement in this run. Curves, dashed edges, checkpoint diamonds
and uncommitted rings retained their original path drawing in these prototypes.
The Cairo prototype rasterized all graph shapes locally while retaining the
application's normal renderer. It also failed the threshold and its diagnostic
strict type check found nine generic-context/stub errors. All product edits
were reverted; patches are retained for inspection, not as validated fixes.

## Validation and limits

The original 17.5 ms, 150 MiB growth and minimum-frame assertions are unchanged.
Only the incomplete invisible-graph diagnostic passes the scale assertion.
Normal controls and all complete graph prototypes fail it. The retained change
has no product UI diff. No full UI suite was repeated after the isolated
performance failures, and no full-suite PASS is claimed.

Consulted `gtk-screenshot-review`. No prototype screenshot review is claimed:
none is retained or visually accepted. A future retained drawing change still
needs light/dark/narrow screenshots, shape and theme review, selection and
accessibility checks, and the original performance acceptance.

Raw JSONL, GL information, scale reports and JUnit XML are beside this report.
Some daemon logs report the fixture's intentionally unavailable Podman runner;
the scale workloads nonetheless complete. Evidence hashes, host load and source
bindings are recorded in `row-pipeline-validation.json`.
Raw XML/tool output and patch context retain their original whitespace;
the whitespace check passes for authored code, documentation and the manifest.

## Next work

Investigate graph snapshot invalidation and the native render-node/texture
lifecycle during row recycling. Do not substitute an invisible graph, a renderer
override or weaker assertions for acceptance. The simple primitives and local
Cairo approaches above have already been tried without meeting the requirement.

Reproduce controls with `runner.txt --desktop --scale-only --label <name>
--phases`; add `--row-probe static-title|static-date|no-graph` only for diagnostics.
Use `--mode isolated` without probes for the original scale and row regressions.
Run the cleanup regression with
`python3 -m unittest tests.unit.test_perf7_observer -v`.
