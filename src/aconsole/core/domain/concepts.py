"""A concept document and the revisions that must survive a later edit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NoReturn

from aconsole.core.domain.errors import DomainError

_KEY = re.compile(r"^[A-Z]{1,4}-\d{1,4}$")
_CONFLICT = 4020
_INVALID = 4021


@dataclass(frozen=True, slots=True, kw_only=True)
class ConceptRevision:
    revision: int
    title: str
    body: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ConceptDocument:
    project_id: str
    key: str
    title: str
    body: str
    revision: int
    history: tuple[ConceptRevision, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ConceptWrite:
    project_id: str
    key: str
    title: str
    body: str
    expected: int


def require_concept_key(key: str) -> None:
    if _KEY.fullmatch(key) is None:
        raise DomainError(code=_INVALID, name="CONCEPT_KEY_INVALID", retryable=False)


def apply_revision(current: ConceptDocument | None, write: ConceptWrite) -> ConceptDocument:
    """Return the next document. A mismatched revision raises and changes nothing."""
    require_concept_key(write.key)
    if current is None:
        if write.expected != 0:
            _conflict()
        snapshot = ConceptRevision(revision=1, title=write.title, body=write.body)
        return ConceptDocument(
            project_id=write.project_id,
            key=write.key,
            title=write.title,
            body=write.body,
            revision=1,
            history=(snapshot,),
        )
    if current.project_id != write.project_id or current.key != write.key or current.revision != write.expected:
        _conflict()
    revision = write.expected + 1
    snapshot = ConceptRevision(revision=revision, title=write.title, body=write.body)
    return ConceptDocument(
        project_id=write.project_id,
        key=write.key,
        title=write.title,
        body=write.body,
        revision=revision,
        history=(*current.history, snapshot),
    )


def _conflict() -> NoReturn:
    raise DomainError(code=_CONFLICT, name="CONCEPT_REVISION_CONFLICT", retryable=False)
