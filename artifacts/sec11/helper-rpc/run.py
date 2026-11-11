"""SEC-27: disposable helper compatibility through real daemon RPC."""
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
OPERATIONS = ('stage', 'restore', 'merge', 'probe')
MODES = ('success', 'fail')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    spec = importlib.util.spec_from_file_location('sec20', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def journal(folder):
    with sqlite3.connect(folder / 'aconsole.db') as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute('SELECT * FROM git_operations ORDER BY id')]


def snapshot(ext, repo):
    result = ext.snapshot(repo)
    result['index_sha256'] = digest(repo / '.git/index')
    refs = ext.git(repo, 'for-each-ref', '--format=%(refname)', 'refs/aconsole/safety').splitlines()
    result['safety'] = {ref: dict(oid=ext.git(repo, 'rev-parse', ref),
        tree=ext.git(repo, 'rev-parse', ref + '^{tree}'),
        content=ext.git(repo, 'show', ref + ':data.txt') + '\n') for ref in refs}
    return result


async def run_case(ext, folder, fmt, operation, mode, engine):
    if engine == 'native':
        return ext.run_case(folder, fmt, operation, mode, engine)
    import msgspec
    from aconsole.contracts.git import (GitChangeResult, GitPathsParams, RestoreFileParams,
        RestoreFilePreviewParams, RestoreFilePreviewResult, MergeWorktreeParams)
    from tests.core.test_git_merge import _start, _preview
    from tests.core.test_git_operations import _row
    from tests.core.test_wire import _stop

    repo = ext.setup(folder, fmt, operation)
    trace = folder / 'trace.jsonl'
    os.environ.update(CENSUS_MODE=mode, CENSUS_TRACE=str(trace))
    record = dict(format=fmt, operation=operation, mode=mode, engine=engine,
        before_start=snapshot(ext, repo), candidate_tree=None, candidate_content=None)
    daemon, task, client, project = await asyncio.wait_for(_start(folder, repo), 30)
    try:
        record['before'] = snapshot(ext, repo)
        record['journal_before'] = journal(folder)
        if operation == 'stage':
            row = await asyncio.wait_for(_row(client, project, repo), 30)
            response = await asyncio.wait_for(client.call('git/stage', GitPathsParams(
                project_id=project, worktree=row.path, paths=('data.txt',),
                expect_head=row.head_oid, expect_index=row.index_digest), GitChangeResult), 30)
            record.update(ok=response.ok, said=response.detail, response=msgspec.to_builtins(response))
        elif operation == 'restore':
            preview = await asyncio.wait_for(client.call('git/restoreFilePreview', RestoreFilePreviewParams(
                project_id=project, worktree=str(repo), path='data.txt',
                source_oid=record['before']['head']), RestoreFilePreviewResult), 30)
            record['preview'] = msgspec.to_builtins(preview)
            response = await asyncio.wait_for(client.call('git/restoreFile', RestoreFileParams(
                project_id=project, worktree=str(repo), path='data.txt', source_oid=preview.source_oid,
                confirm_token=preview.confirm_token), GitChangeResult), 30)
            record.update(ok=response.ok, said=response.detail, response=msgspec.to_builtins(response))
        else:
            preview = await asyncio.wait_for(_preview(client, project, 'source', 'target'), 30)
            record['preview'] = msgspec.to_builtins(preview)
            record.update(ok=preview.probe.status != 'conflict', said=str(preview.blockers))
            if operation == 'merge':
                error = await asyncio.wait_for(client.error('git/mergeWorktree', MergeWorktreeParams(
                    project_id=project, source=preview.source, target=preview.target,
                    source_oid=preview.source_oid, target_oid=preview.target_oid, mode='merge',
                    confirm_token=preview.confirm_token, message='merge census')), 30)
                record.update(ok=False, merge_error=error)
        record['after'] = snapshot(ext, repo)
        record['journal_after'] = journal(folder)
        record['events'] = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    finally:
        await asyncio.wait_for(_stop(daemon, task, client), 45)
    record['journal_after_stop'] = journal(folder)
    record['after_stop'] = snapshot(ext, repo)
    return json.loads(json.dumps(record).replace(str(folder), 'CASE'))


def comparisons(cases):
    fields = ('head', 'head_tree', 'head_content', 'index', 'index_content', 'file', 'ignored', 'untracked')
    return [dict(format=n['format'], operation=n['operation'], mode=n['mode'],
        outcome_equal=n['ok'] == d['ok'],
        state_equal={k: n['after'][k] == d['after'][k] for k in fields},
        candidate_equal=n['candidate_tree'] == d['candidate_tree'])
        for n, d in zip(cases[::2], cases[1::2], strict=True)]


def main():
    with tempfile.TemporaryDirectory(prefix='sec27-') as temporary:
        root = Path(temporary)
        home = root / 'home'
        home.mkdir()
        runtime = root / 's'
        runtime.mkdir(mode=0o700)
        for key in list(os.environ):
            if key.startswith(('GIT_', 'CENSUS_', 'XDG_', 'ACONSOLE_')):
                del os.environ[key]
        os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
            XDG_DATA_HOME=str(home / 'data'), XDG_STATE_HOME=str(home / 'state'),
            XDG_CACHE_HOME=str(home / 'cache'), XDG_RUNTIME_DIR=str(runtime),
            ACONSOLE_TEST_SOCKET_DIR=str(runtime), GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
            LC_ALL='C', GIT_TERMINAL_PROMPT='0', GIT_EDITOR='/usr/bin/true',
            GIT_AUTHOR_DATE='2026-01-01T00:00:00+00:00', GIT_COMMITTER_DATE='2026-01-01T00:00:00+00:00')
        ext = load(HERE.parent / 'helper-census/run.py')
        sources = [*sorted((ROOT / 'src').rglob('*.py')), HERE / 'run.py', HERE / 'verify.py',
            HERE.parent / 'helper-census/run.py', ROOT / 'artifacts/sec11/race_probe.py',
            ROOT / 'tests/core/test_git_host_exec.py', ROOT / 'tests/core/test_git_security_environment.py',
            *sorted((HERE.parent / 'config-namespace').glob('*.py')),
            *[ROOT / f'tests/core/{p}.py' for p in ('test_git_merge','test_git_operations','test_wire','conftest','git_helpers')],
            ROOT / 'tests/socket_paths.py']
        hashes = {str(p.relative_to(ROOT)): digest(p) for p in sources}
        cases = []
        for fmt in FORMATS:
            for operation in OPERATIONS:
                for mode in MODES:
                    for engine in ('native', 'daemon'):
                        folder = root / f'{fmt}-{operation}-{mode}-{engine}'
                        folder.mkdir()
                        case = asyncio.run(run_case(ext, folder, fmt, operation, mode, engine))
                        cases.append(case)
                        print(fmt, operation, mode, engine, case['ok'], flush=True)
                        (HERE / 'partial.json').write_text(json.dumps(cases, indent=2) + '\n')
        assert all(digest(ROOT / p) == h for p, h in hashes.items())
        report = dict(base_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
            python=sys.version, git=subprocess.check_output(['/usr/bin/git','--version'], text=True).strip(),
            source_hashes=hashes, cases=cases, scope='Disposable RPC compatibility; no security acceptance')
        (HERE / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
        (HERE / 'comparison.json').write_text(json.dumps(comparisons(cases), indent=2) + '\n')
        (HERE / 'partial.json').unlink()
        print(f'Saved {len(cases)} cases; source hashes unchanged', flush=True)


if __name__ == '__main__':
    main()
