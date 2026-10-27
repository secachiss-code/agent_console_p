"""Checkpoint refs, retention, and restore guards."""

from datetime import UTC, datetime, timedelta

import pytest

from aconsole.core.domain.checkpoints import (
    SCOPE_NOTE,
    Checkpoint,
    CheckpointDraft,
    CheckpointLog,
    check_keep,
    checkpoint_ref,
    retain,
)
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.tab_status import TabStatus

AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _point(number: int) -> Checkpoint:
    return Checkpoint(id=f"tab-{number}", tab_id="tab", number=number, created_at=AT, label=f"turn {number}")


def test_ref_and_keep_bounds() -> None:
    assert checkpoint_ref("tab/1", 3) == "refs/aconsole/checkpoints/tab-1/3"
    with pytest.raises(ValueError, match="starts at 1"):
        checkpoint_ref("tab", 0)
    assert check_keep(100) == 100
    with pytest.raises(ValueError, match="5 to 500"):
        check_keep(4)
    with pytest.raises(ValueError, match="5 to 500"):
        check_keep(501)


def test_retention_drops_the_oldest() -> None:
    items = tuple(_point(number) for number in range(1, 8))
    kept, dropped = retain(items, 5)
    assert [item.number for item in kept] == [3, 4, 5, 6, 7]
    assert [item.number for item in dropped] == [1, 2]


def test_restore_guards_and_safety_checkpoint() -> None:
    log = CheckpointLog("tab", keep=5)
    created, dropped = log.record(
        CheckpointDraft(created_at=AT, label="first line", files=("a.py",), files_changed=1, insertions=2)
    )
    assert dropped == ()
    assert created.ref == "refs/aconsole/checkpoints/tab/1"
    with pytest.raises(DomainError, match="CHECKPOINT_NOT_FOUND"):
        log.restore("missing", "tok", status=TabStatus.IDLE, at=AT)
    preview = log.preview(created.id, confirm="tok", now=AT, ttl=timedelta(seconds=30))
    assert preview.scope_note == SCOPE_NOTE
    assert preview.files == ("a.py",)
    with pytest.raises(DomainError, match="CHECKPOINT_RESTORE_CONFLICT"):
        log.restore(created.id, "tok", status=TabStatus.WORKING, at=AT)
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        log.restore(created.id, "nope", status=TabStatus.IDLE, at=AT)
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        log.restore(created.id, "tok", status=TabStatus.IDLE, at=AT + timedelta(seconds=30))
    preview = log.preview(created.id, confirm="tok", now=AT, ttl=timedelta(seconds=30))
    restored = log.restore(created.id, preview.confirm_token, status=TabStatus.IDLE, at=AT + timedelta(seconds=1))
    assert restored.restored is True
    assert restored.safety_checkpoint == "tab-2"
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        log.restore(created.id, "tok", status=TabStatus.IDLE, at=AT + timedelta(seconds=2))


def test_recording_past_the_limit_drops_the_oldest() -> None:
    log = CheckpointLog("tab", keep=5)
    for number in range(1, 8):
        log.record(CheckpointDraft(created_at=AT + timedelta(seconds=number), label=f"turn {number}"))
    assert [item.number for item in log._items] == [3, 4, 5, 6, 7]
