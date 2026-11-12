"""Final geometry plus real-Xvfb failure/timeout cleanup controls."""
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
binary = sys.argv[1]
base = [sys.executable, str(root/'tools/native_ui_tests.py'), '--mode', 'geometry', '--xvfb', binary]
results = {}
for label, extra, expected in [
    ('final-geometry', [], 0),
    ('native-error', ['no-such-qa25-test.py'], 4),
    ('native-timeout', ['--timeout', '0.001'], 124),
]:
    out = root/'artifacts/qa25'/label
    result = subprocess.run([*base, '--output', str(out), *extra], cwd=root, timeout=150)
    assert result.returncode == expected, (label, result.returncode)
    metadata = json.loads((out/'run.json').read_text())
    number = metadata['display'].removeprefix(':')
    assert not Path(f'/tmp/.X11-unix/X{number}').exists()
    assert not Path(f'/tmp/.X{number}-lock').exists()
    results[label] = dict(returncode=result.returncode, owned_x_socket_and_lock_removed=True)
(root/'artifacts/qa25/native-controls.json').write_text(json.dumps(results, indent=2)+'\n')
print(results)
