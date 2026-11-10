"""Check saved positive controls and source integrity, not security acceptance."""
import hashlib
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
runner = runpy.run_path(str(HERE / 'run.py'))
report = json.loads((HERE / 'results.json').read_text())
cases = report['cases']
expected = {(fmt, op, mode, engine) for fmt in runner['FORMATS']
            for op in runner['OPERATIONS'] for mode in runner['MODES']
            for engine in ('native', 'product')}
assert len(cases) == len(expected) == 32
assert {(c['format'], c['operation'], c['mode'], c['engine']) for c in cases} == expected
for path, value in report['source_hashes'].items():
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == value, path
comparisons = json.loads((HERE / 'comparison.json').read_text())
assert comparisons == [runner['compare'](n, p) for n, p in zip(cases[::2], cases[1::2], strict=True)]
for c in cases:
    b, a = c['before'], c['after']
    native, success = c['engine'] == 'native', c['mode'] == 'success'
    op = c['operation']
    for field in ('ignored', 'untracked'):
        assert b[field] == a[field]
    assert len(a['head']) == (40 if c['format'] == 'sha1' else 64)
    if native:
        assert c['ok'] == success
        assert c['events']
        phase = 'clean' if op == 'stage' else 'smudge' if op == 'restore' else 'merge'
        assert all(e['phase'] == phase and e['fail'] == (not success) for e in c['events'])
        if phase == 'merge':
            assert all(e['inputs'] == ['base\n', 'ours\n', 'theirs\n'] for e in c['events'])
        else:
            assert all(e['stdin'] == ('WORK:updated\n' if op == 'stage' else 'CLEAN:base\n') for e in c['events'])
    else:
        assert not c['events']
        assert c['ok'] == (op in ('stage', 'restore'))
    if op != 'merge' or not c['ok']:
        assert b['head'] == a['head'] and b['refs'] == a['refs']
    if op == 'stage':
        assert a['file'] == b['file'] == 'WORK:updated\n'
        assert a['index_content'] == ('CLEAN:updated\n' if native and success else
                                      'CLEAN:base\n' if native else 'WORK:updated\n')
        if native and not success:
            assert b == a
    if op == 'restore':
        assert b['index'] == a['index']
        if c['ok']:
            assert a['file'] == ('WORK:base\n' if native else 'CLEAN:base\n')
        else:
            assert native and not success and a['file'] is None
    if op == 'probe':
        assert b == a
        assert c['candidate_content'] == ('RESOLVED:ours+theirs\n' if c['ok'] else None)
        assert bool(c['candidate_tree']) == c['ok']
    if op == 'merge':
        if c['ok']:
            assert a['head'] != b['head']
            assert a['head_content'] == a['index_content'] == a['file'] == 'RESOLVED:ours+theirs\n'
        else:
            assert b == a
            if native:
                raw = c['native_failure_before_reset']
                assert raw['merge_head'] and raw['head'] == b['head']
                assert raw['file'] == 'ours\n'

print(json.dumps(dict(evidence_integrity='PASS', cases=len(cases), pairs=len(comparisons),
                     source_hashes=len(report['source_hashes']),
                     equal_outcomes=sum(c['outcome_equal'] for c in comparisons),
                     security_acceptance='NOT RUN', stage_A='INCOMPLETE'), indent=2))
