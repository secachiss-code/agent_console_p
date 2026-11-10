"""SEC-21 disposable repository-origin census, with no product routing changes."""
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
from aconsole.core.infrastructure.git.operations import GitMutations

FORMATS = ('sha1', 'sha256')
ORIGINS = ('linked', 'unborn', 'bare')
MODES = ('observe', 'pre-refuse', 'ref-refuse')
PHASES = ('pre-commit', 'prepare-commit-msg', 'commit-msg', 'post-commit',
          'post-index-change', 'reference-transaction')
HOOK = '''#!{python}
import json, os, sys
from pathlib import Path
phase = Path(sys.argv[0]).name
data = sys.stdin.read() if phase == 'reference-transaction' else ''
mode = os.environ['CENSUS_MODE']
refuse = ((mode == 'pre-refuse' and phase == 'pre-commit') or
          (mode == 'ref-refuse' and phase == 'reference-transaction' and sys.argv[1:] == ['prepared']))
with open(os.environ['CENSUS_TRACE'], 'a') as out:
    out.write(json.dumps(dict(phase=phase, args=sys.argv[1:], stdin=data, refuse=refuse,
                             cwd=os.getcwd(), env={{k: v for k, v in os.environ.items() if k in
                             ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_PREFIX')}})) + '\\n')
if refuse:
    print('census hook refusal', file=sys.stderr)
    sys.exit(1)
'''

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def command(repo, *args, stdin=''):
    return subprocess.run(['/usr/bin/git', '-C', str(repo), *args], input=stdin,
                          capture_output=True, text=True, timeout=30, check=False)

def git(repo, *args, codes=(0,), stdin=''):
    done = command(repo, '-c', 'core.hooksPath=/dev/null', *args, stdin=stdin)
    if done.returncode not in codes:
        raise RuntimeError((args, done.returncode, done.stderr))
    return done.stdout.strip()

def snapshot(repo, origin):
    head = git(repo, 'rev-parse', '--verify', '--quiet', 'HEAD', codes=(0, 1)) or None
    common = Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    metadata = Path(git(repo, 'rev-parse', '--absolute-git-dir'))
    return dict(head=head, symbolic=git(repo, 'symbolic-ref', 'HEAD'),
                refs=git(repo, 'show-ref', codes=(0, 1)),
                tree=git(repo, 'rev-parse', 'HEAD^{tree}') if head else None,
                message=git(repo, 'log', '-1', '--format=%B') if head else None,
                index=git(repo, 'ls-files', '--stage'),
                index_content=git(repo, 'show', ':data.txt', codes=(0, 128)) if origin != 'bare' else None,
                head_content=git(repo, 'show', 'HEAD:data.txt') if head and origin != 'bare' else None,
                index_sha256=digest(metadata / 'index') if (metadata / 'index').exists() else None,
                files={p.name: p.read_text() for p in repo.iterdir()
                       if p.is_file() and p.name in ('data.txt', 'ignored.txt', 'untracked.txt')},
                common=str(common), metadata=str(metadata),
                bare=git(repo, 'rev-parse', '--is-bare-repository'))

def setup(folder, fmt, origin):
    repo = folder / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'target', f'--object-format={fmt}',
        *(('--bare',) if origin == 'bare' else ()))
    for key, value in (('user.name', 'Census'), ('user.email', 'census@example.invalid'),
                       ('commit.gpgSign', 'false')):
        git(repo, 'config', key, value)
    if origin == 'linked':
        (repo / 'data.txt').write_text('base\n')
        git(repo, 'add', '.')
        git(repo, 'commit', '-qm', 'base')
        linked = folder / 'linked'
        git(repo, 'worktree', 'add', '-qb', 'linked-target', str(linked))
        repo = linked
    if origin != 'bare':
        (repo / 'data.txt').write_text('updated\n')
        common = Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
        (common / 'info/exclude').write_text('ignored.txt\n')
        (repo / 'ignored.txt').write_text('ignored survives\n')
        (repo / 'untracked.txt').write_text('untracked survives\n')
    else:
        # A real empty tree, without importing objects from another object format.
        tree = git(repo, 'mktree', stdin='')
        initial = git(repo, 'commit-tree', tree, '-m', 'base')
        git(repo, 'update-ref', 'refs/heads/target', initial)
    common = Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    for phase in PHASES:
        hook = common / 'hooks' / phase
        hook.write_text(HOOK.format(python=sys.executable))
        hook.chmod(0o700)
    return repo

