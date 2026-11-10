"""Check saved experiment coverage and integrity, not Git containment."""

import hashlib
import json
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
runner = runpy.run_path(str(HERE / 'run.py'))
report = json.loads((HERE / 'results.json').read_text())
cases = report['cases']
comparisons = json.loads((HERE / 'comparison.json').read_text())
expected = {(engine, operation, flow, mode) for engine in ('native', 'product')
            for operation in runner['OPERATIONS'] for flow in runner['FLOWS'] for mode in runner['MODES']}
assert len(cases) == len(expected) == 72
assert {(c['engine'], c['operation'], c['flow'], c['mode']) for c in cases} == expected
for relative, expected_hash in report['source_hashes'].items():
    assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected_hash, relative
assert comparisons == [runner['compare'](n, p) for n, p in zip(cases[::2], cases[1::2], strict=True)]
for case in cases:
    steps = case['steps']
    labels = ['start'] if case['flow'] == 'success' else (
        ['start', 'resolve_stage', 'continue'] if case['flow'] == 'continue' else ['start', 'abort'])
    assert [s['label'] for s in steps] == labels
    if case['flow'] != 'success':
        assert not steps[0]['ok'] and steps[0]['after']['unmerged']
        assert steps[0]['active_mode'] == 'observe'
    for step in steps:
        for name in ('ignored.txt', 'untracked.txt'):
            assert step['before']['files'][name] == step['after']['files'][name]
        if case['engine'] == 'product':
            assert step['events'] == [], (case, step)
        for event in step['events']:
            assert event['cwd'] == 'CASE/repo'
            rows = [line.split() for line in (event['stdin'] or '').splitlines()]
            target_rows = [row for row in rows if len(row) == 3 and row[0] != row[1]
                           and len(row[1]) == 40 and all(c in '0123456789abcdef' for c in row[1])
                           and set(row[1]) != {'0'} and (row[2] == 'refs/heads/target' or
                           (case['operation'] == 'cherry-pick' and row[2] == 'HEAD'
                            and event['head_file'] == 'ref: refs/heads/target'))]
            assert event['target_rows'] == target_rows
            mode, phase, args = step['active_mode'], event['phase'], event['args']
            refusal = ((mode == 'pre_rebase_refuse' and phase == 'pre-rebase') or
                       (mode == 'post_rewrite_fail' and phase == 'post-rewrite') or
                       (mode == 'post_commit_fail' and phase == 'post-commit') or
                       (mode == 'target_prepared_refuse' and phase == 'reference-transaction'
                        and args == ['prepared'] and bool(target_rows)) or
                       (mode == 'target_committed_fail' and phase == 'reference-transaction'
                        and args == ['committed'] and bool(target_rows)))
            assert event['refuse'] == refusal
    before, after = steps[0]['before'], steps[-1]['after']
    refusals = [e for e in steps[-1]['events'] if e['refuse']]
    if case['engine'] == 'product':
        assert steps[-1]['ok']
    elif case['mode'] == 'target_prepared_refuse':
        assert refusals and not steps[-1]['ok']
        assert before['target'] == after['target']
        assert all(e['target_rows'] and e['args'] == ['prepared'] for e in refusals)
        if case['operation'] == 'rebase':
            assert all(row[2] == 'refs/heads/target' for e in refusals for row in e['target_rows'])
    elif case['mode'] == 'target_committed_fail':
        assert refusals and steps[-1]['ok']
    elif case['mode'] == 'pre_rebase_refuse' and case['operation'] == 'rebase' and case['flow'] == 'success':
        assert refusals and not steps[-1]['ok']
        for field in ('head', 'target', 'index', 'files'):
            assert before[field] == after[field]
    elif case['mode'] == 'post_commit_fail' and case['flow'] != 'abort':
        assert refusals and steps[-1]['ok']
    elif case['mode'] == 'post_rewrite_fail' and case['operation'] == 'rebase' and case['flow'] != 'abort':
        assert refusals and steps[-1]['ok']
    if case['flow'] == 'abort' and steps[-1]['ok']:
        for field in ('head', 'target', 'refs', 'symbolic_head', 'index', 'files'):
            assert before[field] == after[field], (case, field)
        assert not after['unmerged']
        assert not any(key.startswith(('rebase-', 'sequencer/', 'CHERRY_PICK_HEAD')) for key in after['state'])
    if case['mode'] == 'observe':
        assert steps[-1]['ok']
        assert not after['unmerged']
        assert after['symbolic_head'] == 'refs/heads/target'

flat = [s for c in comparisons for s in c['steps']]
summary = {'evidence_integrity': 'PASS', 'cases': len(cases), 'pairs': len(comparisons),
           'steps': sum(len(c['steps']) for c in cases), 'source_hashes': len(report['source_hashes']),
           'equal_step_pairs': {key: sum(s[key] for s in flat) for key in ('outcome_equal', 'phases_equal')},
           'final_equal_pairs': {field: sum(c['steps'][-1]['state_equal'][field] for c in comparisons)
                                 for field in flat[0]['state_equal']},
           'refusals': [{'operation': c['operation'], 'flow': c['flow'], 'mode': c['mode'],
                         'step': s['label'], 'phase': e['phase'], 'args': e['args'],
                         'target_rows': e['target_rows']}
                        for c in cases for s in c['steps'] for e in s['events'] if e['refuse']],
           'security_acceptance': 'NOT RUN'}
print(json.dumps(summary, indent=2))
