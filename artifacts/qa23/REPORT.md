# QA-23: defer approval alerts until opening focus or a bounded fallback

Base: `d0ce1d87a5d1c5d6aac0d6ba0719f65093392725` (QA-22).

QA-22 reproduced `_post_alert` on a mapped, inactive, 0x0 window about
113 ms before initial focus. Mapping alone was insufficient readiness.
MainWindow now waits for initial focus or a nominal 500 ms GLib timeout after
map. Focus completes readiness immediately. The timeout evaluates the latest
snapshot, selection and notification policy, so a window that remains inactive
still alerts without another store event. Later focus changes and requests use
the existing episode rules and notification action. Unmap/unrealize cancels
the source; remap starts a new bounded wait. Snapshot mode stays silent.

500 ms is a deliberate opening grace interval, not a WM readiness guarantee.
Initial focus later than that can still produce an alert. Dispatch can also be
later when the main loop is blocked. The observed desktop focus arrived within
the interval; no claim covers every window manager or arbitrarily slow startup.

## Verification

- `native-final.xml`: **22 passed** in 7.03 s, including the unchanged original
  native opening/withdrawal/action-routing test, existing episode/policy tests,
  seven new startup scenarios and the existing Git badge lifecycle regression.
- New scenarios hold the `is_active()` observation false from construction and
  exercise delayed focus, no focus, resolving requests before readiness,
  unmap, destroy and remap on the real GTK loop. Most explicitly drive the real
  GLib source to avoid relying on cold-render wall-clock timing. `deadline`
  leaves its actual timer untouched; remap also waits for the real timeout.
  These are controlled focus observations, not evidence of an externally
  unfocused production app. The unchanged test covers native desktop focus and
  the actual application notification action.
- `desktop-selection.xml`: **47 passed / 9 failed** in 19.97 s. This is the
  original five QA-22 modules plus the badge regression, with appended opening
  tests. All approval tests pass. Six narrow geometry cases and three DES3
  dimensions still fail. Preferences discard passes in this run; the previously
  reproduced intermittent lifecycle failure is **not** considered fixed.
- Ruff on changed product/test files passes. Strict basedpyright on window.py
  with `--venvpath /home/sas/Projects/sp_aconsole_prototype` reports zero errors
  and warnings. `git diff --check` passes.
- `geometry-after.json`: all 53 AConsole Gala records match QA-22's final
  records. No geometry controls or direct database writes were used.

Initial `native-green` evidence is retained: 18 passed / 2 failed because cold
rendering could overrun the test's sleep and destroying the last application
window ended the loop before its test finished. Tests now control source
readiness and hold the application during lifecycle observation. The intermediate
`native-green-2` passed 21 tests; the added natural-deadline case brings the
final set to 22. No product changes were made in response to these harness
failures. The GLib asyncio-policy deprecation warning remains visible.

Run with the native UI interpreter through distrobox-host-exec, from this
worktree, with PYTHONPATH pointing to this worktree's src, GDK_BACKEND=x11 and
GTK_A11Y=test:

```
python -m pytest -o addopts= -q tests/gui/front/test_approval_alerts.py tests/gui/front/test_window_badge_lifecycle.py --junitxml=<fresh-output.xml>
python artifacts/qa23/verify.py
```

## Screenshot review / Surprises & Discoveries

Applied gtk-screenshot-review to the window using a private temporary Xvfb and
the existing fake-core snapshot CLI. `screenshots.txt` adapts the prior MEM-2
recipe to this artifact directory; it downloads/extracts Xvfb without installation.
Read shared-checkout docs/design/DESIGN.md sections 2, 3 and 8 and opened all
three generated images: light, dark and narrow. Composition is unchanged,
Russian text resolves, status shapes are distinct, narrow header and approval
actions fit, and dark mode has no light patches. No visual changes were needed.
The helper captures 1270x790 / 690x790 content for requested 1280x800 / 700x800;
that existing shadow-size convention remains a DES3 failure, not a new accepted
baseline. Temporary-Xvfb rendering warnings do not validate acceleration.

Only window.py and appended regression tests change in src/tests; verifier
checks that all original approval tests are byte-for-byte preserved. Reviewed
baselines and production application ID/routing remain unchanged.

QA-4, performance, CI/install and independent security gates remain open.
SEC-34 still requires actual independent review and separate Stage B authority.
Next work: trace the intermittent preferences dialog map/close race, then
isolate geometry/visual checks from Gala without changing production identity
or silently accepting new reviewed baselines.
