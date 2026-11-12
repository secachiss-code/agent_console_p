"""SEC-25 disposable real daemon experiment; no installation/product routing."""
import asyncio
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


def load(path):
    spec = importlib.util.spec_from_file_location('sec18', path)
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    # Same explicit final-target selector, extended to both real OID lengths.
    assert 'len(row[1]) == 40' in base.HOOK
    base.HOOK = base.HOOK.replace('len(row[1]) == 40', 'len(row[1]) in (40, 64)')
    return base


def journal(folder):
    with sqlite3.connect(folder / 'aconsole.db') as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute('SELECT * FROM git_operations ORDER BY id')]


async def daemon_case(base, folder, operation, flow, mode):
    import msgspec
    from aconsole.contracts.git import (CherryPickParams, GitChangeResult, GitOperationResult,
        GitStateParams, GitStateResult, RebaseParams, WorktreeOperationParams)
    from tests.core.test_git_rewrite import _start
    from tests.core.test_wire import _connect, _daemon, _stop

    repo, source = base.setup(folder, flow != 'success')
    trace = folder / 'trace.jsonl'
    os.environ.update(SEQ_TRACE=str(trace), SEQ_REPO=str(repo), SEQ_OPERATION=operation,
                      SEQ_MODE=mode if flow == 'success' else 'observe')
    daemon, task, client, project = await asyncio.wait_for(_start(folder, repo), 30)
    steps, restarts = [], []

    async def state():
        return msgspec.to_builtins(await asyncio.wait_for(client.call(
            'git/state', GitStateParams(project_id=project), GitStateResult), 45))

    async def restart(label):
        nonlocal daemon, task, client
        before, rows, rpc_before = base.snapshot(repo), journal(folder), await state()
        trace.unlink(missing_ok=True)
        old = daemon
        await asyncio.wait_for(_stop(daemon, task, client), 45)
        stopped_rows = journal(folder)
        daemon = _daemon(folder, serve_ingest=False)
        assert daemon is not old
        task = asyncio.create_task(daemon.serve())
        client = await asyncio.wait_for(_connect(daemon.socket), 30)
        rpc_after = await state()
        events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        restarts.append(dict(label=label, before=before, after=base.snapshot(repo),
                             journal_before=rows, journal_after_stop=stopped_rows,
                             journal_after=journal(folder), rpc_before=rpc_before,
                             rpc_after=rpc_after, events=events))

    async def step(label, active_mode, method=None, params=None, result_type=None):
        os.environ['SEQ_MODE'] = active_mode
        before, rows = base.snapshot(repo), journal(folder)
        trace.unlink(missing_ok=True)
        if method:
            result = await asyncio.wait_for(client.call(method, params, result_type), 45)
            response = msgspec.to_builtins(result)
            ok = result.operation.status == 'finished' if label == 'start' else result.ok
        else:
            done = base.command(repo, '-c', 'core.hooksPath=/dev/null', 'add', '--', 'shared.txt')
            response = dict(exit=done.returncode, stdout=done.stdout, stderr=done.stderr)
            ok = done.returncode == 0
        events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        rpc = await state()
        steps.append(dict(label=label, active_mode=active_mode, ok=ok, events=events,
                          rpc_method=method, response=response, before=before, after=base.snapshot(repo),
                          journal_before=rows, journal_after=journal(folder), rpc_state=rpc))

    try:
        head = base.git(repo, 'rev-parse', 'HEAD')
        params = (RebaseParams(project_id=project, worktree=str(repo), onto_oid=source, expect_head=head)
                  if operation == 'rebase' else CherryPickParams(
                      project_id=project, worktree=str(repo), oid=source, expect_head=head))
        await step('start', mode if flow == 'success' else 'observe',
                   'git/rebase' if operation == 'rebase' else 'git/cherryPick', params, GitOperationResult)
        if flow != 'success':
            assert steps[-1]['response']['operation']['status'] == 'conflicted'
            assert steps[-1]['after']['unmerged']
            await restart('conflict')
            if flow == 'continue':
                (repo / 'shared.txt').write_text('resolved\n')
                await step('resolve_stage', 'observe')
            await step(flow, mode, 'git/' + flow, WorktreeOperationParams(
                project_id=project, worktree=str(repo)), GitChangeResult)
        await restart('terminal')
    finally:
        await asyncio.wait_for(_stop(daemon, task, client), 45)
    return json.loads(json.dumps(dict(engine='daemon', operation=operation, flow=flow, mode=mode,
        adapter='new Daemon.serve instances + Unix RPC + SQLite; tab/Podman fixture',
        steps=steps, restarts=restarts, journal_after_stop=journal(folder))).replace(str(folder), 'CASE'))


