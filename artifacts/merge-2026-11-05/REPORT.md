# Integration of the pulled UI/clipboard update

Branch: `ac/codex-038657e1539f15ad`.
Parents: local `0263d4b` and pulled `origin/main` `66caff5`.

The merge preserves local approval overlays, notification activation, shortcut handlers,
terminal search/zoom, hardened Git execution, plan readiness and framer changes. It adds
remote clipboard text/image transfer, connection-state reporting, startup retry, terminal
retry, review state preservation, project context and the remote audit artifacts.

Conflict resolutions:
- Keep the overlay approval card and session checkbox. Port expiry checks to that card;
  the delayed risky Allow callback also checks expiry before enabling the button.
- Keep the complete window action map. Route its paste action through TerminalClipboard.
- Keep search and zoom alongside desktop clipboard menus and focus cancellation.
- Use the remote startup retry lifecycle and retain notification click actions and backend.
- Subscribe to connection changes once when the panel is installed, and unsubscribe at teardown.
- Retain both startup failure tests and adapt the expiry test to the overlay card.

Additional integration repairs:
- Clipboard Git calls use GitRunner; HostTabs catches GitCommandError. A regression test
  covers a non-repository path, ensuring no attachment is created on failure.
- Add terminal/prepareImagePaste to fake-core result samples. The existing complete-method
  coverage test found the omission in the pulled implementation.
- Keep contract goldens exclusively under tests/fixtures/contracts/golden.
- Remove duplicate translation keys and resolve import/static-analysis errors.

Validation completed:
- Ruff: passed for the complete tree.
- Basedpyright: 0 errors, 0 warnings, 0 notes.
- Import-linter: 6 contracts kept, 0 broken.
- Targeted tabs/clipboard/Git/contracts: 79 passed before the added error regression.
- Clipboard/Git/contracts including the added regression: 46 passed.
- Non-GTK client/IPC suites: 21 passed.

The first broad core run found the missing fake-core result after 735 passing tests.
It was repaired and the broad run repeated: 1,258 tests passed before the core
interpreter reached a UI test importing gi (which is installed only in the UI venv).
This run completed the core and contract paths before reaching tests/ui.
Running tests/ui with the UI interpreter then passed 142 tests before test_spike
needed the unavailable GTK shared library. These counts overlap; they are not a sum.
The separate tools/check_contracts.py entrypoint cannot complete in this worktree:
its local .venv-core and ignored agent-doc prerequisites are absent. Contract tests,
static typing, schema/golden checks exercised by those tests and import rules above
were checked separately; a complete hostrun gate is not claimed.

Environment limits:
- hostrun rejects this new worktree with `cwd escapes projects`. Its policy still points
  named checks at the old shared checkout. Running that checkout would not verify this merge.
- GTK GUI collection fails because this container lacks libgtk-4.so.1. New screenshots and
  GUI acceptance remain unverified. Existing remote screenshots are historical evidence.
- The installed plan service still advertises path-blocked tasks as ready. FE-1 claim was
  rejected by APR-1; CORE-3 by SEC-8. A path-overlap audit found blockers for every pending
  ready node. No acceptance state or reservation was overridden.
