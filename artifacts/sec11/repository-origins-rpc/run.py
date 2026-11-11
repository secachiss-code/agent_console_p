"""SEC-28: disposable repository origins through real daemon Unix RPC."""
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
ORIGINS = ('linked', 'unborn', 'bare')
MODES = ('observe', 'pre-refuse', 'ref-refuse')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load():
    spec = importlib.util.spec_from_file_location('sec21', HERE.parent / 'repository-origins/run.py')
    ext = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ext)
    return ext


def database(folder):
    with sqlite3.connect(folder / 'aconsole.db') as connection:
        connection.row_factory = sqlite3.Row
        return {table: [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]
                for table in ('projects', 'git_operations')}


def events(trace):
    return [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []


async def run_case(ext, folder, fmt, origin, mode, engine):
    if engine == 'native':
        return ext.run_case(folder, fmt, origin, mode, engine)
    import msgspec
    from aconsole.contracts.app import ProjectAddParams, ProjectView
    from aconsole.contracts.git import GitChangeResult, GitPathsParams, GitCommitParams
    from aconsole.contracts.ipc import ResponseErr
    from tests.core.test_git_operations import _row
    from tests.core.test_wire import _daemon, _connect, _stop

    repo = ext.setup(folder, fmt, origin)
    trace = folder / 'trace.jsonl'
    os.environ.update(CENSUS_MODE=mode, CENSUS_TRACE=str(trace))
    record = dict(format=fmt, origin=origin, mode=mode, engine=engine, steps=[],
                  before_start=ext.snapshot(repo, origin))
    daemon = _daemon(folder, serve_ingest=False)
    task = asyncio.create_task(daemon.serve())
    client = await asyncio.wait_for(_connect(daemon.socket), 30)
    try:
        record['database_before_add'] = database(folder)
        reply = await asyncio.wait_for(client.raw('project/add', ProjectAddParams(root=str(repo))), 30)
        if isinstance(reply, ResponseErr):
            record['project_error'] = msgspec.to_builtins(reply.error)
        else:
            project = msgspec.json.decode(reply.result, type=ProjectView)
            record['project'] = msgspec.to_builtins(project)
            record['after_add'] = ext.snapshot(repo, origin)
            record['startup_events'] = events(trace)
            for operation in ('stage', 'commit'):
                trace.unlink(missing_ok=True)
                before = ext.snapshot(repo, origin)
                row = await asyncio.wait_for(_row(client, project.id, repo), 30)
                freshness = dict(project_id=project.id, worktree=row.path,
                                 expect_head=row.head_oid, expect_index=row.index_digest)
                params = (GitPathsParams(**freshness, paths=('data.txt',)) if operation == 'stage'
                          else GitCommitParams(**freshness, message='origin census\n'))
                response = await asyncio.wait_for(client.call('git/' + operation, params, GitChangeResult), 30)
                record['steps'].append(dict(operation=operation, ok=response.ok, said=response.detail,
                    response=msgspec.to_builtins(response), row=msgspec.to_builtins(row), before=before,
                    after=ext.snapshot(repo, origin), events=events(trace), database=database(folder)))
        record['after'] = ext.snapshot(repo, origin)
        record['database_after'] = database(folder)
        record['last_events'] = events(trace)
    finally:
        await asyncio.wait_for(_stop(daemon, task, client), 45)
    record['after_stop'] = ext.snapshot(repo, origin)
    record['database_after_stop'] = database(folder)
    return json.loads(json.dumps(record).replace(str(folder), 'CASE'))


def comparisons(cases):
    result = []
    for n, d in zip(cases[::2], cases[1::2], strict=True):
        pair = dict(format=n['format'], origin=n['origin'], mode=n['mode'],
                    available='project_error' not in d, steps=[])
        if pair['available']:
            pair['steps'] = [dict(operation=a['operation'], outcome_equal=a['ok'] == b['ok'],
                state_equal={k: a['after'][k] == b['after'][k] for k in a['after']},
                phases_equal=[e['phase'] for e in a['events']] == [e['phase'] for e in b['events']])
                for a, b in zip(n['steps'], d['steps'], strict=True)]
        else:
            pair['project_error'] = d['project_error']
        result.append(pair)
    return result


def main():
    with tempfile.TemporaryDirectory(prefix='sec28-') as temporary:
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
        ext = load()
        sources = [*sorted((ROOT / 'src').rglob('*.py')), HERE / 'run.py', HERE / 'verify.py',
            HERE.parent / 'repository-origins/run.py', ROOT / 'artifacts/sec11/race_probe.py',
            ROOT / 'tests/core/test_git_host_exec.py', ROOT / 'tests/core/test_git_security_environment.py',
            *sorted((HERE.parent / 'config-namespace').glob('*.py')),
            *[ROOT / f'tests/core/{p}.py' for p in ('test_git_operations','test_wire','conftest','git_helpers')],
            ROOT / 'tests/socket_paths.py']
        hashes = {str(p.relative_to(ROOT)): digest(p) for p in sources}
        cases = []
        for fmt in FORMATS:
            for origin in ORIGINS:
                for mode in MODES:
                    for engine in ('native', 'daemon'):
                        folder = root / f'{fmt}-{origin}-{mode}-{engine}'
                        folder.mkdir()
                        case = asyncio.run(run_case(ext, folder, fmt, origin, mode, engine))
                        cases.append(case)
                        print(fmt, origin, mode, engine, [s['ok'] for s in case['steps']],
                              case.get('project_error'), flush=True)
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