def comparisons(cases):
    fields = ('head', 'target', 'symbolic_head', 'head_tree', 'target_tree', 'head_message',
              'index', 'unmerged', 'status', 'state', 'files')
    return [dict(format=n['format'], operation=n['operation'], flow=n['flow'], mode=n['mode'], steps=[
        dict(label=a['label'], outcome_equal=a['ok'] == b['ok'],
             phases_equal=[(e['phase'], e['args']) for e in a['events']] ==
                          [(e['phase'], e['args']) for e in b['events']],
             state_equal={key: a['after'][key] == b['after'][key] for key in fields})
        for a, b in zip(n['steps'], d['steps'], strict=True)])
        for n, d in zip(cases[::2], cases[1::2], strict=True)]


def main():
    with tempfile.TemporaryDirectory(prefix='sec25-') as temporary:
        root = Path(temporary)
        home, runtime = root / 'home', root / 's'
        home.mkdir()
        runtime.mkdir(mode=0o700)
        for key in list(os.environ):
            if key.startswith(('GIT_', 'SEQ_', 'EDIT_', 'XDG_', 'ACONSOLE_')):
                del os.environ[key]
        os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
            XDG_DATA_HOME=str(home / 'data'), XDG_STATE_HOME=str(home / 'state'),
            XDG_CACHE_HOME=str(home / 'cache'), XDG_RUNTIME_DIR=str(runtime),
            ACONSOLE_TEST_SOCKET_DIR=str(runtime), GIT_CONFIG_NOSYSTEM='1',
            GIT_CONFIG_GLOBAL='/dev/null', LC_ALL='C', GIT_TERMINAL_PROMPT='0',
            GIT_EDITOR='/usr/bin/true', GIT_AUTHOR_DATE='2026-01-01T00:00:00+00:00',
            GIT_COMMITTER_DATE='2026-01-01T00:00:00+00:00')
        base = load(HERE.parent / 'sequencer-census/run.py')
        sources = [*sorted((ROOT / 'src').rglob('*.py')), ROOT / 'artifacts/sec11/race_probe.py',
            ROOT / 'tests/core/test_git_host_exec.py', ROOT / 'tests/core/test_git_security_environment.py',
            *sorted((HERE.parent / 'config-namespace').glob('*.py')),
            HERE.parent / 'sequencer-census/run.py', ROOT / 'tests/core/test_wire.py',
            ROOT / 'tests/core/test_git_rewrite.py', ROOT / 'tests/core/conftest.py',
            ROOT / 'tests/socket_paths.py', HERE / 'run.py', HERE / 'verify.py']
        hashes = {str(p.relative_to(ROOT)): base.digest(p) for p in sources}
        cases = []
        for fmt in FORMATS:
            os.environ['GIT_DEFAULT_HASH'] = fmt
            for operation in base.OPERATIONS:
                for flow in base.FLOWS:
                    for mode in base.MODES:
                        for engine in ('native', 'daemon'):
                            folder = root / f'{fmt}-{operation}-{flow}-{mode}-{engine}'
                            folder.mkdir()
                            case = (base.run_case(folder, 'native', operation, flow, mode) if engine == 'native'
                                    else asyncio.run(daemon_case(base, folder, operation, flow, mode)))
                            case['format'] = fmt
                            cases.append(case)
                            print(fmt, operation, flow, mode, engine, case['steps'][-1]['ok'], flush=True)
                            (HERE / 'partial.json').write_text(json.dumps(cases, indent=2) + '\n')
        assert all(base.digest(ROOT / p) == h for p, h in hashes.items())
        report = dict(base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            python=sys.version, git=subprocess.check_output(['/usr/bin/git', '--version'], text=True).strip(),
            source_hashes=hashes, cases=cases,
            scope='disposable daemon compatibility and graceful restart; no UI/security acceptance')
        (HERE / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
        (HERE / 'comparison.json').write_text(json.dumps(comparisons(cases), indent=2) + '\n')
        (HERE / 'partial.json').unlink()
        print(f'Saved {len(cases)} cases; source hashes unchanged', flush=True)


if __name__ == '__main__':
    main()
