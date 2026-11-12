"""Verify saved coverage, controls and evidence; not security acceptance."""

import hashlib
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
runner = runpy.run_path(str(HERE / 'run.py'))
report = json.loads((HERE / 'results.json').read_text())
cases = report['cases']
comparisons = json.loads((HERE / 'comparison.json').read_text())
expected = {(engine, operation, mode, fmt) for engine in ('native', 'product')
            for operation in runner['OPERATIONS'] for mode in runner['MODES'] for fmt in runner['FORMATS']}
assert len(cases) == len(expected) == 80
assert {(c['engine'], c['operation'], c['mode'], c['format']) for c in cases} == expected
for path, value in report['source_hashes'].items():
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == value, path
assert comparisons == [runner['compare'](n, p) for n, p in zip(cases[::2], cases[1::2], strict=True)]
for case in cases:
    before, after = case['before'], case['after']
    operation, mode, engine = case['operation'], case['mode'], case['engine']
    for field in ('head', 'symbolic_head', 'index', 'index_sha256', 'source_lock_sha256'):
        assert before[field] == after[field], (operation, mode, engine, field)
    assert before['files']['ignored.txt'] == after['files']['ignored.txt']
    if operation != 'materialize':
        assert before['files'] == after['files']
    for event in case['events']:
        assert event['phase'] not in ('pre-commit', 'prepare-commit-msg', 'commit-msg', 'post-commit')
        rows = [line.split() for line in (event['stdin'] or '').splitlines()]
        targets = [r for r in rows if len(r) == 3 and r[2] in (
            'refs/heads/ac/census-tab', runner['REF'], runner['SAFETY'])]
        assert event['target_rows'] == targets
        for row in targets:
            assert len(row[0]) == len(row[1]) == (40 if case['format'] == 'sha1' else 64)
        refusal = ((event['phase'] == 'reference-transaction' and bool(targets) and
                    ((mode == 'prepared_refuse' and event['args'] == ['prepared']) or
                     (mode == 'committed_fail' and event['args'] == ['committed']))) or
                   (event['phase'] == 'post-checkout' and mode == 'post_checkout_fail'))
        assert event['refuse'] == refusal
        if event['phase'] == 'post-index-change' and operation == 'capture':
            assert event['index'] == 'PRIVATE_INDEX'
    refusals = [e for e in case['events'] if e['refuse']]
    if engine == 'product':
        assert case['ok'] and not case['events']
    elif mode == 'prepared_refuse' and operation != 'materialize':
        assert not case['ok'] and refusals
        assert before['refs'] == after['refs']
        assert not after['dest_exists']
    elif mode == 'committed_fail' and operation != 'materialize':
        assert case['ok'] and refusals
    elif mode == 'post_checkout_fail' and operation in ('worktree', 'detached', 'materialize'):
        assert not case['ok'] and len(refusals) == 1
        if operation in ('worktree', 'detached'):
            assert after['dest_exists'] and after['dest_files'] == {'tracked.txt': before['files']['tracked.txt']}
        else:
            # Restore writes the first path, then its post-checkout fails: later files/removals remain.
            assert after['files']['tracked.txt'] == hashlib.sha256(b'snapshot\n').hexdigest()
            for name in ('extra.txt', 'untracked.txt'):
                assert after['files'][name] == before['files'][name]
    else:
        assert case['ok'] and not refusals
    if operation in ('worktree', 'detached') and case['ok']:
        assert after['dest_exists']
        assert after['dest_files'] == {'tracked.txt': before['files']['tracked.txt']}
        assert after['dest_lock'] == (engine == 'product')
        assert bool(after['metadata']) == (engine == 'product')
    if operation == 'materialize' and case['ok']:
        assert after['files']['tracked.txt'] == hashlib.sha256(b'snapshot\n').hexdigest()
        assert after['files']['untracked.txt'] == hashlib.sha256(b'untracked original\n').hexdigest()
        assert 'extra.txt' not in after['files']
    if operation == 'capture' and case['ok']:
        assert 'tracked.txt' in after['checkpoint_tree'] and 'untracked.txt' in after['checkpoint_tree']
        assert '.aconsole/lock' not in after['checkpoint_tree']
        assert 'ignored.txt' not in after['checkpoint_tree']
    if operation == 'delete' and case['ok']:
        assert runner['REF'] not in after['refs']
    if operation == 'materialize':
        assert before['refs'] == after['refs']
    if engine == 'native' and operation in ('worktree', 'detached') and case['ok']:
        assert any(e['phase'] == 'post-checkout' for e in case['events'])
    if engine == 'native' and operation == 'delete':
        # update-ref -d without expected old OID requests all-zero old/new rows in this Git.
        targets = [r for e in case['events'] for r in e['target_rows']]
        assert targets and all(set(r[0]) == set(r[1]) == {'0'} for r in targets)
    if mode == 'observe':
        assert case['ok']

summary = {'evidence_integrity': 'PASS', 'cases': len(cases), 'pairs': len(comparisons),
           'source_hashes': len(report['source_hashes']),
           'equal_outcomes': sum(c['outcome_equal'] for c in comparisons),
           'equal_phases': sum(c['phases_equal'] for c in comparisons),
           'equal_states': {field: sum(c['state_equal'][field] for c in comparisons)
                            for field in comparisons[0]['state_equal']},
           'native_refusals': [{'operation': c['operation'], 'mode': c['mode'], 'format': c['format'],
                                'phases': [e['phase'] for e in c['events'] if e['refuse']]}
                               for c in cases if any(e['refuse'] for e in c['events'])],
           'security_acceptance': 'NOT RUN', 'stage_A': 'INCOMPLETE'}
print(json.dumps(summary, indent=2))
