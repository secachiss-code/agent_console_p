# Python analyzer pilot

| Stage | Result |
|---|---|
| Managed host | VSCodium 1.143.17232 started under Xvfb with a private profile on Linux x86-64. The host's extension gallery is disabled. |
| Catalog | Ruff 2026.98.0 and Pylint 2026.8.0, with the pinned Python extension dependency. Installation verifies digests and manifests, runs a probe before publishing, and preserves the active release on failure. |
| UI | Project Index → Analyzers. Install, check, update and remove actions use the typed core methods. Checked GTK light, dark and 700 px layouts under Xvfb. |
| MCP | `analyzer_list` and `analyzer_diagnostics` use the requesting tab's worktree. Reports include requested, staged and observed files, revision, analyzer version and diagnostic positions. |
| Live acceptance | A clean data directory downloaded and activated both VSIX packages. Ruff reported F401 and Pylint reported E0401 through MCP. Changing the file produced a new revision and Ruff F821. An unlisted package was refused. |

Re-run the live test with
`ACONSOLE_ANALYZER_LIVE=1 PYTHONPATH=src .venv-core/bin/python -m pytest tests/core/infrastructure/analyzers/test_live.py`.
The host requires the Electron runtime libraries, Xvfb and Xauth. The Debian package declares those dependencies.
Pylint uses the system Python at `/usr/bin/python3`.

The pilot uses staged target files and default extension settings. Without a source-attributed diagnostic,
the VS Code API provides no positive completion signal, so the report uses `unconfirmed`.
The existing configurable batch `index_lint` remains available.
The extension host uses the AConsole user's OS privileges. The private profile separates analysis data.

The screenshot review found duplicate close controls and unnecessary dialog height. The final dialog
uses the native close control and a shorter layout. The narrow capture keeps the analyzer actions visible.

Verification also covered 32 focused core/package tests, two GTK tests, the opt-in live test and 11
contract checks. The Debian package was built and inspected for the bridge and runtime dependencies.
The broad core run was stopped after nine Rust integration setup errors. Its offline Cargo build needs
a `cc` linker, which was absent in the test environment.

The installed pilot exposes a Python preset. Its install button installs the missing Ruff and Pylint packages.
The independent MCP diagnostics switch persists across restarts and starts off for new installations.
Disabling it retains installed packages. The current user's preset was installed and enabled.
