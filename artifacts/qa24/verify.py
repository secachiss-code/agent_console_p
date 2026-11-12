"""Check QA-24 evidence without accepting unrelated desktop failures."""

import ast
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'artifacts/qa24'
BASE = '91e7396d6e6f5b11e6727e52476d9825c72d1791'
results = {}
for name, expected in {
    'early-red': (3, 3),
    'early-red-corrected': (3, 3),
    'native-green': (6, 0),
    'native-final': (30, 0),
    'native-unmap': (1, 1),
    'native-release': (31, 0),
    'desktop-selection': (61, 9),
}.items():
    cases = ET.parse(OUTPUT / (name + '.xml')).findall('.//testcase')
    failures = [case.get('classname') + ':' + case.get('name')
                for case in cases if case.find('failure') is not None]
    assert (len(cases), len(failures)) == expected, (name, failures)
    assert all(case.find('error') is None and case.find('skipped') is None for case in cases)
    results[name] = {'total': len(cases), 'failures': failures}
    if name == 'early-red-corrected':
        assert all('GTK wait failed' in case.find('failure').text for case in cases)
previous = ET.parse(ROOT / 'artifacts/qa23/desktop-selection.xml').findall('.//testcase')
assert set(results['desktop-selection']['failures']) == {
    case.get('classname') + ':' + case.get('name')
    for case in previous if case.find('failure') is not None
}
test = 'tests/gui/front/test_preferences_drafts.py'
original = ast.parse(subprocess.check_output(['git', 'show', BASE + ':' + test], cwd=ROOT))
current = ast.parse((ROOT / test).read_text())
functions = {node.name: ast.dump(node) for node in current.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
for node in original.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        assert functions[node.name] == ast.dump(node), node.name
changed = subprocess.check_output(['git', 'diff', BASE, '--name-only', '--', 'src', 'tests'], cwd=ROOT)
assert set(changed.decode().splitlines()) == {'src/aconsole/ui/views/setup/preferences.py', test}
assert 'All checks passed!' in (OUTPUT / 'lint.log').read_text()
assert '0 errors, 0 warnings' in (OUTPUT / 'types.log').read_text()
assert json.loads((OUTPUT / 'geometry-after.json').read_text())['matches_qa22']
paths = [ROOT / 'src/aconsole/ui/views/setup/preferences.py', ROOT / test,
         *sorted((OUTPUT / 'ui').glob('*.png'))]
assert len(paths) == 5
(OUTPUT / 'validation.json').write_text(json.dumps({
    'base': BASE, 'results': results,
    'hashes': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
    'qa4_accepted': False, 'performance_accepted': False, 'security_accepted': False,
}, indent=2) + '\n')
print('QA-24 evidence verified')
