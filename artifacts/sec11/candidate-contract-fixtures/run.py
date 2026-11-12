"""Finite SEC31 native/adapter lab; all Git mutations live under TemporaryDirectory."""

import json
import platform
import shlex
import shutil
import tempfile
from pathlib import Path

from adapter import STATUS, LabAdapter, digest, git, identity, manifest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CONTRACT = ROOT / "artifacts/sec11/stage-a-contract/contract.json"
C = json.loads(CONTRACT.read_text())
BASE = "e95dde8b26b8bb65845a335dea6f5819d3d16c7d"


def check(value, message):
    if not value:
        raise AssertionError(message)


def must(repo, args, home):
    result = git(repo, args, home)
    check(result.returncode == 0, f"native {args}: {result.stderr!r}")
    return result.stdout


def put(repo, name, data, executable=False):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    if executable:
        path.chmod(0o755)


def init(repo, fmt, home):
    repo.mkdir()
    empty = home / "empty-template"
    empty.mkdir(exist_ok=True)
    must(repo, ["init", "--quiet", "--initial-branch=main", f"--object-format={fmt}", f"--template={empty}"], home)


def commit(repo, message, home):
    must(repo, ["add", "-A"], home)
    must(repo, ["commit", "--quiet", "-m", message], home)


def fixture(repo, fmt, origin, home):
    init(repo, fmt, home)
    put(repo, ".gitignore", "ignored.txt\n")
    put(repo, "modified.txt", "base\n")
    put(repo, "deleted.txt", "base\n")
    if origin == "detached":
        commit(repo, "base", home)
        must(repo, ["checkout", "--quiet", "--detach"], home)
    put(repo, "modified.txt", "updated\n")
    if origin == "detached":
        (repo / "deleted.txt").unlink()
    put(repo, "added.txt", "new\n")
    for name in ("-leading", "space name", "line\nname", "colon:name", "brackets[1]", "юникод", ":(glob)*"):
        put(repo, name, "literal\n")
    put(repo, "untracked.txt", "KEEP untracked\n")
    put(repo, "ignored.txt", "KEEP ignored\n")


PATHS = [
    ".gitignore",
    "modified.txt",
    "deleted.txt",
    "added.txt",
    "-leading",
    "space name",
    "line\nname",
    "colon:name",
    "brackets[1]",
    "юникод",
    ":(glob)*",
]


def state(repo, home):
    return {
        "digest": identity(repo),
        "raw_index_digest": digest((repo / ".git/index").read_bytes()) if (repo / ".git/index").exists() else None,
        "index_hex": must(repo, ["ls-files", "--stage", "-z"], home).hex(),
        "refs_hex": must(repo, ["for-each-ref", "--format=%(refname) %(objectname)"], home).hex(),
        "head_hex": (repo / ".git/HEAD").read_bytes().hex(),
        "files": {k: v for k, v in manifest(repo).items() if not k.startswith(".git/") and k != ".git"},
    }


def request(repo, operation="StageCandidatePaths"):
    value = {"operation": operation, "candidate_id": "fixture", "expected_digest": identity(repo)}
    if operation == "StageCandidatePaths":
        value["paths"] = PATHS
    return value


