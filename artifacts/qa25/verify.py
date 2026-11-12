"""Check original test/image preservation, native outcomes and runner controls."""
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(__file__).resolve().parents[2]
out = root/'artifacts/qa25'
base = '65cbad4dc438d8de33d91a46f242069571fee80b'
assert subprocess.check_output(['git', 'diff', base, '--', 'src', 'tests'], cwd=root) == b''
counts = {}
for label in ['final-geometry', 'native-desktop']:
    suite = ET.parse(out/label/'pytest.xml').getroot().find('testsuite')
    assert suite is not None
    assert [int(suite.attrib.get(k, 0)) for k in ['tests','failures','errors','skipped']] == [31,0,0,0]
    metadata = json.loads((out/label/'run.json').read_text())
    assert metadata['returncode'] == 0
    assert metadata['renderer_override'] is None
    counts[label] = dict(tests=31, failures=0, errors=0, skipped=0)
state = json.loads((out/'desktop-state.json').read_text())
assert state['application_id'] == 'io.github.aconsole.AConsole'
assert state['returncode'] == 0
assert len(state['before']) == 53
assert state['before'] == state['after']
controls = json.loads((out/'runner-controls.json').read_text())
assert controls['pytest_usage_error'] == 4 and controls['timeout'] == 124
assert controls['refuses_overwrite'] and controls['terminated_descendant_after_parent_exit']
native = json.loads((out/'native-controls.json').read_text())
for label, expected in [('final-geometry',0),('native-error',4),('native-timeout',124)]:
    assert native[label] == dict(returncode=expected, owned_x_socket_and_lock_removed=True)
    assert json.loads((out/label/'run.json').read_text())['returncode'] == expected
paths = subprocess.check_output(['git','ls-files','src','tests'],cwd=root,text=True).splitlines()
hashes = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in paths}
hashes['tools/native_ui_tests.py'] = hashlib.sha256((root/'tools/native_ui_tests.py').read_bytes()).hexdigest()
(out/'validation.json').write_text(json.dumps(dict(base=base, counts=counts, hashes=hashes),indent=2)+'\n')
print('PASS: 62 native tests; original src/tests/PNG unchanged; runner cleanup/error controls')
