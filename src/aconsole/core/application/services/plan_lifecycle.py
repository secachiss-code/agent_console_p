"""Frozen topology and contract snapshots, independent of transport and persistence."""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Protocol, TypeGuard

from aconsole.core.application.ports.plan_store import StoredNode, StoredPlan, StoredPlanChange
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.gate_profile import micro_commands
from aconsole.core.domain.plan_admission import AppliedChange, PlanTopology, TopologyContract, validate_contract
from aconsole.core.domain.vault_sync import Note, resolve_ref

_MAX_SCOPE_PATHS = 100


class ContractInput(Protocol):
    @property
    def key(self) -> str: ...
    @property
    def objective(self) -> str: ...
    @property
    def level(self) -> int: ...
    @property
    def parent(self) -> str | None: ...
    @property
    def paths(self) -> Sequence[str]: ...
    @property
    def acceptance(self) -> Sequence[str]: ...
    @property
    def test_cmd(self) -> str | None: ...
    @property
    def boundaries(self) -> str | None: ...
    @property
    def output_format(self) -> str | None: ...
    @property
    def invariants(self) -> str | None: ...
    @property
    def gate_profile(self) -> Mapping[str, object] | None: ...


def topology_contract(node: ContractInput) -> TopologyContract:
    return TopologyContract(
        key=node.key,
        objective=node.objective,
        level=node.level,
        parent=node.parent,
        paths=tuple(node.paths),
        acceptance=tuple(node.acceptance),
        test_cmd=node.test_cmd,
        boundaries=node.boundaries,
        output_format=node.output_format,
        invariants=node.invariants,
        gate_profile_json=json.dumps(node.gate_profile, sort_keys=True, separators=(",", ":")),
    )


def topology_for(plan: StoredPlan) -> PlanTopology:
    return replace(plan.topology or PlanTopology(), nodes=tuple(topology_contract(node) for node in plan.nodes))


def admit_draft_nodes(plan: StoredPlan, nodes: Sequence[ContractInput]) -> PlanTopology:
    """Check an add-only draft insertion. Callers commit the result themselves."""
    if plan.topology is not None and plan.topology.phase != "draft":
        raise DomainError(code=4011, name="PLAN_FROZEN", retryable=False)
    return topology_for(plan).add(tuple(topology_contract(node) for node in nodes))


def require_note_contract(plan: StoredPlan, key: str, note: Note) -> None:
    if plan.topology is None or plan.topology.phase == "draft":
        return
    original = next(node for node in plan.nodes if node.key == key)
    updated = replace(
        original,
        objective=note.objective,
        paths=note.paths,
        acceptance=note.acceptance,
        depends_on=tuple(resolved for raw in note.depends_on if (resolved := resolve_ref(raw)) is not None),
    )
    if topology_contract(original) != topology_contract(updated) or original.depends_on != updated.depends_on:
        # Apply the same domain rule as graph insertion; a note cannot bypass frozen topology.
        _ = plan.topology.add(())


def apply_topology(
    plan: StoredPlan,
    change: StoredPlanChange,
    nodes: Sequence[ContractInput],
    proof_receipt: str | None,
) -> PlanTopology:
    if change.status != "open" or change.plan_id != plan.id or change.kind not in {"add", "split", "backlog"}:
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
    kind = "add" if change.kind == "add" else "split" if change.kind == "split" else "backlog"
    return topology_for(plan).apply_change(
        AppliedChange(
            id=change.id,
            kind=kind,
            nodes=tuple(topology_contract(node) for node in nodes),
            status="applied",
            decided_by="human",
            proof_receipt=proof_receipt,
        )
    )


def apply_plan_change(  # noqa: PLR0913 -- staged graph, decision proof and code-radius dependency
    before: StoredPlan,
    inserted: StoredPlan,
    change: StoredPlanChange,
    offered: Sequence[ContractInput],
    *,
    proof_receipt: str | None,
    radius: Callable[[StoredPlan], tuple[dict[str, tuple[str, ...] | None], bool]],
) -> StoredPlan:
    """Bind newly approved contracts to their frozen hashes and code radius."""
    _validate_gate_profiles(inserted)
    topology = apply_topology(before, change, offered, proof_receipt)
    radii, graph_present = radius(inserted)
    hashes = dict(topology.contract_hashes)
    fresh = {node.key for node in offered}
    nodes: list[StoredNode] = []
    for node in inserted.nodes:
        if node.key not in fresh:
            nodes.append(node)
            continue
        if node.pipeline and graph_present and radii.get(node.key) is None:
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False, extra=(node.key,))
        nodes.append(replace(node, contract_sha256=hashes.get(node.key), blast_radius=radii.get(node.key)))
    return replace(inserted, topology=topology, nodes=tuple(nodes))


