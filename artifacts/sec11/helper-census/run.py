"""SEC-20 disposable helper compatibility census; no host or daemon acceptance."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from aconsole.core.infrastructure.git.operations import GitMutations
from aconsole.core.infrastructure.git.history import GitHistory

OPERATIONS = ('stage', 'restore', 'merge', 'probe')
MODES = ('success', 'fail')
FORMATS = ('sha1', 'sha256')
HELPER = '''import json, os, sys
from pathlib import Path
phase = sys.argv[1]
data = sys.stdin.read() if phase != 'merge' else None
inputs = [Path(p).read_text() for p in sys.argv[2:5]] if phase == 'merge' else None
fail = os.environ['CENSUS_MODE'] == 'fail'
with open(os.environ['CENSUS_TRACE'], 'a') as out:
    out.write(json.dumps(dict(phase=phase, args=sys.argv[2:], stdin=data,
                             inputs=inputs, fail=fail)) + '\\n')
if fail:
    print('census helper refusal', file=sys.stderr)
    sys.exit(1)
if phase == 'merge':
    Path(sys.argv[3]).write_text('RESOLVED:ours+theirs\\n')
elif phase == 'clean':
    sys.stdout.write(data.replace('WORK:', 'CLEAN:'))
else:
    sys.stdout.write(data.replace('CLEAN:', 'WORK:'))
'''

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def command(repo, *args):
    return subprocess.run(['/usr/bin/git', '-C', str(repo), *args], capture_output=True,
                          text=True, input='', timeout=30, check=False)

def git(repo, *args, codes=(0,)):
    result = command(repo, '-c', 'core.hooksPath=/dev/null', *args)
    if result.returncode not in codes:
        raise RuntimeError((args, result.returncode, result.stderr))
    return result.stdout.strip()

def snapshot(repo):
    return {'head': git(repo, 'rev-parse', 'HEAD'),
            'refs': git(repo, 'show-ref'),
            'head_tree': git(repo, 'rev-parse', 'HEAD^{tree}'),
            'head_content': git(repo, 'show', 'HEAD:data.txt') + '\n',
            'index': git(repo, 'ls-files', '--stage'),
            'index_content': git(repo, 'show', ':data.txt', codes=(0, 128)) + '\n',
            'file': (repo / 'data.txt').read_text() if (repo / 'data.txt').exists() else None,
            'ignored': (repo / 'ignored.txt').read_text(),
            'untracked': (repo / 'untracked.txt').read_text(),
            'merge_head': (repo / '.git/MERGE_HEAD').exists()}

def setup(folder, fmt, operation):
    repo = folder / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'target', f'--object-format={fmt}')
    for key, value in (('user.name', 'Census'), ('user.email', 'census@example.invalid'),
                       ('commit.gpgSign', 'false')):
        git(repo, 'config', key, value)
    merge = operation in ('merge', 'probe')
    (repo / '.gitattributes').write_text('data.txt ' + ('merge=census' if merge else 'filter=census') + '\n')
    (repo / 'data.txt').write_text('base\n' if merge else 'CLEAN:base\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')
    if merge:
        git(repo, 'checkout', '-qb', 'source')
        (repo / 'data.txt').write_text('theirs\n')
        git(repo, 'commit', '-qam', 'source')
        git(repo, 'checkout', '-q', 'target')
        (repo / 'data.txt').write_text('ours\n')
        git(repo, 'commit', '-qam', 'target')
    helper = folder / 'helper.py'
    helper.write_text(HELPER)
    argv = shlex.quote(sys.executable) + ' ' + shlex.quote(str(helper))
    if merge:
        git(repo, 'config', 'merge.census.driver', argv + ' merge %O %A %B %L %P')
    else:
        git(repo, 'config', 'filter.census.clean', argv + ' clean')
        git(repo, 'config', 'filter.census.smudge', argv + ' smudge')
        git(repo, 'config', 'filter.census.required', 'true')
        (repo / 'data.txt').write_text('WORK:updated\n')
    (repo / '.git/info/exclude').write_text('ignored.txt\n')
    (repo / 'ignored.txt').write_text('ignored survives\n')
    (repo / 'untracked.txt').write_text('untracked survives\n')
    return repo

def run_case(folder, fmt, operation, mode, engine):
    repo = setup(folder, fmt, operation)
    trace = folder / 'trace.jsonl'
    os.environ.update(CENSUS_MODE=mode, CENSUS_TRACE=str(trace))
    before = snapshot(repo)
    writer = GitMutations()
    tree = None
    intermediate = None
    if engine == 'native':
        args = {'stage': ('--literal-pathspecs', 'add', '-A', '--', 'data.txt'),
                'restore': ('--literal-pathspecs', 'restore', '--source', 'HEAD', '--worktree', '--', 'data.txt'),
                'merge': ('merge', '--no-ff', '-m', 'merge census', 'source'),
                'probe': ('merge-tree', '--write-tree', '--no-messages', '--name-only', '-z', 'target', 'source')}[operation]
        done = command(repo, *args)
        ok, said = done.returncode == 0, done.stdout + done.stderr
        if operation == 'probe' and ok:
            tree = done.stdout.split('\0')[0]
        if operation == 'merge' and not ok:
            intermediate = snapshot(repo)
            # Match GitMutations.merge's existing cleanup, while retaining raw failure state.
            git(repo, 'reset', '--merge')
    elif operation == 'probe':
        done = GitHistory().merge_probe(repo, 'target', 'source')
        ok, said, tree = done.clean, str(done.conflicts), done.tree
    else:
        done = (writer.stage(repo, ('data.txt',)) if operation == 'stage' else
                writer.restore_file(repo, 'HEAD', 'data.txt') if operation == 'restore' else
                writer.merge(repo, 'source', 'merge', 'merge census'))
        ok, said = done.ok, done.said
    events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    # Merge %A paths include random suffixes; retain semantic inputs instead of temporary names.
    for event in events:
        if event['phase'] == 'merge':
            event['args'][:3] = ['BASE_FILE', 'OURS_FILE', 'THEIRS_FILE']
    after = snapshot(repo)
    result = dict(format=fmt, operation=operation, mode=mode, engine=engine, ok=ok, said=said,
                  before=before, after=after, native_failure_before_reset=intermediate, events=events,
                  candidate_tree=tree, candidate_content=git(repo, 'show', f'{tree}:data.txt') + '\n' if tree else None)
    return json.loads(json.dumps(result).replace(str(folder), 'CASE'))

def compare(n, p):
    return {**{k: n[k] for k in ('format', 'operation', 'mode')},
            'outcome_equal': n['ok'] == p['ok'],
            'state_equal': {k: n['after'][k] == p['after'][k] for k in n['after']},
            'candidate_equal': n['candidate_tree'] == p['candidate_tree']}

def main():
    paths = [*sorted((ROOT / 'src').rglob('*.py')), ROOT / 'artifacts/sec11/race_probe.py',
             ROOT / 'tests/core/test_git_host_exec.py',
             *sorted((ROOT / 'artifacts/sec11/config-namespace').glob('*.py')),
             HERE / 'run.py', HERE / 'verify.py']
    hashes = {str(p.relative_to(ROOT)): digest(p) for p in paths}
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    cases = []
    with tempfile.TemporaryDirectory(prefix='sec20-') as temporary:
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
            for operation in OPERATIONS:
                for mode in MODES:
                    for engine in ('native', 'product'):
                        folder = root / f'{fmt}-{operation}-{mode}-{engine}'
                        folder.mkdir()
                        case = run_case(folder, fmt, operation, mode, engine)
                        cases.append(case)
                        print(fmt, operation, mode, engine, case['ok'], flush=True)
    assert all(digest(ROOT / p) == value for p, value in hashes.items())
    result = dict(base_commit=revision, source_hashes=hashes, cases=cases, python=sys.version,
                  git=subprocess.check_output(['/usr/bin/git', '--version'], text=True).strip())
    (HERE / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    (HERE / 'comparison.json').write_text(json.dumps([
        compare(n, p) for n, p in zip(cases[::2], cases[1::2], strict=True)], indent=2) + '\n')

if __name__ == '__main__':
    main()
