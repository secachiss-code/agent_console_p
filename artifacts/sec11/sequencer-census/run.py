"""Container-only native/product sequencer experiment; never product routing."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from aconsole.core.infrastructure.git.operations import GitMutations

HERE = Path(__file__).resolve().parent
OPERATIONS = ("rebase", "cherry-pick")
FLOWS = ("success", "continue", "abort")
MODES = ("observe", "pre_rebase_refuse", "post_rewrite_fail", "post_commit_fail",
         "target_prepared_refuse", "target_committed_fail")
PHASES = ("pre-rebase", "pre-commit", "prepare-commit-msg", "commit-msg", "post-commit",
          "post-rewrite", "post-checkout", "post-merge", "reference-transaction", "post-index-change")
HOOK = r'''#!{python}
import json, os, sys
from pathlib import Path
phase = Path(sys.argv[0]).name
mode = os.environ['SEQ_MODE']
data = sys.stdin.read() if phase in ('reference-transaction', 'post-rewrite') else None
head = (Path(os.environ['SEQ_REPO']) / '.git/HEAD').read_text().strip()
rows = [line.split() for line in (data or '').splitlines()]
# Rebase publishes the branch after detached-HEAD replay. Cherry-pick publishes
# via symbolic HEAD. Ignore ORIG_HEAD, initial detach, symbolic-ref rows/deletions.
target_rows = [row for row in rows if len(row) == 3 and row[0] != row[1]
    and len(row[1]) == 40 and all(c in '0123456789abcdef' for c in row[1])
    and set(row[1]) != {{'0'}} and (row[2] == 'refs/heads/target' or
    (os.environ['SEQ_OPERATION'] == 'cherry-pick' and row[2] == 'HEAD'
     and head == 'ref: refs/heads/target'))]
refuse = ((mode == 'pre_rebase_refuse' and phase == 'pre-rebase') or
    (mode == 'post_rewrite_fail' and phase == 'post-rewrite') or
    (mode == 'post_commit_fail' and phase == 'post-commit') or
    (mode == 'target_prepared_refuse' and phase == 'reference-transaction'
        and sys.argv[1:] == ['prepared'] and bool(target_rows)) or
    (mode == 'target_committed_fail' and phase == 'reference-transaction'
        and sys.argv[1:] == ['committed'] and bool(target_rows)))
with open(os.environ['SEQ_TRACE'], 'a') as stream:
    stream.write(json.dumps({{'phase': phase, 'args': sys.argv[1:], 'stdin': data,
        'cwd': os.getcwd(), 'head_file': head, 'index': os.environ.get('GIT_INDEX_FILE'),
        'git_dir': os.environ.get('GIT_DIR'), 'editor': os.environ.get('GIT_EDITOR'),
        'target_rows': target_rows, 'refuse': refuse}}) + '\n')
if refuse:
    print('sequencer refusal: ' + phase + ' ' + ' '.join(sys.argv[1:]), file=sys.stderr)
    sys.exit(1)
'''


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(repo, *args):
    return subprocess.run(['/usr/bin/git', '-C', str(repo), *args], input='',
                          capture_output=True, text=True, timeout=30, check=False)


def git(repo, *args, codes=(0,)):
    done = command(repo, '-c', 'core.hooksPath=/dev/null', *args)
    if done.returncode not in codes:
        raise RuntimeError(f'{args!r}: {done.returncode}: {done.stderr}')
    return done.stdout.strip()


def commit(repo, name, content, message):
    (repo / name).write_text(content)
    git(repo, 'add', '--', name)
    git(repo, 'commit', '-qm', message)


def setup(folder, conflict):
    repo = folder / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'target')
    for key, value in (('user.name', 'Census'), ('user.email', 'census@example.invalid'),
                       ('commit.gpgSign', 'false'), ('rebase.backend', 'merge')):
        git(repo, 'config', key, value)
    commit(repo, 'shared.txt', 'base\n', 'base')
    git(repo, 'checkout', '-qb', 'source')
    commit(repo, 'shared.txt' if conflict else 'source.txt', 'source\n', 'source')
    source = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-q', 'target')
    commit(repo, 'shared.txt' if conflict else 'target.txt', 'target\n', 'target')
    (repo / '.git/info/exclude').write_text('ignored.txt\n')
    (repo / 'ignored.txt').write_text('preserve ignored\n')
    (repo / 'untracked.txt').write_text('preserve untracked\n')
    for phase in PHASES:
        path = repo / '.git/hooks' / phase
        path.write_text(HOOK.format(python=sys.executable))
        path.chmod(0o700)
    return repo, source


def snapshot(repo):
    head = git(repo, 'rev-parse', 'HEAD')
    target = git(repo, 'rev-parse', 'refs/heads/target')
    metadata = repo / '.git'
    state = {}
    for name in ('rebase-merge', 'rebase-apply', 'sequencer', 'CHERRY_PICK_HEAD',
                 'REBASE_HEAD', 'MERGE_HEAD', 'MERGE_MSG', 'ORIG_HEAD'):
        path = metadata / name
        paths = sorted(path.rglob('*')) if path.is_dir() else [path]
        state.update({str(p.relative_to(metadata)): p.read_text(errors='replace')
                      for p in paths if p.is_file()})
    return {'head': head, 'target': target,
            'symbolic_head': git(repo, 'symbolic-ref', '-q', 'HEAD', codes=(0, 1)),
            'refs': git(repo, 'show-ref'), 'head_tree': git(repo, 'rev-parse', f'{head}^{{tree}}'),
            'target_tree': git(repo, 'rev-parse', f'{target}^{{tree}}'),
            'head_message': git(repo, 'log', '-1', '--format=%B', head),
            'index': git(repo, 'ls-files', '--stage'),
            'unmerged': git(repo, 'ls-files', '--unmerged'),
            'index_sha256': digest(metadata / 'index'),
            'status': git(repo, '--no-optional-locks', 'status', '--porcelain=v1', '--untracked-files=all'),
            'state': state,
            'files': {str(p.relative_to(repo)): digest(p) for p in sorted(repo.rglob('*'))
                      if p.is_file() and '.git' not in p.relative_to(repo).parts}}


def run_case(folder, engine, operation, flow, mode):
    repo, source = setup(folder, flow != 'success')
    trace = folder / 'trace.jsonl'
    os.environ.update(SEQ_TRACE=str(trace), SEQ_REPO=str(repo), SEQ_OPERATION=operation)
    writer = GitMutations()
    steps = []

    def step(label, active_mode, args, product):
        os.environ['SEQ_MODE'] = active_mode
        before = snapshot(repo)
        trace.unlink(missing_ok=True)
        if engine == 'native':
            done = command(repo, *args)
            ok, output, code = done.returncode == 0, done.stdout + done.stderr, done.returncode
        else:
            done = product()
            ok, output, code = done.ok, done.said, None
        events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        # Observation is hook-disabled and happens only after trace capture.
        after = snapshot(repo)
        record = {'label': label, 'active_mode': active_mode, 'native_argv': args if engine == 'native' else None,
                  'ok': ok, 'exit_code': code, 'output': output, 'events': events,
                  'before': before, 'after': after}
        # Normalize only the private fixture directory, retaining metadata roles.
        record = json.loads(json.dumps(record).replace(str(folder), 'CASE'))
        steps.append(record)
        return after

    initial_mode = mode if flow == 'success' else 'observe'
    args = ['rebase', '--no-autostash', source] if operation == 'rebase' else ['cherry-pick', source]
    after = step('start', initial_mode, args,
                 lambda: writer.rebase(repo, source) if operation == 'rebase' else writer.cherry_pick(repo, source))
    if flow != 'success':
        assert not steps[0]['ok'] and after['unmerged'], (engine, operation, flow, mode, steps)
        if flow == 'continue':
            (repo / 'shared.txt').write_text('resolved\n')
            step('resolve_stage', 'observe', ['add', '--', 'shared.txt'],
                 lambda: writer.stage(repo, ('shared.txt',)))
        step(flow, mode, [operation, f'--{flow}'], lambda: writer.step(repo, operation, flow))
    return {'engine': engine, 'operation': operation, 'flow': flow, 'mode': mode,
            'adapter': 'native Git' if engine == 'native' else 'GitMutations (no daemon/service journal)',
            'steps': steps}


def compare(native, product):
    fields = ('head', 'target', 'symbolic_head', 'refs', 'head_tree', 'target_tree', 'head_message',
              'index', 'unmerged', 'status', 'state', 'files')
    return {'operation': native['operation'], 'flow': native['flow'], 'mode': native['mode'],
            'steps': [{'label': left['label'], 'outcome_equal': left['ok'] == right['ok'],
                       'phases_equal': [(e['phase'], e['args']) for e in left['events']]
                           == [(e['phase'], e['args']) for e in right['events']],
                       'state_equal': {field: left['after'][field] == right['after'][field] for field in fields}}
                      for left, right in zip(native['steps'], product['steps'], strict=True)]}


def main():
    sources = [*sorted((ROOT / 'src').rglob('*.py')), ROOT / 'artifacts/sec11/race_probe.py',
               ROOT / 'tests/core/test_git_host_exec.py', Path(__file__).resolve()]
    hashes = {str(p.relative_to(ROOT)): digest(p) for p in sources}
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    results = []
    with tempfile.TemporaryDirectory(prefix='sec18-') as temporary:
        root = Path(temporary)
        home = root / 'home'
        home.mkdir()
        for name in list(os.environ):
            if name.startswith('GIT_') or name.startswith('SEQ_'):
                del os.environ[name]
        os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                          GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', LC_ALL='C',
                          GIT_TERMINAL_PROMPT='0', GIT_EDITOR='/usr/bin/true',
                          GIT_AUTHOR_DATE='2026-01-01T00:00:00+00:00',
                          GIT_COMMITTER_DATE='2026-01-01T00:00:00+00:00')
        for operation in OPERATIONS:
            for flow in FLOWS:
                for mode in MODES:
                    for engine in ('native', 'product'):
                        folder = root / f'{engine}-{operation}-{flow}-{mode}'
                        folder.mkdir()
                        results.append(run_case(folder, engine, operation, flow, mode))
                        print(engine, operation, flow, mode, results[-1]['steps'][-1]['ok'], flush=True)
    assert all(digest(ROOT / p) == value for p, value in hashes.items())
    report = {'base_commit': revision, 'python': sys.version,
              'git': subprocess.check_output(['/usr/bin/git', '--version'], text=True).strip(),
              'scope': 'container fixture; no daemon/UI/host security acceptance',
              'source_hashes': hashes, 'cases': results}
    (HERE / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    comparisons = [compare(n, p) for n, p in zip(results[::2], results[1::2], strict=True)]
    (HERE / 'comparison.json').write_text(json.dumps(comparisons, indent=2) + '\n')
    print(f'Saved {len(results)} cases; source hashes unchanged')


if __name__ == '__main__':
    main()
