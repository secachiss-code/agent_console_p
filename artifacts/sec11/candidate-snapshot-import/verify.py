"""Local evidence integrity; neither independent review nor security acceptance."""

import copy
import json
import subprocess
import sys
from pathlib import Path

from importer import digest, UPSTREAM
from run import BASE, FILES, HERE, ROOT, previous


def require(value, message):
    if not value:
        raise ValueError(message)


def semantics(data):
    require(data["stage_A"] == "INCOMPLETE" and data["SEC11"] == data["SEC13"] == "failed", "security promotion")
    require(
        data["containment"] is False
        and data["live_capture"] is False
        and data["production_routing"] is False
        and data["security_acceptance"] == "NOT RUN",
        "authority expansion",
    )
    cases = data["cases"]
    keys = [(r["group"], r["name"], r["format"], r.get("origin"), r.get("operation"), r.get("refuse")) for r in cases]
    require(len(keys) == len(set(keys)), "duplicate cases")
    require(
        {r["group"] for r in cases} == {"roundtrip", "reject", "retry", "collision", "adapter-deny", "native-helper"},
        "unknown/missing group",
    )
    paths = (
        "/tmp/outside",
        "../outside",
        "a/../../outside",
        "a//b",
        "./x",
        "",
        "nul\0x",
        "a\\b",
        ".GiT/config",
        "sub/.git/config",
        "bad\ud800name",
    )
    rejection_names = (
        {"path-" + repr(p) for p in paths}
        | {"kind-" + k for k in ("symlink", "hardlink", "fifo", "socket", "device")}
        | {
            "dependency-" + p
            for p in (
                ".git/commondir",
                ".git/gitdir",
                ".git/objects/info/alternates",
                ".git/objects/info/http-alternates",
                ".git/modules/x",
                ".git/info/sparse-checkout",
                ".git/sharedindex.fake",
                ".git/unknown",
                ".gitmodules",
            )
        }
        | {
            "unknown-field",
            "entry-unknown-field",
            "invalid-root-mode",
            "boolean-schema",
            "duplicate-path",
            "file-parent",
            "missing-parent",
            "missing-git",
            "external-gitdir",
            "include",
            "conditional-include",
            "worktree",
            "hooks-path",
            "unknown-config",
            "fsmonitor",
            "promisor",
            "missing-bytes",
            "corrupt-bytes",
            "invalid-hex",
            "bad-mode",
            "changed-mode-stale-agreement",
            "changed-bytes-stale-agreement",
            "missing-file-stale-agreement",
            "wrong-format",
            "missing-HEAD",
            "dangling-HEAD",
            "bad-index",
            "duplicate-json-key",
            "truncated-json",
            "agreed-truncated-json",
            "agreed-duplicate-json-key",
            "gitlink-index",
        }
    )
    for fmt in ("sha1", "sha256"):
        for origin in ("detached", "unborn"):
            subset = [r for r in cases if r["format"] == fmt and r.get("origin") == origin]
            positive = [r for r in subset if r["group"] == "roundtrip"]
            require(len(positive) == 1, "roundtrip gap")
            p = positive[0]
            snapshot = digest(json.dumps(p["manifest"], sort_keys=True).encode())
            require(
                snapshot == p["before"]["digest"] == p["source_before"] == p["imported"]["candidate_digest"],
                "manifest drift",
            )
            require(
                p["imported"]["bundle_sha256"] == p["bundle_sha256"]
                and p["imported"]["entries"] == len(p["manifest"])
                and p["imported"]["root_mode"] == p["root_mode"],
                "bundle identity",
            )
            require(p["inspect"]["ok"] and p["stage"]["ok"], "positive refused")
            require(
                p["inspect"]["output_hex"] == p["native_status_hex"]
                and p["stage"]["output_hex"] == p["native_tree_hex"],
                "native parity",
            )
            require(
                p["inspect"]["candidate_digest"] == snapshot and p["stage"]["candidate_digest"] == p["after"]["digest"],
                "operation digest",
            )
            require(
                p["stage"]["index_digest"] == p["after"]["raw_index_digest"]
                and p["after"]["index_hex"] == p["native_after"]["index_hex"],
                "index parity",
            )
            for key in ("files", "refs_hex", "head_hex"):
                require(p["before"][key] == p["after"][key], "positive mutation")
            expected = rejection_names | {
                "interrupt-" + str(i) for i in (0, len(p["manifest"]) // 2, len(p["manifest"]) - 1)
            }
            if origin == "detached":
                expected |= {"missing-object", "corrupt-object"}
            require({r["name"] for r in subset if r["group"] == "reject"} == expected, "reject matrix gap")
            retry = [r for r in subset if r["group"] == "retry"]
            require(len(retry) == 1 and retry[0]["expected"] == retry[0]["actual"] == snapshot, "retry gap")
            require(
                {r["name"] for r in subset if r["group"] == "collision"} == {"registered", "symlink"}, "collision gap"
            )
            denies = [r for r in subset if r["group"] == "adapter-deny"]
            require(
                {(r["name"], r["operation"]) for r in denies}
                == {
                    (n, o)
                    for n in (
                        "required-filter",
                        "merge-helper",
                        "filter-attribute",
                        "info-attribute",
                        "executable-hook",
                    )
                    for o in ("InspectCandidate", "StageCandidatePaths")
                },
                "adapter denial gap",
            )
        native = [r for r in cases if r["group"] == "native-helper" and r["format"] == fmt]
        require(
            {(r["name"], r["refuse"]) for r in native}
            == {(n, f) for n in ("clean", "smudge", "merge") for f in (False, True)},
            "native helper gap",
        )
        for r in native:
            require((r["exit"] != 0) == r["refuse"] and r["name"] in r["marker"], "native helper invocation")
            if not r["refuse"]:
                expected = {"clean": "CLEAN:updated\n", "smudge": "WORK:base\n", "merge": "RESOLVED:ours+theirs\n"}[
                    r["name"]
                ]
                require(
                    (r["file"] if r["name"] == "smudge" else r["index_blob"]) == expected,
                    "native helper transformation",
                )
    require(all(r["format"] in ("sha1", "sha256") for r in cases), "unknown format")
    for r in cases:
        if "source_before" in r:
            require(r["source_before"] == r["source_after"], "source mutation")
        if r["group"] == "reject":
            require(bool(r["diagnostic"]), "missing rejection")
            for key in ("bundle", "protected", "registered", "directories"):
                require(r[key + "_before"] == r[key + "_after"], "import rejection mutation")
        if r["group"] == "collision":
            require(r["protected_before"] == r["protected_after"], "collision mutation")
        if r["group"] == "adapter-deny":
            require(r["outcome"]["ok"] is False and r["outcome"]["retry_automatically"] is False, "false denial")
            require(r["before"] == r["after"] and r["commands"] == [], "denial execution/mutation")
            require(
                r["outcome"]["diagnostic"]
                == ("UNSUPPORTED_HOOK" if r["name"] == "executable-hook" else "UNSUPPORTED_HELPER"),
                "diagnostic drift",
            )


def verify(path):
    data = json.loads(path.read_text())
    semantics(data)
    require(
        data["base_commit"] == BASE and data["contract_sha256"] == digest(previous.CONTRACT.read_bytes()),
        "contract drift",
    )
    require(data["artifact_hashes"] == {n: digest((HERE / n).read_bytes()) for n in FILES}, "artifact drift")
    require(
        data["upstream_hashes"]
        == {n: digest((UPSTREAM / n).read_bytes()) for n in ("adapter.py", "run.py", "verify.py", "results.json")},
        "upstream drift",
    )
    actual = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted((ROOT / "src").rglob("*.py"))}
    require(
        data["source_hashes"] == actual == json.loads((UPSTREAM / "results.json").read_text())["source_hashes"],
        "product drift",
    )
    for name, expected in actual.items():
        require(
            digest(subprocess.check_output(["git", "show", f"{BASE}:{name}"], cwd=ROOT)) == expected,
            "base product drift",
        )
    mutations = {
        "security-promotion": lambda d: d.update(stage_A="PASS"),
        "live-capture-promotion": lambda d: d.update(live_capture=True),
        "lost-case": lambda d: d["cases"].pop(0),
        "tree-change": lambda d: d["cases"][0]["stage"].update(output_hex="00"),
        "manifest-change": lambda d: d["cases"][0]["manifest"].pop(".git/config"),
        "rejection-mutation": lambda d: next(r for r in d["cases"] if r["group"] == "reject").update(
            protected_after="changed"
        ),
        "false-helper-success": lambda d: next(r for r in d["cases"] if r["group"] == "adapter-deny")["outcome"].update(
            ok=True
        ),
        "native-helper-no-transform": lambda d: next(
            r for r in d["cases"] if r["group"] == "native-helper" and r["name"] == "clean" and not r["refuse"]
        ).update(index_blob="raw"),
    }
    rejected = []
    for name, mutation in mutations.items():
        changed = copy.deepcopy(data)
        mutation(changed)
        try:
            semantics(changed)
        except ValueError:
            rejected.append(name)
        else:
            raise ValueError("accepted semantic negative " + name)
    return dict(
        local_integrity="PASS",
        finite_lab="PASS",
        stage_A="INCOMPLETE",
        SEC11="failed",
        SEC13="failed",
        security_acceptance="NOT RUN",
        containment=False,
        cases=len(data["cases"]),
        groups={g: sum(r["group"] == g for r in data["cases"]) for g in sorted({r["group"] for r in data["cases"]})},
        product_files=len(actual),
        negative_controls_rejected=rejected,
        results_sha256=digest(path.read_bytes()),
        artifact_hashes=data["artifact_hashes"],
    )


if __name__ == "__main__":
    print(
        json.dumps(verify(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "results.json"), indent=2, sort_keys=True)
    )
