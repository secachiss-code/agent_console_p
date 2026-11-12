# QA-22: Gala geometry persistence and independent native lifecycle failures

Base: `99b278cd9f4b70729aa409872a848f2835a5034f` (QA-21).
Diagnostic only. QA-4 still fails. Product code, original tests and reviewed
images are unchanged.

## Geometry mechanism

The installed Gala binary contains `gala_window_state_saver_on_map`,
`gala_window_state_saver_save_window_state`, SQLite queries keyed by app ID
and window index, and the filename `windowstate.db`. The live database at
`~/.local/share/io.elementary.gala/windowstate.db` contains
`io.github.aconsole.AConsole.desktop`, index 0, position 0/0, size 1920x1080.
Connections used by probe.py are explicitly read-only.

The official [WindowStateSaver source, pinned to 0fa3daaa](https://github.com/elementary/gala/blob/0fa3daaa43ac8c832952d45acb558266d96bd3db/src/Misc/WindowStateSaver.vala)
selects this record in `on_map` and calls `Meta.Window.move_resize_frame`.
It saves geometry on window unmanagement. This is upstream reference code,
not a claim that the installed package was built from this exact commit.
environment.json pins installed versions, executable and reference-source
hashes; the probes retain symbols from the installed executable.

The following native minimal Adw windows run sequentially on the live X11
desktop. They contain a label, with no AConsole UI or persistence code.
Each size was observed at both 100 ms and 700 ms, without maximization or
fullscreen:

| Control | Requested | Actual | Saved index-0 record after close |
| --- | --- | --- | --- |
| production ID, normal | 700x800 | 1920x1080 | 1920x1080 |
| production ID, nonresizable | 700x800 | 700x800 | **700x800** |
| alternative unrecognized ID | 700x800 | 700x800 | no record |
| production ID, normal, repeated | 700x800 | 700x800 | 700x800 |
| production ID, nonresizable restoration | 1920x1080 | 1920x1080 | **1920x1080** |
| production ID, normal, restoration check | 700x800 | 1920x1080 | 1920x1080 |

Together the record reads, installed binary, minimal controls and reversal
identify Gala's persisted geometry as the mechanism. No X11 packet trace or
live debugger breakpoint was taken; the precise native call path is inferred
from those controls and the upstream implementation.

### Unexpected persistence and restoration

The nonresizable control was intended to bypass the saver condition. Although
it prevented the initial resize, Gala still persisted 700x800 on close. Thus
the experiment **did change saved desktop state indirectly**. It must not be
described as leaving preferences untouched or as a safe test-isolation fix.
Whether tracking occurred before updated resize hints reached Gala was not
traced. Do not assume the reference source's exclusion condition held at the
moment the installed Gala registered the window.

The subsequent restoration control returned the original geometry through its
own window lifecycle, without a direct SQLite write. All 53 original AConsole
records match before/after, including after the full traced suite. The original
1920x1080 result also returns. No other desktop window was resized by these
scripts. Do not rerun geometry controls casually: Gala can save their state.

## Approval notification: an independent opening race

The original two tests, with observational method wrappers, give 1 PASS /
1 FAIL in 1.36 s. The approval test fails even in this small selection.
The trace shows:

- 1.1445 s: approvals synchronize with mapped=true, active=false, size 0x0.
- 1.1455 s: `_post_alert` runs in that state.
- 1.2589 s: the window receives active=true.
- 1.2593 s: the alert is withdrawn.

`get_mapped()` in MainWindow._sync_approvals therefore does not establish that
the opening window has settled. Focus arrives after notification dispatch.
This explains the observed startup notification without depending on the
eventual 1920x1080 geometry. A fix must still support requests arriving while
the application remains unfocused and preserve notification action routing.
No production identity or notification destination was changed here.

## Preferences discard: isolated PASS, suite failure localized

The original preferences test passes in the two-test selection. In the original
five-module selection, with the same observational wrappers, it fails again.
The full selection gives **37 PASS / 11 FAIL in 15.77 s**, the same failures
as QA-21; log and JUnit XML retain each result. Wrapper timing is an
instrumented reproduction, not an unchanged-suite acceptance run.

In the failing trace, cancel and discard responses are both received at
13.7377 / 13.7428 s while preferences report mapped=true but size 0x0.
At 13.8542 s `_discard_after_prompt` runs at 800x800 and calls `force_close`;
no `_closed` callback follows before the test's original 2 s wait expires.
In the isolated PASS, `_closed` occurs synchronously inside the discard
callback and changes `_active` to false.

The installed libadwaita is 1.5.0. Its [dialog implementation](https://github.com/GNOME/libadwaita/blob/1.5.0/src/adw-dialog.c)
opens embedded sheets on the second map tick; force_close sets the sheet's
open state to false. This suggests a close-before-opening lifecycle race.
Private sheet state and tick ordering were not traced, so that specific
mechanism remains a hypothesis. The confirmed boundary is receipt of discard
and execution of force_close without the closed signal. It is separate from
missing input delivery and is not proven to be caused by Gala geometry.

## Validation and next work

`python artifacts/qa22/verify.py` checks geometry controls and full restoration,
XML counts with no skips/errors, the notification-before-focus ordering, and
unchanged tracked src/tests against the base (including reviewed PNGs).
It writes source/test SHA-256 hashes to validation.json. Python compile and
Ruff E/F/I checks pass, with E402 excluded for GI version selection. No full
strict typing or full Ruff acceptance is claimed for these diagnostic scripts.

For a fresh traced reproduction, use the native UI interpreter from this
worktree with PYTHONPATH=src, GDK_BACKEND=x11 and GTK_A11Y=test:

```
python artifacts/qa22/run.py --trace-only fresh-label
```

This selects the two original scenarios. The recorded full-module selection
is chosen by the exact label desktop-full-trace; existing labels refuse
overwrite. run.py now propagates the final pytest exit status; the recorded
initial driver runs printed desktop-trace=1 / desktop-full-trace=1 but returned
zero before that hardening. Raw XML and logs are the outcome evidence.

Next: fix initial notification readiness, reproduce the embedded-dialog
map-tick ordering, then choose native test/snapshot isolation with explicit
production-ID and action-routing coverage. Geometry controls are not a
production fix. QA-4, original accelerated MEM-2 performance, remote CI,
installation and security gates remain unresolved; Stage B is not authorized.
