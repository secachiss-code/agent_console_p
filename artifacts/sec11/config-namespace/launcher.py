"""Laboratory launcher inside unshare -Ur -m; never imported by the product.

Only temporary repositories in the diagnostic suite are supported. This is a
configuration-race experiment, not a general filesystem or execution sandbox.
"""

import ctypes
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def run(argv, **kwargs):
    result = subprocess.run(argv, check=False, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{argv!r}: {result.returncode}: {result.stderr!r}")
    return result.stdout


def main():
    argv = sys.argv[1:]
    git = argv[0]
    at = argv.index("-C")
    prefix = argv[: at + 2]
    # This helper must only run in a new namespace; do not modify mounts if
    # invoked directly. The parent records its own namespace identity.
    expected = os.environ.pop("ACONSOLE_EXPERIMENT_PARENT_MNT")
    if os.readlink("/proc/self/ns/mnt") == expected:
        raise RuntimeError("a private mount namespace is required")
    run(["/usr/bin/mount", "--make-rprivate", "/"])
    listed = run([*prefix, "config", "--null", "--list", "--includes"])
    common = Path(os.fsdecode(run([
        *prefix, "rev-parse", "--path-format=absolute", "--git-common-dir",
    ])).strip())
    entries = []
    for record in listed.split(b"\0"):
        if not record:
            continue
        key, sep, value = record.partition(b"\n")
        name = os.fsdecode(key)
        if name.lower().startswith(("include.", "includeif.")):
            continue
        if re.fullmatch(r"filter\..*\.(clean|smudge|process|required)", name):
            continue
        if re.fullmatch(r"(merge\..*\.driver|diff\..*\.textconv|trailer\..*\.(cmd|command))", name):
            continue
        if name.lower() == "extensions.worktreeconfig":
            continue
        entries.append((name, os.fsdecode(value) if sep else "true"))

    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith("GIT_CONFIG_") or key == "GIT_CONFIG":
            del env[key]
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    with tempfile.TemporaryDirectory(prefix="sec13-config-") as folder:
        # Build on a private tmpfs, then make its superblock read-only.
        run(["/usr/bin/mount", "-t", "tmpfs", "-o", "mode=0700", "tmpfs", folder])
        snapshot = Path(folder) / "config"
        snapshot.touch()
        for key, value in entries:
            run([git, "config", "--file", str(snapshot), "--add", key, value], env=env)
        # Disabling this extension prevents a config.worktree created later
        # from being opened. Its existing effective settings were flattened.
        run([git, "config", "--file", str(snapshot), "extensions.worktreeConfig", "false"], env=env)
        target = common / "config"
        if not target.is_file() or target.is_symlink():
            raise RuntimeError("prototype requires a regular common config")
        # A read-only bind of a normal temporary file is insufficient: the
        # parent can mutate its backing inode through the original pathname.
        # This tmpfs exists only in the child namespace and has no writable
        # descriptors left when we remount the entire filesystem read-only.
        # Direct remount avoids libmount reinterpreting uid=1000 from mountinfo
        # inside the new user namespace (where only uid=0 is mapped).
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.mount(None, os.fsencode(folder), None, 32 | 1, None) != 0:
            raise OSError(ctypes.get_errno(), "private tmpfs read-only remount failed")
        run(["/usr/bin/mount", "--bind", str(snapshot), str(target)])
        run(["/usr/bin/umount", folder])
        # The inode is pinned by the mount. Changes or rename replacements in
        # the parent namespace cannot change this process's mounted snapshot.
        ready = env.pop("ACONSOLE_EXPERIMENT_READY", None)
        release = env.pop("ACONSOLE_EXPERIMENT_RELEASE", None)
        if ready:
            Path(ready).write_text("ready\n")
            import time
            deadline = time.monotonic() + 10
            while not Path(release).exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("parent did not release experiment")
                time.sleep(.005)
        result = subprocess.run(argv, env=env, check=False)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
