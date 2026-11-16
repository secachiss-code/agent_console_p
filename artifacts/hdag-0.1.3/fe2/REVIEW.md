# FE-2 — plan page

L0 directions are column headings; L1 modules are graph vertices. Cards show the
attempt count and latest verdict; the full verdict is in the selected details
and tooltip. The core reads attempt summaries in one Writer call per plan view,
and records successful micro verdicts while keeping the attempt running.

The heading shows phase and a human Freeze action. Inbox requests appear in a
separate scrollable list under Needs attention; merged verifying nodes also
appear there. Unblock requires a note. Request buttons are disabled while the
RPC is pending and retain the entered note on refusal. Inbox transport failures
remain visible and preserve the loaded requests. All added labels have ru/en
translations. Fake-core freeze now updates its snapshot and publishes PlanUpdated
to subscribers, so the native button exercises the complete RPC path.

Screenshots: plan-light.png and plan-dark.png at 1280x800, plan-narrow.png at
700x800. Captured and inspected with native host GTK using the screenshot-review
skill. The worktree is outside hostrun's accepted project directory, so the
preview uses host-spawn with the project's existing UI environment and actual
PlanGraph widgets. No input injection or package installation was needed.

## Surprises & Discoveries

- Initial verdict shortening cut words without an ellipsis. Replaced it with
  word-aware shortening and recaptured all three screenshots.
- Fake-core freeze returned a result but did not publish the updated plan. The
  native RPC test exposed phase reverting to draft; fake persistence/event
  behavior was corrected and the test now passes.
- docs/design/DESIGN.md is not present in this checkout. Review used the FE-2
  acceptance contract and the existing GTK stylesheet: columns, readable labels,
  status colors, no clipped controls at 700px and no light patches in dark mode.

Validation: 14 native GTK tests, 71 core/UI/contracts regression tests and 10
layout/sidebar tests passed. 48 fake-core/layout/sidebar/contracts tests also pass. Source typecheck, Ruff,
raw SQL guard and six architecture contracts pass; check_contracts passes 11 checks
(two external docs checks skipped because docs/contracts is not linked). Screenshots use
representative contract data; the live user's plan was not frozen or reviewed.
