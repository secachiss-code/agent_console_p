"""Check SEC-28 evidence, native controls and public API limits, not security."""
import hashlib
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
r = runpy.run_path(str(HERE / 'run.py'))
report = json.loads((HERE / 'results.json').read_text())
cases = report['cases']
expected = {(f,o,m,e) for f in r['FORMATS'] for o in r['ORIGINS']
            for m in r['MODES'] for e in ('native','daemon')}
assert len(cases) == len(expected) == 36
assert {(c['format'],c['origin'],c['mode'],c['engine']) for c in cases} == expected
for p,h in report['source_hashes'].items():
    assert hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h,p
comp = json.loads((HERE / 'comparison.json').read_text())
assert comp == r['comparisons'](cases)
for pair in comp:
    assert pair['available'] == (pair['origin'] != 'bare')
    for step in pair['steps']:
        assert step['outcome_equal']
        assert all(equal for key,equal in step['state_equal'].items() if key != 'index_sha256')
        assert step['phases_equal'] == (step['operation'] == 'commit')
for c in cases:
    bare = c['origin'] == 'bare'
    native = c['engine'] == 'native'
    width = 40 if c['format'] == 'sha1' else 64
    if not native:
        assert c['database_before_add'] == dict(projects=[], git_operations=[])
        assert c['after_stop'] == c['after']
        assert c['database_after_stop'] == c['database_after']
        assert c['database_after']['git_operations'] == []
        if bare:
            assert not c['steps']
            assert c['project_error']['message'] == 'PROJECT_NOT_A_GIT_REPO'
            assert c['after'] == c['before_start']
            assert c['database_after'] == c['database_before_add']
            assert c['last_events'] == []
            continue
        assert c['before_start'] == c['after_add']
        assert c['startup_events'] == []
        assert len(c['database_after']['projects']) == 1
        assert c['project']['root'] == ('CASE/linked' if c['origin'] == 'linked' else 'CASE/repo')
        assert c['database_after']['projects'][0]['root_path'] == c['project']['root']
    first,last = c['steps']
    assert [s['operation'] for s in c['steps']] == (['commit-tree','update-ref'] if bare else ['stage','commit'])
    assert first['ok']
    refused = (c['mode'] == 'ref-refuse') or (c['mode'] == 'pre-refuse' and not bare)
    assert last['ok'] == (not refused)
    for s in c['steps']:
        b,a = s['before'],s['after']
        assert a['bare'] == ('true' if bare else 'false')
        assert (a['common'] != a['metadata']) == (c['origin'] == 'linked')
        for state in (b,a):
            if state['head']:
                assert len(state['head']) == len(state['tree']) == width
        assert a['files'] == b['files']
        if s is first or refused:
            assert a['head'] == b['head'] and a['refs'] == b['refs']
        if bare:
            assert a['index'] == b['index'] == ''
            assert a['index_sha256'] == b['index_sha256'] is None
        else:
            assert a['files'] == {'data.txt':'updated\n','ignored.txt':'ignored survives\n',
                                  'untracked.txt':'untracked survives\n'}
            assert a['index_content'] == 'updated'
            if s is first:
                assert [e['phase'] for e in s['events']] == (['post-index-change'] if native else [])
            else:
                assert a['index'] == b['index']
        if not native:
            assert s['database']['git_operations'] == []
            assert s['response']['ok'] == s['ok'] and s['response']['head_oid'] == a['head']
            assert s['row']['head_oid'] == b['head']
        for e in s['events']:
            assert e['cwd'] == ('CASE/linked' if c['origin'] == 'linked' else 'CASE/repo')
            if e['phase'] == 'reference-transaction':
                assert e['stdin'].strip()
                for line in e['stdin'].splitlines():
                    old,new,ref = line.split()
                    assert len(old) == len(new) == width
            assert e['refuse'] == ((c['mode'] == 'pre-refuse' and e['phase'] == 'pre-commit') or
                (c['mode'] == 'ref-refuse' and e['phase'] == 'reference-transaction' and e['args'] == ['prepared']))
    if bare:
        assert first['events'] == []
        assert [e['args'] for e in last['events']] == ([['prepared'],['aborted']] if refused else [['prepared'],['committed']])
    else:
        phases = [e['phase'] for e in last['events']]
        assert phases[:2] == ['post-index-change','pre-commit']
        assert any(e['refuse'] for e in last['events']) == refused
        if c['mode'] == 'pre-refuse':
            assert phases == ['post-index-change','pre-commit']
        if last['ok']:
            for phase in ('pre-commit','prepare-commit-msg','commit-msg','post-commit'):
                assert phases.count(phase) == 1
        reference_events = [e for e in last['events'] if e['phase'] == 'reference-transaction']
        transactions = [e for e in reference_events if any(line.split()[2] == last['before']['symbolic']
            for line in e['stdin'].splitlines())]
        assert [e['args'] for e in transactions] == ([] if c['mode'] == 'pre-refuse' else
            [['prepared'],['aborted']] if refused else [['prepared'],['committed']])
        for event in transactions:
            assert any(line.split()[2] == last['before']['symbolic'] for line in event['stdin'].splitlines())
        cleanup = [e for e in reference_events if e not in transactions]
        assert [e['args'] for e in cleanup] == ([['aborted'],['prepared'],['committed']] if last['ok'] else [])
        assert all(e['stdin'] == f"{'0' * width} {'0' * width} AUTO_MERGE\n" for e in cleanup)
        if c['origin'] == 'unborn':
            assert first['before']['head'] is None
        else:
            assert f"{first['before']['head']} refs/heads/target" in last['after']['refs']
    if last['ok']:
        assert last['after']['head'] != last['before']['head']
        assert last['after']['message'] == 'origin census'
        if bare:
            assert last['after']['head'] == first['candidate']
            assert last['after']['tree'] == first['before']['tree']
        else:
            assert last['after']['head_content'] == 'updated'
steps = [s for p in comp for s in p['steps']]
print(json.dumps(dict(evidence_integrity='PASS', cases=len(cases), pairs=len(comp),
    available_pairs=sum(p['available'] for p in comp), step_pairs=len(steps),
    equal_outcomes=sum(s['outcome_equal'] for s in steps),
    source_hashes=len(report['source_hashes']), daemon_journal_rows=0,
    security_acceptance='NOT RUN', stage_A='INCOMPLETE'), indent=2))
