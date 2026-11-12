"""SEC-26 disposable RPC census; no changes to product or routing."""
import asyncio
from datetime import UTC, datetime
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
OPERATIONS = ('worktree', 'detached', 'restore')
MODES = ('observe', 'prepared_refuse', 'committed_fail', 'post_checkout_fail')


def load():
    spec = importlib.util.spec_from_file_location('sec19', HERE.parent / 'worktree-checkpoint-census/run.py')
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    return base


def rows(folder):
    with sqlite3.connect(folder / 'aconsole.db') as db:
        db.row_factory = sqlite3.Row
        return {table: [dict(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY id')]
                for table in ('tabs', 'workspaces', 'work_sessions', 'checkpoints', 'git_operations')}


def events(trace):
    return [json.loads(s) for s in trace.read_text().splitlines()] if trace.exists() else []


def install(base, repo):
    hook = base.HOOK.replace("r[2] in ('refs/heads/ac/census-tab', '{ref}', '{safety}')",
        "r[2].startswith(('refs/heads/ac/', 'refs/aconsole/checkpoints/', 'refs/aconsole/sessions/', 'refs/aconsole/executors/'))")
    for phase in base.PHASES:
        path = repo / '.git/hooks' / phase
        path.write_text(hook.format(python=sys.executable, ref=base.REF, safety=base.SAFETY))
        path.chmod(0o700)


def snapshot(base, repo, dest):
    result = base.snapshot(repo, dest)
    result['checkpoint_refs'] = base.git(repo, 'for-each-ref', '--format=%(refname) %(objectname)',
                                         'refs/aconsole/checkpoints/')
    result['checkpoint_trees'] = {line.split()[0]: base.git(repo, 'ls-tree', '-r', line.split()[1])
        for line in result['checkpoint_refs'].splitlines()}
    result['checkpoint_tree_oids'] = {line.split()[0]: base.git(repo, 'rev-parse', line.split()[1] + '^{tree}')
        for line in result['checkpoint_refs'].splitlines()}
    return result


async def daemon_case(base, folder, fmt, operation, mode):
    import msgspec
    from aconsole.contracts.app import (OpenTabParams, TabView, CheckpointListParams,
        CheckpointListResult, CheckpointIdParams, RestorePreviewResult, RestoreParams)
    from aconsole.contracts.common import AgentKind, WorkspaceMode
    from aconsole.contracts.ipc import ResponseOk, ResponseErr
    from tests.core.test_git_rewrite import _start
    from tests.core.test_wire import _stop

    repo = base.setup(folder, fmt, 'capture' if operation == 'restore' else 'worktree')
    install(base, repo)
    trace = folder / 'trace.jsonl'
    os.environ.update(CENSUS_MODE='observe', CENSUS_TRACE=str(trace), GIT_DEFAULT_HASH=fmt)
    daemon, task, client, project = await asyncio.wait_for(_start(folder, repo), 30)
    prep = []
    dest = folder / 'unused'
    try:
        primary = None
        if operation in ('detached', 'restore'):
            primary = await asyncio.wait_for(client.call('tab/open', OpenTabParams(project_id=project,
                agent=AgentKind.CLAUDE, workspace=WorkspaceMode.MAIN_CHECKOUT, start_now=False), TabView), 45)
            prep.append(dict(method='tab/open', response=msgspec.to_builtins(primary)))
        if operation == 'restore':
            # Explicit fixture seed through the assembled service, not a claimed capture RPC.
            seed = await asyncio.wait_for(daemon._in_worker(lambda: daemon.review.checkpoints.on_user_prompt(
                repo, str(primary.id), 'checkpoint', datetime(2026, 1, 1, tzinfo=UTC))), 45)
            listing = await client.call('checkpoint/list', CheckpointListParams(tab_id=primary.id), CheckpointListResult)
            assert str(listing.checkpoints[0].id) == seed.id
            preview = await client.call('checkpoint/restorePreview',
                CheckpointIdParams(checkpoint_id=listing.checkpoints[0].id), RestorePreviewResult)
            prep.append(dict(method='service seed + checkpoint/list + checkpoint/restorePreview',
                listing=msgspec.to_builtins(listing), files=list(preview.files)))
            (repo / 'tracked.txt').write_text('later\n')
            (repo / 'untracked.txt').write_text('later untracked\n')
            (repo / 'extra.txt').write_text('remove on restore\n')
            method = 'checkpoint/restore'
            params = RestoreParams(checkpoint_id=listing.checkpoints[0].id, confirm_token=preview.confirm_token)
        else:
            method = 'tab/open'
            params = OpenTabParams(project_id=project, agent=AgentKind.CLAUDE,
                workspace=WorkspaceMode.NEW_WORKTREE, start_now=False,
                worktree_name='census', base_branch='target', isolated=operation == 'detached',
                work_session_id=primary.work_session_id if primary else None)
        before = snapshot(base, repo, dest)
        before_rows = rows(folder)
        trace.unlink(missing_ok=True)
        os.environ['CENSUS_MODE'] = mode
        reply = await asyncio.wait_for(client.raw(method, params), 60)
        ok = isinstance(reply, ResponseOk)
        assert ok or isinstance(reply, ResponseErr)
        response = json.loads(bytes(reply.result)) if ok else msgspec.to_builtins(reply.error)
        if ok and operation != 'restore':
            dest = Path(response['worktree_path'])
        measured_events = events(trace)
        after = snapshot(base, repo, dest)
        after_rows = rows(folder)
        listing_after = (msgspec.to_builtins(await client.call('checkpoint/list',
            CheckpointListParams(tab_id=primary.id), CheckpointListResult)) if operation == 'restore' else None)
    finally:
        await asyncio.wait_for(_stop(daemon, task, client), 45)
    return dict(engine='daemon', format=fmt, operation=operation, mode=mode, ok=ok,
        rpc_method=method, response=response, preparation=prep, events=measured_events,
        before=before, after=after, rows_before=before_rows, rows_after=after_rows,
        rows_after_stop=rows(folder), listing_after=listing_after)


def native_case(base, folder, fmt, operation, mode):
    repo = base.setup(folder, fmt, 'materialize' if operation == 'restore' else operation)
    install(base, repo)
    dest, trace = folder / 'dest', folder / 'trace.jsonl'
    os.environ.update(CENSUS_MODE=mode, CENSUS_TRACE=str(trace))
    before = snapshot(base, repo, dest)
    calls = []
    error = None
    try:
        if operation == 'restore':
            # Restore RPC first captures a safety checkpoint, then materializes the original.
            original = base.REF
            base.REF = 'refs/aconsole/checkpoints/census/2'
            try:
                base.native_operation(repo, dest, 'capture', calls)
            finally:
                base.REF = original
            base.native_operation(repo, dest, 'materialize', calls)
        else:
            base.native_operation(repo, dest, operation, calls)
        ok = True
    except base.NativeFailure as exc:
        ok, error = False, str(exc)
    return dict(engine='native', format=fmt, operation=operation, mode=mode, ok=ok,
        error=error, native_calls=calls, events=events(trace), before=before,
        after=snapshot(base, repo, dest))


def comparisons(cases):
    fields = ('head', 'symbolic_head', 'index', 'files', 'dest_files')
    return [dict(format=n['format'], operation=n['operation'], mode=n['mode'],
        outcome_equal=n['ok'] == d['ok'],
        phases_equal=[(e['phase'], e['args']) for e in n['events']] == [(e['phase'], e['args']) for e in d['events']],
        state_equal={k: n['after'][k] == d['after'][k] for k in fields})
        for n, d in zip(cases[::2], cases[1::2], strict=True)]


def main():
    with tempfile.TemporaryDirectory(prefix='sec26-') as temporary:
        root = Path(temporary)
        home, runtime = root / 'home', root / 's'
        home.mkdir()
        runtime.mkdir(mode=0o700)
        for key in list(os.environ):
            if key.startswith(('GIT_', 'CENSUS_', 'XDG_', 'ACONSOLE_', 'FAKE_')):
                del os.environ[key]
        os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
            XDG_DATA_HOME=str(home / 'data'), XDG_STATE_HOME=str(home / 'state'),
            XDG_CACHE_HOME=str(home / 'cache'), XDG_RUNTIME_DIR=str(runtime),
            ACONSOLE_TEST_SOCKET_DIR=str(runtime), FAKE_HOME=str(home),
            GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', LC_ALL='C',
            GIT_TERMINAL_PROMPT='0', GIT_EDITOR='/usr/bin/true',
            GIT_AUTHOR_DATE='2026-01-01T00:00:00+00:00', GIT_COMMITTER_DATE='2026-01-01T00:00:00+00:00')
        base = load()
        sources = [*sorted((ROOT / 'src').rglob('*.py')), ROOT / 'artifacts/sec11/race_probe.py',
            ROOT / 'tests/core/test_git_host_exec.py', ROOT / 'tests/core/test_git_security_environment.py',
            *sorted((HERE.parent / 'config-namespace').glob('*.py')),
            HERE.parent / 'worktree-checkpoint-census/run.py', ROOT / 'tests/core/test_wire.py',
            ROOT / 'tests/core/test_git_rewrite.py', ROOT / 'tests/core/conftest.py',
            ROOT / 'tests/socket_paths.py', HERE / 'run.py', HERE / 'verify.py']
        hashes = {str(p.relative_to(ROOT)): base.digest(p) for p in sources}
        cases = []
        for fmt in FORMATS:
            for operation in OPERATIONS:
                for mode in MODES:
                    for engine in ('native', 'daemon'):
                        folder = root / f'{fmt}-{operation}-{mode}-{engine}'
                        folder.mkdir()
                        case = (native_case(base, folder, fmt, operation, mode) if engine == 'native'
                            else asyncio.run(daemon_case(base, folder, fmt, operation, mode)))
                        case = json.loads(json.dumps(case).replace(str(folder), 'CASE'))
                        for event in case['events']:
                            if event['index']:
                                event['index'] = 'PRIVATE_INDEX'
                        cases.append(case)
                        print(fmt, operation, mode, engine, case['ok'], flush=True)
                        (HERE / 'partial.json').write_text(json.dumps(cases, indent=2) + '\n')
        assert all(base.digest(ROOT / p) == h for p, h in hashes.items())
        report = dict(base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            python=sys.version, git=subprocess.check_output(['/usr/bin/git', '--version'], text=True).strip(),
            source_hashes=hashes, cases=cases)
        (HERE / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
        (HERE / 'comparison.json').write_text(json.dumps(comparisons(cases), indent=2) + '\n')
        (HERE / 'partial.json').unlink()
        print(f'Saved {len(cases)} cases', flush=True)


if __name__ == '__main__':
    main()