def freeze_plan(
    plan: StoredPlan,
    radii: Mapping[str, tuple[str, ...] | None],
    *,
    at: datetime,
    graph_present: bool,
) -> StoredPlan:
    _validate_gate_profiles(plan)
    topology = topology_for(plan)
    if topology.phase == "frozen":
        return plan
    parents = {node.key: node for node in plan.nodes}
    for node in plan.nodes:
        parent = None if node.parent is None else parents.get(node.parent)
        validate_contract(node, parent)
        if node.pipeline and (
            not node.acceptance
            or not node.test_cmd
            or parent is None
            or not parent.invariants
            or not parent.gate_profile
            or (graph_present and radii.get(node.key) is None)
        ):
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False, extra=(node.key,))
    frozen = topology.freeze(at=at, decided_by="human")
    hashes = dict(frozen.contract_hashes)
    children = dict(frozen.baseline_children)
    return replace(
        plan,
        topology=frozen,
        updated_at=at,
        nodes=tuple(
            replace(
                node,
                contract_sha256=hashes[node.key],
                blast_radius=radii.get(node.key),
                baseline_children=children[node.key],
            )
            for node in plan.nodes
        ),
    )


def _validate_gate_profiles(plan: StoredPlan) -> None:
    for node in plan.nodes:
        if node.level == 0:
            _ = micro_commands(node.gate_profile or {}, location=f"{node.key}.gate_profile")


def repair_direction_profiles(
    plan: StoredPlan, profiles: Mapping[str, dict[str, object]], *, at: datetime
) -> StoredPlan:
    """Replace legacy L0 placeholders after L1 execution, keeping the L1 receipt bindings."""
    if plan.topology is None or plan.topology.phase != "frozen":
        raise DomainError(code=4011, name="PLAN_FROZEN", retryable=False)
    directions = {node.key: node for node in plan.nodes if node.level == 0}
    if not profiles or profiles.keys() - directions.keys():
        detail = "expected existing L0 keys"
        raise _direction_profile_error(detail)
    if any(node.lease_id is not None for node in plan.nodes):
        raise DomainError(code=4006, name="LEASE_HELD", retryable=True)
    if any(node.status not in {"done", "cancelled"} for node in plan.nodes if node.level == 1):
        detail = "direction repair requires closed L1 work"
        raise _direction_profile_error(detail)

    changed: dict[str, dict[str, object]] = {}
    for key, profile in profiles.items():
        original = directions[key].gate_profile or {}
        if profile == original:
            continue
        if directions[key].status not in {"pending", "blocked"}:
            detail = f"{key}: direction profile repair requires pending or blocked status"
            raise _direction_profile_error(detail)
        _validate_direction_repair(key, original, profile)
        changed[key] = profile
    if not changed:
        return plan

    candidate = replace(
        plan,
        nodes=tuple(
            replace(node, gate_profile=changed[node.key]) if node.key in changed else node for node in plan.nodes
        ),
    )
    topology = topology_for(candidate).refresh_direction_profiles(keys=set(changed))
    hashes = dict(topology.contract_hashes)
    return replace(
        candidate,
        topology=topology,
        updated_at=at,
        nodes=tuple(
            replace(node, contract_sha256=hashes[node.key]) if node.key in changed else node for node in candidate.nodes
        ),
    )