def run():
    rows = []
    with tempfile.TemporaryDirectory(prefix="sec31-lab-") as scratch:
        work = Path(scratch)
        home = work / "home"
        home.mkdir()
        sequence = 0

        def clone(original):
            nonlocal sequence
            sequence += 1
            target = work / f"candidate-{sequence}"
            shutil.copytree(original, target, symlinks=True)
            return target

        def denied(
            original,
            fmt,
            origin,
            group,
            name,
            mutate=lambda p: None,
            edit_request=lambda q: None,
            diagnostic="UNSUPPORTED_OPERATION",
            operation="StageCandidatePaths",
        ):
            original_before = identity(original)
            candidate = clone(original)
            mutate(candidate)
            req = request(candidate, operation)
            edit_request(req)
            before = identity(candidate)
            adapter = LabAdapter({"fixture": candidate}, home)
            outcome = adapter.execute(req)
            after = identity(candidate)
            check(outcome.get("diagnostic") == diagnostic and not outcome["ok"], name + ": " + str(outcome))
            check(before == after and original_before == identity(original), name + " mutated input")
            check(not adapter.commands, name + " invoked candidate execution")
            rows.append(
                {
                    "group": group,
                    "name": name,
                    "format": fmt,
                    "origin": origin,
                    "operation": operation,
                    "outcome": outcome,
                    "before": before,
                    "after": after,
                    "original_before": original_before,
                    "original_after": identity(original),
                    "commands": adapter.commands,
                }
            )

        for fmt in ("sha1", "sha256"):
            for origin in ("detached", "unborn"):
                original = work / f"original-{fmt}-{origin}"
                fixture(original, fmt, origin, home)
                before_original = identity(original)
                native, candidate = clone(original), clone(original)
                before = state(candidate, home)
                adapter = LabAdapter({"fixture": candidate}, home)
                inspected = adapter.execute(request(candidate, "InspectCandidate"))
                check(inspected["ok"], "inspect failed")
                native_status = must(native, STATUS, home)
                check(bytes.fromhex(inspected["output_hex"]) == native_status, "status differs")
                check(identity(candidate) == before["digest"], "inspect wrote")
                # Unborn delete path must exist; detached case tests a tracked deletion.
                if origin == "unborn":
                    put(native, "deleted.txt", "base\n")
                must(native, ["--literal-pathspecs", "add", "-A", "--", *PATHS], home)
                native_tree = must(native, ["write-tree"], home)
                staged = adapter.execute(request(candidate))
                check(staged["ok"] and bytes.fromhex(staged["output_hex"]) == native_tree, "tree parity")
                after = state(candidate, home)
                native_after = state(native, home)
                check(after["index_hex"] == native_after["index_hex"], "index entry parity")
                check(
                    before["files"] == after["files"]
                    and before["refs_hex"] == after["refs_hex"]
                    and before["head_hex"] == after["head_hex"],
                    "stage wrote files/refs",
                )
                check(identity(original) == before_original, "original modified")
                tree_entries = must(candidate, ["ls-tree", "-rz", native_tree.decode().strip()], home).hex()
                actual_paths = {
                    record.split(b"\t", 1)[1].decode() for record in bytes.fromhex(tree_entries).split(b"\0") if record
                }
                check(
                    actual_paths == set(PATHS) - ({"deleted.txt"} if origin == "detached" else set()),
                    "literal tree paths",
                )
                rows.append(
                    {
                        "group": "T-positive",
                        "name": "literal-status-stage",
                        "format": fmt,
                        "origin": origin,
                        "inspect": inspected,
                        "stage": staged,
                        "before": before,
                        "after": after,
                        "native_after": native_after,
                        "native_status_hex": native_status.hex(),
                        "native_tree_hex": native_tree.hex(),
                        "tree_entries_hex": tree_entries,
                        "original_before": before_original,
                        "original_after": identity(original),
                        "commands": adapter.commands,
                    }
                )

                helpers = {
                    "required-filter": ("filter.demo.clean", "false"),
                    "optional-filter": ("filter.demo.smudge", "cat"),
                    "filter-process": ("filter.demo.process", "false"),
                    "unused-driver": ("merge.unused.driver", "false"),
                    "signing": ("gpg.program", "/unavailable"),
                    "fsmonitor": ("core.fsmonitor", "/unavailable"),
                    "external-diff": ("diff.external", "false"),
                    "textconv": ("diff.demo.textconv", "cat"),
                    "config-command": ("core.sshCommand", "false"),
                }
                for name, (key, value) in helpers.items():

                    def configure_helper(p, k=key, v=value, n=name):
                        must(p, ["config", k, v], home)
                        if n == "required-filter":
                            must(p, ["config", "filter.demo.required", "true"], home)
                            must(p, ["config", "filter.demo.smudge", "false"], home)
                            put(p, ".gitattributes", "modified.txt filter=demo\n")
                            put(p, "modified.txt", "WORK:must-not-stage-raw\n")

                    denied(original, fmt, origin, "T-helper", name, configure_helper, diagnostic="UNSUPPORTED_HELPER")
                for name, text in (
                    ("filter-attribute", "*.txt filter=demo\n"),
                    ("merge-attribute", "*.txt merge=demo\n"),
                    ("unset-filter", "*.txt -filter\n"),
                    ("info-attribute", "*.txt filter=demo\n"),
                ):
                    denied(
                        original,
                        fmt,
                        origin,
                        "T-helper",
                        name,
                        lambda p, t=text, n=name: put(
                            p, ".git/info/attributes" if n == "info-attribute" else ".gitattributes", t
                        ),
                        diagnostic="UNSUPPORTED_HELPER",
                    )

                for phase in C["phase_contract"]["observed_hook_phases"] + [
                    "unknown-hook",
                    "executable.sample",
                    "pre-merge-fallback",
                ]:
                    for operation in ("InspectCandidate", "StageCandidatePaths"):

                        def hook(p, phase=phase):
                            text = "#!/bin/sh\nprintf 'WORKER HOOK RAN' > worker-hook-marker\n"
                            if phase == "pre-merge-fallback":
                                text = FALLBACK.read_text()
                            put(
                                p,
                                ".git/hooks/" + ("pre-merge-commit" if phase == "pre-merge-fallback" else phase),
                                text,
                                True,
                            )

                        denied(
                            original,
                            fmt,
                            origin,
                            "T-hooks",
                            phase,
                            hook,
                            diagnostic="UNSUPPORTED_HOOK",
                            operation=operation,
                        )
                denied(
                    original,
                    fmt,
                    origin,
                    "T-hooks",
                    "hooksPath",
                    lambda p: must(p, ["config", "core.hooksPath", "elsewhere"], home),
                    diagnostic="UNSUPPORTED_HOOK",
                )
                for name in ("pre-commit", "post-index-change", "pre-merge-commit.sample"):
                    candidate = clone(original)
                    put(candidate, ".git/hooks/" + name, "#!/bin/sh\nexit 77\n")
                    adapter = LabAdapter({"fixture": candidate}, home)
                    pre = identity(candidate)
                    inspect = adapter.execute(request(candidate, "InspectCandidate"))
                    staged = adapter.execute(request(candidate))
                    check(inspect["ok"] and staged["ok"], "non-executable hook rejected")
                    rows.append(
                        {
                            "group": "T-hooks",
                            "name": "nonexecutable-" + name,
                            "format": fmt,
                            "origin": origin,
                            "inspect": inspect,
                            "stage": staged,
                            "before": pre,
                            "after": identity(candidate),
                            "original_before": before_original,
                            "original_after": identity(original),
                            "commands": adapter.commands,
                        }
                    )

                for path in (
                    "/tmp/outside",
                    "../outside",
                    "a/../../outside",
                    "bad\0name",
                    ".git/config",
                    "a/.GiT/config",
                    "",
                    ".",
                    "a//b",
                ):
                    denied(
                        original,
                        fmt,
                        origin,
                        "T-input",
                        "path-" + repr(path),
                        edit_request=lambda q, p=path: q.update(paths=[p]),
                    )
                for field in ("argv", "env", "executable", "options"):
                    denied(
                        original,
                        fmt,
                        origin,
                        "T-input",
                        "unknown-" + field,
                        edit_request=lambda q, f=field: q.update({f: "untrusted"}),
                    )
                for name, paths in (
                    ("empty-paths", []),
                    ("duplicate", ["added.txt", "added.txt"]),
                    ("bad-type", [1]),
                    ("too-many", [str(i) for i in range(4097)]),
                ):
                    denied(original, fmt, origin, "T-input", name, edit_request=lambda q, p=paths: q.update(paths=p))
                denied(
                    original,
                    fmt,
                    origin,
                    "T-input",
                    "stale-digest",
                    edit_request=lambda q: q.update(expected_digest="0" * 64),
                    diagnostic="INPUT_UNSTABLE",
                )
                denied(
                    original,
                    fmt,
                    origin,
                    "T-input",
                    "candidate-path",
                    edit_request=lambda q: q.update(candidate_id=str(original)),
                    diagnostic="UNSUPPORTED_ORIGIN",
                )
                for name, mutate in {
                    "alternates": lambda p: put(p, ".git/objects/info/alternates", "/unavailable\n"),
                    "commondir": lambda p: put(p, ".git/commondir", "../other\n"),
                    "submodule": lambda p: put(p, ".gitmodules", '[submodule "x"]\npath=x\n'),
                    "sparse-index": lambda p: put(p, ".git/info/sparse-checkout", "*\n"),
                    "split-index": lambda p: put(p, ".git/sharedindex.fake", "data\n"),
                    "include": lambda p: must(p, ["config", "include.path", "/unavailable"], home),
                    "includeIf": lambda p: must(p, ["config", "includeIf.gitdir:/.path", "/unavailable"], home),
                    "symlink-parent": lambda p: (p / "escape").symlink_to(original, target_is_directory=True),
                    "bare-config": lambda p: must(p, ["config", "core.bare", "true"], home),
                    "external-gitdir": lambda p: replace_gitdir(p),
                }.items():
                    denied(original, fmt, origin, "T-input", name, mutate, diagnostic="UNSUPPORTED_ORIGIN")
                # Mode/content drift after request capture must invalidate the digest.
                for name, mutate in (
                    ("hook-mode-drift", lambda p: (p / ".git/hooks/pre-commit").chmod(0o755)),
                    ("attribute-drift", lambda p: put(p, ".gitattributes", "*.txt filter=late\n")),
                    ("ignore-drift", lambda p: put(p, ".gitignore", "*\n")),
                ):
                    candidate = clone(original)
                    if name == "hook-mode-drift":
                        put(candidate, ".git/hooks/pre-commit", "#!/bin/sh\nexit 77\n")
                    req = request(candidate)
                    mutate(candidate)
                    pre = identity(candidate)
                    adapter = LabAdapter({"fixture": candidate}, home)
                    outcome = adapter.execute(req)
                    check(
                        outcome.get("diagnostic") == "INPUT_UNSTABLE"
                        and identity(candidate) == pre
                        and not adapter.commands,
                        name,
                    )
                    rows.append(
                        {
                            "group": "T-input",
                            "name": name,
                            "format": fmt,
                            "origin": origin,
                            "outcome": outcome,
                            "before": pre,
                            "after": identity(candidate),
                            "original_before": before_original,
                            "original_after": identity(original),
                            "commands": adapter.commands,
                        }
                    )
                for name in (
                    [r["id"] for r in C["excluded"]]
                    + [r["id"] for r in C["lifecycle"]]
                    + [
                        "root",
                        "amend",
                        "sign",
                        "merge-no-checkout",
                        "squash",
                        "ff",
                        "rebase",
                        "cherry-pick",
                        "continue",
                        "abort",
                        "checkpoint-capture",
                        "retention-delete",
                        "close-worktree",
                        "cleanup-worktree",
                        "rollback-create",
                        "prune",
                        "GitRunner.read",
                        "GitRunner.write",
                        "_run_hook",
                    ]
                ):
                    denied(original, fmt, origin, "T-deny", name, edit_request=lambda q, n=name: q.update(operation=n))
            rows.extend(native_hooks(work, fmt, home))
            rows.extend(native_fallback(work, fmt, home))
            rows.extend(native_helpers(work, fmt, home))
    return {
        "schema": 1,
        "base_commit": BASE,
        "stage_A": "INCOMPLETE",
        "SEC11": "failed",
        "SEC13": "failed",
        "security_acceptance": "NOT RUN",
        "production_routing": False,
        "containment": False,
        "runtime_limits": "NOT IMPLEMENTED; laboratory only",
        "git": git(HERE, ["--version"], Path("/tmp")).stdout.decode().strip(),
        "python": platform.python_version(),
        "contract_sha256": digest(CONTRACT.read_bytes()),
        "fallback_sha256": digest(FALLBACK.read_bytes()),
        "source_hashes": {
            str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted((ROOT / "src").rglob("*.py"))
        },
        "artifact_hashes": {
            n: digest((HERE / n).read_bytes()) for n in ("adapter.py", "run.py", "verify.py", "REPORT.md")
        },
        "cases": rows,
    }


