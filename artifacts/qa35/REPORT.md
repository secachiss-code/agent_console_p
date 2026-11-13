# QA-35: installed AConsole update

User explicitly requested updating the application after building and checking it.
Prerequisite QA34 passed, committed as b80b2a11. The host update installed that exact deb:
SHA256 `3d4d491c40c7c39aac579b3aed8242fbb747ee7a66dd75cc55c02561a63e8944`.

`pkexec /usr/bin/dpkg --install` exited 0. dpkg status is `install ok installed 0.1.1 amd64`.
All **299** application and migration files match QA34 frozen source hashes separately
in installed core and UI. Absolute launchers run from /tmp with env -i; UI help exits 0,
core help expected 2, core free-threaded/UI GIL import checks exit 0.

The existing UI PID2727 and core PID2745 remain running, unchanged before/after update.
The on-disk installation is updated; those existing processes must be restarted to load
all new code. No process was killed or session interrupted. Restart is still required.

results.json records exact argv, exits, times, source/package hashes and installed hashes.
process-state.json retains app PID/argv and hashes of raw process inventories. Full host
process listings stay locally in ignored logs; other transaction/check logs are committed.
Offline verifier validates the evidence without reinstalling. It validates raw process
inventories when present, otherwise uses the retained hashes and selected app process record.

Reproduce only with explicit host update authorization: host UI Python runs update.py,
which first requires QA34 successful evidence and refuses overwriting results.
`/usr/bin/python3 artifacts/qa35/verify.py` checks retained installation evidence.
This local update does not accept remote CI, QA4, security or Stage B gates.
