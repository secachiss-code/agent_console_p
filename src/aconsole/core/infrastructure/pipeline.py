"""Durable and disposable adapters for the application L2 service."""

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal, final
from uuid import uuid4

import msgspec
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from aconsole.core.application.ports.gate_runner import GateRunner
from aconsole.core.application.services.context_shaper import ContextShaper, DirectionContext
from aconsole.core.application.services.gates import (
    BlastGate,
    BlastProfile,
    GateState,
    MicroGate,
    MicroOutcome,
    MicroProfile,
    MicroReceipt,
    MicroRequest,
)
from aconsole.core.application.services.pipeline import (
    Attempt,
    PipelineActor,
    PipelineBook,
    PipelineContract,
    PipelineService,
)
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.pipeline import PipelineState, StateBlock, genesis_hash, retry
from aconsole.core.domain.scope import check_scope
from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex
from aconsole.core.infrastructure.codegraph.skeleton import python_skeleton, python_slice
from aconsole.core.infrastructure.git.checkpoints import GitCheckpoints, remove_worktree_file
from aconsole.core.infrastructure.git.diff import GitDiff
from aconsole.core.infrastructure.git.runner import GitRunner
from aconsole.core.infrastructure.persistence.cache.schema import (
    direction_prefix_cache,
    node_scratchpad,
    session_file_tracker,
    verification_gate_cache,
)
from aconsole.core.infrastructure.persistence.cache.store import CacheStore
from aconsole.core.infrastructure.persistence.models import GateReceipt, NodeAttempt, Tab
from aconsole.core.infrastructure.persistence.repositories.attempts import (
    AttemptDatabase,
    AttemptLease,
    check_attempt_lease,
)
from aconsole.core.infrastructure.persistence.repositories.code_graph import persistent_impact
from aconsole.core.infrastructure.persistence.writer import Writer

_MODULE_ARGV_LENGTH = 3


def micro_profile(profile: Mapping[str, object]) -> MicroProfile:
    try:
        raw = TypeAdapter(dict[str, object]).validate_python(profile.get("micro", profile))
    except ValueError as exc:
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False) from exc
    return MicroProfile(**{key: _argv(raw.get(key)) for key in ("format", "fix", "lint", "types", "tests")})


def _argv(raw: object) -> tuple[str, ...]:
    try:
        argv = TypeAdapter(tuple[str, ...]).validate_python(raw)
    except ValueError as exc:
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False) from exc
    if not argv or any(not arg or "\x00" in arg for arg in argv):
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
    return argv


