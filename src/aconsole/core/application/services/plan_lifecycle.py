"""Frozen topology and contract snapshots, independent of transport and persistence."""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from aconsole.core.application.ports.plan_store import StoredNode, StoredPlan, StoredPlanChange
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.gate_profile import micro_commands
from aconsole.core.domain.plan_admission import AppliedChange, PlanTopology, TopologyContract, validate_contract
from aconsole.core.domain.vault_sync import Note, resolve_ref


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
    parents = {node.key: node for node in plan.nodes}
    checked: set[str] = set()
    for node in plan.nodes:
        if node.pipeline and node.parent in parents and node.parent not in checked:
            parent = parents[node.parent]
            _ = micro_commands(parent.gate_profile or {}, location=f"{parent.key}.gate_profile")
            checked.add(parent.key)


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