def replace_gitdir(p):
    shutil.rmtree(p / ".git")  # disposable fixture only
    put(p, ".git", "gitdir: /unavailable\n")


FALLBACK = Path("/usr/share/git-core/templates/hooks/pre-merge-commit.sample")


def native_hooks(work, fmt, home):
    repo = work / ("native-hooks-" + fmt)
    init(repo, fmt, home)
    trace = work / ("hooks-" + fmt)
    for phase in C["phase_contract"]["observed_hook_phases"]:
        put(
            repo,
            ".git/hooks/" + phase,
            "#!/bin/sh\nprintf '%s\\n' " + shlex.quote(phase) + " >> " + shlex.quote(str(trace)) + "\n",
            True,
        )
    put(repo, "base", "base\n")
    commit(repo, "base", home)
    must(repo, ["checkout", "-qb", "side"], home)
    put(repo, "side", "side\n")
    commit(repo, "side", home)
    must(repo, ["checkout", "-q", "main"], home)
    put(repo, "main", "main\n")
    commit(repo, "main", home)
    must(repo, ["merge", "--no-edit", "side"], home)
    must(repo, ["commit", "--amend", "--no-edit", "--quiet"], home)
    must(repo, ["checkout", "-qb", "topic"], home)
    put(repo, "topic", "topic\n")
    commit(repo, "topic", home)
    must(repo, ["checkout", "-q", "main"], home)
    put(repo, "extra", "extra\n")
    commit(repo, "extra", home)
    must(repo, ["checkout", "-q", "topic"], home)
    must(repo, ["rebase", "main"], home)
    events = trace.read_text().splitlines()
    check(
        set(events) == set(C["phase_contract"]["observed_hook_phases"]),
        "native hook phases missing: " + str(set(C["phase_contract"]["observed_hook_phases"]) - set(events)),
    )
    put(repo, ".git/hooks/pre-commit", "#!/bin/sh\nexit 71\n", True)
    before = state(repo, home)
    refusal = git(repo, ["commit", "--allow-empty", "-m", "must-refuse"], home)
    after = state(repo, home)
    check(refusal.returncode != 0 and before["refs_hex"] == after["refs_hex"], "native hook refusal")
    return [
        {
            "group": "native-hooks",
            "name": "eleven-phases-and-refusal",
            "format": fmt,
            "events": events,
            "refusal_exit": refusal.returncode,
            "before": before,
            "after": after,
        }
    ]