def _validate_direction_repair(key: str, original: Mapping[str, object], profile: dict[str, object]) -> None:
    old_environment = original.get("environment")
    placeholder = "0" * 64
    if not _environment_fields(old_environment) or old_environment["image"] not in (
        placeholder,
        "sha256:" + placeholder,
    ):
        detail = f"{key}.gate_profile.environment is not a legacy placeholder"
        raise _direction_profile_error(detail)
    direction_fields = {"direction", "environment"}
    if {k: v for k, v in original.items() if k not in direction_fields} != {
        k: v for k, v in profile.items() if k not in direction_fields
    }:
        detail = f"{key}.gate_profile: only direction and environment may change"
        raise _direction_profile_error(detail)
    _ = micro_commands(profile, location=f"{key}.gate_profile")

    environment = profile.get("environment")
    if not _environment_fields(environment):
        detail = f"{key}.gate_profile.environment requires container and image"
        raise _direction_profile_error(detail)
    container, image = environment["container"], environment["image"]
    if not isinstance(container, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", container) is None:
        detail = f"{key}.gate_profile.environment.container is invalid"
        raise _direction_profile_error(detail)
    digest = image.removeprefix("sha256:") if isinstance(image, str) else ""
    if digest == placeholder or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        detail = f"{key}.gate_profile.environment.image requires a nonzero SHA-256 digest"
        raise _direction_profile_error(detail)
    commands = profile.get("direction")
    if not _json_list(commands) or not commands or not all(_valid_argv(argv) for argv in commands):
        detail = f"{key}.gate_profile.direction requires nonempty argv lists"
        raise _direction_profile_error(detail)


def _valid_argv(value: object) -> bool:
    return (
        _json_list(value)
        and bool(value)
        and all(isinstance(arg, str) and arg.strip() and "\x00" not in arg for arg in value)
    )


def _environment_fields(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and value.keys() == {"container", "image"}


def _json_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _direction_profile_error(detail: str) -> DomainError:
    return DomainError(code=4013, name="NODE_CONTRACT", retryable=False, extra=(detail,))


def repair_gate_profiles(plan: StoredPlan, profiles: Mapping[str, dict[str, object]], *, at: datetime) -> StoredPlan:
    """Explicitly repair legacy invalid L0 profiles without replacing nodes or execution history."""
    if plan.topology is None or plan.topology.phase != "frozen":
        raise DomainError(code=4011, name="PLAN_FROZEN", retryable=False)
    directions = {node.key: node for node in plan.nodes if node.level == 0}
    if not profiles or profiles.keys() - directions.keys():
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False, extra=("expected existing L0 keys",))
    if any(node.lease_id is not None for node in plan.nodes):
        raise DomainError(code=4006, name="LEASE_HELD", retryable=True)
    if any(node.status not in {"pending", "blocked"} for node in plan.nodes):
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False, extra=("plan execution has started",))
    changed = False
    for key, profile in profiles.items():
        _ = micro_commands(profile, location=f"{key}.gate_profile")
        if profile == directions[key].gate_profile:
            continue
        try:
            _ = micro_commands(directions[key].gate_profile or {}, location=f"{key}.gate_profile")
        except DomainError:
            changed = True
        else:
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False, extra=(f"{key} profile is valid",))
    candidate = replace(
        plan,
        nodes=tuple(
            replace(node, gate_profile=profiles[node.key]) if node.key in profiles else node for node in plan.nodes
        ),
    )
    _validate_gate_profiles(candidate)
    if not changed:
        return plan
    # Freeze again to bind every child to its repaired parent, retaining the original graph and radius.
    frozen = freeze_plan(
        replace(candidate, topology=replace(plan.topology, phase="draft")),
        {node.key: node.blast_radius for node in plan.nodes if node.blast_radius is not None},
        at=at,
        graph_present=False,
    )
    return replace(
        frozen,
        topology=replace(
            topology_for(frozen),
            baseline_nodes=plan.topology.baseline_nodes,
            baseline_children=plan.topology.baseline_children,
            gross_added=plan.topology.gross_added,
        ),
        nodes=tuple(
            replace(node, baseline_children=original.baseline_children)
            for node, original in zip(frozen.nodes, plan.nodes, strict=True)
        ),
    )


def repair_plan_paths(
    plan: StoredPlan,
    paths: Mapping[str, tuple[str, ...]],
    expected: Mapping[str, str],
    *,
    at: datetime,
) -> StoredPlan:
    """Explicitly revise open frozen scopes; completed contracts and execution history stay intact."""
    if plan.topology is None or plan.topology.phase != "frozen":
        raise DomainError(code=4011, name="PLAN_FROZEN", retryable=False)
    by_key = {node.key: node for node in plan.nodes}
    if not paths or paths.keys() != expected.keys() or paths.keys() - by_key.keys():
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
    if any(node.lease_id is not None for node in plan.nodes):
        raise DomainError(code=4006, name="LEASE_HELD", retryable=True)
    for key, offered in paths.items():
        node = by_key[key]
        if node.status not in {"pending", "blocked", "failed"} or node.contract_sha256 != expected[key]:
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False, extra=(key,))
        if not offered or len(offered) > _MAX_SCOPE_PATHS:
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False, extra=(key,))
    candidate = replace(
        plan, nodes=tuple(replace(node, paths=paths[node.key]) if node.key in paths else node for node in plan.nodes)
    )
    if candidate == plan:
        return plan
    frozen = freeze_plan(
        replace(candidate, topology=replace(plan.topology, phase="draft")),
        {node.key: node.blast_radius for node in plan.nodes if node.blast_radius is not None},
        at=at,
        graph_present=False,
    )
    return replace(
        frozen,
        topology=replace(
            topology_for(frozen),
            frozen_at=plan.topology.frozen_at,
            baseline_nodes=plan.topology.baseline_nodes,
            baseline_children=plan.topology.baseline_children,
            gross_added=plan.topology.gross_added,
        ),
        nodes=tuple(
            replace(node, baseline_children=old.baseline_children)
            for node, old in zip(frozen.nodes, plan.nodes, strict=True)
        ),
    )
