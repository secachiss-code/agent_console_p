"""Local packet integrity only. Does not install, sandbox, approve or run probes."""

import copy
import json
import subprocess

from collect import BASE, HERE, ROOT, collect, digest

AREAS = (
    "runtime-identity", "startup", "input-fd", "fd-leak", "mounts", "exec-abi",
    "sec11-original", "sec13-original", "canaries", "requests", "parity", "helper-hook",
    "stream", "resources", "partial-failure", "cancel-crash", "forged-response",
    "deny-scope", "evidence-authority",
)
DIAGNOSTICS = {
    "UNSUPPORTED_OPERATION", "UNSUPPORTED_ORIGIN", "INPUT_UNSTABLE", "UNSUPPORTED_HELPER",
    "UNSUPPORTED_HOOK", "CANDIDATE_QUARANTINED", "RUNTIME_UNAVAILABLE", "EXECUTION_FAILED",
    "MALFORMED_RESPONSE", "OUTPUT_LIMIT", "RESOURCE_LIMIT", "CANCELLED", "RECOVERY_REQUIRED",
}


def require(value, message):
    if not value:
        raise ValueError(message)


def semantics(c, upstream):
    require(c["schema"] == 1 and c["base_commit"] == BASE, "Unknown packet base/schema")
    require(c["disposition"] == {
        "packet": "PREPARED_FOR_REVIEW", "actual_review": "NOT PERFORMED",
        "stage_B_authorized": False, "stage_A": "INCOMPLETE", "SEC11": "failed", "SEC13": "failed",
        "installed_acceptance": "NOT RUN", "production_routing": False,
        "host_permissions_changed": False, "runtime_implemented": False,
    }, "Authority or security promotion")
    scope = c["scope"]
    require(scope["operations"] == [o["id"] for o in upstream["operations"]], "Operation expansion")
    require(all(scope[k] is False for k in (
        "raw_argv", "caller_env", "caller_executable", "fallback", "live_publication",
    )), "Execution authority expansion")
    require(scope["hook_phases"] == [] and scope["formats"] == ["sha1", "sha256"], "Hook/format expansion")
    require(scope["origins"] == ["standalone-detached", "standalone-unborn"], "Origin expansion")
    require(scope["excluded_families"] == [e["id"] for e in upstream["excluded"]], "Exclusion lost")
    op = c["operation_abi"]
    status = upstream["operations"][0]["argv_templates"][0]
    ls = ["ls-files", "--stage", "-z"]
    require(op["stage_mutations"] == upstream["operations"][1]["argv_templates"], "Mutation expansion")
    require(op["inspect_observations"] == [status], "Inspect observation expansion")
    require(op["stage_observations_before"] == op["stage_observations_after"] == [status, ls], "Observation expansion")
    require(op["observation_decision"] == "PROPOSED_EXPLICIT_V1_EXTENSION_REQUIRES_REVIEW", "Unreviewed authority")
    require(op["common_fields"] == ["operation", "candidate_id", "expected_digest"], "Request field expansion")
    require(op["stage_extra_fields"] == ["paths"], "Extra stage input")
    response = c["response_abi"]
    require(response["limits"] == {
        "request_bytes": 1048576, "json_depth": 16, "paths": 4096, "path_bytes": 4096,
        "parsed_output_bytes": 1048576, "records": 4096, "diagnostic_bytes": 4096,
        "response_bytes": 8388608, "combined_stream_bytes": 8388608,
    }, "Response bound drift")
    require(response["success_fields"] == ["ok", "operation", "status", "candidate_digest", "index_digest"], "Success identity lost")
    require(response["stage_success_fields"] == ["tree_oid", "changed_paths_hex", "index_changed_paths_hex"], "Stage delta lost")
    require(response["failure_fields"] == [
        "ok", "diagnostic", "phase", "execution_started", "retry_automatically", "disposition",
        "recovery_required", "candidate_digest", "index_digest", "identity_state", "observations_omitted",
        "stderr_hex", "stderr_truncated", "exit", "signal",
    ], "Failure identity/recovery lost")
    require(set(response["diagnostics"]) == DIAGNOSTICS, "Missing diagnostic")
    require(all(row == {"success": False, "retry_automatically": False, "quarantine_after_execution": True}
                for row in response["diagnostics"].values()), "Success/retry fallback")
    runtime = c["runtime_abi"]
    require(runtime["status"] == "PROPOSED_NOT_INSTALLED", "Installed claim")
    require(runtime["limits"] == {
        "wall_seconds": 30, "memory_MiB": 256, "processes": 16, "scratch_bytes": 67108864,
        "candidate_bytes": 67108864, "candidate_entries": 16384, "cancel_grace_seconds": 1,
        "combined_stream_bytes": 8388608,
    }, "Runtime bound drift")
    require(runtime["mounts"]["read_only"] == [
        "immutable runtime root", "sealed input bytes", "InspectCandidate entire candidate",
    ], "Read-only boundary lost")
    require(runtime["mounts"]["writable"] == [
        "StageCandidatePaths private candidate index/objects and lock paths only", "bounded private scratch",
    ], "Write grant expansion")
    require(runtime["mounts"]["absent"] == [
        "live project and original gitdir/common-dir", "host home and /run/user",
        "core/hostrun/DB/UI sockets and credentials", "network", "host proc/process namespace", "host devices",
    ], "Host exposure")
    require(runtime["fds"]["allowed"] == [
        "0: bounded request pipe to trusted adapter; Git stdin is EOF",
        "1: bounded response pipe; Git stdout is private supervisor pipe", "2: bounded diagnostic pipe",
        "setup-only: verified runtime and sealed bundle FDs consumed/closed before Git",
    ], "FD expansion")
    probes = c["probe_policy"]
    require(probes["sources"] == [
        "artifacts/sec11/race_probe.py", "artifacts/sec11/config-namespace/test_snapshot_races.py",
    ], "Original probe lost")
    require(probes["unchanged"] is True and probes["skip_xfail_allowed"] is False
            and probes["positive_controls_required"] is True, "Weakened probes")
    require([(r["id"], r["area"]) for r in c["acceptance"]] == [
        (f"B{i:02}", area) for i, area in enumerate(AREAS, 1)
    ], "Acceptance matrix gap")
    require(all(r["status"] == "NOT RUN" and all(r[k] for k in (
        "stimulus", "expected", "positive_control",
    )) for r in c["acceptance"]), "Invented acceptance/missing native control")
    require(set(c["remaining_gates"]) == set("ABCDE") and len(c["sequence"]) == 6, "Lost later gate")
    # These are explicit review requirements in prose, not enforcement by this verifier.
    for key in ("installation", "identity", "launch_exec", "input", "environment", "descendants",
                "resources", "cancel", "startup", "durability"):
        require(bool(runtime[key]), f"Missing runtime design requirement {key}")


