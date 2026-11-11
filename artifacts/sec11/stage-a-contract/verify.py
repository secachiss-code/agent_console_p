"""SEC-30 local integrity checks; not runtime or independent security acceptance."""

import copy
import hashlib
import json
import subprocess

from collect import HERE, ROOT, collect, digest


def require(value, message):
    if not value:
        raise ValueError(message)


def semantics(c, ledger):
    require(c["status"] == "specified_not_implemented", "Implementation claim")
    require(c["stage_A"] == "INCOMPLETE" and c["SEC11"] == c["SEC13"] == "failed", "Security promotion")
    require(c["security_acceptance"] == "NOT RUN", "Acceptance claim")
    require(all(c[k] is False for k in ("stage_B_authorized", "production_routing", "host_permissions_changed")), "Authority expansion")
    require(c["scope"]["raw_argv"] is False and c["scope"]["extra_env"] is False, "Generic execution interface")
    require(c["scope"]["phase_execution"] == c["phase_contract"]["supported"] == [], "Hook execution expansion")
    require(c["phase_contract"]["observed_hook_phases"] == list(ledger["phase_evidence"]), "Lost phase accounting")
    operations = c["operations"]
    require([o["id"] for o in operations] == ["InspectCandidate", "StageCandidatePaths"], "Operation expansion")
    require(operations[0]["argv_templates"] == [["status", "--porcelain=v2", "-z", "--branch", "--untracked-files=all", "--ignore-submodules=none"]], "Status template drift")
    require(operations[1]["argv_templates"] == [["--literal-pathspecs", "add", "-A", "--", "{paths...}"], ["write-tree"]], "Stage template drift")
    excluded = {r["id"]: r for r in c["excluded"]}
    expected = {"unstage_restore", "commit_root_amend", "merge_squash_ff", "sequencer_continue_abort",
                "checkpoint_prompt_restore_delete", "worktree_create_remove_prune", "history_diff_preview",
                "auxiliary_git", "external_helpers", "installed_hooks", "origin_variants"}
    require(set(excluded) == expected and len(c["excluded"]) == len(expected), "Missing exclusion")
    for key, diagnostic in (("external_helpers", "UNSUPPORTED_HELPER"), ("installed_hooks", "UNSUPPORTED_HOOK"), ("origin_variants", "UNSUPPORTED_ORIGIN")):
        require(excluded[key]["diagnostic"] == diagnostic, "Missing specific refusal")
    anchors = set(c["source_refs"])
    catalog = set(json.loads((ROOT / "artifacts/sec11/stage-a-ledger/evidence.json").read_text())["catalog"])
    used = set()
    for row in operations + c["excluded"] + c["lifecycle"]:
        require(row["source_refs"] and set(row["source_refs"]) <= anchors, "Unbound source")
        require(row["evidence"] and set(row["evidence"]) <= catalog, "Unbound evidence")
        used.update(row["source_refs"])
    require(used == anchors, "Unused/unreviewed source anchor")
    require(all(r["support"] == "planned_candidate_only" and r["phases"] == [] for r in operations), "Supported runtime claim")
    require(all(r["support"] == "excluded_v1" and r["phases"] == [] and r["reason"] for r in c["excluded"]), "Excluded operation enabled")
    require({r["id"] for r in c["lifecycle"]} == {
        "prompt_capture", "session_prompt_capture", "keep_count_delete", "retention_outbox_delete",
        "legacy_tab_remove", "git_cleanup_remove", "create_rollback_remove", "worktree_prune"}, "Missing lifecycle")
    require(all(r["disposition"] == "excluded_v1" and r["measurement"] == "source_reachability_only_not_new_runtime_trace" for r in c["lifecycle"]), "Lifecycle acceptance overclaim")
    decisions = {d["id"]: d for d in c["decisions"]}
    require(set(decisions) == {d["id"] for d in ledger["decisions"]}, "Lost compatibility decision")
    require(all(decisions[k]["state"] == "specified_for_v1_not_implemented" for k in ("D-helper", "D-phases", "D-scope")), "Unimplemented decision promoted")
    tests = {t["id"] for t in c["acceptance"]}
    require(tests == set(c["remaining_gaps"]) and len(tests) == 8, "Lost gate")
    require(all(t["status"] == "NOT RUN" and t["cases"] for t in c["acceptance"]), "Unmeasured gate promoted")
    require(all(set(d["acceptance"]) <= tests for d in decisions.values()), "Unbound decision acceptance")
    for row in c["diagnostics"].values():
        require(row["success"] is False and row["retry_automatically"] is False and row["required_detail"], "Fallback/retry expansion")


