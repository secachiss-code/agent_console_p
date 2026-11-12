# PERF-7: explicit texture reuse and native Picture trial

Base: `589db72b376a99ce8d5b8560f91f7171a6071cdc`.
**Acceptance remains FAIL. No product change is retained.**

The previous invisible-graph diagnostic suggested investigating rendering and
snapshot lifecycle. Two new prototypes test that direction without deliberately
hiding graph content. They attempt to render every original path, color, halo,
checkpoint and uncommitted shape; neither has visual acceptance.

The first retains GraphCell's Python snapshot implementation but renders its
original GSK paths to a texture once per geometry/theme/size/scale/renderer key.
A shared LRU holds at most 128 textures. Subsequent snapshots append the texture.
The second uses the same bounded cache with native Gtk.Picture, updating its
paintable on row changes and mapping. It avoids the Python snapshot override,
but fixes the raster's logical height to 36 px and is an experimental rendering
change, not a validated replacement. Patches preserve both rejected prototypes.

## Measurements

Runs were sequential on the desktop's accelerated Intel ADL-N GPU using GTK
4.14.5 and GskNglRenderer. No renderer override or incomplete-row probe was used.
Ambient desktop load was not controlled; load snapshots and hashes are in
`texture-validation.json`. These are individual observations, not estimates
of a statistically established improvement.

| Run | Median ms | Frames / 4 s | p95 ms | RSS MiB (entry) | Tests |
| --- | ---: | ---: | ---: | ---: | --- |
| Restored product control | 33.5 | 115 | 50.8 | 269 (250) | 1 pass, 1 fail |
| Explicit cached texture | 32.8 | 132 | 49.7 | 270 (250) | 4 pass, 1 fail |
| Native Gtk.Picture | 33.1 | 128 | 42.1 | 271 (250) | 3 pass, 2 fail |

All use the unchanged 10,000-row, four-second workload and fail the original
17.5 ms assertion. The minimum-frame assertion passes. Observed RSS growth
is below 150 MiB, but the assertion occurs after the failed frame assertion
and is not reported as an executed PASS. The Picture trial also fails the
existing redraw regression: it no longer explicitly calls queue_draw on an
unparented widget. The test was not rewritten to accept the experiment.

Phase callbacks suggest a smaller layout-to-paint interval: median 5.643 ms
for control, 2.069 ms for cached texture, 1.077 ms for Picture. However,
update-to-layout medians are respectively 3.915, 3.740 and 5.353 ms;
before-paint-to-update remains around 11–12 ms. These signal intervals include
GTK handlers, are not exclusive function costs, and cannot prove one cause.
The observer's frame records include measurement setup/paging; phase analysis
filters to records with a positive scroll offset. The authoritative frame
acceptance numbers above come from the original test's own scale reports.

Texture reuse and removing Python snapshot dispatch were insufficient in these
trials. This narrows the next investigation toward row allocation, ListView
recycling and layout work alongside update/bind dispatch. It does not establish
that rendering is irrelevant or that a particular native function is responsible.
Do not repeat these two trials as untested remedies or substitute invisible
content for acceptance.

## Validation and reproduction

The graph source was restored byte-for-byte after both failed trials. The
benchmark, product source tree and tests have no final diff from the base.
The cleanup observer regression still passes all four subcases. A full GUI
suite was not repeated after the isolated performance failures.

Consulted gtk-screenshot-review. No prototype is retained and no light/dark/
narrow visual acceptance is claimed. A future retained rendering change still
requires the skill's screenshot review and theme/geometry/accessibility checks.
The prototype caches also need lifecycle and theme-change validation before
any potential adoption; bounded entry count alone does not establish that.

Host interpreter: `/home/sas/Projects/sp_aconsole_prototype/.venv-ui/bin/python`.
Control command: `artifacts/perf7/runner.txt --desktop --scale-only --label
texture-control --phases`. Prototype commands omit `--scale-only` and use labels
`texture-trial` / `picture-trial`, running scale, tooltips and redraw tests.
Raw XML, phase JSONL, GL information and scale reports are beside this document.
The fixture logs its unavailable Podman runner as before; the UI workloads
finish and produce the scale reports. No protected installation, routing,
security gate or external CI state was changed.