def verify():
    c = json.loads((HERE / "contract.json").read_text())
    upstream = json.loads((ROOT / "artifacts/sec11/stage-a-contract/contract.json").read_text())
    semantics(c, upstream)
    index = json.loads((HERE / "source-index.json").read_text())
    require(index == collect(), "Source/evidence index drift")
    subprocess.run(["git", "merge-base", "--is-ancestor", BASE, "HEAD"], cwd=ROOT, check=True)
    pinned = index["product_source_hashes"] | index["upstream_hashes"]
    for path, expected in pinned.items():
        blob = subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT)
        require(digest(blob) == expected, f"Base blob drift: {path}")
    prior = json.loads((ROOT / "artifacts/sec11/candidate-result-contract/results.json").read_text())
    require(index["product_source_hashes"] == prior["source_hashes"], "SEC33 product drift")
    checks = json.loads((HERE / "upstream-reverification.json").read_text())
    folders = {
        "sec30": "stage-a-contract", "sec31": "candidate-contract-fixtures",
        "sec32": "candidate-snapshot-import", "sec33": "candidate-result-contract",
    }
    require(set(checks) == set(folders), "Lost upstream verification")
    for node, folder in folders.items():
        saved = json.loads((ROOT / "artifacts/sec11" / folder / "validation.json").read_text())
        require(checks[node] == {
            "verifier_exit": 0, "matches_saved_validation": True, "result": saved,
        }, "Changed upstream validation")
    mutations = {
        "self-review": lambda d: d["disposition"].update(actual_review="PASS"),
        "stage-B-authorization": lambda d: d["disposition"].update(stage_B_authorized=True),
        "stage-A-promotion": lambda d: d["disposition"].update(stage_A="PASS"),
        "routing": lambda d: d["disposition"].update(production_routing=True),
        "raw-argv": lambda d: d["scope"].update(raw_argv=True),
        "caller-env": lambda d: d["scope"].update(caller_env=True),
        "hook-execution": lambda d: d["scope"]["hook_phases"].append("pre-commit"),
        "extra-observation": lambda d: d["operation_abi"]["inspect_observations"].append(["diff"]),
        "extra-mutation": lambda d: d["operation_abi"]["stage_mutations"].append(["update-ref"]),
        "implicit-observation-approval": lambda d: d["operation_abi"].update(observation_decision="APPROVED"),
        "response-limit": lambda d: d["response_abi"]["limits"].update(response_bytes=999999999),
        "failure-retry": lambda d: d["response_abi"]["diagnostics"]["EXECUTION_FAILED"].update(retry_automatically=True),
        "lost-recovery": lambda d: d["response_abi"]["failure_fields"].remove("recovery_required"),
        "host-mount": lambda d: d["runtime_abi"]["mounts"]["writable"].append("host home"),
        "fd-leak": lambda d: d["runtime_abi"]["fds"]["allowed"].append("core socket"),
        "runtime-limit": lambda d: d["runtime_abi"]["limits"].update(processes=0),
        "weaken-probe": lambda d: d["probe_policy"].update(skip_xfail_allowed=True),
        "lost-native-control": lambda d: d["acceptance"][7].update(positive_control=""),
        "invented-installed-pass": lambda d: d["acceptance"][0].update(status="PASS"),
        "lost-case": lambda d: d["acceptance"].pop(),
        "lost-publication-gate": lambda d: d["remaining_gates"].pop("D"),
    }
    rejected = []
    for name, mutation in mutations.items():
        modified = copy.deepcopy(c)
        mutation(modified)
        try:
            semantics(modified, upstream)
        except ValueError:
            rejected.append(name)
        else:
            raise ValueError(f"Accepted negative control: {name}")
    return {
        "local_packet_integrity": "PASS", "actual_review": "NOT PERFORMED", "stage_B_authorized": False,
        "installed_acceptance": "NOT RUN", "stage_A": "INCOMPLETE", "SEC11": "failed", "SEC13": "failed",
        "base_commit": BASE, "product_files": len(index["product_source_hashes"]),
        "upstream_files": len(index["upstream_hashes"]), "planned_installed_cases": len(c["acceptance"]),
        "negative_controls_rejected": rejected,
        "artifact_hashes": {n: digest((HERE / n).read_bytes()) for n in (
            "contract.json", "source-index.json", "collect.py", "verify.py", "REPORT.md",
            "upstream-reverification.json", "lint-all.txt",
        )},
    }


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2, sort_keys=True))
