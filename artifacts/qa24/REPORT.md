# QA-24: close preferences after their opening frame callbacks

Base: `91e7396d6e6f5b11e6727e52476d9825c72d1791` (QA-23).

QA-22 observed confirmed discard reaching force_close without a closed signal.
New native regressions reproduce this boundary deterministically: clean close,
discard and successful save before the first allocated frame each time out
waiting for the original `_active` flag to clear (`early-red-corrected.xml`,
3 failures). They use real GTK widgets, real response buttons and the supported
GTK asyncio loop. No focus, map, sheet or force_close methods are replaced.
The early-response cases deliberately deliver button clicks programmatically;
the unchanged original tests cover ordinary draft interaction and cancel/save
failure/later edits.

The [libadwaita 1.5.0 implementation](https://github.com/GNOME/libadwaita/blob/1.5.0/src/adw-dialog.c)
opens embedded sheets on the second map tick, while force_close sets their open
state false. Closing before opening can therefore be lost. This source analysis
and reproduction support the workaround; private sheet fields and the exact
internal tick order in the previously intermittent full-suite failure were not
instrumented here.

Preferences now queue confirmed close through three widget ticks, covering the
two opening ticks regardless of callback registration order and leaving a
nested alert's response stack. This applies to clean close, explicit discard
and confirmed save. Immediately before closing, clean/save paths recheck the
current draft, retaining any newer edits. Explicit discard retains its explicit
intent. Requests coalesce; unmap and closed remove pending callbacks. Hiding a
parent cancels a pending close; showing it again allows a fresh close request.
There is no wall-clock deadline: an unmapped or non-rendering widget cannot
advance frame ticks. The closed handler still releases the viewmodel subscription
and invalidates late responses using the existing generation logic. No new
worker cancellation policy is introduced.

## Verification

- `native-release.xml`: **31 passed in 12.11 s**. Original preferences tests,
  five early-close variants (clean/discard/save, plus edits during clean/save
  frame waits), reopening each variant, hiding/remapping a parent, all approval
  tests and the badge lifecycle regression. Checks include actual unmap,
  subscription release and confirmed settings values.
- `desktop-selection.xml`: **52 passed / 9 failed in 22.11 s**. Original QA-22
  five modules plus badge and five new early-close variants. This run predates
  the additional hiding-parent test, covered by native-release. All preferences
  tests pass. The failed cases exactly match QA-23: six narrow geometry cases
  and three DES3 image dimensions. QA-4 remains failed.
- `native-green.xml`: 6 passed; intermediate `native-final.xml`: 30 passed.
- `early-red.xml` retains the first test attempt: clean close failed correctly,
  but two other failures came from calling an unavailable AlertDialog.response
  method. Corrected tests click the actual buttons and all three fail at the
  close wait, as recorded separately. `native-unmap.xml` retains a test mistake:
  the hidden parent was never presented again. Re-presenting it fixes that
  harness failure; no product change followed it.
- Full Ruff on changed product/tests passes. Strict basedpyright on
  preferences.py with the existing shared-checkout venv reports zero errors
  and warnings. Diagnostic scripts pass Ruff E/F/I (E501 excluded), compilation
  and `verify.py`. No strict typing claim applies to diagnostic scripts.
- Verifier compares ASTs of every original test/helper with the base, checks
  exact remaining failures, no skips/errors, unchanged other src/tests and
  reviewed PNGs, and records SHA-256 hashes. Production identity and action
  routing are unchanged.
- Read-only SQLite observation: all 53 production AConsole Gala records equal
  the QA-22 final records (`geometry-after.json`). No geometry controls or
  direct desktop database writes were used.

Native reproduction, through the host UI interpreter, from this worktree:

```
python artifacts/qa24/run.py fresh-label tests/gui/front/test_preferences_drafts.py tests/gui/front/test_approval_alerts.py tests/gui/front/test_window_badge_lifecycle.py
python artifacts/qa24/verify.py
```

Labels refuse overwrite. The native interpreter must have the installed GI/GTK
libraries and a desktop display. The driver selects this worktree's src and
propagates pytest's exit status. QA-22's old trace script wraps the removed
_discard_after_prompt method; use its original commit for historical replay.

## Screenshot review / Surprises & Discoveries

Applied gtk-screenshot-review using the prior temporary-Xvfb adaptation and
built-in fake demo core. Read shared-checkout DESIGN.md; opened light, dark and
700px prefs images. Top viewport composition remains unchanged: Russian labels
resolve, controls fit at narrow size, and dark mode has no light patches. The
lower settings sections are below the scroll viewport and were not captured.
No visual change was required. Existing snapshot dimensions exclude 10px of
window shadow (1270x790 / 690x790 for 1280x800 / 700x800 requests); this does not
accept the DES3 size discrepancy or replace reviewed baselines. Xvfb emits EGL
and keyboard warnings, so these images do not establish accelerated rendering.

Next: isolate geometry/pixel checks from Gala while preserving production
identity and action-routing coverage. Original accelerated MEM-2 performance,
remote CI, build/install and independent security gates remain unresolved.
SEC-34 still needs actual independent review and separate Stage B authority;
generic plan continuation does not authorize Stage B.
