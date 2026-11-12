# SEC-13 — private config namespace does not survive pathname replacement

Base: `6dc7d72df2ebd5ec410633243d355e24235260df`.
**Verdict: FAIL. SEC-11 remains failed. No product code was changed.**

This standalone experiment tests an alternative to the earlier blanket exec
denial, which prevented approved hooks. The private-configuration approach
passes the original race and preserves hooks, but fails a later deterministic
race. It must not be promoted to a product launcher in its current form.

## Mechanism and finding

Each experimental Git spawn enters a new user/mount namespace with private mount
propagation. The launcher reads the effective config, flattens includes, removes
driver definitions and includes, and disables worktree config discovery. Existing
effective worktree settings are included in the snapshot. Global/system config
and inherited config-override variables are disabled for the final Git process.
The existing runner's explicit command-line safety overrides remain in place.

The final launcher builds the snapshot on a private tmpfs, remounts that filesystem
read-only, binds its config file over the common Git config, then unmounts the
temporary source path. It does not deny exec, so the explicitly enabled pre-commit
hook still works. This is a config experiment, not an execution or host-data sandbox.

The adversarial tests wait for a ready-file handshake **after** the bind mount is
installed, change the original configuration from the parent namespace, and only
then release the child to run status. Replacing `.git/config` with rename bypasses
the file mount: Git's subsequent pathname lookup reaches the replacement and runs
the new filter. Updating `include.path` using ordinary `git config` has the same
effect, because Git uses lock-and-rename to publish that change. Both cases fail
for ordinary repositories and linked worktrees. This is a reproduced limitation
of this implementation, not a claim that all namespace-based designs are unsafe.

In-place config mutation and creation of config.worktree pass in both repository
forms. Their positive controls then run unguarded Git and confirm that the marker
executes outside the child namespace. The parent mount namespace identity is also
checked in those passing cases. A normal read-only bind of a parent-visible
temporary file would additionally leave its backing inode writable; the private
read-only tmpfs avoids that separate issue, but does not prevent pathname replacement.

## Final serial verification on unchanged experiment sources

| Phase | Result |
| --- | --- |
| Original product race, unchanged | 1 failed, 0 errors; 0.129 s |
| Original race + existing host-exec/environment regressions with plugin | 43 passed; 25.343 s |
| Explicit post-mount mutations with ordinary/linked repositories | 4 passed, 4 failed; 1.293 s |

`run.py` preserves the actual nonzero exit for both failing phases, writes complete
logs/JUnit and `results.json`, and verifies source hashes before and after the run.
No skip/xfail or inverted security assertion was introduced. The original race
probe, existing tests, runner and all product source are unchanged.

The final failing test IDs are replace-False, replace-True, include-False and
include-True. They fail at `assert not marker.exists()`, after a successful Git
status that returns the modified file. These are real executions of marker scripts
confined to newly created temporary repositories.

These runs execute **inside the agent container**, using the existing locked
core environment. They do not establish a new host-namespace result. No package
was installed, host policy changed, service restarted or repository published.
No fresh full core/UI or coverage claim is made; product source is unchanged.

## Reproduction

From this worktree, with its existing core environment:

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/config-namespace/run.py
```

Expected overall exit: 1. This is deliberately not part of regular pytest
collection. The namespace plugin is loaded only by explicit `-p namespace_plugin`.

## Exploratory failures and limits

Earlier JUnit files are preserved separately and are not final-source evidence.
`prototype.xml` is the initial 43-pass ordinary-file-bind prototype. The first
related-suite attempt was interrupted after a source revision; it has no complete
JUnit/result and is not counted as validation. Subsequent attempts exposed missing
optional fcntl seal constants, a failed memfd bind, and libmount's reinterpretation
of uid=1000 inside the new user namespace. The final launcher uses private tmpfs
and a direct read-only remount syscall instead. `races-complete.xml` also records
an early positive-control mistake: a clean filter can run more than once, so the
new control now checks for one or more marker lines. The security assertion
before that control is unchanged and still fails on the four bypasses.

The prototype only accepts a regular common config file. Bare/SHA256 repositories,
symlinked config paths, reftable, submodules, untrusted Git-dir discovery, aliases,
config mutations inside hooks, unknown executable settings and hostile concurrent
changes during snapshot preparation were not comprehensively reviewed. Startup
overhead was not benchmarked. These limitations independently preclude adoption.

Next SEC-11 work must protect directory/path resolution as well as snapshot bytes,
or put untrusted Git and drivers behind an execution boundary that preserves
approved hooks. Another enumeration or another file-only bind is insufficient.

Primary references consulted for the experiment: [Git config scopes, includes and
environment](https://git-scm.com/docs/git-config), [Linux mount namespaces and
propagation](https://man7.org/linux/man-pages/man7/mount_namespaces.7.html), and
[Linux fcntl seal UAPI](https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/fcntl.h).
The verdict above is based on the saved executions, not inferred from documentation.
