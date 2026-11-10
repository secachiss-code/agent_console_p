"""Disposable container census; does not install or route a product worker."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = HERE.parent / 'sequencer-census/run.py'
spec = importlib.util.spec_from_file_location('sec18_census', BASE)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
FORMATS = ('sha1', 'sha256')
OPERATIONS = ('rebase', 'cherry-pick')
MODES = ('observe', 'prepare_message', 'commit_message', 'pre_index', 'prepare_index')
MARKER = 'SEC22 hook message'
CONTENT = 'SEC22 hook index\n'
HOOK = r'''#!{python}
import json, os, subprocess, sys
from pathlib import Path
phase = Path(sys.argv[0]).name
mode = os.environ['EDIT_MODE']
data = sys.stdin.read() if phase in ('reference-transaction', 'post-rewrite') else None
message = Path(sys.argv[1]) if phase in ('prepare-commit-msg', 'commit-msg') else None
before = message.read_text() if message else None
action = None
if (mode == 'prepare_message' and phase == 'prepare-commit-msg') or (mode == 'commit_message' and phase == 'commit-msg'):
    message.write_text('SEC22 hook message\n\n' + before)
    action = 'message'
if (mode == 'pre_index' and phase == 'pre-commit') or (mode == 'prepare_index' and phase == 'prepare-commit-msg'):
    Path('hook-added.txt').write_text('SEC22 hook index\n')
    done = subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', 'add', '--', 'hook-added.txt'], capture_output=True, text=True, timeout=30)
    if done.returncode:
        raise RuntimeError(done.stderr)
    action = 'index'
with open(os.environ['EDIT_TRACE'], 'a') as stream:
    stream.write(json.dumps({{'phase': phase, 'args': sys.argv[1:], 'stdin': data,
        'cwd': os.getcwd(), 'index': os.environ.get('GIT_INDEX_FILE'),
        'git_dir': os.environ.get('GIT_DIR'), 'editor': os.environ.get('GIT_EDITOR'),
        'action': action, 'message_before': before,
        'message_after': message.read_text() if message else None}}) + '\n')
'''


def snapshot(repo):
    state = base.snapshot(repo)
    state['tree_entries'] = base.git(repo, 'ls-tree', '-r', 'HEAD')
    for label, revision in (('head', 'HEAD:hook-added.txt'), ('index', ':hook-added.txt')):
        state[label + '_hook_content'] = base.git(repo, 'show', revision, codes=(0, 128))
    return state


def run_case(folder, engine, operation, mode, object_format):
    os.environ['GIT_DEFAULT_HASH'] = object_format
    repo, source = base.setup(folder, True)
    for phase in base.PHASES:
        (repo / '.git/hooks' / phase).write_text(HOOK.format(python=sys.executable))
    assert base.git(repo, 'rev-parse', '--show-object-format') == object_format
    trace = folder / 'trace.jsonl'
    os.environ['EDIT_TRACE'] = str(trace)
    writer = base.GitMutations()
    steps = []

    def step(label, active_mode, args, product):
        os.environ['EDIT_MODE'] = active_mode
        before = snapshot(repo)
        trace.unlink(missing_ok=True)
        if engine == 'native':
            done = base.command(repo, *args)
            ok, output, code = done.returncode == 0, done.stdout + done.stderr, done.returncode
        else:
            done = product()
            ok, output, code = done.ok, done.said, None
        events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        record = dict(label=label, active_mode=active_mode, ok=ok, output=output,
                      exit_code=code, events=events, before=before, after=snapshot(repo),
                      native_argv=args if engine == 'native' else None)
        steps.append(json.loads(json.dumps(record).replace(str(folder), 'CASE')))

    args = ['rebase', '--no-autostash', source] if operation == 'rebase' else ['cherry-pick', source]
    step('start', 'observe', args, lambda: writer.rebase(repo, source) if operation == 'rebase' else writer.cherry_pick(repo, source))
    assert not steps[-1]['ok'] and steps[-1]['after']['unmerged']
    (repo / 'shared.txt').write_text('resolved\n')
    step('resolve_stage', 'observe', ['add', '--', 'shared.txt'], lambda: writer.stage(repo, ('shared.txt',)))
    step('continue', mode, [operation, '--continue'], lambda: writer.step(repo, operation, 'continue'))
    return dict(engine=engine, operation=operation, mode=mode, format=object_format,
                adapter='native Git' if engine == 'native' else 'GitMutations; no daemon/service journal', steps=steps)


def compare(native, product):
    result = base.compare(native | {'flow': 'continue'}, product | {'flow': 'continue'})
    result['format'] = native['format']
    for comparison, left, right in zip(result['steps'], native['steps'], product['steps'], strict=True):
        for field in ('tree_entries', 'head_hook_content', 'index_hook_content'):
            comparison['state_equal'][field] = left['after'][field] == right['after'][field]
    return result


def main():
    sources = [*sorted((ROOT / 'src').rglob('*.py')), ROOT / 'artifacts/sec11/race_probe.py',
               ROOT / 'tests/core/test_git_host_exec.py', *sorted((HERE.parent / 'config-namespace').glob('*.py')),
               BASE, HERE / 'run.py', HERE / 'verify.py']
    hashes = {str(p.relative_to(ROOT)): base.digest(p) for p in sources}
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    cases = []
    with tempfile.TemporaryDirectory(prefix='sec22-') as temporary:
        root = Path(temporary)
        home = root / 'home'
        home.mkdir()
        for name in list(os.environ):
            if name.startswith(('GIT_', 'SEQ_', 'EDIT_')):
                del os.environ[name]
        os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                          GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', LC_ALL='C',
                          GIT_TERMINAL_PROMPT='0', GIT_EDITOR='/usr/bin/true',
                          GIT_AUTHOR_DATE='2026-01-01T00:00:00+00:00', GIT_COMMITTER_DATE='2026-01-01T00:00:00+00:00')
        for object_format in FORMATS:
            for operation in OPERATIONS:
                for mode in MODES:
                    for engine in ('native', 'product'):
                        folder = root / f'{object_format}-{operation}-{mode}-{engine}'
                        folder.mkdir()
                        case = run_case(folder, engine, operation, mode, object_format)
                        cases.append(case)
                        print(object_format, operation, mode, engine, case['steps'][-1]['ok'], flush=True)
    assert all(base.digest(ROOT / p) == value for p, value in hashes.items())
    report = dict(base_commit=revision, python=sys.version, source_hashes=hashes, cases=cases,
                  git=subprocess.check_output(['/usr/bin/git', '--version'], text=True).strip(),
                  scope='container native/GitMutations evidence; no daemon, UI or security acceptance')
    (HERE / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    comparisons = [compare(n, p) for n, p in zip(cases[::2], cases[1::2], strict=True)]
    (HERE / 'comparison.json').write_text(json.dumps(comparisons, indent=2) + '\n')
    print(f'Saved {len(cases)} cases; source hashes unchanged')


if __name__ == '__main__':
    main()
