"""SEC-24 evidence integrity, positive controls and persisted RPC outcomes."""
import hashlib
import json
from pathlib import Path
import runpy

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
r=runpy.run_path(str(HERE / 'run.py'))
report=json.loads((HERE / 'results.json').read_text())
cases=report['cases']
expected={(f,s,m,e) for f in r['FORMATS'] for s in r['SCENARIOS'] for m in r['MODES'] for e in ('native','daemon')}
assert len(cases)==len(expected)==72
assert {(c['format'],c['scenario'],c['mode'],c['engine']) for c in cases}==expected
for p,h in report['source_hashes'].items():
    assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h,p
comp=json.loads((HERE/'comparison.json').read_text())
assert comp==r['comparisons'](cases)
controls=[]
for c in cases:
    b,a=c['before'],c['after']
    mode,scenario,engine=c['mode'],c['scenario'],c['engine']
    assert len(a['target'])==(40 if c['format']=='sha1' else 64)
    assert a['target']!=b['target'] if c['ok'] else a['target']==b['target']
    for name in ('base.txt','ignored.txt','untracked.txt'):
        assert b['files'][name]==a['files'][name]
    assert a['symbolic_head']==b['symbolic_head']==('refs/heads/anchor' if engine=='daemon' else 'refs/heads/target')
    if c['ok']:
        assert a['target_tree']==c['expected_tree']
        assert a['target_message']==r['MESSAGE'].strip()
        assert a['target_parents']==(c['target'] if scenario=='squash' else c['target']+' '+c['source'])
    refs_before=dict(line.split(' ',1)[::-1] for line in b['refs'].splitlines())
    refs_after=dict(line.split(' ',1)[::-1] for line in a['refs'].splitlines())
    assert set(refs_after)==set(refs_before)  # This merge API does not create a safety ref.
    for ref,oid in refs_before.items():
        if ref!='refs/heads/target':
            assert refs_after[ref]==oid
    refused=[e for e in c['events'] if e['refuse']]
    for event in c['events']:
        assert event['cwd']=='CASE/repo'
        if event['refuse'] and mode.startswith('ref_'):
            assert event['phase']=='reference-transaction' and event['target']
            assert any(line.split()[-1:] == ['refs/heads/target'] for line in event['stdin'].splitlines())
    if engine=='native':
        assert c['events']
        assert c['ok']==(mode in ('observe','post_fail','ref_committed'))
        assert bool(refused)==(mode!='observe')
        assert c['commands'][-1]['code']==0 if c['ok'] else c['commands'][-1]['code']!=0
        if mode=='ref_prepared':
            assert any(e['target'] and e['args']==['aborted'] for e in c['events'])
        if mode=='ref_committed':
            assert a['target']!=b['target']  # A late hook error cannot imply rollback.
        controls.append(dict(format=c['format'],scenario=scenario,mode=mode,
                             refused_phases=[e['phase'] for e in refused],ok=c['ok']))
    else:
        assert b['head']==a['head']
        assert b['index']==a['index'] and b['index_sha256']==a['index_sha256']
        assert b['files']==a['files'] and b['status']==a['status']
        assert c['preview']['target_worktree'] is None and c['preview']['blockers']==[]
        assert c['journal_before']==[]
        assert c['journal_after']==c['journal_after_stop']
        rows=c['journal_after']
        assert len(rows)==1
        row=rows[0]
        response=c['response']['operation']
        assert c['rpc_operation']==c['response']
        assert c['rpc_state']['operations']==[]
        assert row['id']==response['id']
        assert row['status']==response['status']==('finished' if c['ok'] else 'failed')
        assert row['result_oid']==response['result_oid']==(a['target'] if c['ok'] else None)
        assert row['source_oid']==c['source'] and row['target_oid']==c['target']
        assert row['expected_tree']==c['expected_tree'] and row['worktree'] is None
        assert row['kind']=='merge' and row['mode']==('squash' if scenario=='squash' else 'merge')
        assert not row['follow_up_pending']
        if scenario=='merge_sample':
            assert not c['ok'] and 'git-sh-setup: not found' in response['message']
            assert not refused  # No downstream refusal is proven after fallback failure.
        else:
            assert c['ok']==(mode!='pre_refuse')
            assert bool(refused)==(mode=='pre_refuse')
            assert not any(e['phase'] in ('prepare-commit-msg','reference-transaction','post-merge','post-commit') for e in c['events'])
for fmt in r['FORMATS']:
    for mode in r['MODES'][1:]:
        assert any(c['format']==fmt and c['mode']==mode and c['refused_phases'] for c in controls)
summary=dict(evidence_integrity='PASS',cases=len(cases),pairs=len(comp),source_hashes=len(report['source_hashes']),
    equal_outcomes=sum(c['outcome_equal'] for c in comp),equal_phases=sum(c['phases_equal'] for c in comp),
    target_equal={k:sum(c['target_equal'][k] for c in comp) for k in comp[0]['target_equal']},
    daemon_finished=sum(c['ok'] for c in cases if c['engine']=='daemon'),
    native_positive_controls=controls,stage_A='INCOMPLETE',security_acceptance='NOT RUN')
print(json.dumps(summary,indent=2))
