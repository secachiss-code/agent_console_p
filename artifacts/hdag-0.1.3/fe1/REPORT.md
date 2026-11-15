# FE-1 — planning tab identity

The dialog offers «Адаптировать план» and requires an explicit model. Core
preserves mode/model in TabView, StoredTab and SQLite, including restore and fork.
Claude and Codex launch argv carry `--model`. A new planning session receives the
kernel-generated goal, current plan nodes and contract schema as its initial task.
Resuming or forking a native session does not inject the initial task again.

Validation: 61 tab/restore/launcher tests; 33 planning/contract tests; 11 native
GTK dialog/session tests. Basedpyright on changed contract/service/repository/UI
and bootstrap: 0 errors, 0 warnings. Ruff on changed Python files passes.

## Surprises & Discoveries

gtk-screenshot-review applied. hostrun rejected this managed worktree because its
path is outside the configured Projects root. Devtools lacks libpango and its
isolated-X helper found no Wayland socket. No packages were installed. GUI tests
and previews used the existing host GTK via host-spawn with the worktree's src
on PYTHONPATH, a recording CoreClient and the demo snapshot. Tests activate widget
signals; no keyboard or pointer input was injected into the desktop.

Reviewed planning-light.png and planning-dark.png (1280×800), and
planning-narrow.png (700×800). The real modal dialog shows translated Russian
labels, model field and enabled Open button. Fields fit at 700 px; dark theme
has no light patches. docs/design/DESIGN.md is absent, so comparison to that
mockup is not claimed. These previews verify the planning dialog, not a live
Claude/Codex planning session.
