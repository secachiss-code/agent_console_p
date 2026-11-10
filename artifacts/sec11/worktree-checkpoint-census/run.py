"""Disposable API-level hook census. No daemon, host boundary or product routing."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from aconsole.core.infrastructure.git.checkpoints import GitCheckpoints, CheckpointGitError
from aconsole.core.infrastructure.git.worktrees import Worktrees, WorktreeRequest, WorktreeError

OPERATIONS = ('worktree', 'detached', 'capture', 'materialize', 'delete')
MODES = ('observe', 'prepared_refuse', 'committed_fail', 'post_checkout_fail')
FORMATS = ('sha1', 'sha256')
REF = 'refs/aconsole/checkpoints/census/1'
SAFETY = 'refs/aconsole/executors/census/base'
PHASES = ('reference-transaction', 'post-checkout', 'post-index-change', 'pre-commit',
          'prepare-commit-msg', 'commit-msg', 'post-commit')
HOOK = r'''#!{python}
import json, os, sys
from pathlib import Path
phase = Path(sys.argv[0]).name
mode = os.environ['CENSUS_MODE']
data = sys.stdin.read() if phase == 'reference-transaction' else None
rows = [line.split() for line in (data or '').splitlines()]
targets = [r for r in rows if len(r) == 3 and
    r[2] in ('refs/heads/ac/census-tab', '{ref}', '{safety}')]
refuse = ((phase == 'reference-transaction' and bool(targets) and
    ((mode == 'prepared_refuse' and sys.argv[1:] == ['prepared']) or
     (mode == 'committed_fail' and sys.argv[1:] == ['committed']))) or
    (phase == 'post-checkout' and mode == 'post_checkout_fail'))
with open(os.environ['CENSUS_TRACE'], 'a') as stream:
    stream.write(json.dumps({{'phase': phase, 'args': sys.argv[1:], 'stdin': data,
        'cwd': os.getcwd(), 'git_dir': os.environ.get('GIT_DIR'),
        'index': os.environ.get('GIT_INDEX_FILE'), 'target_rows': targets,
        'refuse': refuse}}) + '\n')
if refuse:
    print('census refusal: ' + phase, file=sys.stderr)
    sys.exit(1)
'''


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(repo, args, *, env=None):
    return subprocess.run(['/usr/bin/git', '-C', str(repo), *args], input='',
                          capture_output=True, text=True, timeout=30, env=env, check=False)


def git(repo, *args, codes=(0,)):
    done = command(repo, ['-c', 'core.hooksPath=/dev/null', *args])
    if done.returncode not in codes:
        raise RuntimeError((args, done.returncode, done.stderr))
    return done.stdout.strip()


def setup(folder, object_format, operation):
    repo = folder / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'target', f'--object-format={object_format}')
    for key, value in (('user.name', 'Census'), ('user.email', 'census@example.invalid'),
                       ('commit.gpgSign', 'false')):
        git(repo, 'config', key, value)
    (repo / 'tracked.txt').write_text('base\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')
    (repo / '.git/info/exclude').write_text('ignored.txt\n.aconsole/lock\n')
    (repo / 'ignored.txt').write_text('ignored must survive\n')
    (repo / 'untracked.txt').write_text('untracked original\n')
    if operation in ('capture', 'materialize', 'delete'):
        (repo / 'tracked.txt').write_text('staged\n')
        git(repo, 'add', 'tracked.txt')
        (repo / 'tracked.txt').write_text('snapshot\n')
        (repo / '.aconsole').mkdir()
        (repo / '.aconsole/lock').write_text('runtime lock\n')
    if operation in ('materialize', 'delete'):
        # Identical preparation in both engines, hooks absent at this point.
        GitCheckpoints().capture(repo, REF, 'checkpoint')
    if operation == 'materialize':
        (repo / 'tracked.txt').write_text('later\n')
        (repo / 'untracked.txt').write_text('later untracked\n')
        (repo / 'extra.txt').write_text('remove on restore\n')
    for phase in PHASES:
        path = repo / '.git/hooks' / phase
        path.write_text(HOOK.format(python=sys.executable, ref=REF, safety=SAFETY))
        path.chmod(0o700)
    return repo


def snapshot(repo, dest):
    def files(root):
        return {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob('*'))
                if p.is_file() and '.git' not in p.relative_to(root).parts
                and str(p.relative_to(root)) != '.aconsole/lock'} if root.exists() else {}

    return {'head': git(repo, 'rev-parse', 'HEAD'),
            'symbolic_head': git(repo, 'symbolic-ref', 'HEAD'),
            'refs': git(repo, 'show-ref'),
            'checkpoint_tree': git(repo, 'ls-tree', '-r', REF, codes=(0, 128)),
            'index': git(repo, 'ls-files', '--stage'),
            'index_sha256': digest(repo / '.git/index'),
            'files': files(repo), 'dest_exists': dest.exists(), 'dest_files': files(dest),
            'worktrees': git(repo, 'worktree', 'list', '--porcelain'),
            'dest_lock': (dest / '.aconsole/lock').is_file(),
            'source_lock_sha256': digest(repo / '.aconsole/lock') if (repo / '.aconsole/lock').exists() else None,
            'metadata': {p.name: p.read_text() for p in sorted((repo / '.git/aconsole-worktrees').glob('*'))}}


class NativeFailure(Exception):
    pass


def native_operation(repo, dest, operation, calls):
    def run(*args, env=None):
        done = command(repo, list(args), env=env)
        calls.append({'argv': args, 'index': (env or {}).get('GIT_INDEX_FILE'),
                      'code': done.returncode, 'stdout': done.stdout, 'stderr': done.stderr})
        if done.returncode:
            raise NativeFailure(done.stderr)
        return done.stdout.strip()

    if operation == 'worktree':
        run('worktree', 'add', '-b', 'ac/census-tab', str(dest), 'HEAD')
    elif operation == 'detached':
        base = run('rev-parse', '--verify', 'HEAD')
        run('update-ref', SAFETY, base)
        run('worktree', 'add', '--detach', str(dest), base)
    elif operation == 'capture':
        # Same plumbing/author/message as GitCheckpoints.capture, without GitRunner overrides.
        parent = run('rev-parse', '--verify', '--quiet', 'HEAD')
        with tempfile.NamedTemporaryFile(prefix='census-index-', dir=repo.parent) as index:
            env = dict(os.environ, GIT_INDEX_FILE=index.name)
            run('read-tree', parent, env=env)
            run('add', '-A', '--', '.', env=env)
            run('update-index', '--force-remove', '--', '.aconsole/lock', env=env)
            tree = run('write-tree', env=env)
        env = dict(os.environ, GIT_AUTHOR_NAME='AConsole', GIT_AUTHOR_EMAIL='aconsole@localhost',
                   GIT_COMMITTER_NAME='AConsole', GIT_COMMITTER_EMAIL='aconsole@localhost')
        oid = run('commit-tree', tree, '-p', parent, '-m', 'checkpoint', env=env)
        run('update-ref', REF, oid)
        run('diff', '--numstat', '--no-ext-diff', '--no-textconv', parent, oid)
        run('ls-tree', '-r', '-z', '--name-only', oid)
    elif operation == 'materialize':
        paths = set(run('ls-tree', '-r', '-z', '--name-only', REF).split('\0')) - {''}
        for name in sorted(paths):
            run('restore', '--source', REF, '--worktree', '--', name)
        current = set(run('ls-files', '-c', '-o', '--exclude-standard', '-z').split('\0')) - {''}
        for name in sorted(current - paths, reverse=True):
            if name != '.aconsole/lock':
                # Fixture paths are flat ordinary files. No symlink/deletion boundary claim.
                (repo / name).unlink(missing_ok=True)
    else:
        run('update-ref', '-d', REF)


def run_case(folder, engine, operation, mode, object_format):
    repo = setup(folder, object_format, operation)
    dest = folder / 'dest'
    trace = folder / 'trace.jsonl'
    os.environ.update(CENSUS_MODE=mode, CENSUS_TRACE=str(trace))
    before = snapshot(repo, dest)
    calls = []
    try:
        if engine == 'native':
            native_operation(repo, dest, operation, calls)
        elif operation in ('worktree', 'detached'):
            writer = Worktrees(Path('/usr/bin/git'))
            request = WorktreeRequest(repo=repo, dest=dest, slug='census', tab_id='tab')
            if operation == 'worktree':
                writer.create(request)
            else:
                writer.create_detached(request, SAFETY, repo)
        else:
            writer = GitCheckpoints()
            if operation == 'capture':
                writer.capture(repo, REF, 'checkpoint')
            elif operation == 'materialize':
                writer.materialize(repo, REF)
            else:
                writer.delete(repo, REF)
        ok, error = True, None
    except (NativeFailure, WorktreeError, CheckpointGitError) as exc:
        ok, error = False, {'type': type(exc).__name__, 'message': str(exc),
                            'detail': getattr(exc, 'detail', None)}
    events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    result = {'engine': engine, 'operation': operation, 'mode': mode, 'format': object_format,
              'ok': ok, 'error': error, 'native_calls': calls, 'events': events,
              'before': before, 'after': snapshot(repo, dest)}
    # Normalize private index names only, preserving the presence of a private index.
    raw = json.dumps(result).replace(str(folder), 'CASE')
    result = json.loads(raw)
    for event in result['events']:
        if event['index'] and ('census-index-' in event['index'] or 'aconsole-index-' in event['index']):
            event['index'] = 'PRIVATE_INDEX'
    return result


def compare(native, product):
    return {**{k: native[k] for k in ('operation', 'mode', 'format')},
            'outcome_equal': native['ok'] == product['ok'],
            'phases_equal': [(e['phase'], e['args']) for e in native['events']]
                == [(e['phase'], e['args']) for e in product['events']],
            'state_equal': {k: native['after'][k] == product['after'][k]
                            for k in native['after']}}


def main():
    sources = [*sorted((ROOT / 'src').rglob('*.py')), ROOT / 'artifacts/sec11/race_probe.py',
               ROOT / 'tests/core/test_git_host_exec.py',
               *sorted((ROOT / 'artifacts/sec11/config-namespace').glob('*.py')),
               Path(__file__).resolve()]
    hashes = {str(p.relative_to(ROOT)): digest(p) for p in sources}
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    cases = []
    with tempfile.TemporaryDirectory(prefix='sec19-') as temporary:
        root = Path(temporary)
        home = root / 'home'
        home.mkdir()
        for key in list(os.environ):
            if key.startswith(('GIT_', 'CENSUS_')):
                del os.environ[key]
        os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                          GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', LC_ALL='C',
                          GIT_TERMINAL_PROMPT='0', GIT_EDITOR='/usr/bin/true',
                          GIT_AUTHOR_DATE='2026-01-01T00:00:00+00:00',
                          GIT_COMMITTER_DATE='2026-01-01T00:00:00+00:00')
        for object_format in FORMATS:
            for operation in OPERATIONS:
                for mode in MODES:
                    for engine in ('native', 'product'):
                        folder = root / f'{object_format}-{operation}-{mode}-{engine}'
                        folder.mkdir()
                        case = run_case(folder, engine, operation, mode, object_format)
                        cases.append(case)
                        print(object_format, operation, mode, engine, case['ok'], flush=True)
    assert all(digest(ROOT / p) == value for p, value in hashes.items())
    results = {'base_commit': revision, 'source_hashes': hashes, 'python': sys.version,
               'git': subprocess.check_output(['/usr/bin/git', '--version'], text=True).strip(),
               'scope': 'container GitCheckpoints/Worktrees APIs; no daemon/host boundary', 'cases': cases}
    (HERE / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    comparisons = [compare(n, p) for n, p in zip(cases[::2], cases[1::2], strict=True)]
    (HERE / 'comparison.json').write_text(json.dumps(comparisons, indent=2) + '\n')
    print('Saved', len(cases), 'cases; source hashes unchanged')


if __name__ == '__main__':
    main()
