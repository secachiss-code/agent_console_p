"""Check SEC-27 contents, positive controls and RPC persistence, not security."""
import hashlib
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
r = runpy.run_path(str(HERE / 'run.py'))
report = json.loads((HERE / 'results.json').read_text())
cases = report['cases']
expected = {(f,o,m,e) for f in r['FORMATS'] for o in r['OPERATIONS']
            for m in r['MODES'] for e in ('native','daemon')}
assert len(cases) == len(expected) == 32
assert {(c['format'],c['operation'],c['mode'],c['engine']) for c in cases} == expected
for p,h in report['source_hashes'].items():
    assert hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h,p
comp = json.loads((HERE / 'comparison.json').read_text())
assert comp == r['comparisons'](cases)
for c in cases:
    b,a = c['before'],c['after']
    native,success = c['engine'] == 'native',c['mode'] == 'success'
    op = c['operation']
    assert len(a['head']) == (40 if c['format'] == 'sha1' else 64)
    for key in ('ignored','untracked'):
        assert a[key] == b[key]
    if native:
        assert c['ok'] == success
        assert len(c['events']) == 1
        event = c['events'][0]
        phase = 'clean' if op == 'stage' else 'smudge' if op == 'restore' else 'merge'
        assert event['phase'] == phase and event['fail'] == (not success)
        if phase == 'merge':
            assert event['inputs'] == ['base\n','ours\n','theirs\n']
        else:
            assert event['stdin'] == ('WORK:updated\n' if op == 'stage' else 'CLEAN:base\n')
    else:
        assert not c['events']
        assert c['before_start'] == b
        assert c['after_stop'] == a
        assert c['journal_before'] == c['journal_after'] == c['journal_after_stop'] == []
        assert c['ok'] == (op in ('stage','restore'))
        if op in ('stage','restore'):
            assert c['response']['ok'] and c['response']['head_oid'] == a['head']
        else:
            assert c['preview']['probe']['status'] == 'conflict'
            assert c['preview']['modes'] == []
            assert 'conflict' in c['preview']['blockers']
            if op == 'merge':
                assert c['merge_error'] == 'MERGE_CONFLICT'
    if op != 'merge' or not c['ok']:
        assert b['head'] == a['head'] and b['head_tree'] == a['head_tree']
    if op == 'stage':
        assert a['file'] == b['file'] == 'WORK:updated\n'
        assert a['index_content'] == ('CLEAN:updated\n' if native and success else
                                      'CLEAN:base\n' if native else 'WORK:updated\n')
        assert a['refs'] == b['refs']
        if native and not success:
            assert a == b
    if op == 'restore':
        assert a['index'] == b['index']
        assert a['file'] == ('WORK:base\n' if native and success else None if native else 'CLEAN:base\n')
        if native:
            assert a['refs'] == b['refs']
        else:
            assert a['index_sha256'] == b['index_sha256']
            assert not b['safety'] and len(a['safety']) == 1
            safety = next(iter(a['safety'].values()))
            assert safety['content'] == 'WORK:updated\n'
            assert len(safety['oid']) == len(safety['tree']) == len(a['head'])
            before_refs = dict(line.split(' ',1)[::-1] for line in b['refs'].splitlines())
            after_refs = dict(line.split(' ',1)[::-1] for line in a['refs'].splitlines())
            assert set(after_refs) - set(before_refs) == set(a['safety'])
            assert all(after_refs[k] == v for k,v in before_refs.items())
            assert c['preview']['exists_in_source']
    if op == 'probe':
        assert a == b
        assert c['candidate_content'] == ('RESOLVED:ours+theirs\n' if c['ok'] else None)
        assert bool(c['candidate_tree']) == c['ok']
    if op == 'merge':
        if c['ok']:
            assert a['head'] != b['head']
            assert a['head_content'] == a['index_content'] == a['file'] == 'RESOLVED:ours+theirs\n'
        else:
            assert a == b
            if native:
                assert c['native_failure_before_reset']['merge_head']
                assert c['native_failure_before_reset']['file'] == 'ours\n'
print(json.dumps(dict(evidence_integrity='PASS', cases=len(cases), pairs=len(comp),
    source_hashes=len(report['source_hashes']), equal_outcomes=sum(c['outcome_equal'] for c in comp),
    equal_files=sum(c['state_equal']['file'] for c in comp),
    daemon_journal_rows=0, security_acceptance='NOT RUN', stage_A='INCOMPLETE'), indent=2))
