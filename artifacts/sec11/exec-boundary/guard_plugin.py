"""Apply the experimental guard only within this standalone pytest run."""
from pathlib import Path
import subprocess
import sys
import pytest
from aconsole.core.infrastructure.git.runner import GitOutput,GitRunner

@pytest.fixture(autouse=True)
def guarded(monkeypatch):
    def execute(self,argv,stdin,env,limit):
        done=subprocess.run([sys.executable,str(Path(__file__).with_name('guard.py.txt')),*argv],input=stdin,capture_output=True,env=env,timeout=limit,check=False)
        return GitOutput(code=done.returncode,stdout=done.stdout,stderr=done.stderr.decode(errors='replace'))
    monkeypatch.setattr(GitRunner,'_execute',execute)
