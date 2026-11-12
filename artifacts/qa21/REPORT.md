# QA-21: application identity controls native desktop geometry

Base: `7d8e5cd37d97f1ac5fe88ce2327ea0c1ee3d3d02` (MEM-2).
This is a diagnostic result, not a product fix or QA-4 acceptance.

## Result

The unchanged five modules covering window layout, keyboard routing, DES3
pixels, approval alerts and preferences drafts give:

| Environment | Passed | Failed | Duration |
| --- | ---: | ---: | ---: |
| Live X11 desktop, Mutter(Gala) | 37 | 11 | 17.17 s |
| Private Xvfb, 1280x800, no WM | 48 | 0 | 15.87 s |

No renderer override is added to either suite. DES3 retains its own existing
Cairo/scale/TZ settings. Private Xvfb emits DRI3 warnings and is not evidence
of accelerated rendering or scale performance. The suite has no skips/errors.
All product Python, test Python and reviewed PNG bytes match the base commit;
`validation.json` records hashes checked by `verify.py`.

The live desktop retains the same eleven failures as MEM-2: six narrow layout
cases, three DES3 image dimensions, the initial approval notification and the
dirty-preferences discard wait. Passing them privately establishes an
environment-dependent reproduction. It does not establish that all eleven
share the geometry cause.

## Identity experiment

A separate script runs the real AConsoleApp/MainWindow and demo fake core. It
requests 700x800 through SnapshotRequest, records the first snapshot callback,
then waits 700 ms before recording and capturing again. Only the application's
ID changes in the middle control. The sequence runs serially on the same live
desktop, with no settings changes:

| Label | Application ID | First callback | After 700 ms |
| --- | --- | --- | --- |
| identity-original-1 | io.github.aconsole.AConsole | 1920x1080 | 1920x1080 |
| identity-control | io.github.aconsole.GeometryProbe | 700x800 | 700x800 |
| identity-original-2 | io.github.aconsole.AConsole | 1920x1080 | 1920x1080 |

GTK's default size also changes to 1920x1080 for the original ID. In all six
observations the window is active, not maximized and not fullscreen.
X11 WM_NORMAL_HINTS has the same minimum 482x482 and frame extents
61/61/55/67. This rules out those minimum-size hints forcing a screen-sized
window, and shows that a fixed extra 700 ms before capture does not fix this
case. The repeated original/control/original comparison demonstrates that
application identity is a causal input in this desktop environment. The
actor/protocol that overwrites geometry is not yet identified; saved Gala
geometry is a hypothesis, not a verified mechanism. Read-only WM schema
inspection is retained in wm-settings.txt and does not identify that actor.

Minimal Adw.ApplicationWindow controls in desktop-geometry.json and
private-geometry.json settle at 700x800 and 690x790 respectively. The latter
reflects the existing no-WM shadow convention. app-desktop.* is a preliminary
alternative-ID control; the three explicitly named identity-* outputs are the
authoritative repeated comparison. The identity experiment delays capture
for diagnosis and is not an unchanged test-suite PASS.

## Reproduction and checks

Use the host UI virtualenv's Python from this worktree:

```
python artifacts/qa21/run.py --desktop --label desktop-recheck
python artifacts/qa21/run.py --label private-recheck
python artifacts/qa21/matrix.py
python artifacts/qa21/verify.py
```

run.py refuses to overwrite suite labels and returns the actual pytest exit
status after cleaning up its owned X server. The original two recorded
runs preceded that return-status/refusal hardening; their pytest outcomes
are preserved in raw logs and XML. matrix.py rewrites its own diagnostic
labels when explicitly rerun. No live desktop preferences are written.

Verifier PASS: XML counts, identity sequence and all source/test/PNG hashes.
Ruff E/F/I checks pass (E402 excluded for GI version selection before imports).
No full strict typing or full Ruff-policy acceptance is claimed for these
standalone observation scripts. No source, layout or test assertion changed.

## Remaining work

Identify the desktop geometry writer/protocol before choosing a fix. Consider
an isolated native test display and a separate deterministic snapshot identity,
but verify their integration and notification routing rather than silently
changing production identity or the reviewed baselines. Isolate the approval
and preferences failures separately; their private PASS does not prove why
they fail on the desktop. The current original accelerated 17.5 ms scale
assertion still fails in MEM-2 evidence and is not rerun or accepted here.
Remote CI/deb installation, independent security review and Stage B gates
remain unresolved and unauthorized by this diagnostic.
