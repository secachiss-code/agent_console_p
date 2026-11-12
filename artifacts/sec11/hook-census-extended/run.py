"""Disposable container experiment; no product imports this runner."""

import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
BASE_RUNNER = ROOT / "artifacts/sec11/hook-census/run.py"
base = runpy.run_path(str(BASE_RUNNER))
git, setup = base["git"], base["setup"]
from aconsole.core.infrastructure.git.operations import GitMutations
from aconsole.core.infrastructure.git.runner import GitRunner

PHASES = (*base["PHASES"], "post-rewrite")
OPERATIONS = ("root", "amend", "merge", "ref_merge", "squash", "ref_squash", "fast_forward")
MODES = ("observe", "pre_refuse", "prepare_refuse", "post_fail", "ref_prepared", "ref_committed")
HOOK = r'''#!{python}
import json, os, sys
from pathlib import Path
phase = Path(sys.argv[0]).name
mode = os.environ['EXT_MODE']
with open(os.environ['EXT_TRACE'], 'a') as stream:
    stream.write(json.dumps({{'phase': phase, 'args': sys.argv[1:],
        'cwd': os.getcwd(), 'index': os.environ.get('GIT_INDEX_FILE'),
        'git_dir': os.environ.get('GIT_DIR'), 'editor': os.environ.get('GIT_EDITOR'),
        'stdin': sys.stdin.read() if phase in ('reference-transaction', 'post-rewrite') else None}}) + '\n')
refuse = ((mode == 'pre_refuse' and phase == 'pre-commit') or
          (mode == 'prepare_refuse' and phase == 'prepare-commit-msg') or
          (mode == 'post_fail' and phase in ('post-commit', 'post-merge', 'post-rewrite')) or
          (mode == 'ref_prepared' and phase == 'reference-transaction' and sys.argv[1:] == ['prepared']) or
          (mode == 'ref_committed' and phase == 'reference-transaction' and sys.argv[1:] == ['committed']))
if refuse:
    print('extended refusal: ' + phase + ' ' + ' '.join(sys.argv[1:]), file=sys.stderr)
    sys.exit(1)
'''


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(repo):
    oid = git(repo, "rev-parse", "--verify", "--quiet", "HEAD", codes=(0, 1)) or None
    target = git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/target", codes=(0, 1)) or None
    return {
        "head": oid, "target": target,
        "target_tree": git(repo, "rev-parse", f"{target}^{{tree}}") if target else None,
        "target_message": git(repo, "log", "-1", "--format=%B", target) if target else None,
        "index": git(repo, "ls-files", "--stage"),
        "index_sha256": digest(repo / ".git/index") if (repo / ".git/index").exists() else None,
        "status": git(repo, "--no-optional-locks", "status", "--porcelain=v1", "--untracked-files=all"),
        "files": {str(p.relative_to(repo)): digest(p) for p in sorted(repo.rglob("*"))
                  if p.is_file() and ".git" not in p.relative_to(repo).parts},
        "merge_head": (repo / ".git/MERGE_HEAD").read_text() if (repo / ".git/MERGE_HEAD").exists() else None,
    }


