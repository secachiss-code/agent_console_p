"""Validate saved evidence; PASS does not mean security or compatibility acceptance."""
import hashlib
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
runner = runpy.run_path(str(HERE / 'run.py'))
report = json.loads((HERE / 'results.json').read_text())
cases = report['cases']
expected = {(f, o, m, e) for f in runner['FORMATS'] for o in runner['ORIGINS']
            for m in runner['MODES'] for e in ('native', 'product')}
assert len(cases) == len(expected) == 36
assert {(c['format'], c['origin'], c['mode'], c['engine']) for c in cases} == expected
for path, value in report['source_hashes'].items():
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == value, path
comparisons = json.loads((HERE / 'comparison.json').read_text())
assert comparisons == [runner['compare'](n, p) for n, p in zip(cases[::2], cases[1::2], strict=True)]
for c in cases:
    bare = c['origin'] == 'bare'
    native = c['engine'] == 'native'
    first, last = c['steps']
    assert [s['operation'] for s in c['steps']] == (['commit-tree', 'update-ref'] if bare else ['stage', 'commit'])
    assert first['ok']
    refused = c['mode'] == 'ref-refuse' and (native or not bare)
    refused |= c['mode'] == 'pre-refuse' and not bare
    assert last['ok'] == (not refused), (c['origin'], c['mode'], c['engine'])
    for s in c['steps']:
        b, a = s['before'], s['after']
        assert a['bare'] == ('true' if bare else 'false')
        assert (a['common'] != a['metadata']) == (c['origin'] == 'linked')
        for state in (b, a):
            if state['head']:
                assert len(state['head']) == (40 if c['format'] == 'sha1' else 64)
        assert a['files'] == b['files']
        if s is first or refused:
            assert a['head'] == b['head'] and a['refs'] == b['refs']
        if bare:
            assert a['index'] == b['index'] == ''
            assert a['index_sha256'] == b['index_sha256'] is None
        else:
            assert a['files']['data.txt'] == 'updated\n'
            assert a['files']['ignored.txt'] == 'ignored survives\n'
            assert a['files']['untracked.txt'] == 'untracked survives\n'
            if s is first:
                assert a['index'].endswith('\tdata.txt')
                assert a['index_content'] == 'updated'
                assert [e['phase'] for e in s['events']] == (['post-index-change'] if native else [])
            else:
                assert a['index'] == b['index']
        if not native and (bare or s is first):
            assert s['events'] == []
        for e in s['events']:
            assert e['cwd'] == ('CASE/repo' if c['origin'] != 'linked' else 'CASE/linked')
            if e['phase'] == 'reference-transaction':
                assert e['stdin'].strip()
                for row in e['stdin'].splitlines():
                    old, new, ref = row.split()
                    assert len(old) == len(new) == (40 if c['format'] == 'sha1' else 64)
            assert e['refuse'] == ((c['mode'] == 'pre-refuse' and e['phase'] == 'pre-commit') or
                (c['mode'] == 'ref-refuse' and e['phase'] == 'reference-transaction' and e['args'] == ['prepared']))
    if not bare:
        phases = [e['phase'] for e in last['events']]
        assert phases[:2] == ['post-index-change', 'pre-commit']
        if last['ok']:
            assert phases.count('pre-commit') == phases.count('prepare-commit-msg') == phases.count('commit-msg') == phases.count('post-commit') == 1
        assert any(e['refuse'] for e in last['events']) == refused
        if c['origin'] == 'unborn':
            assert first['before']['head'] is None
        else:
            # The main checkout's branch remains intact during linked writes.
            base = first['before']['head']
            assert f'{base} refs/heads/target' in last['after']['refs']
    if bare and native:
        assert first['events'] == []
        assert [e['args'] for e in last['events']] == ([['prepared'], ['aborted']] if refused else [['prepared'], ['committed']])
    if last['ok']:
        assert last['after']['head'] != last['before']['head']
        assert last['after']['message'] == 'origin census'
        if not bare:
            assert last['after']['head_content'] == last['after']['index_content'] == 'updated'
        if bare:
            assert last['after']['head'] == first['candidate']
            assert last['after']['tree'] == first['before']['tree']

steps = [s for c in comparisons for s in c['steps']]
print(json.dumps(dict(evidence_integrity='PASS', cases=len(cases), steps=len(cases)*2,
                     step_pairs=len(steps), equal_outcomes=sum(s['outcome_equal'] for s in steps),
                     source_hashes=len(report['source_hashes']), security_acceptance='NOT RUN',
                     stage_A='INCOMPLETE'), indent=2))
