"""Local evidence consistency, not independent security acceptance."""

import copy
import json
import subprocess
import sys
from pathlib import Path

from model import changed_paths, digest, encode_status, parse_status
from run import BASE, FILES, HERE, ROOT, previous


def require(value, message):
    if not value:
        raise ValueError(message)


def semantics(data):
    for key, value in dict(
        schema=1,
        base_commit=BASE,
        stage_A="INCOMPLETE",
        SEC11="failed",
        SEC13="failed",
        containment=False,
        security_acceptance="NOT RUN",
        production_routing=False,
        live_capture=False,
    ).items():
        require(data[key] == value, "scope drift " + key)
    cases = data["cases"]
    groups = {g: [r for r in cases if r["group"] == g] for g in {r["group"] for r in cases}}
    require(
        {g: len(v) for g, v in groups.items()}
        == {
            "native-parity": 4,
            "execution-failure": 4,
            "add-failure": 4,
            "restage": 4,
            "response-failure": 12,
            "diagnostic-bound": 4,
            "preflight-deny": 24,
            "preservation": 4,
            "parser-positive": 14,
            "parser-reject": 48,
        },
        "matrix gap",
    )
    for width in (40, 64):
        from run import parser_cases

        expected = parser_cases(width)
        actual = [r for r in cases if r["group"].startswith("parser-") and r["width"] == width]
        require(actual == expected, "parser evidence mismatch")
    stems = {fmt + "-" + origin for fmt in ("sha1", "sha256") for origin in ("detached", "unborn")}
    for group in ("native-parity", "execution-failure", "add-failure", "preservation"):
        require({r["name"] for r in groups[group]} == stems, "format/origin gap")
    for r in groups["native-parity"]:
        width = 40 if r["format"] == "sha1" else 64
        before, after, native, stage = r["before"], r["after"], r["native_after"], r["stage"]
        require(r["inspect"]["ok"] and stage["ok"], "positive refused")
        for response, raw in ((r["inspect"], r["native_status_hex"]), (stage, r["native_after_status_hex"])):
            require(encode_status(response["status"]).hex() == raw, "lossy records")
            require(parse_status(bytes.fromhex(raw), width) == response["status"], "record type drift")
        require(
            r["inspect"]["candidate_digest"] == before["digest"] == r["imported"]["candidate_digest"],
            "inspect/import identity",
        )
        require(stage["tree_oid"] == r["native_tree"] and len(stage["tree_oid"]) == width, "tree mismatch")
        require(
            stage["changed_paths_hex"] == r["native_changed_paths_hex"] == changed_paths(stage["status"]),
            "changed paths mismatch",
        )
        require(stage["index_changed_paths_hex"] == r["native_index_changed_paths_hex"], "index delta mismatch")
        require(
            after["index_hex"] == native["index_hex"] and stage["index_digest"] == after["raw_index_digest"],
            "index mismatch",
        )
        require(stage["candidate_digest"] == after["digest"] != before["digest"], "staging identity mismatch")
        require(r["stale"]["diagnostic"] == "INPUT_UNSTABLE" and r["stale_after"] == after["digest"], "stale accepted")
        require(all(before[k] == after[k] for k in ("files", "head_hex", "refs_hex")), "mutation outside staging")
        decoded = {bytes.fromhex(p).decode() for p in stage["changed_paths_hex"]}
        require(
            {"line\nname", "tab\tname", 'quote"name', ":(glob)*", "-leading", "юникод", "rename-new"} <= decoded,
            "literal coverage gap",
        )
        if r["origin"] == "detached":
            require("deleted.txt" in decoded and "rename-old" in decoded, "delete/rename gap")
            require(
                any(s["kind"] == "rename" and s["original_path_hex"] == b"rename-old".hex() for s in stage["status"]),
                "native rename gap",
            )
    for r in groups["execution-failure"] + groups["add-failure"]:
        result = r["result"]
        require(
            not result["ok"] and result["phase"] == "execution" and result["execution_started"], "failure phase drift"
        )
        require(result["diagnostic"] == "EXECUTION_FAILED" and result["exit"] != 0, "missing native failure")
        require(result["disposition"] == "quarantine" and not result["retry_automatically"], "unsafe retry")
        require(len(bytes.fromhex(result["stderr_hex"])) <= 4096, "unbounded stderr")
        before, after = result["before_manifest"], result["after_manifest"]
        require(
            result["changed_entries"]
            == sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p)),
            "actual changes lost",
        )
        require(
            result["candidate_digest"] == digest(json.dumps(after, sort_keys=True).encode()), "failure digest mismatch"
        )
        if r["group"] == "execution-failure":
            require(r["before"]["raw_index_digest"] != r["after"]["raw_index_digest"], "partial index lost")
            require(
                result["index_digest"] == r["after"]["raw_index_digest"]
                and result["candidate_digest"] == r["after"]["digest"],
                "partial state mismatch",
            )
            require(
                ".git/index" in result["changed_entries"] and ".git/index.lock" in result["changed_entries"],
                "fault state lost",
            )
            require(r["retry"]["diagnostic"] == "CANDIDATE_QUARANTINED", "quarantine drift")
            require(
                [c[0] for c in r["commands"]] == ["ls-files", "--literal-pathspecs", "write-tree"],
                "failure command shape",
            )
            require(all(r["before"][k] == r["after"][k] for k in ("files", "head_hex", "refs_hex")), "failure mutation")
        else:
            require(result["candidate_digest"] == r["after"], "add failure digest")
    for r in groups["restage"]:
        require(
            r["result"]["ok"]
            and r["result"]["changed_paths_hex"] == r["expected_staged"]
            and r["result"]["index_changed_paths_hex"] == [],
            "restage semantics",
        )
        require(r["before"] == r["after"] == r["result"]["candidate_digest"], "restage mutation")
    for r in groups["response-failure"]:
        result = r["result"]
        require(
            not result["ok"] and result["diagnostic"] in ("MALFORMED_RESPONSE", "OUTPUT_LIMIT"), "response failure lost"
        )
        require(
            result["candidate_digest"] == r["after"]
            and result["disposition"] == "quarantine"
            and not result["retry_automatically"],
            "response quarantine",
        )
        if (
            r["name"].endswith(("limit", "wire"))
            or r["name"].startswith("sha1-unborn")
            or r["name"].startswith("sha256-unborn")
        ):
            require(result["execution_started"] and r["before"] != r["after"], "response partial state lost")
        else:
            require(not result["execution_started"] and r["before"] == r["after"], "response refusal mutated")
    for r in groups["diagnostic-bound"]:
        result = r["result"]
        require(
            result["diagnostic"] == "EXECUTION_FAILED"
            and result["stderr_truncated"]
            and len(bytes.fromhex(result["stderr_hex"])) == 4096,
            "diagnostic limit",
        )
        require(result["candidate_digest"] == r["actual"], "diagnostic state mismatch")
    for r in groups["preflight-deny"]:
        require(
            not r["result"]["ok"] and r["result"]["phase"] == "preflight" and not r["result"]["retry_automatically"],
            "preflight drift",
        )
        require(
            r["result"]["diagnostic"] in ("UNSUPPORTED_HELPER", "UNSUPPORTED_HOOK")
            and r["before"] == r["after"]
            and r["commands"] == [],
            "preflight executed",
        )
    for r in groups["preservation"]:
        require(
            all(r[k + "_before"] == r[k + "_after"] for k in ("bundle", "source", "protected")),
            "source/bundle/other candidate mutation",
        )


