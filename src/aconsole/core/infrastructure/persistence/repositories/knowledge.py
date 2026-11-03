"""Facts and handovers through the single writer. A code reference is a node of its own, linked by an edge."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import final

import msgspec
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aconsole.core.application.ports.knowledge_store import StoredFact, StoredHandover
from aconsole.core.application.ports.plan_store import StoreWriteError
from aconsole.core.infrastructure.persistence.models import Handover, KbEdge, KbEdgeKind, KbNode, KbType, Project, Tab
from aconsole.core.infrastructure.persistence.writer import Writer

_LOG = logging.getLogger(__name__)
_REF_PREFIX = "ref-"
_ORIGIN = "agent"
_TITLE_LIMIT = 300

type _Work = Callable[[Session], None]


@final
class KnowledgeDatabase:
    """`KnowledgeStore` on the `kb_nodes`, `kb_edges`, and `handovers` tables."""

    def __init__(self, writer: Writer) -> None:
        self._writer: Writer = writer

    def load(self) -> tuple[tuple[StoredFact, ...], tuple[StoredHandover, ...]]:
        found: list[tuple[tuple[StoredFact, ...], tuple[StoredHandover, ...]]] = []

        def collect(session: Session) -> None:
            found.append((load_facts(session), load_handovers(session)))

        _ = self._writer.call(collect)
        return found[0]

    def save_fact(self, fact: StoredFact, supersedes: str | None) -> None:
        self._write(lambda session: save_fact(session, fact, supersedes), f"fact {fact.id}")

    def save_handover(self, packet: StoredHandover) -> None:
        self._write(lambda session: save_handover(session, packet), f"handover {packet.id}")

    def _write(self, work: _Work, label: str) -> None:
        try:
            _ = self._writer.call(work)
        except (SQLAlchemyError, TimeoutError, ValueError) as exc:
            message = f"{label} was not saved: {exc}"
            raise StoreWriteError(message) from exc


def save_fact(session: Session, fact: StoredFact, supersedes: str | None) -> None:
    """Add one fact. A project that is not in the database has nowhere to keep it, as with plans."""
    if session.get(Project, fact.project_id) is None:
        _LOG.warning("fact %s has no project row; it is not saved", fact.id)
        return
    session.add(
        KbNode(
            id=fact.id,
            project_id=fact.project_id,
            node_type=KbType(fact.kind),
            title=fact.title[:_TITLE_LIMIT],
            note_ref=fact.note_ref,
            source=fact.source,
            body=fact.body,
            is_stale=fact.stale,
            verified_commit=fact.verified_commit,
            updated_at=fact.updated_at,
        )
    )
    session.flush()
    for ref in dict.fromkeys(fact.code_refs):
        target = _ref_node(session, fact, ref)
        session.add(KbEdge(src_id=fact.id, dst_id=target, kind=KbEdgeKind.CODE_REF, origin=_ORIGIN))
    if supersedes is not None:
        previous = session.get(KbNode, supersedes)
        if previous is not None:
            previous.is_stale = True
            previous.updated_at = fact.updated_at
            session.add(KbEdge(src_id=fact.id, dst_id=supersedes, kind=KbEdgeKind.SUPERSEDES, origin=_ORIGIN))


def _ref_node(session: Session, fact: StoredFact, ref: str) -> str:
    digest = hashlib.sha256(f"{fact.project_id}\0{ref}".encode()).hexdigest()[:32]
    node_id = f"{_REF_PREFIX}{digest}"
    if session.get(KbNode, node_id) is None:
        session.add(
            KbNode(
                id=node_id,
                project_id=fact.project_id,
                node_type=KbType.MODULE,
                title=ref[:_TITLE_LIMIT],
                source=ref,
                is_stale=False,
                updated_at=fact.updated_at,
            )
        )
        session.flush()
    return node_id


def load_facts(session: Session) -> tuple[StoredFact, ...]:
    rows = session.scalars(select(KbNode)).all()
    sources = {row.id: row.source or "" for row in rows if row.id.startswith(_REF_PREFIX)}
    refs: dict[str, list[str]] = {}
    for edge in session.scalars(select(KbEdge).where(KbEdge.kind == KbEdgeKind.CODE_REF)).all():
        refs.setdefault(edge.src_id, []).append(sources.get(edge.dst_id, ""))
    facts = [row for row in rows if not row.id.startswith(_REF_PREFIX)]
    return tuple(
        StoredFact(
            id=row.id,
            project_id=row.project_id,
            kind=row.node_type.value,
            title=row.title,
            body=row.body or "",
            source=row.source or "",
            code_refs=tuple(refs.get(row.id, ())),
            stale=row.is_stale,
            note_ref=row.note_ref or "",
            updated_at=_aware(row.updated_at),
            verified_commit=row.verified_commit,
        )
        for row in sorted(facts, key=lambda row: _number(row.id))
    )


def save_handover(session: Session, packet: StoredHandover) -> None:
    if session.get(Project, packet.project_id) is None:
        _LOG.warning("handover %s has no project row; it is not saved", packet.id)
        return
    tab_id = packet.tab_id if packet.tab_id is not None and session.get(Tab, packet.tab_id) is not None else None
    session.add(
        Handover(
            id=packet.id,
            project_id=packet.project_id,
            source_tab_id=tab_id,
            summary_markdown=packet.goal,
            packet={
                "done": list(packet.done),
                "decisions": list(packet.decisions),
                "open_questions": list(packet.open_questions),
                "next_steps": list(packet.next_steps),
                "failing_checks": list(packet.failing_checks),
            },
            note_ref=packet.note_ref,
            created_at=packet.created_at,
        )
    )


def load_handovers(session: Session) -> tuple[StoredHandover, ...]:
    rows = sorted(session.scalars(select(Handover)).all(), key=lambda row: _number(row.id))
    return tuple(_stored_handover(row) for row in rows)


def _stored_handover(row: Handover) -> StoredHandover:
    packet = row.packet or {}
    return StoredHandover(
        id=row.id,
        project_id=row.project_id,
        tab_id=row.source_tab_id,
        note_ref=row.note_ref or "",
        goal=row.summary_markdown,
        done=_strings(packet, "done"),
        decisions=_strings(packet, "decisions"),
        open_questions=_strings(packet, "open_questions"),
        next_steps=_strings(packet, "next_steps"),
        failing_checks=_strings(packet, "failing_checks"),
        created_at=_aware(row.created_at),
    )


def _strings(packet: dict[str, object], key: str) -> tuple[str, ...]:
    return msgspec.convert(packet.get(key, ()), tuple[str, ...])


def _number(identifier: str) -> int:
    digits = identifier.rpartition("-")[2]
    return int(digits) if digits.isdigit() else 0


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
