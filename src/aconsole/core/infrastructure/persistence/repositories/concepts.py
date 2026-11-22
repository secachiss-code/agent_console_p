"""Concept revisions. A new row is appended; the previous snapshot stays."""

from __future__ import annotations

import os
from datetime import datetime
from typing import NoReturn

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aconsole.core.domain.clock import FrozenClock
from aconsole.core.domain.concepts import ConceptDocument, ConceptRevision, ConceptWrite, apply_revision
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.ids import new_ulid
from aconsole.core.infrastructure.persistence.models import ConceptDocument as ConceptDocumentRow
from aconsole.core.infrastructure.persistence.models import ConceptRevision as ConceptRevisionRow
from aconsole.core.infrastructure.persistence.models import Project

_CONFLICT = 4020


def load_concept(session: Session, project_id: str, key: str) -> ConceptDocument | None:
    row = _document(session, project_id, key)
    if row is None:
        return None
    return _published(session, row)


def save_concept(session: Session, write: ConceptWrite, now: datetime) -> ConceptDocument:
    """Compare-and-swap one key. A conflict leaves that key and every other key as they were."""
    if session.get(Project, write.project_id) is None:
        raise DomainError(code=2001, name="PROJECT_NOT_FOUND", retryable=False)
    current = load_concept(session, write.project_id, write.key)
    revised = apply_revision(current, write)
    if current is None:
        _insert(session, revised, now)
    else:
        _advance(session, write, revised, now)
    session.flush()
    stored = load_concept(session, write.project_id, write.key)
    if stored is None:
        _conflict()
    return stored


def _insert(session: Session, document: ConceptDocument, now: datetime) -> None:
    snapshot = document.history[-1]
    try:
        session.add(
            ConceptDocumentRow(
                id=_ulid(now),
                project_id=document.project_id,
                key=document.key,
                title=document.title,
                body=document.body,
                revision=document.revision,
                updated_at=now,
            )
        )
        session.flush()
    except IntegrityError:
        session.rollback()
        _conflict()
    row = _document(session, document.project_id, document.key)
    if row is None:
        _conflict()
    session.add(
        ConceptRevisionRow(
            id=_ulid(now),
            document_id=row.id,
            revision=snapshot.revision,
            title=snapshot.title,
            body=snapshot.body,
            created_at=now,
        )
    )


def _advance(session: Session, write: ConceptWrite, document: ConceptDocument, now: datetime) -> None:
    snapshot = document.history[-1]
    _ = session.execute(
        update(ConceptDocumentRow)
        .where(
            ConceptDocumentRow.project_id == write.project_id,
            ConceptDocumentRow.key == write.key,
            ConceptDocumentRow.revision == write.expected,
        )
        .values(title=document.title, body=document.body, revision=document.revision, updated_at=now)
    )
    session.expire_all()
    row = _document(session, write.project_id, write.key)
    if row is None or row.revision != document.revision:
        _conflict()
    session.add(
        ConceptRevisionRow(
            id=_ulid(now),
            document_id=row.id,
            revision=snapshot.revision,
            title=snapshot.title,
            body=snapshot.body,
            created_at=now,
        )
    )


def _document(session: Session, project_id: str, key: str) -> ConceptDocumentRow | None:
    return session.scalar(
        select(ConceptDocumentRow).where(
            ConceptDocumentRow.project_id == project_id,
            ConceptDocumentRow.key == key,
        )
    )


def _published(session: Session, row: ConceptDocumentRow) -> ConceptDocument:
    snapshots = session.scalars(
        select(ConceptRevisionRow).where(ConceptRevisionRow.document_id == row.id).order_by(ConceptRevisionRow.revision)
    ).all()
    history = tuple(ConceptRevision(revision=item.revision, title=item.title, body=item.body) for item in snapshots)
    return ConceptDocument(
        project_id=row.project_id,
        key=row.key,
        title=row.title,
        body=row.body,
        revision=row.revision,
        history=history,
    )


def _conflict() -> NoReturn:
    raise DomainError(code=_CONFLICT, name="CONCEPT_REVISION_CONFLICT", retryable=False)


def _ulid(now: datetime) -> str:
    return new_ulid(FrozenClock(now), os.urandom)
