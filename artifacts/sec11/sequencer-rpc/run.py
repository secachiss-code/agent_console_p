"""SEC-23 disposable daemon RPC census. No product routing or installation."""
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


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def journal(folder):
    with sqlite3.connect(folder / 'aconsole.db') as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute('SELECT * FROM git_operations ORDER BY id')]


async def daemon_case(edits, folder, operation, mode, object_format):
    import msgspec
    from aconsole.contracts.git import (
        CherryPickParams, GitChangeResult, GitOperationResult, GitStateParams,
        GitStateResult, RebaseParams, WorktreeOperationParams,
    )
    from tests.core.test_git_rewrite import _start
    from tests.core.test_wire import _stop

    base = edits.base
    os.environ['GIT_DEFAULT_HASH'] = object_format
    repo, source = base.setup(folder, True)
    for phase in base.PHASES:
        (repo / '.git/hooks' / phase).write_text(edits.HOOK.format(python=sys.executable))
    trace = folder / 'trace.jsonl'
    os.environ.update(EDIT_TRACE=str(trace), EDIT_MODE='observe')
    assert base.git(repo, 'rev-parse', '--show-object-format') == object_format
    daemon, task, client, project = await asyncio.wait_for(_start(folder, repo), timeout=30)
    steps = []

    async def step(label, active_mode, method=None, params=None, result_type=None):
        os.environ['EDIT_MODE'] = active_mode
        before = edits.snapshot(repo)
        before_journal = journal(folder)
        trace.unlink(missing_ok=True)
        if method:
            result = await asyncio.wait_for(client.call(method, params, result_type), timeout=45)
            response = msgspec.to_builtins(result)
            ok = result.operation.status == 'finished' if label == 'start' else result.ok
        else:
            # User resolves/stages the conflict outside the daemon, as in the UI workflow.
            done = base.command(repo, '-c', 'core.hooksPath=/dev/null', 'add', '--', 'shared.txt')
            response = dict(exit=done.returncode, stdout=done.stdout, stderr=done.stderr)
            ok = done.returncode == 0
        events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        state = await asyncio.wait_for(client.call('git/state', GitStateParams(project_id=project), GitStateResult), 45)
        steps.append(dict(label=label, active_mode=active_mode, ok=ok, events=events,
                          rpc_method=method, response=response, before=before, after=edits.snapshot(repo),
                          journal_before=before_journal, journal_after=journal(folder),
                          rpc_state=msgspec.to_builtins(state)))

    try:
        head = base.git(repo, 'rev-parse', 'HEAD')
        if operation == 'rebase':
            params = RebaseParams(project_id=project, worktree=str(repo), onto_oid=source, expect_head=head)
            method = 'git/rebase'
        else:
            params = CherryPickParams(project_id=project, worktree=str(repo), oid=source, expect_head=head)
            method = 'git/cherryPick'
        await step('start', 'observe', method, params, GitOperationResult)
        assert steps[0]['response']['operation']['status'] == 'conflicted'
        assert steps[0]['after']['unmerged']
        (repo / 'shared.txt').write_text('resolved\n')
        await step('resolve_stage', 'observe')
        await step('continue', mode, 'git/continue', WorktreeOperationParams(project_id=project, worktree=str(repo)), GitChangeResult)
    finally:
        await asyncio.wait_for(_stop(daemon, task, client), timeout=45)
    record = dict(engine='daemon', operation=operation, mode=mode, format=object_format,
                  adapter='real Daemon.serve + Unix RPC + SQLite; tab/Podman ports fixture',
                  steps=steps, journal_after_stop=journal(folder))
    return json.loads(json.dumps(record).replace(str(folder), 'CASE'))