def run_case(folder, fmt, origin, mode, engine):
    repo = setup(folder, fmt, origin)
    trace = folder / 'trace.jsonl'
    os.environ.update(CENSUS_TRACE=str(trace), CENSUS_MODE=mode)
    writer = GitMutations()
    steps = []
    operations = ('commit-tree', 'update-ref') if origin == 'bare' else ('stage', 'commit')
    candidate = None
    for op in operations:
        before = snapshot(repo, origin)
        trace.unlink(missing_ok=True)
        if op == 'stage':
            args = ('--literal-pathspecs', 'add', '-A', '--', 'data.txt')
        elif op == 'commit':
            args = ('commit', '-F', '-')
        elif op == 'commit-tree':
            args = ('commit-tree', before['tree'], '-p', before['head'], '-F', '-')
        else:
            args = ('update-ref', 'refs/heads/target', candidate, before['head'])
        if engine == 'native':
            done = command(repo, *args, stdin='origin census\n' if op in ('commit', 'commit-tree') else '')
            ok, said = done.returncode == 0, done.stdout + done.stderr
            if op == 'commit-tree' and ok:
                candidate = done.stdout.strip()
        else:
            if op == 'stage':
                done = writer.stage(repo, ('data.txt',))
            elif op == 'commit':
                done = writer.commit(repo, 'origin census\n')
            elif op == 'commit-tree':
                done = writer.commit_tree(repo, before['tree'], (before['head'],), 'origin census\n')
                candidate = done.oid
            else:
                done = writer.move_ref(repo, 'refs/heads/target', candidate, before['head'])
            ok, said = done.ok, done.said
        events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        steps.append(dict(operation=op, ok=ok, said=said, before=before,
                          after=snapshot(repo, origin), events=events, candidate=candidate))
    return json.loads(json.dumps(dict(format=fmt, origin=origin, mode=mode, engine=engine,
                                     steps=steps)).replace(str(folder), 'CASE'))

def compare(n, p):
    return dict(format=n['format'], origin=n['origin'], mode=n['mode'], steps=[
        dict(operation=a['operation'], outcome_equal=a['ok'] == b['ok'],
             state_equal={k: a['after'][k] == b['after'][k] for k in a['after']},
             phases_equal=[e['phase'] for e in a['events']] == [e['phase'] for e in b['events']])
        for a, b in zip(n['steps'], p['steps'], strict=True)])

def main():
    paths = [*sorted((ROOT / 'src').rglob('*.py')), ROOT / 'artifacts/sec11/race_probe.py',
             ROOT / 'tests/core/test_git_host_exec.py',
             *sorted((ROOT / 'artifacts/sec11/config-namespace').glob('*.py')),
             HERE / 'run.py', HERE / 'verify.py']
    hashes = {str(p.relative_to(ROOT)): digest(p) for p in paths}
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    cases = []
    with tempfile.TemporaryDirectory(prefix='sec21-') as temporary:
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
        for fmt in FORMATS:
            for origin in ORIGINS:
                for mode in MODES:
                    for engine in ('native', 'product'):
                        folder = root / f'{fmt}-{origin}-{mode}-{engine}'
                        folder.mkdir()
                        case = run_case(folder, fmt, origin, mode, engine)
                        cases.append(case)
                        print(fmt, origin, mode, engine, [s['ok'] for s in case['steps']], flush=True)
    assert all(digest(ROOT / p) == value for p, value in hashes.items())
    (HERE / 'results.json').write_text(json.dumps(dict(base_commit=revision, source_hashes=hashes,
        cases=cases, python=sys.version,
        git=subprocess.check_output(['/usr/bin/git', '--version'], text=True).strip()), indent=2) + '\n')
    (HERE / 'comparison.json').write_text(json.dumps([
        compare(n, p) for n, p in zip(cases[::2], cases[1::2], strict=True)], indent=2) + '\n')

if __name__ == '__main__':
    main()
