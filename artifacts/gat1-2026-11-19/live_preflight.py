from pathlib import Path
import json
import os
import tempfile
import sys
sys.path.insert(0, str(Path.cwd() / "src"))
from aconsole.core.infrastructure.containers.podman import Podman
from aconsole.core.infrastructure.gates.runner import ContainerGateRunner
root = Path(os.environ['XDG_RUNTIME_DIR']) / 'aconsole/preflight'
root.mkdir(parents=True, exist_ok=True, mode=0o700)
root.chmod(0o700)
with tempfile.TemporaryDirectory(dir=root) as directory:
    source = Path(directory)
    (source / 'probe.txt').write_text('isolated-preflight\n')
    commands = (('/bin/cat', 'probe.txt'),)
    runner = ContainerGateRunner(podman=Podman(Path('/usr/bin/podman')), container='agent-codex',
        user='root', worktree='/unmounted-worktree', profile=commands, state_home=source)
    results = runner.preflight(source, root, commands)
    report = [dict(returncode=r.returncode, output=r.output, argv=r.argv) for r in results]
    Path('artifacts/gat1-2026-11-19/live-preflight.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))
    assert all(r.returncode == 0 for r in results)
