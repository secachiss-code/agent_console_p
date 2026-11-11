"""SEC-24 disposable no-checkout merge RPC compatibility evidence."""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
FORMATS = ('sha1', 'sha256')
SCENARIOS = ('merge_sample', 'merge_direct', 'squash')
MODES = ('observe', 'pre_refuse', 'prepare_refuse', 'post_fail', 'ref_prepared', 'ref_committed')
MESSAGE = 'SEC24 merge subject\n'
HOOK = r'''#!{python}
import json, os, sys
from pathlib import Path
phase = Path(sys.argv[0]).name
mode = os.environ['REF_MODE']
stdin = sys.stdin.read() if phase == 'reference-transaction' else None
target = stdin is not None and any(line.split()[-1:] == ['refs/heads/target'] for line in stdin.splitlines())
refuse = ((mode == 'pre_refuse' and phase in ('pre-commit', 'pre-merge-commit')) or
          (mode == 'prepare_refuse' and phase == 'prepare-commit-msg') or
          (mode == 'post_fail' and phase in ('post-commit', 'post-merge')) or
          (mode == 'ref_prepared' and target and sys.argv[1:] == ['prepared']) or
          (mode == 'ref_committed' and target and sys.argv[1:] == ['committed']))
with open(os.environ['REF_TRACE'], 'a') as stream:
    stream.write(json.dumps(dict(phase=phase, args=sys.argv[1:], stdin=stdin, target=target,
        refuse=refuse, cwd=os.getcwd(), git_dir=os.environ.get('GIT_DIR'),
        index=os.environ.get('GIT_INDEX_FILE'))) + '\n')
if refuse:
    print('SEC24 refusal: ' + phase + ' ' + ' '.join(sys.argv[1:]), file=sys.stderr)
    sys.exit(1)
'''


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    spec = importlib.util.spec_from_file_location('sec16', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def journal(folder):
    with sqlite3.connect(folder / 'aconsole.db') as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute('SELECT * FROM git_operations ORDER BY id')]


def snapshot(ext, repo):
    result = ext.snapshot(repo)
    result.update(refs=ext.git(repo, 'for-each-ref', '--format=%(objectname) %(refname)'),
                  symbolic_head=ext.git(repo, 'symbolic-ref', 'HEAD'),
                  target_parents=ext.git(repo, 'log', '-1', '--format=%P', 'target'),
                  target_entries=ext.git(repo, 'ls-tree', '-r', 'target'))
    return result


def setup(ext, folder, engine, scenario, mode, fmt):
    os.environ['GIT_DEFAULT_HASH'] = fmt
    repo, source, target, tree = ext.setup(folder, 'ref_squash' if scenario == 'squash' else 'ref_merge')
    assert ext.git(repo, 'rev-parse', '--show-object-format') == fmt
    if engine == 'native':
        ext.git(repo, 'checkout', '-q', 'target')
    (repo / 'base.txt').write_text('unstaged must survive\n')
    (repo / 'untracked.txt').write_text('preserve untracked\n')
    (repo / '.git/info/exclude').write_text('ignored.txt\n')
    (repo / 'ignored.txt').write_text('preserve ignored\n')
    sample = repo / '.git/hooks/pre-merge-commit.sample'
    for phase in ext.PHASES:
        hook = repo / '.git/hooks' / phase
        hook.write_bytes(sample.read_bytes() if scenario == 'merge_sample' and phase == 'pre-merge-commit'
                         else HOOK.format(python=sys.executable).encode())
        hook.chmod(0o700)
    trace = folder / 'trace.jsonl'
    os.environ.update(REF_TRACE=str(trace), REF_MODE=mode)
    return repo, source, target, tree, trace, digest(sample)


async def run_case(ext, folder, engine, scenario, mode, fmt):
    repo, source, target, tree, trace, sample = setup(ext, folder, engine, scenario, mode, fmt)
    mode_git = 'squash' if scenario == 'squash' else 'merge'
    before = snapshot(ext, repo)
    record = dict(engine=engine, scenario=scenario, mode=mode, format=fmt, before=before,
                  source=source, target=target, expected_tree=tree, sample_sha256=sample)
    if engine == 'native':
        trace.unlink(missing_ok=True)
        commands = []
        args = ['merge', '--squash', source] if mode_git == 'squash' else ['merge', '--no-ff', '-m', MESSAGE, source]
        for argv in (args, ['commit', '-F', '-']) if mode_git == 'squash' else (args,):
            done = subprocess.run(['/usr/bin/git', '-C', str(repo), *argv], input=MESSAGE,
                                  capture_output=True, text=True, timeout=30, check=False)
            commands.append(dict(argv=argv, code=done.returncode, stdout=done.stdout, stderr=done.stderr))
            if done.returncode:
                break
        record.update(ok=done.returncode == 0, commands=commands)
    else:
        import msgspec
        from aconsole.contracts.git import GitOperationParams, GitOperationResult, GitStateParams, GitStateResult, MergeWorktreeParams
        from tests.core.test_git_merge import _start, _preview
        from tests.core.test_wire import _stop
        daemon, task, client, project = await asyncio.wait_for(_start(folder, repo), 30)
        try:
            preview = await asyncio.wait_for(_preview(client, project, 'source', 'target'), 45)
            assert preview.target_worktree is None and preview.blockers == ()
            record['preview'] = msgspec.to_builtins(preview)
            record['journal_before'] = journal(folder)
            record['before'] = snapshot(ext, repo)
            trace.unlink(missing_ok=True)
            params = MergeWorktreeParams(project_id=project, source=preview.source, target=preview.target,
                source_oid=preview.source_oid, target_oid=preview.target_oid, mode=mode_git,
                confirm_token=preview.confirm_token, message=MESSAGE)
            response = await asyncio.wait_for(client.call('git/mergeWorktree', params, GitOperationResult), 45)
            record.update(ok=response.operation.status == 'finished', response=msgspec.to_builtins(response))
            record['events'] = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
            record['after'] = snapshot(ext, repo)
            record['journal_after'] = journal(folder)
            record['rpc_state'] = msgspec.to_builtins(await asyncio.wait_for(client.call('git/state', GitStateParams(project_id=project), GitStateResult), 45))
            record['rpc_operation'] = msgspec.to_builtins(await asyncio.wait_for(client.call('git/operation', GitOperationParams(operation_id=response.operation.id), GitOperationResult), 45))
        finally:
            await asyncio.wait_for(_stop(daemon, task, client), 45)
        record['journal_after_stop'] = journal(folder)
    if engine == 'native':
        record['events'] = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        record['after'] = snapshot(ext, repo)
    return json.loads(json.dumps(record).replace(str(folder), 'CASE'))


