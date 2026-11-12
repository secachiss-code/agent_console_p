# QA-19: release native test windows

Base: `e1a8d559c40a7dddce1fb1e19310275bcecba92e`.
Product code, styles, the scale workload and its assertions are unchanged.
QA-19 fixes test isolation. It does not close PERF-7 or the security/release gates.

## Change and verification

Native frontend fixtures now destroy windows created by the current test after
its fixture finalizers complete. They preserve windows that existed on entry,
including nested owners, and bypass close-request guards only during cleanup.
No garbage collection, GLib iteration, timer cancellation or renderer change
is introduced. Two real GTK cases exercise normal completion and an injected
exception while keeping an outer window alive. The cleanup also runs after
actual failing tests, as the full-suite lifecycle observations demonstrate.

The shared helper checks already loaded GTK modules. It does not import GI,
which would enable the GIL. The tests/ui fixture also handles GTK first being
imported inside a test, covering the spike window found at the end of the first
candidate run. A fresh host core interpreter with `-Xgil=0` preserves its disabled
GIL and has no `gi` module after entering/exiting this helper.

| Native validation | Passed | Failed | Scale median | Residual windows before scale / at end |
| --- | ---: | ---: | ---: | ---: |
| GPU full candidate | 464 | 12 | 17.9 ms | 0 / 1 |
| GPU isolated Git tests | 5 | 0 | 17.0 ms | 0 / 0 |
| Private Xvfb full, final cleanup | 475 | 1 | 23.8 ms | 0 / 0 |

The first candidate's final window belongs to `tests/ui/test_spike.py`, outside
the initial frontend fixture's scope. Adding the same lazy cleanup to tests/ui
eliminates it in the final private full run. That run observes zero toplevels
at every setup/teardown snapshot, including failed-test teardown. The two GPU
runs precede this helper refinement; GTK was already loaded for their frontend
tests, but they are not claimed as runs of the exact final helper bytes.

GPU runs use Intel ADL-N and GskNglRenderer on GTK 4.14.5. Private Xvfb uses
unaccelerated llvmpipe; it is a functional control, not hardware performance
acceptance. GPU full/isolated durations are 137.31/16.89 seconds; the private
full run takes 133.20 seconds. Raw environment, renderer, load, RSS, phase data,
JUnit and original scale reports are preserved in artifacts/perf7/qa19-*.
The aggregate evidence hashes and complete failure lists are in analysis.json.

## Remaining failures

The private full run passes all 475 non-scale cases, including native keyboard,
selection, narrow layout, reviewed DES3 images, preferences and notifications.
Its sole failure is the unchanged 17.5 ms scale assertion. The GPU full median
17.9 ms also exceeds that threshold, so PERF-7 remains failed. Its memory-growth
assertion is after the failed frame assertion and is not claimed as executed.
The isolated GPU test passes all original assertions, including memory growth.

The live-desktop full run has the six previously recorded failures plus six
narrow-window geometry failures exposed by window isolation. The geometry probe
records a requested 700x800 window configured at 1920x1080 on Pantheon X11;
it is neither maximized nor fullscreen and has only focused/resizable GDK flags.
This does not prove desktop tiling. The mechanism producing that configure size
is unresolved. These failures disappear on private Xvfb, without changing
buttons, resizability, breakpoints, images or assertions. Further desktop-size
negotiation work belongs to QA-4; this report does not declare a desktop PASS.

No view/style/layout changes were made and no new screenshot review is claimed.
Ruff, compilation, the real GTK cleanup cases and the free-threaded guard pass.
The earlier passive/diagnostic causal comparison is in
artifacts/perf7/LIFECYCLE.md. Independent security review, installed namespace
integration, remote CI, packaging installation and release acceptance remain open.

## Reproduce

With the host UI interpreter, from this worktree:

```
artifacts/perf7/runner.txt --desktop --mode full --label qa19-full --lifecycle --phases
artifacts/perf7/runner.txt --desktop --mode isolated --label qa19-isolated --lifecycle
artifacts/perf7/runner.txt --mode full --label qa19-private --lifecycle
-m pytest -o addopts= -q tests/gui/front/test_window_cleanup.py
artifacts/qa19/geometry_probe.py
```

The final two commands need GDK_BACKEND=x11, GTK_A11Y=test, PYTHONPATH=src.
The runner's private mode downloads Xvfb into a temporary directory, extracts it
locally, and terminates the owned server afterward. It installs no system package.
Stored lifecycle JSONL is losslessly compressed. Rebuild the summary with:

```
python3 artifacts/perf7/analyze_lifecycle.py qa19-full qa19-isolated qa19-private
```
