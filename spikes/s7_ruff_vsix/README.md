# S7: standalone Ruff VSIX activation proof

This spike runs the real Ruff Open VSX package in an isolated VSCodium profile
under Xvfb, opens a Python file with a deliberate `F401`, and captures VS Code
diagnostics from a tiny development probe extension.

The Ruff extension's native language server is explicitly enabled. The bridge
under test is the standard Language Server Protocol (JSON-RPC) over the server
process's stdio, managed by VSCodium's extension host.

## Run

The prepared host used for this proof is expected at
`/tmp/aconsole-analyzer-host-check/1.143.17232/bin/codium`, with extracted Electron
libraries in `/tmp/aconsole-analyzer-host-check/deps/usr/lib/x86_64-linux-gnu`.
Override these locations with `VSCODIUM_BIN` and `ELECTRON_LIB_DIR` if needed.

Run `bash spikes/s7_ruff_vsix/run.sh`. Runtime files, VSIX, isolated user data,
extensions, logs, and the JSON diagnostic result go under
`/tmp/aconsole-ruff-vsix-stage1` by default. Set
`RUFF_VSIX_RUNTIME_DIR` to change that path.

The proof succeeds only when VSCodium's extension host activates the probe and
the Ruff extension publishes an `F401` diagnostic for the fixture.

See [RESULTS.md](RESULTS.md) for the completed stage 1 run.
