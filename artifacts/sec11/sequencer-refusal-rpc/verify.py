"""Recompute coverage, positive controls, persisted journal and restart evidence."""
import hashlib
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
rpc = runpy.run_path(str(HERE / 'run.py'))
base = rpc['load'](HERE.parent / 'sequencer-census/run.py')
report = json.loads((HERE / 'results.json').read_text())
cases = report['cases']
expected = {(f, o, w, m, e) for f in rpc['FORMATS'] for o in base.OPERATIONS
            for w in base.FLOWS for m in base.MODES for e in ('native', 'daemon')}
assert len(cases) == len(expected) == 144
assert {(c['format'], c['operation'], c['flow'], c['mode'], c['engine']) for c in cases} == expected
for path, value in report['source_hashes'].items():
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == value, path
comparisons = json.loads((HERE / 'comparison.json').read_text())
assert comparisons == rpc['comparisons'](cases)
controls, restarts = [], []
for c in cases:
    first, last = c['steps'][0], c['steps'][-1]
    initial, final = first['before'], last['after']
    assert [s['label'] for s in c['steps']] == (
        ['start'] if c['flow'] == 'success' else ['start', 'resolve_stage', 'continue']
        if c['flow'] == 'continue' else ['start', 'abort'])
    if c['flow'] != 'success':
        assert first['active_mode'] == 'observe' and not first['ok'] and first['after']['unmerged']
    assert last['active_mode'] == c['mode']
    for s in c['steps']:
        for name in ('ignored.txt', 'untracked.txt'):
            assert s['before']['files'][name] == s['after']['files'][name]
        for state in (s['before'], s['after']):
            assert len(state['head']) == (40 if c['format'] == 'sha1' else 64)
        for e in s['events']:
            rows = [line.split() for line in (e['stdin'] or '').splitlines()]
            selected = [r for r in rows if len(r) == 3 and r[0] != r[1] and
                len(r[1]) in (40, 64) and all(x in '0123456789abcdef' for x in r[1]) and
                set(r[1]) != {'0'} and (r[2] == 'refs/heads/target' or
                (c['operation'] == 'cherry-pick' and r[2] == 'HEAD' and e['head_file'] == 'ref: refs/heads/target'))]
            assert e['target_rows'] == selected
            mode, phase = s['active_mode'], e['phase']
            refuse = ((mode, phase) in (('pre_rebase_refuse', 'pre-rebase'),
                ('post_rewrite_fail', 'post-rewrite'), ('post_commit_fail', 'post-commit')) or
                (mode == 'target_prepared_refuse' and phase == 'reference-transaction' and
                 e['args'] == ['prepared'] and bool(selected)) or
                (mode == 'target_committed_fail' and phase == 'reference-transaction' and
                 e['args'] == ['committed'] and bool(selected)))
            assert e['refuse'] == refuse
    if c['engine'] == 'native':
        refusals = [e for s in c['steps'] for e in s['events'] if e['refuse']]
        controls.append(dict(format=c['format'], operation=c['operation'], flow=c['flow'],
                             mode=c['mode'], refusal_events=len(refusals), ok=last['ok']))
        if c['mode'] == 'target_prepared_refuse':
            assert refusals and not last['ok']
            assert final['target'] == initial['target']
        elif c['flow'] == 'success' and c['mode'] == 'pre_rebase_refuse' and c['operation'] == 'rebase':
            assert refusals and not last['ok'] and final['head'] == initial['head']
        else:
            assert last['ok']
    else:
        assert all(s['events'] == [] for s in c['steps'])
        assert last['ok']
        assert first['journal_before'] == []
        assert first['rpc_method'] == ('git/rebase' if c['operation'] == 'rebase' else 'git/cherryPick')
        row = first['journal_after'][0]
        assert len(first['journal_after']) == 1
        assert row['kind'] == c['operation'] and row['source_oid'] == initial['head']
        assert first['response']['operation']['id'] == row['id']
        assert row['status'] == ('finished' if c['flow'] == 'success' else 'conflicted')
        for s in c['steps']:
            rows = s['journal_after']
            assert len(rows) == 1 and rows[0]['id'] == row['id']
            open_ids = [r['id'] for r in rows if r['status'] == 'conflicted']
            assert [o['id'] for o in s['rpc_state']['operations']] == open_ids
            assert f"{initial['head']} refs/aconsole/before/{row['id']}" in s['after']['refs'].splitlines()
            oldrefs = dict(line.split(' ', 1)[::-1] for line in initial['refs'].splitlines())
            newrefs = dict(line.split(' ', 1)[::-1] for line in s['after']['refs'].splitlines())
            assert set(newrefs) - set(oldrefs) == {f"refs/aconsole/before/{row['id']}"}
            assert newrefs['refs/heads/source'] == oldrefs['refs/heads/source']
        terminal = last['journal_after'][0]
        assert terminal['status'] == ('aborted' if c['flow'] == 'abort' else 'finished')
        assert not terminal['follow_up_pending']
        if c['flow'] != 'abort':
            assert terminal['result_oid'] == final['head']
        assert c['journal_after_stop'] == last['journal_after']
        assert [r['label'] for r in c['restarts']] == (['terminal'] if c['flow'] == 'success' else ['conflict', 'terminal'])
        for r in c['restarts']:
            assert r['journal_before'] == r['journal_after_stop'] == r['journal_after']
            assert r['events'] == []
            assert r['before'] == r['after'], (c['format'], c['operation'], c['flow'], c['mode'], r['label'])
            assert r['rpc_before']['operations'] == r['rpc_after']['operations']
            restarts.append(dict(format=c['format'], operation=c['operation'], flow=c['flow'],
                                 mode=c['mode'], label=r['label'], status=r['journal_after'][0]['status']))
        assert c['restarts'][-1]['rpc_after']['operations'] == []
    if c['flow'] == 'abort' and last['ok']:
        for key in ('head', 'target', 'symbolic_head', 'index', 'files'):
            assert final[key] == initial[key], (c['engine'], c['mode'], key)
    if last['ok']:
        assert not final['unmerged'] and final['symbolic_head'] == 'refs/heads/target'
        assert not any(key.startswith(('rebase-', 'sequencer/', 'CHERRY_PICK_HEAD')) for key in final['state'])
# Each injected refusal/failure must actually execute in a native positive control.
for f in rpc['FORMATS']:
    for mode in base.MODES[1:]:
        assert any(c['refusal_events'] for c in controls if c['format'] == f and c['mode'] == mode)
for c in comparisons:
    if c['mode'] == 'observe':
        assert all(c['steps'][-1]['state_equal'].values())
steps = [s for c in comparisons for s in c['steps']]
print(json.dumps(dict(evidence_integrity='PASS', cases=len(cases), steps=sum(len(c['steps']) for c in cases),
    step_pairs=len(steps), equal_outcomes=sum(s['outcome_equal'] for s in steps),
    equal_phases=sum(s['phases_equal'] for s in steps),
    final_equal_pairs={field: sum(c['steps'][-1]['state_equal'][field] for c in comparisons)
                       for field in steps[0]['state_equal']},
    source_hashes=len(report['source_hashes']), positive_controls=controls,
    graceful_restarts=len(restarts), restart_states=restarts,
    security_acceptance='NOT RUN', stage_A='INCOMPLETE'), indent=2))
