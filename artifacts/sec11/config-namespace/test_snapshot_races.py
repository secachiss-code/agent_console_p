"""Mutate the original configuration after the child confirms its bind mount."""

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tests.core.git_helpers import add_worktree, commit, git, new_repo
from tests.core.test_git_host_exec import _script


@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("mutation", ["replace", "include", "worktree", "inplace"])
def test_original_config_changes_after_mount(tmp_path, linked, mutation):
    repo = new_repo(tmp_path / "repo")
    commit(repo, "f.txt", "one\n", "seed")
    work = add_worktree(repo, tmp_path / "linked", "worker") if linked else repo
    (work / "f.txt").write_text("two\n")
    (work / ".gitattributes").write_text("* filter=late\n")
    marker = tmp_path / "executed"
    script = tmp_path / "late.sh"
    _script(script, marker)
    if mutation == "worktree":
        git(repo, "config", "extensions.worktreeConfig", "true")
    ready, release = tmp_path / "ready", tmp_path / "release"
    env = dict(os.environ, ACONSOLE_EXPERIMENT_PARENT_MNT=os.readlink("/proc/self/ns/mnt"),
               ACONSOLE_EXPERIMENT_READY=str(ready), ACONSOLE_EXPERIMENT_RELEASE=str(release))
    original_mount = os.readlink("/proc/self/ns/mnt")
    command = ["/usr/bin/unshare", "--user", "--map-root-user", "--mount", sys.executable,
               str(Path(__file__).with_name("launcher.py")), "/usr/bin/git", "-C", str(work),
               "status", "--porcelain"]
    child = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if child.poll() is not None:
                stdout, stderr = child.communicate()
                pytest.fail(f"launcher failed before snapshot: {stdout!r} {stderr!r}")
            assert time.monotonic() < deadline, "snapshot handshake timed out"
            time.sleep(.005)
        if mutation == "replace":
            original = repo / ".git" / "config"
            replacement = tmp_path / "replacement"
            replacement.write_bytes(original.read_bytes())
            subprocess.run(["/usr/bin/git", "config", "--file", str(replacement),
                            "filter.late.clean", str(script)], check=True)
            replacement.replace(original)
        elif mutation == "include":
            included = tmp_path / "late.config"
            subprocess.run(["/usr/bin/git", "config", "--file", str(included),
                            "filter.late.clean", str(script)], check=True)
            git(repo, "config", "include.path", str(included))
        elif mutation == "worktree":
            git(work, "config", "--worktree", "filter.late.clean", str(script))
        else:
            # Unlike git config's lock/rename update, this preserves the
            # original inode and isolates the file-content case.
            with (repo / ".git" / "config").open("a") as stream:
                stream.write(f'\n[filter "late"]\n\tclean = {script}\n')
        release.write_text("go\n")
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr
        assert b"f.txt" in stdout
        assert not marker.exists()
        assert os.readlink("/proc/self/ns/mnt") == original_mount
        # The attack is real and still present outside the namespace. The
        # ordinary, unguarded Git command executes it in this temporary repo.
        git(work, "status", "--porcelain")
        executions = marker.read_text().splitlines()
        assert executions and all(line == "ran" for line in executions)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