def run_case(folder, engine, operation, mode):
    if operation == "root":
        repo = folder / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "target")
        git(repo, "config", "user.name", "Census")
        git(repo, "config", "user.email", "census@example.invalid")
        (repo / "staged.txt").write_text("root\n")
        git(repo, "add", "--", "staged.txt")
        source = target = tree = None
    else:
        repo, source, target, tree = setup(folder, operation)
        if operation == "amend":
            (repo / "staged.txt").write_text("amend\n")
            git(repo, "add", "--", "staged.txt")
    (repo / "untracked.txt").write_text("preserve untracked\n")
    (repo / ".git/info/exclude").write_text("ignored.txt\n")
    (repo / "ignored.txt").write_text("preserve ignored\n")
    if operation != "root":
        (repo / "base.txt").write_text("unstaged must survive\n")
    trace = folder / "trace.jsonl"
    hooks = repo / ".git/hooks"
    sample = hooks / "pre-merge-commit.sample"
    sample_hash = digest(sample)
    for phase in PHASES:
        path = hooks / phase
        # Use the installed sample verbatim: it sources git-sh-setup and execs
        # pre-commit. Direct merge_hooks lacks Git's native hook environment.
        path.write_bytes(sample.read_bytes() if phase == "pre-merge-commit"
                         else HOOK.format(python=sys.executable).encode())
        path.chmod(0o700)
    os.environ.update(EXT_TRACE=str(trace), EXT_MODE=mode)
    before = snapshot(repo)
    trace.unlink(missing_ok=True)
    message = "extended subject\n"
    if engine == "native":
        if operation.startswith("ref_"):
            # Match SEC-15's intentional oracle: target checkout versus anchor.
            git(repo, "-c", "core.hooksPath=/dev/null", "checkout", "-q", "target")
            (repo / "base.txt").write_text("unstaged must survive\n")
            before = snapshot(repo)
            trace.unlink(missing_ok=True)
        if operation in ("root", "amend"):
            args = ["commit", *(["--amend"] if operation == "amend" else []), "-F", "-"]
        elif operation == "fast_forward":
            args = ["merge", "--ff-only", "--no-edit", source]
        elif operation in ("merge", "ref_merge"):
            args = ["merge", "--no-ff", "-m", message, source]
        else:
            args = ["merge", "--squash", source]
        done = subprocess.run(["/usr/bin/git", "-C", str(repo), *args], input=message,
                              capture_output=True, text=True, timeout=30, check=False)
        if done.returncode == 0 and operation in ("squash", "ref_squash"):
            done = subprocess.run(["/usr/bin/git", "-C", str(repo), "commit", "-F", "-"],
                                  input=message, capture_output=True, text=True, timeout=30, check=False)
        ok, output = done.returncode == 0, done.stdout + done.stderr
        adapter = "native Git"
    else:
        writer = GitMutations()
        adapter = "GitMutations"
        if operation == "root":
            done = writer.commit(repo, message)
        elif operation == "amend":
            # The product has no public amend operation. Probe runner support
            # explicitly, without pretending this is a daemon/user flow.
            result = GitRunner().write(repo, "commit", "--amend", "-F", "-",
                                       stdin=message.encode(), hooks=True)
            ok, output = result.code == 0, result.said
            adapter = "GitRunner.write (no public amend API)"
        elif operation.startswith("ref_"):
            done = writer.merge_hooks(repo, tree, message, squash=operation == "ref_squash")
            if done.ok:
                parents = (target, source) if operation == "ref_merge" else (target,)
                done = writer.commit_tree(repo, tree, parents, done.text or message)
                if done.ok and done.oid:
                    done = writer.move_ref(repo, "refs/heads/target", done.oid, target)
        else:
            done = writer.merge(repo, source, operation, message)
        if operation != "amend":
            ok, output = done.ok, done.said
    # End trace before observing state. Normalize only ephemeral locations.
    events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    for event in events:
        event["cwd"] = "repo" if event["cwd"] == str(repo) else "other"
        event["index"] = "temporary" if event["index"] and "aconsole-hook-" in event["index"] else event["index"]
        event["args"] = ["MESSAGE_FILE" if "COMMIT_EDITMSG" in arg else arg for arg in event["args"]]
    after = snapshot(repo)
    return {"engine": engine, "operation": operation, "mode": mode, "adapter": adapter,
            "ok": ok, "output": output.replace(str(repo), "REPO"), "events": events,
            "sample_sha256": sample_hash, "before": before, "after": after,
            "target_changed": before["target"] != after["target"],
            "index_bytes_unchanged": before["index_sha256"] == after["index_sha256"],
            "worktree_files_unchanged": before["files"] == after["files"]}


def main():
    destination = Path(__file__).resolve().parent
    sources = [*sorted((ROOT / "src").rglob("*.py")), BASE_RUNNER,
               ROOT / "artifacts/sec11/race_probe.py", ROOT / "tests/core/test_git_host_exec.py",
               Path(__file__).resolve()]
    hashes = {str(p.relative_to(ROOT)): digest(p) for p in sources}
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    results = []
    with tempfile.TemporaryDirectory(prefix="sec16-") as temporary:
        root = Path(temporary)
        home = root / "home"
        home.mkdir()
        for name in list(os.environ):
            if name.startswith("GIT_"):
                del os.environ[name]
        os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / "config"),
                          GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", LC_ALL="C",
                          GIT_TERMINAL_PROMPT="0", GIT_EDITOR="/usr/bin/true",
                          GIT_AUTHOR_DATE="2026-01-01T00:00:00+00:00",
                          GIT_COMMITTER_DATE="2026-01-01T00:00:00+00:00")
        for operation in OPERATIONS:
            for mode in MODES:
                for engine in ("native", "product"):
                    folder = root / f"{engine}-{operation}-{mode}"
                    folder.mkdir()
                    os.environ.pop("EXT_TRACE", None)
                    result = run_case(folder, engine, operation, mode)
                    results.append(result)
                    print(engine, operation, mode, result["ok"], flush=True)
    assert all(digest(ROOT / p) == value for p, value in hashes.items())
    comparisons = []
    for native, product in zip(results[::2], results[1::2], strict=True):
        comparisons.append({"operation": native["operation"], "mode": native["mode"],
                            "outcome_equal": native["ok"] == product["ok"],
                            "tree_equal": native["after"]["target_tree"] == product["after"]["target_tree"],
                            "message_equal": native["after"]["target_message"] == product["after"]["target_message"],
                            "phases_equal": [(e["phase"], e["args"]) for e in native["events"]]
                                == [(e["phase"], e["args"]) for e in product["events"]]})
    report = {"base_commit": revision, "python": sys.version,
              "git": subprocess.check_output(["/usr/bin/git", "--version"], text=True).strip(),
              "scope": "container experiment; no daemon/UI/host security acceptance",
              "source_hashes": hashes, "cases": results}
    (destination / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    (destination / "comparison.json").write_text(json.dumps(comparisons, indent=2) + "\n")
    print(f"Saved {len(results)} cases; source hashes unchanged")


if __name__ == "__main__":
    main()
