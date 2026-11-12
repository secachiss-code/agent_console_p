"""Local saved-evidence checks, not independent acceptance or runtime enforcement."""

import copy
import json
import subprocess
import sys
from pathlib import Path

from adapter import STATUS, digest
from run import BASE, CONTRACT, HERE, PATHS, ROOT, C


def require(value, message):
    if not value:
        raise ValueError(message)


def semantics(data):
    require(data["stage_A"] == "INCOMPLETE" and data["SEC11"] == data["SEC13"] == "failed", "security promotion")
    require(
        data["security_acceptance"] == "NOT RUN"
        and data["containment"] is False
        and data["production_routing"] is False,
        "authority expansion",
    )
    rows = data["cases"]
    require(
        len({(r["group"], r["name"], r["format"], r.get("origin"), r.get("operation"), r.get("refuse")) for r in rows})
        == len(rows),
        "duplicate cases",
    )
    for fmt in ("sha1", "sha256"):
        for origin in ("detached", "unborn"):
            subset = [r for r in rows if r["format"] == fmt and r.get("origin") == origin]
            positive = [r for r in subset if r["group"] == "T-positive"]
            require(len(positive) == 1, "positive matrix gap")
            p = positive[0]
            require(p["inspect"]["ok"] and p["stage"]["ok"], "positive failure")
            require(
                p["inspect"]["output_hex"] == p["native_status_hex"]
                and p["stage"]["output_hex"] == p["native_tree_hex"],
                "status/tree parity",
            )
            require(
                p["after"]["index_hex"] == p["native_after"]["index_hex"] and p["tree_entries_hex"],
                "index/tree missing",
            )
            require(p["before"]["digest"] == p["inspect"]["candidate_digest"], "inspect wrote")
            require(p["stage"]["index_digest"] == p["after"]["raw_index_digest"], "index digest mismatch")
            tree_paths = {
                record.split(b"\t", 1)[1].decode()
                for record in bytes.fromhex(p["tree_entries_hex"]).split(b"\0")
                if record
            }
            require(
                tree_paths == set(PATHS) - ({"deleted.txt"} if origin == "detached" else set()), "literal tree paths"
            )
            for key in ("refs_hex", "head_hex", "files"):
                require(p["before"][key] == p["after"][key], "positive changed " + key)
            require(
                p["commands"] == [STATUS, ["--literal-pathspecs", "add", "-A", "--", *PATHS], ["write-tree"]],
                "command expansion",
            )
            expected = {
                "T-helper": {
                    "required-filter",
                    "optional-filter",
                    "filter-process",
                    "unused-driver",
                    "signing",
                    "fsmonitor",
                    "external-diff",
                    "textconv",
                    "config-command",
                    "filter-attribute",
                    "merge-attribute",
                    "unset-filter",
                    "info-attribute",
                },
                "T-deny": {r["id"] for r in C["excluded"]}
                | {r["id"] for r in C["lifecycle"]}
                | {
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
                },
            }
            for group, names in expected.items():
                require({r["name"] for r in subset if r["group"] == group} == names, "matrix gap " + group)
            hooks = [r for r in subset if r["group"] == "T-hooks"]
            for phase in C["phase_contract"]["observed_hook_phases"] + [
                "unknown-hook",
                "executable.sample",
                "pre-merge-fallback",
            ]:
                require(
                    {r.get("operation") for r in hooks if r["name"] == phase}
                    == {"InspectCandidate", "StageCandidatePaths"},
                    "hook gap",
                )
            require(
                {r["name"] for r in hooks if "outcome" not in r}
                == {
                    "nonexecutable-pre-commit",
                    "nonexecutable-post-index-change",
                    "nonexecutable-pre-merge-commit.sample",
                },
                "inert control gap",
            )
            require(len([r for r in subset if r["group"] == "T-input"]) == 32, "input matrix gap")
        native = [r for r in rows if r["format"] == fmt and r["group"] == "native-hooks"]
        require(
            len(native) == 1
            and set(native[0]["events"]) == set(C["phase_contract"]["observed_hook_phases"])
            and native[0]["refusal_exit"] != 0,
            "native hook control gap",
        )
        fallback = [r for r in rows if r["format"] == fmt and r["group"] == "native-fallback"]
        require(len(fallback) == 2 and {r["refuse"] for r in fallback} == {False, True}, "fallback controls missing")
        for row in fallback:
            require(row["marker"] == "fallback" and (row["exit"] != 0) == row["refuse"], "native fallback invocation")
            require((row["before"]["refs_hex"] == row["after"]["refs_hex"]) == row["refuse"], "fallback ref outcome")
        controls = [r for r in rows if r["format"] == fmt and r["group"] == "native-helper"]
        require(
            {(r["name"], r["refuse"]) for r in controls}
            == {(k, f) for k in ("clean", "smudge", "merge") for f in (False, True)},
            "native helper control gap",
        )
        for row in controls:
            require(
                (row["exit"] != 0) == row["refuse"] and row["name"] in row["marker"], "native helper invocation/refusal"
            )
            if not row["refuse"]:
                expected = {"clean": "CLEAN:updated\n", "smudge": "WORK:base\n", "merge": "RESOLVED:ours+theirs\n"}[
                    row["name"]
                ]
                require(
                    (row["file"] if row["name"] == "smudge" else row["index_blob"]) == expected,
                    "helper transformation lost",
                )
    for row in rows:
        if "original_before" in row:
            require(row["original_before"] == row["original_after"], "original mutated")
        if "outcome" in row:
            out = row["outcome"]
            require(not out["ok"] and out["retry_automatically"] is False and out["detail"], "deny success/retry")
            require(row["before"] == row["after"] and row["commands"] == [], "deny wrote/executed")
            if row["group"] in ("T-helper", "T-hooks", "T-deny"):
                require(
                    out["diagnostic"]
                    == {
                        "T-helper": "UNSUPPORTED_HELPER",
                        "T-hooks": "UNSUPPORTED_HOOK",
                        "T-deny": "UNSUPPORTED_OPERATION",
                    }[row["group"]],
                    "wrong diagnostic",
                )
            elif row["group"] == "T-input":
                unstable = {"stale-digest", "hook-mode-drift", "attribute-drift", "ignore-drift"}
                origins = {
                    "candidate-path",
                    "alternates",
                    "commondir",
                    "submodule",
                    "sparse-index",
                    "split-index",
                    "include",
                    "includeIf",
                    "symlink-parent",
                    "bare-config",
                    "external-gitdir",
                }
                expected = (
                    "INPUT_UNSTABLE"
                    if row["name"] in unstable
                    else "UNSUPPORTED_ORIGIN"
                    if row["name"] in origins
                    else "UNSUPPORTED_OPERATION"
                )
                require(out["diagnostic"] == expected, "input diagnostic drift")
        elif row["group"] == "T-hooks":
            require(row["inspect"]["ok"] and row["stage"]["ok"], "inert hook failed")


