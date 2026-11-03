"""The frontmatter last synced for each vault note, in `vault_files`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import final

import msgspec
from sqlalchemy import select
from sqlalchemy.orm import Session

from aconsole.core.domain.vault_sync import Note
from aconsole.core.infrastructure.persistence.models import VaultFile
from aconsole.core.infrastructure.persistence.writer import Writer

_KIND = "plan_node"
_OWNER = "shared"
_CORE = "app"


@final
class VaultFileDatabase:
    """`BaseStore` for `VaultSync`."""

    def __init__(self, writer: Writer, now: Callable[[], datetime] | None = None) -> None:
        self._writer: Writer = writer
        self._now: Callable[[], datetime] = (lambda: datetime.now(UTC)) if now is None else now

    def load(self) -> dict[str, Note]:
        found: dict[str, Note] = {}

        def collect(session: Session) -> None:
            for row in session.scalars(select(VaultFile)).all():
                note = _note(row.base_frontmatter)
                if note is not None:
                    found[row.note_ref] = note

        _ = self._writer.call(collect)
        return found

    def save(self, note_ref: str, note: Note) -> None:
        at = self._now()
        base: dict[str, object] = asdict(note)

        def store(session: Session) -> None:
            row = session.get(VaultFile, note_ref)
            if row is None:
                row = VaultFile(note_ref=note_ref, kind=_KIND, owner=_OWNER, last_writer=_CORE, synced_at=at)
                session.add(row)
            row.base_frontmatter = base
            row.synced_at = at

        self._writer.post(store)


def _note(saved: dict[str, object] | None) -> Note | None:
    if saved is None:
        return None
    try:
        return Note(
            status=str(saved["status"]),
            owner_agent=str(saved["owner_agent"]),
            depends_on=_texts(saved["depends_on"]),
            paths=_texts(saved["paths"]),
            acceptance=_texts(saved["acceptance"]),
            review_required=saved["review_required"] is True,
            tags=_texts(saved["tags"]),
            lease=str(saved["lease"]),
            updated=str(saved["updated"]),
            title=str(saved["title"]),
            objective=str(saved["objective"]),
        )
    except KeyError:
        return None


def _texts(value: object) -> tuple[str, ...]:
    try:
        return msgspec.convert(value, tuple[str, ...], strict=False)
    except msgspec.ValidationError:
        return ()
