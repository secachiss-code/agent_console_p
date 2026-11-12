"""Explicit, container-only compatibility census. Never imported by the product."""

import ast
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

GIT = "/usr/bin/git"
PHASES = ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit",
          "pre-merge-commit", "post-merge", "reference-transaction", "post-index-change")
HOOK = '''#!{python}
import json, os
from pathlib import Path
import subprocess, sys
phase = Path(sys.argv[0]).name
mode = os.environ['CENSUS_MODE']
with open(os.environ['CENSUS_TRACE'], 'a') as stream:
    stream.write(json.dumps({{'phase': phase, 'args': sys.argv[1:],
        'cwd': os.getcwd(), 'index': os.environ.get('GIT_INDEX_FILE'),
        'editor': os.environ.get('GIT_EDITOR')}}) + '\\n')
if phase in ('pre-commit', 'pre-merge-commit'):
    if mode == 'refuse':
        print('census refusal', file=sys.stderr)
        sys.exit(1)
    if mode == 'stage':
        Path('hook-added.txt').write_text('hook content\\n')
        subprocess.run(['/usr/bin/git', 'add', '--', 'hook-added.txt'], check=True)
if phase == 'commit-msg' and mode == 'message':
    with open(sys.argv[1], 'a') as stream:
        stream.write('\\nEdited-by-census\\n')
'''


def git(repo, *args, codes=(0,), input=None):
    done = subprocess.run([GIT, "-C", str(repo), *args], input=input,
                          text=True, capture_output=True, check=False, timeout=30)
    if done.returncode not in codes:
        raise RuntimeError(f"git {args!r}: {done.returncode}: {done.stderr}")
    return done.stdout.strip()


def commit(repo, name, text, message):
    (repo / name).write_text(text)
    git(repo, "add", "--", name)
    git(repo, "commit", "-qm", message)


def setup(folder, operation):
    repo = folder / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "target")
    git(repo, "config", "user.name", "Census")
    git(repo, "config", "user.email", "census@example.invalid")
    git(repo, "config", "commit.gpgSign", "false")
    commit(repo, "base.txt", "base\n", "base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-qb", "source")
    commit(repo, "source.txt", "source\n", "source")
    source = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "target")
    if operation != "fast_forward":
        commit(repo, "target.txt", "target\n", "target")
    if operation == "commit":
        (repo / "staged.txt").write_text("staged\n")
        git(repo, "add", "--", "staged.txt")
        # Keep one unstaged change to detect accidental broad staging.
        (repo / "base.txt").write_text("unstaged\n")
    target = git(repo, "rev-parse", "target")
    tree = None
    if operation.startswith("ref_"):
        tree = git(repo, "merge-tree", "--write-tree", "target", "source").splitlines()[0]
        git(repo, "checkout", "-qb", "anchor", base)
    return repo, source, target, tree


def snapshot(repo):
    oid = git(repo, "rev-parse", "target")
    return {"oid": oid, "tree": git(repo, "rev-parse", "target^{tree}"),
            "message": git(repo, "log", "-1", "--format=%B", "target"),
            "files": git(repo, "ls-tree", "-r", "--name-only", "target").splitlines(),
            "index": git(repo, "ls-files", "--stage"),
            "status": git(repo, "status", "--porcelain"),
            "head": git(repo, "rev-parse", "HEAD")}


def run_case(folder, engine, operation, mode):
    repo, source, target, tree = setup(folder, operation)
    trace = folder / "trace.jsonl"
    for phase in PHASES:
        path = repo / ".git" / "hooks" / phase
        path.write_text(HOOK.format(python=sys.executable))
        path.chmod(0o700)
    os.environ.update(CENSUS_TRACE=str(trace), CENSUS_MODE=mode)
    before = snapshot(repo)
    # Snapshot commands are outside the measured operation.
    trace.unlink(missing_ok=True)
    message = "census subject\n"
    if engine == "product":
        writer = GitMutations()
        if operation == "commit":
            done = writer.commit(repo, message)
        elif operation.startswith("ref_"):
            checked = writer.merge_hooks(repo, tree, message, squash=operation == "ref_squash")
            done = checked
            if checked.ok:
                parents = (target, source) if operation == "ref_merge" else (target,)
                made = writer.commit_tree(repo, tree, parents, checked.text or message)
                done = made
                if made.ok and made.oid:
                    done = writer.move_ref(repo, "refs/heads/target", made.oid, target)
        else:
            done = writer.merge(repo, source, operation, message)
        ok, said = done.ok, done.said
    else:
        # Native ref-path oracle uses a target checkout to model Git's normal
        # hook semantics. Its cwd/index are deliberately different from the
        # product's root+throwaway-index path; this is recorded in the report.
        if operation.startswith("ref_"):
            git(repo, "checkout", "-q", "target")
            trace.unlink(missing_ok=True)
        if operation == "commit":
            args = ["commit", "-F", "-"]
        elif operation == "fast_forward":
            args = ["merge", "--ff-only", "--no-edit", source]
        elif operation in ("merge", "ref_merge"):
            args = ["merge", "--no-ff", "-m", message, source]
        else:
            args = ["merge", "--squash", source]
        done = subprocess.run([GIT, "-C", str(repo), *args], input=message,
                              capture_output=True, text=True, check=False, timeout=30)
        if done.returncode == 0 and operation in ("squash", "ref_squash"):
            done = subprocess.run([GIT, "-C", str(repo), "commit", "-F", "-"], input=message,
                                  capture_output=True, text=True, check=False, timeout=30)
        ok, said = done.returncode == 0, done.stdout + done.stderr
    events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    after = snapshot(repo)
    # Paths are ephemeral; keep roles rather than inaccessible deleted names.
    for event in events:
        event["cwd"] = "repo" if event["cwd"] == str(repo) else "other"
        event["args"] = ["MESSAGE_FILE" if "COMMIT_EDITMSG" in arg else arg for arg in event["args"]]
        index = event["index"]
        event["index"] = "temporary" if index and "aconsole-hook-" in index else index
    return {"engine": engine, "operation": operation, "mode": mode, "ok": ok,
            "output": said.replace(str(repo), "REPO"), "events": events,
            "before": before, "after": after,
            "target_unchanged": after["oid"] == before["oid"],
            "index_unchanged": after["index"] == before["index"],
            "hook_file_committed": "hook-added.txt" in after["files"],
            "message_edited": "Edited-by-census" in after["message"]}


