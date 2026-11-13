# QA-34: fresh package build and isolated installation

Source HEAD: `87846bc5819f174640a6754ad8b32e000bfd424d`. All 305 tracked build inputs have identical before/after SHA256 manifests.
Fresh package: `artifacts/qa34/deb/aconsole_0.1.1_amd64.deb`, 72781894 bytes,
SHA256 `3d4d491c40c7c39aac579b3aed8242fbb747ee7a66dd75cc55c02561a63e8944`. The previous QA17 package was not reused.

Built with existing locked UI/core environments via temporary worktree symlinks, removed afterwards. Build exit 0 (55.98 s). No dependency environment sync or product changes.

A new rootless Podman container used Ubuntu 24.04 image ID `7da97f743f397971f35dd21cd2d19a7da070a150e0cfa3eda559e71891ec7533`,
digest `sha256:2198e4c65076e9cb0b3d695219df667c0e3b0072da64ca3c2648aca17dcecaf5`. Inspect confirms no host bind mounts or volumes.
Package copied through Podman cp; in-container SHA256 matches the build.
apt update and actual apt/dpkg installation exited 0 (122.75 s for install).
The transaction log records dependency installation and execution of `aconsole.postinst configure`.
Installed status: `install ok installed 0.1.1 amd64`.

From /tmp with env -i: absolute UI launcher help exit 0, core launcher help expected exit 2;
core free-threaded and UI GIL import assertions exit 0. Cleanup rm exit 0; container exists exit 1.
Source hashes unchanged. Raw command argv, timings, exits and log hashes are in results.json.

Reproduce the preserved executed runner with the host UI interpreter and `artifacts/qa34/run.txt`;
it refuses to overwrite results. The exact executed script is retained as .txt without changing its bytes.
`/usr/bin/python3 artifacts/qa34/verify.py` checks retained evidence offline.
The 70 MB deb is retained locally in ignored deb/; logs and manifests are committed.

This establishes local isolated package installation only. Remote CI, QA4, security, independent review
and Stage B acceptance remain unchanged. QA35 separately handles the user-requested host update.