def native_fallback(work, fmt, home):
    rows = []
    for refuse in (False, True):
        repo = work / f"native-fallback-{fmt}-{refuse}"
        init(repo, fmt, home)
        put(repo, "base", "base\n")
        commit(repo, "base", home)
        must(repo, ["checkout", "-qb", "side"], home)
        put(repo, "side", "side\n")
        commit(repo, "side", home)
        must(repo, ["checkout", "-q", "main"], home)
        put(repo, "main", "main\n")
        commit(repo, "main", home)
        put(repo, ".git/hooks/pre-merge-commit", FALLBACK.read_text(), True)
        marker = repo / ".git/fallback-marker"
        put(
            repo,
            ".git/hooks/pre-commit",
            "#!/bin/sh\nprintf fallback > " + shlex.quote(str(marker)) + "\nexit " + ("74" if refuse else "0") + "\n",
            True,
        )
        before = state(repo, home)
        result = git(repo, ["merge", "--no-edit", "side"], home)
        after = state(repo, home)
        check(marker.read_text() == "fallback" and (result.returncode != 0) == refuse, "native sample fallback control")
        check((before["refs_hex"] == after["refs_hex"]) == refuse, "fallback ref result")
        rows.append(
            {
                "group": "native-fallback",
                "name": "installed-sample",
                "format": fmt,
                "refuse": refuse,
                "exit": result.returncode,
                "marker": marker.read_text(),
                "before": before,
                "after": after,
                "stderr_hex": result.stderr.hex(),
            }
        )
    return rows


