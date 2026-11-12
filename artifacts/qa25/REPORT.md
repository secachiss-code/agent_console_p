# QA-25: explicit native geometry and desktop test scopes

Base: `65cbad4dc438d8de33d91a46f242069571fee80b` (QA-24).

`tools/native_ui_tests.py` provides two reproducible entrypoints documented in
README. Geometry runs the original B0/B1/DES3 modules on an owned, authenticated
Xvfb without a window manager. Desktop runs the original approvals, preferences
and badge lifecycle modules on the real X11 desktop. Production identity,
product source, all existing tests and reviewed PNGs are unchanged.

## Evidence

- `final-geometry/pytest.xml`: **31 passed**, no failures, errors or skips;
  original narrow layout and all three DES3 PNG comparisons included.
- `native-desktop/pytest.xml`: **31 passed**, no failures, errors or skips;
  includes original notification action activation, opening-focus regressions,
  preferences early-close/save/discard/remap and badge disposal.
- `desktop-state.json`: real AConsoleApp reports
  `io.github.aconsole.AConsole`; all **53** production Gala records match before
  and after. SQLite observation uses mode=ro. No desktop setting or geometry
  control is written by this work.
- The original notification test overrides notification send/withdraw and
  activates the real application action programmatically. It checks in-process
  action routing and tab selection; it does **not** exercise a notification
  daemon click over D-Bus. This limitation is unchanged.
- Runner error controls: pytest usage error returns **4**, timeout **124**;
  failed Xvfb never yields a display; existing output refuses overwrite.
  Successful/error/timeout native runs remove their X socket and lock.
  Process cleanup also kills an owned descendant ignoring SIGTERM after its
  parent exited. Tests operate only on newly created process groups.
- Full Ruff and strict basedpyright on the tool pass. Artifact scripts pass
  Ruff E/F/I (E402/E501 excluded). `verify.py` checks counts, outcomes, preserved
  source/test/PNG bytes and records SHA-256 hashes. Artifact scripts are not
  claimed to satisfy full strict typing.

## Implementation and reproduction

The runner requires a caller-provided installed Xvfb or `--xvfb` executable;
there are no automatic downloads. It creates a mode-0600 Xauthority cookie,
disables TCP, waits for Xvfb's readiness FD (not just an existing socket), then
runs pytest in an owned process group. Finally it releases that group and its
X server, including timeouts and Python exceptions. The X server lock decides
races when selecting an unused display; a startup conflict fails explicitly.
Default timeout is 120 seconds. Logs, JUnit and launch metadata go to a fresh
output directory. The tool applies no renderer override.

Run using the host UI virtualenv interpreter from this worktree:

```
python tools/native_ui_tests.py --mode geometry --xvfb /path/to/Xvfb --output artifacts/fresh-geometry
python tools/native_ui_tests.py --mode desktop --output artifacts/fresh-desktop
python artifacts/qa25/check_runner.py
python artifacts/qa25/verify.py
```

`desktop.py` records the desktop suite plus runtime identity/read-only Gala
comparison; its fixed evidence filenames refuse rerun. `final_native.py` takes
an Xvfb path and records the finite native success/error/timeout controls,
also refusing existing output directories. Both use this checkout's source.
The tool is an explicit developer entrypoint; remote CI has not been changed
or accepted by this node.

## Observations and limits

The initial `geometry/xvfb.log` records a failed setup: Debian container Xvfb
could not load libunwind in the container. No pytest ran in that attempt.
After downloading/extracting the host Ubuntu Xvfb package into /tmp, the native
host UI interpreter ran the evidence above. `native-geometry` is the first
31-pass native run; `final-geometry` repeats after typed argument handling was
added. An initial check_runner invocation with plain python lacked pytest;
rerunning with the UI virtualenv produced the retained error/timeout controls.
The first basedpyright invocation found annotation/unused-result errors, fixed
before the final checks; an invocation without shared --venvpath subsequently
reported zero diagnostics but exit 3 for missing local venv. Final --venvpath
check exits 0. Native tests retain the existing GI event-policy deprecation.

No views/styles/layout changed, so no new screenshot design review is needed.
DES3 compares against the existing approved images without accepting replacements.
Xvfb is without WM; its existing shadow convention matches those baselines.
That is not acceptance of the previous desktop image dimensions or a product
geometry fix. Original tests run directly on Gala still depend on saved window
state. Xvfb warnings do not prove accelerated rendering.

QA-4 remains failed: original accelerated MEM-2/performance assertions, full
suite, remote CI, build/install and independent security acceptance remain
outstanding. The 10000 rows / 4 s / 17.5 ms / 150 MiB / min 30 frames requirements
are unchanged. SEC11/13 and Stage B authority/review gates remain unresolved.
