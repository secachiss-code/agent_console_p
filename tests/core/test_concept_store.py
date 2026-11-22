"""Concept revisions stay, a repeated revision conflicts, and another key is left alone."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, override

import pytest
from sqlalchemy.orm import Session

from aconsole.core.application.ports.plan_store import ConceptStore
from aconsole.core.domain.concepts import ConceptDocument, ConceptWrite, apply_revision
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.models import Project
from aconsole.core.infrastructure.persistence.repositories.concepts import load_concept, save_concept

if TYPE_CHECKING:
    from pathlib import Path

_NOW = datetime(2026, 11, 22, tzinfo=UTC)


class _Memory(ConceptStore):
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], ConceptDocument] = {}

    @override
    def load_concept(self, project_id: str, key: str) -> ConceptDocument | None:
        return self.documents.get((project_id, key))

    @override
    def save_concept(
        self,
        project_id: str,
        key: str,
        title: str,
        body: str,
        expected_revision: int,
    ) -> ConceptDocument:
        revised = apply_revision(
            self.documents.get((project_id, key)),
            ConceptWrite(project_id=project_id, key=key, title=title, body=body, expected=expected_revision),
        )
        self.documents[(project_id, key)] = revised
        return revised


def _write(key: str, title: str, body: str, expected: int) -> ConceptWrite:
    return ConceptWrite(project_id="p", key=key, title=title, body=body, expected=expected)


def _project(session: Session) -> None:
    session.add(
        Project(
            id="p",
            name="n",
            root_path="/p",
            default_branch="main",
            languages=[],
            created_at=_NOW,
        )
    )
    session.commit()


def test_previous_revisions_are_kept(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'concepts.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    with Session(engine) as session:
        _project(session)
        first = save_concept(session, _write("PL-1", "First", "one", 0), _NOW)
        second = save_concept(session, _write("PL-1", "Second", "two", first.revision), _NOW)
        session.commit()
        stored = load_concept(session, "p", "PL-1")
    engine.dispose()
    assert stored is not None
    assert second.revision == 2
    assert [item.body for item in stored.history] == ["one", "two"]


def test_two_writers_with_one_revision_conflict_and_the_neighbor_stays(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'concepts.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    with Session(engine) as session:
        _project(session)
        original = save_concept(session, _write("PL-1", "First", "one", 0), _NOW)
        neighbor = save_concept(session, _write("PL-2", "Neighbor", "side", 0), _NOW)
        _ = save_concept(session, _write("PL-1", "Winner", "kept", original.revision), _NOW)
        with pytest.raises(DomainError) as caught:
            _ = save_concept(session, _write("PL-1", "Loser", "dropped", original.revision), _NOW)
        assert caught.value.name == "CONCEPT_REVISION_CONFLICT"
        stored = load_concept(session, "p", "PL-1")
        side = load_concept(session, "p", "PL-2")
        assert stored is not None
        assert stored.body == "kept"
        assert side == neighbor
    engine.dispose()


def test_store_protocol_rejects_a_bad_key() -> None:
    store: ConceptStore = _Memory()
    with pytest.raises(DomainError) as caught:
        _ = store.save_concept("p", "not a key", "t", "b", 0)
    assert caught.value.name == "CONCEPT_KEY_INVALID"
