# IDX-1 and frozen-profile repair UI

Updated 2026-11-17: AConsole 0.1.3+d is installed on the host (dpkg verified),
and the installed core/UI import the new graph helper and repair dialog.
Plan-900 profiles were repaired through the installed host IPC client, preserving
all 23 node IDs/statuses and frozen phase. No direct database writes were used.
The earlier container CLI refusal was resolved by using the standard host CLI.

IDX-1 is implemented in commit 990c83fa6d4d3aced21ceb80f6a763d7551c9fff.
Release/UI preparation is commit d55fd4cf. Micro and blast gates passed on the
second attempt. The first micro run exposed two old impact-test expectations;
those compatibility updates were separated into the preparation commit. The
kernel selected dependent tests beyond the four-file node scope. Strict types
now pass for both implementation files AND both scoped test files.

Following the user's instruction to continue, IDX-1 was accepted through the
standard host IPC review action. The node is done. Commit and report are
attached to node-902. The final UI restart is launched separately after the
agent's final reply; its status is recorded under
/home/sas/.local/share/aconsole/updates/0.1.3+d/.

Installed package: dist/aconsole_0.1.3+d_amd64.deb
SHA-256: ee84a8f9a22c8b1fe853be4be0d24e6b09b24a2d706b4a6d1692d9e2831222d1
The package contains the implementation before the gate's formatting-only
normalization; later test typing changes are not application runtime payload.

## Changes

- Index detects Python, Rust, TypeScript and root C# solution/project markers
  together, executes each enabled extractor once, and merges successful graphs.
- Foreign IDs and both edge endpoints receive ts:, rs:, cs: exactly once.
  Python IDs and node paths remain stable. Graph protocol stays version 1.
- An extractor failure preserves other successful languages; when every enabled
  extractor fails the index still raises CODEGRAPH_EXTRACTOR_FAILED.
- Tests cover colliding IDs, language settings, marker scope, partial failures,
  repeated synchronization, and a real SQLite fact edge referencing Python.
- The plan page now offers “Исправить проверки” for a frozen, unstarted plan.
  Its dialog accepts a JSON file or pasted profiles, sends plan/save with
  repair_gate_profiles and unchanged title/goal, and never replaces nodes.
  Core refusal leaves the input visible for correction. Duplicate submissions
  are suppressed while a request is pending.

## Verification

See checks/core.txt and core.xml for the combined core and translation run;
checks/gui.txt for the four passing GUI tests; checks/ruff.txt and types.txt
for lint and production type checks. git diff --check passes.

Hostrun GUI execution could not start because its isolated Xwayland launcher
found no Wayland socket. GTK tests were then run against temporary extracted
Debian GTK/Adwaita/VTE libraries, first using a private Broadway server and then
an authenticated private Xvfb display. No system packages were installed.
The local Xvfb copy uses a temporary xkbcomp path. Installed application sessions
were not involved. The private X run passes all four tests.

## Screenshot review

Reviewed ui/plan-{light,dark,narrow}.png and
ui/repair-{light,dark,narrow}.png with application CSS and Russian translations.
The first capture showed the repair button under floating window controls.
Moved it beside the return-to-terminal action and captured again. The final
700 px layout has no clipping; the dark dialog has no light background patches;
labels resolve to translated text. Screenshots use a disposable UI fixture.

## Live repair and installation evidence

checks/live-before-repair.json and live-after-repair.json record the IPC repair.
checks/pipeline-receipts.json records gate receipts; checks/blast-log.txt is the
completed dependency-test log. The installer completed through host pkexec and
`dpkg-query -W aconsole` returned 0.1.3+d. Installed runtime imports were checked.

The prepared JSON preserves the original direction/environment, including the
placeholder image digest. Pinning the actual image remains necessary before
L0 direction closure, as specified by the plan.