def verify(path):
    data = json.loads(path.read_text())
    semantics(data)
    require(data["artifact_hashes"] == {n: digest((HERE / n).read_bytes()) for n in FILES}, "artifact drift")
    require(data["contract_sha256"] == digest(previous.CONTRACT.read_bytes()), "contract drift")
    require(all(digest((ROOT / p).read_bytes()) == h for p, h in data["upstream_hashes"].items()), "upstream drift")
    source = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted((ROOT / "src").rglob("*.py"))}
    require(
        data["source_hashes"] == source == json.loads((previous.HERE / "results.json").read_text())["source_hashes"],
        "product drift",
    )
    for p, h in source.items():
        require(digest(subprocess.check_output(["git", "show", f"{BASE}:{p}"], cwd=ROOT)) == h, "base source drift")
    mutations = {
        "security-promotion": lambda d: d.update(stage_A="PASS"),
        "routing-promotion": lambda d: d.update(production_routing=True),
        "lost-case": lambda d: d["cases"].pop(),
        "lost-status": lambda d: next(r for r in d["cases"] if r["group"] == "native-parity")["stage"]["status"].pop(),
        "wrong-tree": lambda d: next(r for r in d["cases"] if r["group"] == "native-parity")["stage"].update(
            tree_oid="0" * 40
        ),
        "lost-path": lambda d: next(r for r in d["cases"] if r["group"] == "native-parity")["stage"][
            "changed_paths_hex"
        ].pop(),
        "lost-partial-state": lambda d: next(r for r in d["cases"] if r["group"] == "execution-failure")[
            "result"
        ].update(changed_entries=[]),
        "retry-failure": lambda d: next(r for r in d["cases"] if r["group"] == "execution-failure")["result"].update(
            retry_automatically=True
        ),
        "source-mutation": lambda d: next(r for r in d["cases"] if r["group"] == "preservation").update(
            source_after="changed"
        ),
        "accepted-malformed": lambda d: next(r for r in d["cases"] if r["group"] == "parser-reject").update(
            diagnostic="PASS"
        ),
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
            raise ValueError("accepted negative " + name)
    return dict(
        local_integrity="PASS",
        finite_lab="PASS",
        stage_A="INCOMPLETE",
        SEC11="failed",
        SEC13="failed",
        security_acceptance="NOT RUN",
        containment=False,
        product_files=len(source),
        cases=len(data["cases"]),
        groups={g: sum(r["group"] == g for r in data["cases"]) for g in sorted({r["group"] for r in data["cases"]})},
        negative_controls_rejected=rejected,
        results_sha256=digest(path.read_bytes()),
        artifact_hashes=data["artifact_hashes"],
    )


if __name__ == "__main__":
    print(
        json.dumps(verify(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "results.json"), indent=2, sort_keys=True)
    )