def comparisons(cases):
    return [dict(format=n['format'], scenario=n['scenario'], mode=n['mode'],
                 outcome_equal=n['ok'] == d['ok'],
                 phases_equal=[e['phase'] for e in n['events']] == [e['phase'] for e in d['events']],
                 target_equal={k:n['after'][k] == d['after'][k] for k in ('target', 'target_tree', 'target_message', 'target_parents', 'target_entries')})
            for n,d in zip(cases[::2], cases[1::2], strict=True)]


def main():
    with tempfile.TemporaryDirectory(prefix='sec24-') as temporary:
        root = Path(temporary)
        home = root / 'home'
        home.mkdir()
        runtime = root / 's'
        runtime.mkdir(mode=0o700)
        for key in list(os.environ):
            if key.startswith(('GIT_', 'REF_', 'EXT_', 'XDG_', 'ACONSOLE_')):
                del os.environ[key]
        os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'), XDG_DATA_HOME=str(home / 'data'),
            XDG_STATE_HOME=str(home / 'state'), XDG_CACHE_HOME=str(home / 'cache'), XDG_RUNTIME_DIR=str(runtime),
            ACONSOLE_TEST_SOCKET_DIR=str(runtime), GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
            LC_ALL='C', GIT_TERMINAL_PROMPT='0', GIT_EDITOR='/usr/bin/true',
            GIT_AUTHOR_DATE='2026-01-01T00:00:00+00:00', GIT_COMMITTER_DATE='2026-01-01T00:00:00+00:00')
        ext = load(HERE.parent / 'hook-census-extended/run.py')
        original_git = ext.git
        ext.git = lambda repo, *args, **kwargs: original_git(repo, '-c', 'core.hooksPath=/dev/null', *args, **kwargs)
        sources = [*sorted((ROOT / 'src').rglob('*.py')), HERE / 'run.py', HERE / 'verify.py',
            HERE.parent / 'hook-census/run.py', HERE.parent / 'hook-census-extended/run.py',
            ROOT / 'artifacts/sec11/race_probe.py', ROOT / 'tests/core/test_git_host_exec.py',
            ROOT / 'tests/core/test_git_security_environment.py', *sorted((HERE.parent / 'config-namespace').glob('*.py')),
            *[ROOT / f'tests/core/{p}.py' for p in ('test_git_merge','test_wire','conftest','git_helpers')], ROOT / 'tests/socket_paths.py']
        hashes = {str(p.relative_to(ROOT)):digest(p) for p in sources}
        cases=[]
        for fmt in FORMATS:
            for scenario in SCENARIOS:
                for mode in MODES:
                    for engine in ('native','daemon'):
                        folder=root / f'{fmt}-{scenario}-{mode}-{engine}'
                        folder.mkdir()
                        case=asyncio.run(run_case(ext,folder,engine,scenario,mode,fmt))
                        cases.append(case)
                        print(fmt,scenario,mode,engine,case['ok'],flush=True)
                        (HERE / 'partial.json').write_text(json.dumps(cases,indent=2)+'\n')
        assert all(digest(ROOT / p)==h for p,h in hashes.items())
        report=dict(base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            python=sys.version,git=subprocess.check_output(['/usr/bin/git','--version'],text=True).strip(),
            source_hashes=hashes,cases=cases,scope='disposable daemon RPC compatibility; no installed runtime or security acceptance')
        (HERE / 'results.json').write_text(json.dumps(report,indent=2)+'\n')
        (HERE / 'comparison.json').write_text(json.dumps(comparisons(cases),indent=2)+'\n')
        (HERE / 'partial.json').unlink()
        print(f'Saved {len(cases)} cases; source hashes unchanged',flush=True)

if __name__=='__main__':
    main()
