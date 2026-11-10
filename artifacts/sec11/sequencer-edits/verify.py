"""Recompute coverage, comparisons and positive controls from saved evidence."""
import hashlib
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
runner = runpy.run_path(str(HERE / 'run.py'))
report = json.loads((HERE / 'results.json').read_text())
cases = report['cases']
expected = {(f, o, m, e) for f in runner['FORMATS'] for o in runner['OPERATIONS']
            for m in runner['MODES'] for e in ('native', 'product')}
assert len(cases) == len(expected) == 40
assert {(c['format'], c['operation'], c['mode'], c['engine']) for c in cases} == expected
for path, value in report['source_hashes'].items():
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == value, path
comparisons = json.loads((HERE / 'comparison.json').read_text())
assert comparisons == [runner['compare'](n, p) for n, p in zip(cases[::2], cases[1::2], strict=True)]
controls = []
for c in cases:
    first, stage, last = c['steps']
    assert [s['label'] for s in c['steps']] == ['start', 'resolve_stage', 'continue']
    assert first['active_mode'] == stage['active_mode'] == 'observe'
    assert last['active_mode'] == c['mode']
    assert not first['ok'] and first['after']['unmerged']
    assert stage['ok'] and not stage['after']['unmerged']
    assert last['ok'], (c['format'], c['operation'], c['mode'], c['engine'], last['output'])
    final = last['after']
    assert not final['unmerged']
    assert final['symbolic_head'] == 'refs/heads/target'
    assert final['head'] == final['target'] != first['before']['target']
    assert not any(key.startswith(('rebase-', 'sequencer/', 'CHERRY_PICK_HEAD')) for key in final['state'])
    for s in c['steps']:
        for name in ('ignored.txt', 'untracked.txt'):
            assert s['before']['files'][name] == s['after']['files'][name]
        for state in (s['before'], s['after']):
            assert len(state['head']) == (40 if c['format'] == 'sha1' else 64)
        if c['engine'] == 'product':
            assert s['events'] == []
        for e in s['events']:
            assert e['cwd'] == 'CASE/repo'
            mode, phase = s['active_mode'], e['phase']
            action = ('message' if (mode, phase) in (('prepare_message', 'prepare-commit-msg'), ('commit_message', 'commit-msg'))
                      else 'index' if (mode, phase) in (('pre_index', 'pre-commit'), ('prepare_index', 'prepare-commit-msg')) else None)
            assert e['action'] == action
            if action == 'message':
                assert e['message_after'] == runner['MARKER'] + '\n\n' + e['message_before']
            elif e['message_before'] is not None:
                assert e['message_before'] == e['message_after']
    actions = [e for e in last['events'] if e['action']]
    if c['engine'] == 'native':
        phases = [e['phase'] for e in last['events']]
        assert phases.count('prepare-commit-msg') == phases.count('post-commit') == 1
        for phase_name in ('pre-commit', 'commit-msg'):
            assert phases.count(phase_name) == int(c['operation'] == 'cherry-pick')
        assert phases.count('post-rewrite') == int(c['operation'] == 'rebase')
        phase = {'prepare_message': 'prepare-commit-msg', 'commit_message': 'commit-msg',
                 'pre_index': 'pre-commit', 'prepare_index': 'prepare-commit-msg'}.get(c['mode'])
        invoked = phase is not None and any(e['phase'] == phase for e in last['events'])
        assert len(actions) == int(invoked)
        controls.append(dict(format=c['format'], operation=c['operation'], mode=c['mode'],
                             phase=phase, invoked=invoked,
                             head_message=final['head_message'], head_hook_content=final['head_hook_content'],
                             index_hook_content=final['index_hook_content']))
    else:
        assert not actions
    changed_message = any(e['action'] == 'message' for e in actions)
    changed_index = any(e['action'] == 'index' for e in actions)
    assert (runner['MARKER'] in final['head_message']) == changed_message
    # Measured Git behavior: prepare-commit-msg stages after the commit tree is
    # chosen; the edit survives in index/worktree, but is absent from HEAD.
    committed_index = changed_index and c['mode'] == 'pre_index'
    assert final['head_hook_content'] == (runner['CONTENT'].strip() if committed_index else '')
    assert final['index_hook_content'] == (runner['CONTENT'].strip() if changed_index else '')
    assert ('hook-added.txt' in final['files']) == changed_index
    if changed_index:
        assert final['files']['hook-added.txt'] == hashlib.sha256(runner['CONTENT'].encode()).hexdigest()
        assert 'hook-added.txt' in final['index']
    assert ('hook-added.txt' in final['tree_entries']) == committed_index
    assert ('A  hook-added.txt' in final['status']) == (changed_index and not committed_index)

# A mode absent in one operation must have a real positive control elsewhere.
for mode in runner['MODES'][1:]:
    for f in runner['FORMATS']:
        assert any(c['invoked'] for c in controls if c['mode'] == mode and c['format'] == f)
for c in comparisons:
    if c['mode'] == 'observe':
        assert all(c['steps'][-1]['state_equal'].values())
steps = [s for c in comparisons for s in c['steps']]
print(json.dumps(dict(evidence_integrity='PASS', cases=len(cases), steps=len(cases)*3,
                     step_pairs=len(steps), equal_outcomes=sum(s['outcome_equal'] for s in steps),
                     final_equal_pairs={field: sum(c['steps'][-1]['state_equal'][field] for c in comparisons)
                                        for field in steps[0]['state_equal']},
                     source_hashes=len(report['source_hashes']), positive_controls=controls,
                     security_acceptance='NOT RUN', stage_A='INCOMPLETE'), indent=2))