def verify(path):
    data = json.loads(path.read_text())
    semantics(data)
    require(data["base_commit"] == BASE and data["contract_sha256"] == digest(CONTRACT.read_bytes()), "contract drift")
    upstream = json.loads((ROOT / "artifacts/sec11/stage-a-contract/source-index.json").read_text())
    actual = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted((ROOT / "src").rglob("*.py"))}
    require(data["source_hashes"] == actual == upstream["product_source_hashes"], "product drift")
    for name, expected in actual.items():
        blob = subprocess.check_output(["git", "show", f"{BASE}:{name}"], cwd=ROOT)
        require(digest(blob) == expected, "base product mismatch")
    require(
        data["artifact_hashes"]
        == {n: digest((HERE / n).read_bytes()) for n in ("adapter.py", "run.py", "verify.py", "REPORT.md")},
        "artifact drift",
    )
    mutations = {
        "promotion": lambda d: d.update(stage_A="PASS"),
        "lost_case": lambda d: d["cases"].pop(0),
        "helper_false_success": lambda d: next(r for r in d["cases"] if r["group"] == "T-helper")["outcome"].update(
            ok=True
        ),
        "tree_change": lambda d: d["cases"][0]["stage"].update(output_hex="00"),
        "deny_mutation": lambda d: next(r for r in d["cases"] if "outcome" in r).update(after="changed"),
        "raw_exec": lambda d: d["cases"][0]["commands"].append(["commit-tree"]),
    }
    rejected = []
    for name, mutate in mutations.items():
        modified = copy.deepcopy(data)
        mutate(modified)
        try:
            semantics(modified)
        except ValueError:
            rejected.append(name)
        else:
            raise ValueError("negative control accepted " + name)
    return {
        "local_integrity": "PASS",
        "lab_compatibility": "PASS for finite fixtures only",
        "stage_A": "INCOMPLETE",
        "SEC11": "failed",
        "SEC13": "failed",
        "security_acceptance": "NOT RUN",
        "production_routing": False,
        "cases": len(data["cases"]),
        "groups": {g: sum(r["group"] == g for r in data["cases"]) for g in sorted({r["group"] for r in data["cases"]})},
        "product_files": len(actual),
        "negative_controls_rejected": rejected,
        "results_sha256": digest(path.read_bytes()),
        "artifact_hashes": data["artifact_hashes"],
    }


if __name__ == "__main__":
    print(
        json.dumps(verify(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "results.json"), indent=2, sort_keys=True)
    )
