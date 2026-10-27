"""A1.2 primitives."""

from datetime import UTC, datetime, timedelta

import pytest

from aconsole.core.domain.agent import AgentCapabilities, AgentKind
from aconsole.core.domain.clock import Clock, FrozenClock
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.events import DomainEvent
from aconsole.core.domain.ids import new_ulid

_ALPHABET = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def test_frozen_clock_rejects_naive_and_returns_the_instant() -> None:
    clock = FrozenClock(AT)
    assert clock.now() == AT
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(AT.replace(tzinfo=None))


def test_system_clock_is_aware() -> None:
    instant = Clock().now()
    assert instant.tzinfo is not None


def test_ulid_shape_and_time_order() -> None:
    early = new_ulid(FrozenClock(AT), lambda _n: b"\x00" * 10)
    later = new_ulid(FrozenClock(AT + timedelta(seconds=2)), lambda _n: b"\xff" * 10)
    assert len(early) == 26
    assert set(early) <= _ALPHABET
    assert early < later
    assert early.endswith("0" * 16)


def test_ulid_rejects_bad_randomness_and_time() -> None:
    with pytest.raises(ValueError, match="10 bytes"):
        new_ulid(FrozenClock(AT), lambda _n: b"\x00")
    before_epoch = datetime(1970, 1, 1, tzinfo=UTC) - timedelta(seconds=5)
    with pytest.raises(ValueError, match="48-bit"):
        new_ulid(FrozenClock(before_epoch), lambda _n: b"\x00" * 10)


def test_domain_error_and_event() -> None:
    error = DomainError(code=2003, name="TAB_NOT_FOUND", retryable=False)
    assert str(error) == "TAB_NOT_FOUND (2003)"
    event = DomainEvent.stamp("tab_opened", FrozenClock(AT))
    assert event == DomainEvent(name="tab_opened", at=AT)


def test_agent_capabilities_names() -> None:
    caps = AgentCapabilities(
        hooks=True,
        statusline=False,
        limits=True,
        resume=False,
        fork=False,
        skills=True,
        approvals=True,
        checkpoints=False,
    )
    assert AgentKind.CLAUDE == "claude"
    assert caps.names() == frozenset({"hooks", "limits", "skills", "approvals"})