@final
class PipelineStorage:
    def __init__(self, writer: Writer, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.writer = writer
        self.database = AttemptDatabase(writer)
        self.now = now

    def history(self, node_id: str) -> tuple[Attempt, ...]:
        return tuple(
            Attempt(
                id=row.id,
                number=row.attempt_no,
                base_oid=row.base_oid,
                status=row.status,
                head_block=row.head_block or "",
                compact_verdict=row.compact_verdict,
            )
            for row in self.database.attempts(node_id)
        )

    def blocks(self, attempt_id: str) -> tuple[StateBlock, ...]:
        return tuple(msgspec.json.decode(row.body, type=StateBlock) for row in self.database.blocks(attempt_id))

    def start(self, contract: PipelineContract, actor: PipelineActor, number: int, base_oid: str) -> Attempt:
        tab_known = self.writer.call(lambda session: session.get(Tab, actor.tab_id) is not None)
        row = NodeAttempt(
            id=uuid4().hex[:26],
            node_id=contract.node_id,
            attempt_no=number,
            status="running",
            tab_id=actor.tab_id if tab_known else None,
            agent_kind=actor.agent,
            model=actor.model,
            base_oid=base_oid,
            started_at=self.now(),
        )
        self.database.start(row, lease=self.lease(actor, contract))
        return Attempt(
            id=row.id,
            number=number,
            base_oid=base_oid,
            status="running",
            head_block=row.head_block or genesis_hash(contract.module.contract_sha256),
        )

    def append(self, attempt_id: str, block: StateBlock, *, actor: PipelineActor, contract: PipelineContract) -> str:
        return self.database.append(attempt_id, block, now=self.now(), lease=self.lease(actor, contract))

    def finish(
        self,
        attempt_id: str,
        status: Literal["passed", "failed", "abandoned"],
        verdict: str,
        *,
        actor: PipelineActor,
        contract: PipelineContract,
    ) -> None:
        self.database.finish(
            attempt_id, status, now=self.now(), compact_verdict=verdict[:500], lease=self.lease(actor, contract)
        )

    def lease(self, actor: PipelineActor, contract: PipelineContract) -> AttemptLease:
        return AttemptLease(tab_id=actor.tab_id, fence=contract.lease.fence, at=self.now())

    def receipt_state(self, receipt_hash: str) -> str | None:
        receipt = self.database.receipt(receipt_hash)
        return receipt.state_hash if receipt is not None and receipt.passed else None


@final
class PipelineCache:
    def __init__(self, cache: CacheStore) -> None:
        self.cache = cache

    def prefix_get(self, plan_id: str, direction_key: str, manifest_sha256: str) -> str | None:
        row = self.cache.get(direction_prefix_cache, {"plan_id": plan_id, "direction_key": direction_key})
        if row is None or row["manifest_sha256"] != manifest_sha256:
            return None
        value: object = row["prefix_blob"]  # pyright: ignore[reportAny] -- validated SQL row boundary
        return value if isinstance(value, str) else None

    def prefix_put(self, plan_id: str, direction_key: str, manifest_sha256: str, prefix: str) -> None:
        self.cache.put(
            direction_prefix_cache,
            [
                {
                    "plan_id": plan_id,
                    "direction_key": direction_key,
                    "manifest_sha256": manifest_sha256,
                    "prefix_sha256": manifest_sha256,
                    "prefix_blob": prefix,
                    "byte_len": len(prefix.encode()),
                    "updated_at": _ms(),
                }
            ],
        )

    def prefix_seen(self, tab_id: str, agent_session_id: str, prefix_sha256: str) -> bool:
        return (
            self.cache.get(
                session_file_tracker,
                {"tab_id": tab_id, "agent_session_id": agent_session_id, "file_path": "prefix:" + prefix_sha256},
            )
            is not None
        )

    def prefix_mark_seen(self, tab_id: str, agent_session_id: str, prefix_sha256: str) -> None:
        self.cache.put(
            session_file_tracker,
            [
                {
                    "tab_id": tab_id,
                    "agent_session_id": agent_session_id,
                    "file_path": "prefix:" + prefix_sha256,
                    "file_sha256": prefix_sha256,
                    "turn_number": 0,
                }
            ],
        )

    def record(self, contract: PipelineContract, actor: PipelineActor, state: PipelineState) -> None:
        self.cache.put(
            node_scratchpad,
            [
                {
                    "node_id": contract.node_id,
                    "tab_id": actor.tab_id,
                    "fence": contract.lease.fence,
                    "attempt_no": state.attempt,
                    "step_type": state.step or "complete",
                    "head_block": state.head_block_hash,
                    "remaining_items_json": "[]",
                    "compact_verdict": None,
                    "diff_stat": None,
                    "updated_at": _ms(),
                }
            ],
        )


@final
class PipelineGit:
    def __init__(
        self, graph: CodeGraphIndex, versions: Callable[[PipelineActor, MicroProfile], str] | None = None
    ) -> None:
        self.graph = graph
        self.git = GitRunner()
        self.checkpoints = GitCheckpoints()
        self.versions = versions or _missing_versions

    def head(self, actor: PipelineActor) -> str:
        return self.git.read(actor.worktree, "rev-parse", "--verify", "HEAD").text.strip()

    def capture(self, actor: PipelineActor, contract: PipelineContract, attempt: Attempt, seq: int) -> GateState:
        temporary = f"refs/aconsole/attempts/{contract.node_id}/staging-{uuid4().hex}"
        try:
            captured = self.checkpoints.capture(actor.worktree, temporary, "AConsole pipeline snapshot")
            oid = self.git.read(actor.worktree, "rev-parse", "--verify", temporary).text.strip()
            # Content-addressed refs never overwrite a prior green snapshot on a changed-state retry.
            ref = f"refs/aconsole/attempts/{contract.node_id}/{attempt.number}/{seq}/{captured.tree}"
            held = self.git.read(actor.worktree, "rev-parse", "--verify", "--quiet", ref, codes=frozenset({0, 1}))
            if not held.text.strip():
                _ = self.git.write(actor.worktree, "update-ref", ref, oid)
        finally:
            self.checkpoints.delete(actor.worktree, temporary)
        delta = self.git.read(
            actor.worktree, "diff", "--binary", "--no-ext-diff", "--no-textconv", attempt.base_oid, ref
        ).text
        skeletons = tuple(
            (path, python_skeleton(self._source(actor, path)))
            for path in contract.module.blast_radius or ()
            if path.endswith(".py")
        )
        fingerprint = json.dumps(
            (
                captured.tree,
                contract.module.contract_sha256,
                contract.direction.gate_profile_json,
                skeletons,
                self.versions(actor, contract.profile),
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        return GateState(state_hash=_sha(fingerprint), delta_ref=ref, delta_sha256=_sha(delta))

    def changed(self, actor: PipelineActor, attempt: Attempt) -> tuple[str, ...]:
        return tuple(
            path
            for path in GitDiff().changed_paths(actor.worktree, attempt.base_oid)
            if path.endswith(".py") and (actor.worktree / path).is_file()
        )

    def tests(self, actor: PipelineActor, changed: tuple[str, ...]) -> tuple[str, ...]:
        paths: set[str] = {path for path in changed if path.startswith("tests/")}
        graph = self.graph.graph(actor.project_id)
        names = {node.id: node.path for node in graph.nodes}
        for path in changed:
            impact = persistent_impact(self.graph, actor.project_id, path, 1, 1000, None)
            if impact is not None:
                paths.update(
                    names[module]
                    for layer in impact[0]
                    for module in layer
                    if names[module].startswith("tests/") and names[module].endswith(".py")
                )
        return tuple(sorted(paths))[:3]

    def blast_tests(self, actor: PipelineActor, attempt: Attempt) -> tuple[str, ...]:
        graph = self.graph.graph(actor.project_id)
        names = {node.id: node.path for node in graph.nodes}
        # The nearest dependent tests run first; cap only after combining all changed roots.
        distances: dict[str, int] = {}
        for path in GitDiff().changed_paths(actor.worktree, attempt.base_oid):
            if path.startswith("tests/") and path.endswith(".py"):
                distances[path] = 0
            impact = persistent_impact(self.graph, actor.project_id, path, 3, 10000, None)
            if impact is None:
                continue
            if impact[1]:
                raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
            for depth, layer in enumerate(impact[0], 1):
                for module in layer:
                    candidate = names[module]
                    if candidate.startswith("tests/") and candidate.endswith(".py"):
                        distances[candidate] = min(distances.get(candidate, depth), depth)
        return tuple(sorted(distances, key=lambda path: (distances[path], path)))[:30]

    def reset(self, actor: PipelineActor, contract: PipelineContract, ref: str) -> None:
        saved = set(self.git.read(actor.worktree, "ls-tree", "-r", "--name-only", "-z", ref).text.split("\0")) - {""}
        current = set(self._paths(actor))
        for path in sorted(saved | current, reverse=True):
            if check_scope(contract.module.paths, (path,)).violations:
                continue
            if path in saved:
                _ = self.git.write(actor.worktree, "restore", "--source", ref, "--worktree", "--", path)
            else:
                remove_worktree_file(actor.worktree, path)

    def context(
        self, actor: PipelineActor, contract: PipelineContract, symbols: tuple[str, ...]
    ) -> tuple[DirectionContext, str | None]:
        paths = tuple(
            path
            for path in self._paths(actor)
            if path.endswith(".py") and not check_scope(contract.direction.scope, (path,)).violations
        )
        direction = replace(
            contract.direction, skeletons=tuple((path, python_skeleton(self._source(actor, path))) for path in paths)
        )
        targets = [path for path in paths if not check_scope(contract.module.paths, (path,)).violations]
        sliced = None
        if targets:
            selected = python_slice(self._source(actor, targets[0]), symbols)
            sliced = (
                targets[0]
                + "\n"
                + (
                    selected
                    or "\n".join(
                        f"{n}: {line}" for n, line in enumerate(self._source(actor, targets[0]).splitlines()[:120], 1)
                    )
                )
            )
        return direction, sliced

    def _paths(self, actor: PipelineActor) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(self.git.read(actor.worktree, "ls-files", "-c", "-o", "--exclude-standard", "-z").text.split("\0"))
                - {""}
            )
        )

    @staticmethod
    def _source(actor: PipelineActor, relative: str) -> str:
        path = (actor.worktree / relative).resolve()
        if not path.is_relative_to(actor.worktree.resolve()):
            raise DomainError(code=4015, name="SCOPE_VIOLATION", retryable=False, extra=(relative,))
        if not path.is_file():
            return ""
        with path.open("rb") as source:
            return source.read(1024 * 1024).decode("utf-8", errors="replace")


@final
class MicroRuntime:
    def __init__(
        self,
        storage: PipelineStorage,
        cache: PipelineCache,
        workspace: PipelineGit,
        runner: Callable[[PipelineActor, tuple[tuple[str, ...], ...]], GateRunner],
    ) -> None:
        self.storage, self.cache, self.workspace, self.runner = storage, cache, workspace, runner
        self._versions: dict[tuple[str, tuple[tuple[str, ...], ...]], str] = {}

    def versions(self, actor: PipelineActor, profile: MicroProfile) -> str:
        commands = tuple(
            sorted(
                {
                    (*argv[:_MODULE_ARGV_LENGTH], "--version")
                    if len(argv) >= _MODULE_ARGV_LENGTH and argv[1] == "-m"
                    else (argv[0], "--version")
                    for argv in (profile.format, profile.fix, profile.lint, profile.types, profile.tests)
                }
            )
        )
        key = (actor.tab_id, commands)
        if key not in self._versions:
            runner = self.runner(actor, commands)
            results = tuple(runner.run(command, tier=1) for command in commands)
            if any(result.returncode != 0 for result in results):
                raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
            self._versions[key] = _sha(json.dumps(tuple((result.argv, result.output) for result in results)))
        return self._versions[key]

    def run(
        self, actor: PipelineActor, contract: PipelineContract, attempt: Attempt, request: MicroRequest
    ) -> MicroOutcome:
        def capture() -> GateState:
            return self.workspace.capture(actor, contract, attempt, request.pipeline.seq)

        gate = MicroGate(
            bind_runner=lambda commands: self.runner(actor, commands),
            capture=capture,
            cache=_MicroCache(self.cache.cache, self.storage, request, capture),
            audit=_MicroAudit(self.storage, attempt, actor, contract),
        )
        return gate.run(request, contract.profile)

    def blast(
        self,
        actor: PipelineActor,
        contract: PipelineContract,
        attempt: Attempt,
        request: MicroRequest,
        check_only: bool,  # noqa: FBT001 -- callback port includes a read-only probe flag
    ) -> MicroOutcome | None:
        profile = BlastProfile(test_cmd=contract.module.test_cmd or "", tests=contract.profile.tests)

        def capture() -> GateState:
            return self.workspace.capture(actor, contract, attempt, request.pipeline.seq)

        gate = BlastGate(
            bind_runner=lambda commands: self.runner(actor, commands),
            capture=capture,
            cache=_MicroCache(
                self.cache.cache, self.storage, request, capture, argv_hash=profile.argv_hash(request.test_files)
            ),
            audit=_MicroAudit(self.storage, attempt, actor, contract, tier="blast"),
        )
        outcome = gate.run(request, profile, check_only=check_only)
        if outcome is not None and outcome.cached and outcome.receipt.passed and attempt.status == "running":
            _MicroAudit(self.storage, attempt, actor, contract, tier="blast").record(request, outcome)
        return outcome


@final
class _MicroCache:
    def __init__(
        self,
        cache: CacheStore,
        storage: PipelineStorage,
        request: MicroRequest,
        capture: Callable[[], GateState],
        *,
        argv_hash: str | None = None,
    ) -> None:
        self.cache, self.storage, self.request, self.capture = cache, storage, request, capture
        self.argv_hash = argv_hash

    def get(self, state_hash: str, tier: str) -> MicroOutcome | None:
        row = self.cache.get(verification_gate_cache, {"state_hash": state_hash, "tier": tier})
        if row is None and tier != "blast":
            return None
        state = self.capture()
        receipt_id: object = None if row is None else row["receipt_hash"]
        receipt = self.storage.database.receipt(receipt_id) if isinstance(receipt_id, str) else None
        if receipt is None and tier == "blast" and self.argv_hash is not None:

            def read(session: Session) -> GateReceipt | None:
                found = session.scalar(
                    select(GateReceipt)
                    .where(
                        GateReceipt.node_id == self.request.node_id,
                        GateReceipt.tier == tier,
                        GateReceipt.state_hash == state.state_hash,
                        GateReceipt.argv_sha256 == self.argv_hash,
                        GateReceipt.passed.is_(True),
                    )
                    .order_by(GateReceipt.created_at.desc())
                    .limit(1)
                )
                if found is not None:
                    session.expunge(found)
                return found

            loaded = self.storage.writer.call(read)
            receipt = loaded if isinstance(loaded, GateReceipt) else None
        if (
            receipt is None
            or receipt.node_id != self.request.node_id
            or receipt.tier != tier
            or receipt.state_hash != state.state_hash
        ):
            return None
        micro = MicroReceipt(
            node_id=receipt.node_id,
            state=state,
            argv_sha256=receipt.argv_sha256,
            passed=receipt.passed,
            compact_verdict=receipt.compact_verdict,
            remaining_items=tuple(receipt.compact_verdict.splitlines()[1:]),
            results=(),
        )
        pipeline, exhausted = self.request.pipeline, False
        if not receipt.passed:
            try:
                pipeline = retry(pipeline)
            except DomainError:
                exhausted = True
        return MicroOutcome(receipt=micro, pipeline=pipeline, exhausted=exhausted, cached=True)

    def put(self, state_hash: str, tier: str, outcome: MicroOutcome) -> None:
        self.cache.put(
            verification_gate_cache,
            [
                {
                    "state_hash": state_hash,
                    "tier": tier,
                    "node_id": outcome.receipt.node_id,
                    "passed": int(outcome.receipt.passed),
                    "compact_verdict": outcome.receipt.compact_verdict,
                    "receipt_hash": outcome.receipt.receipt_hash,
                    "cached_at": _ms(),
                }
            ],
        )


@final
class _MicroAudit:
    def __init__(
        self,
        storage: PipelineStorage,
        attempt: Attempt,
        actor: PipelineActor,
        contract: PipelineContract,
        *,
        tier: str = "micro",
    ) -> None:
        self.storage, self.attempt = storage, attempt
        self.actor, self.contract = actor, contract
        self.tier = tier

    def record(self, request: MicroRequest, outcome: MicroOutcome) -> None:
        receipt = outcome.receipt
        results = receipt.results

        def write(session: Session) -> None:
            check_attempt_lease(
                session,
                request.node_id,
                replace(self.storage.lease(self.actor, self.contract), allow_merged=self.tier == "blast"),
            )
            if session.get(GateReceipt, receipt.receipt_hash) is None:
                session.add(
                    GateReceipt(
                        receipt_hash=receipt.receipt_hash,
                        node_id=request.node_id,
                        tier=self.tier,
                        state_hash=receipt.state.state_hash,
                        argv_sha256=receipt.argv_sha256,
                        exit_code=next((result.returncode for result in results if result.returncode), 0),
                        passed=receipt.passed,
                        compact_verdict=receipt.compact_verdict,
                        log_ref=results[-1].log_path if results else None,
                        duration_ms=sum(result.duration_ms for result in results),
                        created_at=datetime.now(UTC),
                    )
                )
            attempt = session.get(NodeAttempt, self.attempt.id)
            if attempt is None or attempt.status != "running":
                message = "gate verdict has no running attempt"
                raise ValueError(message)
            attempt.compact_verdict = receipt.compact_verdict
            if not receipt.passed or self.tier == "blast":
                attempt.status = "passed" if receipt.passed else "failed"
                attempt.finished_at = datetime.now(UTC)

        _ = self.storage.writer.call(write)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _ms() -> int:
    return time.time_ns() // 1_000_000


def build_pipeline(
    *,
    book: PipelineBook,
    graph: CodeGraphIndex,
    writer: Writer,
    runner: Callable[[PipelineActor, tuple[tuple[str, ...], ...]], GateRunner],
) -> tuple[PipelineService, CacheStore]:
    cache = CacheStore.open_runtime()
    scratch = PipelineCache(cache)
    storage = PipelineStorage(writer)
    workspace = PipelineGit(graph)
    runtime = MicroRuntime(storage, scratch, workspace, runner)
    workspace.versions = runtime.versions
    return PipelineService(
        book=book,
        store=storage,
        workspace=workspace,
        scratch=scratch,
        shaper=ContextShaper(scratch),
        micro=runtime.run,
        blast=runtime.blast,
    ), cache


def _missing_versions(_actor: PipelineActor, _profile: MicroProfile) -> str:
    raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
