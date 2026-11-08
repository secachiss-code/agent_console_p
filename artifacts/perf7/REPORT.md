# PERF-7: avoid display-wide tooltip queries during row recycling

Base commit: `68db5c50ceec1735f6c3826db447a6fabbf50cf4`. Candidate is the commit
containing this report. The original scale benchmark and all its assertions
(10,000 rows, four seconds, 17.5 ms median, 150 MiB growth) remain unchanged.

## Change

Git rows answer `query-tooltip` with the current cached string instead of setting
`tooltip-text` for every recycled item. Each binding still updates the accessible
DESCRIPTION; keyboard and pointer queries both receive plain, unescaped text via
Gtk.Tooltip.set_text. Only the row under the pointer triggers an immediate query
when its data changes, avoiding stale content in an already open tooltip.
No renderer, model, selection, date formatting, badge or style change is made.

GTK documents [query-tooltip](https://docs.gtk.org/gtk4/signal.Widget.query-tooltip.html)
as the callback used to populate tooltips. The implementation of
[set_tooltip_text](https://github.com/GNOME/gtk/blob/main/gtk/gtkwidget.c)
triggers a display tooltip query when mapped. This source explains the hypothesis;
measurements below come from the installed native GTK, not from that source version.

## Measurements

The diagnostic label proxy (baseline-label-cost) times the original label calls:
9,327 title.set_tooltip_text calls total 1.028 s, compared with 0.084 s for
setting the title and 0.099 s for setting the date label. The proxy adds overhead
and is used only to locate cost, not as a performance gate.

PERF-5 baseline isolated medians: 73.5 / 75.2 ms; full: 91.6 ms.
Candidate first isolated median: 44.5 ms, 77 frames, RSS growth about 44 MiB.
This remains **FAIL** against 17.5 ms. Process/load/renderer observations are
preserved in JSONL and GL reports; different process load prevents attributing
the entire before/after difference to one operation.

Candidate full: **443 passed, 1 failed** in 167.46 s. Median 65.0 ms,
59 frames, p95 105.5 ms, RSS 2,153 MiB (entry 2,118 MiB). The only failure
remains the unchanged scale test. The new pointer/keyboard/recycling/accessible
property regression passes, as do the existing selection/badge/localization tests.
Ruff and strict basedpyright for the modified view pass.

| Run | Renderer | Load before (1 min) | Median |
| --- | --- | ---: | ---: |
| candidate-isolated-1 | GskGLRenderer | 2.95 | 44.5 ms |
| candidate-full | GskGLRenderer | 3.82 | 65.0 ms |


The runner is adapted from PERF-5; it creates a private Xvfb, clean temporary
XDG directories and a real core per test. Runs are serial. `--labels` enables
the diagnostic proxy; normal candidate runs do not. The additional observer tick
and GC callbacks do not change test data or GC policy. The full command is:

```
distrobox-host-exec /home/sas/Projects/sp_aconsole_prototype/.venv-ui/bin/python artifacts/perf7/runner.txt --mode full --label candidate-full
```

## Surprises & Discoveries

- The original tooltip setter dominates the measured label mutation cost.
- The first optimization improves the observed median but does not meet the gate.
- hostrun snapshots failed to initialize GTK and referenced the main checkout.
- Fake demo snapshots in this worktree were empty and could not validate rows.
- Used tools/git_snapshot.py on a real generated repository in private Xvfb instead:
  real-git/git-light.png, git-dark.png, git-narrow.png were opened and reviewed.
  Rows/badges/selection are visible; narrow inspector stacks below the list;
  text is translated and the dark theme has no light patches. No visual discrepancy
  from the existing Git pane layout was found. docs/design/DESIGN.md is absent
  in this checkout, so strict comparison against that mockup remains unavailable.

## Status

PERF-7 remains open until the unchanged performance acceptance is satisfied.
A hardware-accelerated desktop was not measured. Remaining GTK/layout and software
renderer costs are not established by this change; do not claim smooth scrolling
or a green suite solely from cheaper tooltip updates.

<!-- candidate-isolated-1: GskGLRenderer; load=[2.95263671875, 2.8251953125, 2.89013671875]; rows=10000 -->

<!-- candidate-full: GskGLRenderer; load=[3.81982421875, 2.8466796875, 2.81201171875]; rows=10000 -->
