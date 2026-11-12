"""Recompute SEC-26 evidence integrity separately from compatibility acceptance."""
import hashlib
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
rpc = runpy.run_path(str(HERE / 'run.py'))
report = json.loads((HERE / 'results.json').read_text())
cases = report['cases']
expected = {(f, o, m, e) for f in rpc['FORMATS'] for o in rpc['OPERATIONS']
            for m in rpc['MODES'] for e in ('native', 'daemon')}
assert len(cases) == len(expected) == 48
assert {(c['format'], c['operation'], c['mode'], c['engine']) for c in cases} == expected
for path, value in report['source_hashes'].items():
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == value, path
comparisons = json.loads((HERE / 'comparison.json').read_text())
assert comparisons == rpc['comparisons'](cases)
for c in cases:
    before, after = c['before'], c['after']
    for field in ('head', 'symbolic_head', 'index', 'index_sha256', 'source_lock_sha256'):
        assert before[field] == after[field], (c['operation'], field)
    assert before['files']['ignored.txt'] == after['files']['ignored.txt']
    assert len(after['head']) == (40 if c['format'] == 'sha1' else 64)
    for e in c['events']:
        selected = [r for r in [line.split() for line in (e['stdin'] or '').splitlines()]
            if len(r) == 3 and r[2].startswith(('refs/heads/ac/', 'refs/aconsole/checkpoints/', 'refs/aconsole/sessions/', 'refs/aconsole/executors/'))]
        assert e['target_rows'] == selected
        refuse = ((e['phase'] == 'reference-transaction' and bool(selected) and
            ((c['mode'] == 'prepared_refuse' and e['args'] == ['prepared']) or
             (c['mode'] == 'committed_fail' and e['args'] == ['committed']))) or
            (e['phase'] == 'post-checkout' and c['mode'] == 'post_checkout_fail'))
        assert e['refuse'] == refuse
    if c['engine'] == 'native':
        assert c['events']
        assert c['ok'] == (c['mode'] in ('observe', 'committed_fail'))
        if c['mode'] != 'observe':
            assert any(e['refuse'] for e in c['events'])
        if c['mode'] == 'prepared_refuse':
            assert after['refs'] == before['refs']
            assert after['files'] == before['files']
            assert not after['dest_exists']
    else:
        assert c['ok'] and not c['events']
        assert c['rows_after'] == c['rows_after_stop']
        assert not c['rows_after']['git_operations']
        if c['operation'] == 'restore':
            points = c['rows_after']['checkpoints']
            assert len(c['rows_before']['checkpoints']) == 1 and len(points) == 2
            assert len(c['listing_after']['checkpoints']) == 2
            assert c['response']['restored']
            assert c['response']['safety_checkpoint'].endswith('-2')
            for row in points:
                assert row['git_ref'] in after['checkpoint_trees']
                assert row['tree_sha'] == after['checkpoint_tree_oids'][row['git_ref']]
                assert '.aconsole/lock' not in after['checkpoint_trees'][row['git_ref']]
                assert 'ignored.txt' not in after['checkpoint_trees'][row['git_ref']]
        else:
            assert len(c['rows_after']['tabs']) == (2 if c['operation'] == 'detached' else 1)
            tab = next(row for row in c['rows_after']['tabs'] if row['id'] == c['response']['id'])
            workspace = next(r for r in c['rows_after']['workspaces'] if r['id'] == tab['workspace_id'])
            assert workspace['path'] == c['response']['worktree_path']
            assert after['dest_exists'] and after['dest_lock'] and after['metadata']
            assert after['dest_files']['tracked.txt'] == before['files']['tracked.txt']
    if c['operation'] == 'restore':
        restored = c['engine'] == 'daemon' or c['mode'] in ('observe', 'committed_fail')
        if restored:
            assert after['files']['tracked.txt'] == hashlib.sha256(b'snapshot\n').hexdigest()
            assert after['files']['untracked.txt'] == hashlib.sha256(b'untracked original\n').hexdigest()
            assert 'extra.txt' not in after['files']
        elif c['mode'] == 'post_checkout_fail':
            assert after['files']['tracked.txt'] == hashlib.sha256(b'snapshot\n').hexdigest()
            assert after['files']['untracked.txt'] == before['files']['untracked.txt']
            assert after['files']['extra.txt'] == before['files']['extra.txt']
print(json.dumps(dict(evidence_integrity='PASS', cases=len(cases), pairs=len(comparisons),
    outcome_equal=sum(c['outcome_equal'] for c in comparisons),
    phases_equal=sum(c['phases_equal'] for c in comparisons),
    source_hashes=len(report['source_hashes']), security_acceptance=False,
    stage_a_complete=False), indent=2))
