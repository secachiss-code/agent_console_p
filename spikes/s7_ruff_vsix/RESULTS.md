# Stage 1 result

**PASS — standalone Ruff VSIX activation and diagnostic publication verified.**

- Host: Linux `x86_64`; `Xvfb` and `xvfb-run` available.
- Editor: prepared VSCodium `1.143.17232`, invoked through its `bin/codium` CLI wrapper.
- Extension: Open VSX `charliermarsh.ruff` `2026.98.0`, explicitly selected for `linux-x64`; bundled Ruff is `0.17.6`.
- Isolation: user data, extension directory, and workspace under `/tmp/aconsole-ruff-vsix-stage1`; no external VS Code process. Install and GUI launch both succeeded without `--no-sandbox`.
- Bridge: native Ruff language server (`ruff server`) through LSP/JSON-RPC over stdio.
- Diagnostic: VSCodium's diagnostics API returned source `Ruff`, code `F401`, message `` `os` imported but unused ``, at `probe.py:1:8`. The event arrived 305 ms after probe activation.
- Runner exit: `0`; result JSON: `/tmp/aconsole-ruff-vsix-stage1/diagnostics.json`.

The first probe assertion treated `Diagnostic.code` as a string. VSCodium returns
it as an object whose `value` is `F401`; the probe was corrected to handle that
shape, and the final run passed.

One preliminary preflight accidentally invoked the raw Electron executable
instead of the CLI wrapper with isolated paths. It started VSCodium against its
default host profile and was terminated; all installation and proof runs used
the wrapper and isolated `/tmp` profile described above.