def comparisons(cases):
    fields = ('head', 'target', 'symbolic_head', 'head_tree', 'target_tree', 'head_message',
              'index', 'unmerged', 'status', 'state', 'files', 'tree_entries',
              'head_hook_content', 'index_hook_content')
    # Refs intentionally compared separately: daemon adds a safety ref per operation.
    return [dict(format=n['format'], operation=n['operation'], mode=n['mode'], steps=[
        dict(label=a['label'], outcome_equal=a['ok'] == b['ok'],
             phases_equal=[e['phase'] for e in a['events']] == [e['phase'] for e in b['events']],
             state_equal={key: a['after'][key] == b['after'][key] for key in fields})
        for a, b in zip(n['steps'], d['steps'], strict=True)])
        for n, d in zip(cases[::2], cases[1::2], strict=True)]


def main():
    with tempfile.TemporaryDirectory(prefix='sec23-') as temporary:
        root = Path(temporary)
        home = root / 'home'
        home.mkdir()
        runtime = root / 's'
        runtime.mkdir(mode=0o700)
        for key in list(os.environ):
            if key.startswith(('GIT_', 'SEQ_', 'EDIT_', 'XDG_', 'ACONSOLE_')):
                del os.environ[key]
        os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                          XDG_DATA_HOME=str(home / 'data'), XDG_STATE_HOME=str(home / 'state'),
                          XDG_CACHE_HOME=str(home / 'cache'), XDG_RUNTIME_DIR=str(runtime),
                          ACONSOLE_TEST_SOCKET_DIR=str(runtime),
                          GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', LC_ALL='C',
                          GIT_TERMINAL_PROMPT='0', GIT_EDITOR='/usr/bin/true',
                          GIT_AUTHOR_DATE='2026-01-01T00:00:00+00:00', GIT_COMMITTER_DATE='2026-01-01T00:00:00+00:00')
        edits = load('sec22', HERE.parent / 'sequencer-edits/run.py')
        sources = [*sorted((ROOT / 'src').rglob('*.py')), ROOT / 'artifacts/sec11/race_probe.py',
                   ROOT / 'tests/core/test_git_host_exec.py', ROOT / 'tests/core/test_git_security_environment.py',
                   *sorted((HERE.parent / 'config-namespace').glob('*.py')),
                   HERE.parent / 'sequencer-census/run.py', HERE.parent / 'sequencer-edits/run.py',
                   ROOT / 'tests/core/test_wire.py', ROOT / 'tests/core/test_git_rewrite.py',
                   ROOT / 'tests/core/conftest.py', ROOT / 'tests/socket_paths.py',
                   HERE / 'run.py', HERE / 'verify.py']
        hashes = {str(p.relative_to(ROOT)): edits.base.digest(p) for p in sources}
        cases = []
        for fmt in edits.FORMATS:
            for operation in edits.OPERATIONS:
                for mode in edits.MODES:
                    for engine in ('native', 'daemon'):
                        folder = root / f'{fmt}-{operation}-{mode}-{engine}'
                        folder.mkdir()
                        case = (edits.run_case(folder, 'native', operation, mode, fmt) if engine == 'native'
                                else asyncio.run(daemon_case(edits, folder, operation, mode, fmt)))
                        cases.append(case)
                        print(fmt, operation, mode, engine, case['steps'][-1]['ok'], flush=True)
                        (HERE / 'partial.json').write_text(json.dumps(cases, indent=2) + '\n')
        assert all(edits.base.digest(ROOT / p) == h for p, h in hashes.items())
        report = dict(base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                      python=sys.version, git=subprocess.check_output(['/usr/bin/git', '--version'], text=True).strip(),
                      source_hashes=hashes, cases=cases,
                      scope='disposable daemon/service compatibility evidence; no UI or security acceptance')
        (HERE / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
        (HERE / 'comparison.json').write_text(json.dumps(comparisons(cases), indent=2) + '\n')
        (HERE / 'partial.json').unlink()
        print(f'Saved {len(cases)} cases; source hashes unchanged', flush=True)


if __name__ == '__main__':
    main()
