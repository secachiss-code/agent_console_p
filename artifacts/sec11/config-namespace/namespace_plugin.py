"""Standalone pytest plugin; not loaded by the normal test suite."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from aconsole.core.infrastructure.git.runner import GitOutput, GitRunner


@pytest.fixture(autouse=True)
def private_config(monkeypatch):
    def execute(_self, argv, stdin, env, limit):
        child = dict(env, ACONSOLE_EXPERIMENT_PARENT_MNT=os.readlink("/proc/self/ns/mnt"))
        result = subprocess.run([
            "/usr/bin/unshare", "--user", "--map-root-user", "--mount",
            sys.executable, str(Path(__file__).with_name("launcher.py")), *argv,
        ], input=stdin, capture_output=True, env=child, timeout=limit, check=False)
        return GitOutput(code=result.returncode, stdout=result.stdout,
                         stderr=result.stderr.decode(errors="replace"))

    monkeypatch.setattr(GitRunner, "_execute", execute)
