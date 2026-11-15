"""Attempts survive cold cache, reject broken chains, and retain gate audit tails."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, inspect, select
from sqlalchemy.exc import IntegrityError

from aconsole.core.domain.pipeline import StateBlock, canonical_block, genesis_hash
from aconsole.core.domain.plan import NodeStatus
from aconsole.core.infrastructure.persistence.cleanup import remove_gate_logs, sweep
from aconsole.core.infrastructure.persistence.engine import MIGRATIONS, migrate, write_engine
from aconsole.core.infrastructure.persistence.models import (
    GateReceipt,
    NodeAttempt,
    NodeStepBlock,
    Plan,
    PlanNode,
    PlanStatus,
    Project,
)
from aconsole.core.infrastructure.persistence.repositories.attempts import AttemptDatabase
from aconsole.core.infrastructure.persistence.writer import Writer

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session

_NOW = datetime(2026, 11, 15, tzinfo=UTC)
_CONTRACT = "a" * 64


@pytest.fixture
def history(tmp_path: Path) -> Iterator[tuple[AttemptDatabase, Writer]]:
    url = f"sqlite:///{tmp_path / 'state.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    writer = Writer(engine)
    writer.start()
    writer.call(_seed)
    try:
        yield AttemptDatabase(writer), writer
    finally:
        writer.stop()
        engine.dispose()


def _seed(session: Session) -> None:
    session.add(Project(id="p", name="P", root_path="/p", languages=[], created_at=_NOW))
    session.flush()
    session.add(
        Plan(
            id="plan",
            project_id="p",
            title="Plan",
            goal="keep",
            status=PlanStatus.ACTIVE,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    session.flush()
    for node in ("n", "other"):
        session.add(
            PlanNode(
                id=node,
                plan_id="plan",
                node_key=node,
                title=node,
                objective="keep",
                status=NodeStatus.PENDING,
                paths=[],
                acceptance=[],
                review_required=False,
                layer=0,
                priority=0,
                unmet_blockers=0,
                fence_seq=0,
                contract_sha256=_CONTRACT,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )


def _attempt(number: int = 1, **kwargs: object) -> NodeAttempt:
    return NodeAttempt(
        id=f"attempt-{number}",
        node_id="n",
        attempt_no=number,
        status="running",
        base_oid="b" * 40,
        started_at=_NOW,
        **kwargs,
    )


def _block(**kwargs: object) -> StateBlock:
    base = StateBlock(
        node_id="n",
        attempt=1,
        seq=1,
        step_type="spec",
        contract_hash=_CONTRACT,
        prev_block_hash=genesis_hash(_CONTRACT),
    )
    return replace(base, **kwargs)


def _receipt(name: str, tier: str, *, node: str = "n", age: int = 31, log: str | None = None) -> GateReceipt:
    return GateReceipt(
        receipt_hash=name,
        node_id=node,
        tier=tier,
        state_hash="c" * 64,
        argv_sha256="d" * 64,
        exit_code=0,
        passed=True,
        compact_verdict="passed",
        log_ref=log,
        duration_ms=1,
        created_at=_NOW - timedelta(days=age),
    )


def test_history_survives_restart_and_loss_of_cache(history: tuple[AttemptDatabase, Writer], tmp_path: Path) -> None:
    database, writer = history
    database.start(_attempt())
    block = _block(token_cost_usd=0.01)
    digest = database.append("attempt-1", block, now=_NOW)
    assert database.append("attempt-1", block, now=_NOW) == digest
    database.save_receipt(_receipt("receipt", "micro"))
    database.finish("attempt-1", "passed", now=_NOW, compact_verdict="green")
    cache = tmp_path / "cache.db"
    cache.write_bytes(b"disposable")
    cache.unlink()
    writer.stop()
    restarted = Writer(write_engine(f"sqlite:///{tmp_path / 'state.db'}"))
    restarted.start()
    try:
        stored = AttemptDatabase(restarted)
        (attempt,) = stored.attempts("n")
        assert (attempt.status, attempt.head_block, attempt.cost_usd) == ("passed", digest, 0.01)
        assert attempt.started_at == _NOW
        (row,) = stored.blocks("attempt-1")
        assert row.body == canonical_block(block).decode()
        assert stored.receipt("receipt") is not None
    finally:
        restarted.stop()
        restarted._engine.dispose()


@pytest.mark.parametrize(
    "changes",
    [
        {"node_id": "other"},
        {"attempt": 2},
        {"seq": 2},
        {"prev_block_hash": "wrong"},
        {"contract_hash": "wrong"},
    ],
)
def test_invalid_block_cannot_change_head(history: tuple[AttemptDatabase, Writer], changes: dict[str, object]) -> None:
    database, _ = history
    database.start(_attempt())
    with pytest.raises(ValueError, match="chain"):
        database.append("attempt-1", _block(**changes), now=_NOW)
    assert database.blocks("attempt-1") == ()
    assert database.attempts("n")[0].head_block == genesis_hash(_CONTRACT)


def test_attempt_limit_and_retry_chain(history: tuple[AttemptDatabase, Writer]) -> None:
    database, _ = history
    last = genesis_hash(_CONTRACT)
    for number in range(1, 5):
        database.start(_attempt(number))
        block = _block(attempt=number, step_type="spec" if number == 1 else "patch", prev_block_hash=last)
        last = database.append(f"attempt-{number}", block, now=_NOW)
        database.finish(f"attempt-{number}", "failed", now=_NOW, compact_verdict="retry")
    with pytest.raises(ValueError, match="limit"):
        database.start(_attempt(5))
    assert len(database.attempts("n")) == 4
    assert database.attempts("n")[-1].head_block == last


def test_start_rejects_running_or_skipped_attempt(history: tuple[AttemptDatabase, Writer]) -> None:
    database, _ = history
    with pytest.raises(ValueError, match="number"):
        database.start(_attempt(2))
    database.start(_attempt())
    with pytest.raises(ValueError, match="running"):
        database.start(_attempt(2))


def test_database_constraints_and_cascade(history: tuple[AttemptDatabase, Writer]) -> None:
    database, writer = history
    with pytest.raises(IntegrityError):
        writer.call(lambda session: session.add(_attempt(5)))
    database.start(_attempt())
    with pytest.raises(IntegrityError):
        writer.call(
            lambda session: session.add(
                NodeAttempt(
                    id="duplicate",
                    node_id="n",
                    attempt_no=1,
                    status="running",
                    base_oid="b",
                    started_at=_NOW,
                )
            )
        )
    database.append("attempt-1", _block(), now=_NOW)
    database.save_receipt(_receipt("receipt", "micro"))
    writer.call(lambda session: session.execute(delete(PlanNode).where(PlanNode.id == "n")))
    assert database.attempts("n") == ()
    assert database.blocks("attempt-1") == ()
    assert database.receipt("receipt") is None


def test_receipt_is_immutable(history: tuple[AttemptDatabase, Writer]) -> None:
    database, _ = history
    database.save_receipt(_receipt("receipt", "micro"))
    database.save_receipt(_receipt("receipt", "micro"))
    with pytest.raises(ValueError, match="different content"):
        database.save_receipt(_receipt("receipt", "blast"))
    assert database.receipt("receipt").tier == "micro"


def test_retention_keeps_last_per_node_and_tier_and_deletes_logs(
    history: tuple[AttemptDatabase, Writer],
    tmp_path: Path,
) -> None:
    database, _ = history
    logs = tmp_path / "gates"
    logs.mkdir()
    for tier in ("micro", "blast", "direction"):
        for name, age in (("old", 40), ("tail", 31), ("fresh", 1)):
            path = logs / f"{tier}-{name}.log"
            path.write_text("log")
            database.save_receipt(_receipt(f"{tier}-{name}", tier, age=age, log=str(path)))
        database.save_receipt(_receipt(f"other-{tier}", tier, node="other", age=50))
    # All receipts can be old: retain the deterministic latest on a timestamp tie.
    database.save_receipt(_receipt("tie-a", "micro", node="other", age=50))
    database.save_receipt(_receipt("tie-z", "micro", node="other", age=50))
    assert database.prune(now=_NOW, log_root=logs) == 8
    for tier in ("micro", "blast", "direction"):
        assert database.receipt(f"{tier}-fresh") is not None
        assert (logs / f"{tier}-fresh.log").exists()
        assert not (logs / f"{tier}-old.log").exists()
        retained = "tie-z" if tier == "micro" else f"other-{tier}"
        assert database.receipt(retained) is not None
    assert database.prune(now=_NOW, log_root=logs) == 0


def test_cleanup_preserves_shared_logs_and_confines_deletion(
    history: tuple[AttemptDatabase, Writer],
    tmp_path: Path,
) -> None:
    database, _ = history
    shared = tmp_path / "shared.log"
    shared.write_text("keep")
    database.save_receipt(_receipt("old", "micro", log=str(shared)))
    database.save_receipt(_receipt("new", "micro", age=0, log=str(shared)))
    assert database.prune(now=_NOW, log_root=tmp_path) == 1
    assert shared.exists()
    root = tmp_path / "gates"
    root.mkdir()
    outside = tmp_path / "outside.log"
    outside.write_text("keep")
    link = root / "link.log"
    link.symlink_to(outside)
    remove_gate_logs((str(outside), str(link)), root)
    assert outside.exists()
    assert link.is_symlink()


def test_sweep_integrates_receipt_retention(history: tuple[AttemptDatabase, Writer]) -> None:
    database, writer = history
    database.save_receipt(_receipt("old", "micro"))
    database.save_receipt(_receipt("new", "micro", age=0))
    results = []
    writer.call(lambda session: results.append(sweep(session, now=_NOW, drop_ref=lambda _: None)))
    assert results[0].receipts_deleted == 1
    assert database.receipt("old") is None


def test_migration_downgrade_and_upgrade_preserve_existing_nodes(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    writer = Writer(engine)
    writer.start()
    writer.call(_seed)
    writer.stop()
    engine.dispose()
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url)
    command.downgrade(cfg, "0010_plan_hdag")
    migrate(url, "head")
    engine = write_engine(url)
    from sqlalchemy.orm import Session

    try:
        assert {"node_attempts", "node_step_blocks", "gate_receipts"} <= set(inspect(engine).get_table_names())
        with Session(engine) as session:
            assert set(session.scalars(select(PlanNode.id))) == {"n", "other"}
            assert list(session.scalars(select(NodeAttempt))) == []
            assert list(session.scalars(select(NodeStepBlock))) == []
    finally:
        engine.dispose()


def test_all_steps_and_retry_are_one_durable_chain(history: tuple[AttemptDatabase, Writer]) -> None:
    database, _ = history
    database.start(_attempt())
    head = genesis_hash(_CONTRACT)
    for seq, step in enumerate(("spec", "patch", "test", "verify"), 1):
        head = database.append("attempt-1", _block(seq=seq, step_type=step, prev_block_hash=head), now=_NOW)
    database.finish("attempt-1", "failed", now=_NOW, compact_verdict="retry")
    database.start(_attempt(2))
    assert database.attempts("n")[-1].head_block == head
    next_head = database.append("attempt-2", _block(attempt=2, step_type="patch", prev_block_hash=head), now=_NOW)
    assert database.blocks("attempt-2")[0].prev_block_hash == database.blocks("attempt-1")[-1].block_hash
    assert database.attempts("n")[-1].head_block == next_head


def test_node_can_set_a_smaller_attempt_limit(history: tuple[AttemptDatabase, Writer]) -> None:
    database, writer = history

    def lower_limit(session: Session) -> None:
        node = session.get(PlanNode, "n")
        assert node is not None
        node.max_attempts = 1

    writer.call(lower_limit)
    database.start(_attempt())
    database.finish("attempt-1", "failed", now=_NOW, compact_verdict="stop")
    with pytest.raises(ValueError, match="limit"):
        database.start(_attempt(2))


def test_failed_prune_does_not_remove_log(
    history: tuple[AttemptDatabase, Writer],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.exc import OperationalError

    from aconsole.core.infrastructure.persistence.repositories import attempts

    database, _ = history
    path = tmp_path / "old.log"
    path.write_text("audit")
    database.save_receipt(_receipt("old", "micro", log=str(path)))
    database.save_receipt(_receipt("new", "micro", age=0))
    original = attempts.trim_gate_receipts

    def fail(session: Session, now: datetime) -> tuple[int, tuple[str, ...]]:
        original(session, now)
        statement = "DELETE"
        raise OperationalError(statement, {}, ValueError("failed commit"))

    monkeypatch.setattr(attempts, "trim_gate_receipts", fail)
    with pytest.raises(OperationalError):
        database.prune(now=_NOW, log_root=tmp_path)
    assert path.exists()
    assert database.receipt("old") is not None
