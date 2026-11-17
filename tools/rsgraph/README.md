# Rust graph extractor

`aconsole-rsgraph` reads `cargo metadata --offline --format-version 1 --no-deps`
JSON on stdin and writes AConsole graph protocol v1 on stdout. It parses source
with the pinned `syn` dependency and never compiles, imports or executes project
code. Errors go to stderr with a nonzero status; stdout contains no partial graph.

Build and install **in devtools**, before activating the updated hostrun service:

```sh
cargo install --offline --locked --path tools/rsgraph \
  --root /home/sas/.containers/containers/devtools/.local
cargo test --offline --locked --manifest-path tools/rsgraph/Cargo.toml
cargo clippy --offline --locked --manifest-path tools/rsgraph/Cargo.toml -- -D warnings
```

The registry cache must contain the locked dependencies when building the image.
The installed binary is `.local/bin/aconsole-rsgraph` in the devtools home.
Hostrun calls that binary after offline metadata and propagates either failure.
Indexing performs no install or build. Missing binaries fail with exit 127.

Workspace crates use `rs-crate:<relative manifest>` and `depends_on` edges.
Modules use `rs:<relative manifest>/<target>/<relative target source>/<module>`;
including the target source prevents lib/bin/example/test collisions. Module
paths name source files relative to the workspace. `mod` and resolved `use`
relations produce `imports` edges. Root, `crate`, `self`, `super`, grouped,
renamed and glob imports are supported. External unresolved names are omitted.
`#[path]` is supported; module symlinks outside the workspace and recursive
module paths are rejected. Output is sorted and deduplicated.

This is a syntax graph: macros and conditional compilation are not evaluated,
and re-export/alias chains are not compiler name resolution. Public signatures,
module declarations and imports form `symbols`; function and method bodies are
excluded. Function-local imports still participate in the graph fingerprint.
Legacy metadata-only JSON remains readable by the Python adapter.