def inventory():
    found = []
    for path in sorted((ROOT / "src/aconsole/core").rglob("*.py")):
        data = path.read_bytes()
        if b"GitRunner" not in data and b"self._git." not in data and b"self._runner." not in data:
            continue
        tree = ast.parse(data)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in ("read", "write", "invoke", "_run_hook"):
                continue
            receiver = ast.unparse(node.func.value)
            if receiver not in ("self._git", "self._runner", "_GIT_RUNNER", "self"):
                continue
            found.append({"path": str(path.relative_to(ROOT)), "line": node.lineno,
                          "receiver": receiver, "method": node.func.attr,
                          "arguments": [ast.unparse(arg) for arg in node.args]})
    return sorted(found, key=lambda row: (row["path"], row["line"]))


def compare(results):
    rows = []
    for operation in ("commit", "merge", "squash", "fast_forward", "ref_merge", "ref_squash"):
        for mode in ("observe", "refuse", "message", "stage"):
            pair = {r["engine"]: r for r in results if r["operation"] == operation and r["mode"] == mode}
            native, product = pair["native"], pair["product"]
            rows.append({
                "operation": operation, "mode": mode,
                "ok_equal": native["ok"] == product["ok"],
                "tree_equal": native["after"]["tree"] == product["after"]["tree"],
                "message_equal": native["after"]["message"] == product["after"]["message"],
                "phase_sequence_equal": [(e["phase"], e["args"]) for e in native["events"]]
                    == [(e["phase"], e["args"]) for e in product["events"]],
                "native_hook_file_committed": native["hook_file_committed"],
                "product_hook_file_committed": product["hook_file_committed"],
                "product_refusal_preserves_target_and_index":
                    product["target_unchanged"] and product["index_unchanged"]
                    if mode == "refuse" and operation != "fast_forward" else None,
            })
    return rows


def main():
    destination = Path(__file__).resolve().parent
    tracked = [*sorted((ROOT / "src").rglob("*.py")), ROOT / "artifacts/sec11/race_probe.py",
               ROOT / "tests/core/test_git_host_exec.py", Path(__file__).resolve()]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked}
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    results = []
    # All repositories, HOME/config and hooks are private disposable fixtures.
    with tempfile.TemporaryDirectory(prefix="sec15-") as temporary:
        root = Path(temporary)
        home = root / "home"
        home.mkdir()
        os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / "config"),
                          GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
                          LC_ALL="C", GIT_TERMINAL_PROMPT="0")
        for name in list(os.environ):
            if name.startswith("GIT_") and name not in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_TERMINAL_PROMPT"):
                del os.environ[name]
        for operation in ("commit", "merge", "squash", "fast_forward", "ref_merge", "ref_squash"):
            for mode in ("observe", "refuse", "message", "stage"):
                for engine in ("native", "product"):
                    folder = root / f"{engine}-{operation}-{mode}"
                    folder.mkdir()
                    # Hooks must be inactive during construction of each case.
                    os.environ.pop("CENSUS_TRACE", None)
                    result = run_case(folder, engine, operation, mode)
                    results.append(result)
                    print(engine, operation, mode, result["ok"], flush=True)
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == digest for p, digest in hashes.items())
    report = {"base_commit": base, "python": sys.version,
              "git": subprocess.check_output([GIT, "--version"], text=True).strip(),
              "scope": "agent container; public GitMutations, not daemon/UI or installed host boundary",
              "source_hashes": hashes, "inventory_kind": "syntactic candidate calls; not whole-program resolution",
              "inventory": inventory(), "cases": results}
    (destination / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    (destination / "comparison.json").write_text(json.dumps(compare(results), indent=2) + "\n")
    print(f"Saved {len(results)} cases; all source hashes unchanged")


if __name__ == "__main__":
    main()