def verify():
    c = json.loads((HERE / "contract.json").read_text())
    ledger = json.loads((ROOT / "artifacts/sec11/stage-a-ledger/ledger.json").read_text())
    semantics(c, ledger)
    index = json.loads((HERE / "source-index.json").read_text())
    require(index == collect(c), "Source/evidence/anchor drift")
    upstream = json.loads((ROOT / "artifacts/sec11/stage-a-ledger/evidence.json").read_text())
    require(index["product_source_hashes"] == upstream["product_source_hashes"], "Product drift since SEC29")
    subprocess.run(["git", "merge-base", "--is-ancestor", c["base_commit"], "HEAD"], cwd=ROOT, check=True)
    for name, expected in index["product_source_hashes"].items():
        data = subprocess.check_output(["git", "show", f'{c["base_commit"]}:{name}'], cwd=ROOT)
        require(hashlib.sha256(data).hexdigest() == expected, f"Base blob mismatch {name}")
    production = [r for r in index["prune_attribute_calls"] if r["path"].startswith("src/")]
    # Reviewed receiver is ApprovalBook, not Worktrees. This scan cannot resolve reflection.
    require(all(r["path"] == "src/aconsole/core/domain/approvals.py" and r["expression"].startswith("self.prune(") for r in production), "New prune caller needs review")
    require(sum(r["path"] == "tests/core/test_worktrees.py" for r in index["prune_attribute_calls"]) == 4, "Worktree prune test evidence changed")
    # Negative controls test semantic guardrails, not runtime policy enforcement.
    rejected = []
    mutations = {
        "stage_promotion": lambda x: x.update(stage_A="PASS"),
        "routing": lambda x: x.update(production_routing=True),
        "lost_phase": lambda x: x["phase_contract"]["observed_hook_phases"].pop(),
        "raw_argv": lambda x: x["scope"].update(raw_argv=True),
        "helper_success": lambda x: x["diagnostics"]["UNSUPPORTED_HELPER"].update(success=True),
        "lost_caller": lambda x: x["lifecycle"][0]["source_refs"].append("invented-capture-rpc"),
        "scope_expansion": lambda x: x["operations"][1]["argv_templates"].append(["commit-tree"]),
    }
    for name, mutate in mutations.items():
        modified = copy.deepcopy(c)
        mutate(modified)
        try:
            semantics(modified, ledger)
        except ValueError:
            rejected.append(name)
        else:
            raise ValueError(f"Negative control accepted: {name}")
    return {"local_integrity": "PASS", "stage_A": "INCOMPLETE", "runtime_tests": "NOT RUN",
            "security_acceptance": "NOT RUN", "base_commit": c["base_commit"],
            "source_files": len(index["product_source_hashes"]), "anchors": len(index["anchors"]),
            "planned_requests": len(c["operations"]), "excluded_families": len(c["excluded"]),
            "lifecycle_paths": len(c["lifecycle"]), "negative_controls_rejected": rejected,
            "artifact_hashes": {n: digest(HERE / n) for n in ("contract.json", "source-index.json", "collect.py", "verify.py", "REPORT.md")}}


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2, sort_keys=True))