def native_helpers(work, fmt, home):
    rows = []
    for kind in ("clean", "smudge", "merge"):
        for refuse in (False, True):
            repo = work / f"native-{fmt}-{kind}-{refuse}"
            init(repo, fmt, home)
            put(repo, "data.txt", "CLEAN:base\n" if kind != "merge" else "base\n")
            put(repo, ".gitattributes", "data.txt " + ("merge=demo\n" if kind == "merge" else "filter=demo\n"))
            commit(repo, "base", home)
            if kind == "merge":
                must(repo, ["checkout", "-qb", "side"], home)
                put(repo, "data.txt", "theirs\n")
                commit(repo, "theirs", home)
                must(repo, ["checkout", "-q", "main"], home)
                put(repo, "data.txt", "ours\n")
                commit(repo, "ours", home)
            marker = repo / ".git/helper-marker"
            script = repo / ".git/lab-helper"
            action = (
                "exit 73"
                if refuse
                else {
                    "clean": "sed 's/WORK:/CLEAN:/g'",
                    "smudge": "sed 's/CLEAN:/WORK:/g'",
                    "merge": "printf 'RESOLVED:ours+theirs\\n' > \"$1\"",
                }[kind]
            )
            put(
                repo,
                ".git/lab-helper",
                "#!/bin/sh\nprintf '%s\\n' " + kind + " >> " + shlex.quote(str(marker)) + "\n" + action + "\n",
                True,
            )
            if kind == "merge":
                must(repo, ["config", "merge.demo.driver", shlex.quote(str(script)) + " %A"], home)
                argv = ["merge", "--no-edit", "side"]
            else:
                must(repo, ["config", "filter.demo.required", "true"], home)
                must(repo, ["config", "filter.demo." + kind, shlex.quote(str(script))], home)
                put(repo, "data.txt", "WORK:updated\n" if kind == "clean" else "discard\n")
                argv = ["add", "--", "data.txt"] if kind == "clean" else ["restore", "--worktree", "--", "data.txt"]
            before = state(repo, home)
            result = git(repo, argv, home)
            after = state(repo, home)
            marker_text = marker.read_text()
            check(kind in marker_text and ((result.returncode != 0) == refuse), "native helper control failed")
            blob = must(repo, ["show", ":data.txt"], home).decode() if not (kind == "merge" and refuse) else None
            file_text = (repo / "data.txt").read_text() if (repo / "data.txt").exists() else None
            if not refuse:
                expected = {"clean": "CLEAN:updated\n", "smudge": "WORK:base\n", "merge": "RESOLVED:ours+theirs\n"}[
                    kind
                ]
                check((file_text if kind == "smudge" else blob) == expected, "native transformation missing")
            rows.append(
                {
                    "group": "native-helper",
                    "name": kind,
                    "format": fmt,
                    "refuse": refuse,
                    "exit": result.returncode,
                    "stdout_hex": result.stdout.hex(),
                    "stderr_hex": result.stderr.hex(),
                    "marker": marker_text,
                    "index_blob": blob,
                    "file": file_text,
                    "before": before,
                    "after": after,
                }
            )
    return rows


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
