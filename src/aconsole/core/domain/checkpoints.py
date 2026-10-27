"""Checkpoint refs, retention, and restore. The git commands live in infrastructure."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.tab_status import TabStatus
from aconsole.core.domain.workspace import path_segment

_NOT_FOUND = 3005
_CONFLICT = 3006
_BAD_TOKEN = 3007
_KEEP_MIN = 5
_KEEP_MAX = 500

SCOPE_NOTE = (
    "восстанавливаются все отслеживаемые и новые файлы worktree; "
    "игнорируемые (.gitignore) не затрагиваются"
)


def checkpoint_ref(tab_id: str, number: int) -> str:
    """`refs/aconsole/checkpoints/<tab>/<n>`."""
    if number < 1:
        message = "checkpoint number starts at 1"
        raise ValueError(message)
    return f"refs/aconsole/checkpoints/{path_segment(tab_id)}/{number}"


def check_keep(keep: int) -> int:
    if keep < _KEEP_MIN or keep > _KEEP_MAX:
        message = "checkpoints_keep must be from 5 to 500"
        raise ValueError(message)
    return keep


@dataclass(frozen=True, kw_only=True)
class CheckpointDraft:
    created_at: datetime
    label: str
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    files: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Checkpoint:
    id: str
    tab_id: str
    number: int
    created_at: datetime
    label: str
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    files: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return checkpoint_ref(self.tab_id, self.number)


@dataclass(frozen=True, kw_only=True)
class RestorePreview:
    checkpoint_id: str
    confirm_token: str
    expires_at: datetime
    scope_note: str
    files: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class RestoreResult:
    restored: bool
    safety_checkpoint: str


def retain(items: tuple[Checkpoint, ...], keep: int) -> tuple[tuple[Checkpoint, ...], tuple[Checkpoint, ...]]:
    """Keep the newest `keep` checkpoints. Older ones are dropped."""
    _ = check_keep(keep)
    ordered = tuple(sorted(items, key=lambda item: item.number))
    if len(ordered) <= keep:
        return ordered, ()
    return ordered[-keep:], ordered[:-keep]


class CheckpointLog:
    """One tab's checkpoint list and one-shot restore tokens."""

    tab_id: str
    keep: int

    def __init__(self, tab_id: str, *, keep: int) -> None:
        self.tab_id = tab_id
        self.keep = check_keep(keep)
        self._items: list[Checkpoint] = []
        self._tokens: dict[str, tuple[str, datetime]] = {}

    def record(self, draft: CheckpointDraft) -> tuple[Checkpoint, tuple[Checkpoint, ...]]:
        number = 1 if not self._items else self._items[-1].number + 1
        created = Checkpoint(
            id=f"{path_segment(self.tab_id)}-{number}",
            tab_id=self.tab_id,
            number=number,
            created_at=draft.created_at,
            label=draft.label,
            files_changed=draft.files_changed,
            insertions=draft.insertions,
            deletions=draft.deletions,
            files=draft.files,
        )
        kept, dropped = retain((*self._items, created), self.keep)
        self._items = list(kept)
        return created, dropped

    def get(self, checkpoint_id: str) -> Checkpoint:
        for item in self._items:
            if item.id == checkpoint_id:
                return item
        raise DomainError(code=_NOT_FOUND, name="CHECKPOINT_NOT_FOUND", retryable=False, extra=(checkpoint_id,))

    def preview(self, checkpoint_id: str, *, confirm: str, now: datetime, ttl: timedelta) -> RestorePreview:
        found = self.get(checkpoint_id)
        expires = now + ttl
        self._tokens[checkpoint_id] = (confirm, expires)
        return RestorePreview(
            checkpoint_id=found.id,
            confirm_token=confirm,
            expires_at=expires,
            scope_note=SCOPE_NOTE,
            files=found.files,
        )

    def restore(
        self,
        checkpoint_id: str,
        confirm: str,
        *,
        status: TabStatus,
        at: datetime,
    ) -> RestoreResult:
        found = self.get(checkpoint_id)
        if status is TabStatus.WORKING:
            raise DomainError(
                code=_CONFLICT,
                name="CHECKPOINT_RESTORE_CONFLICT",
                retryable=False,
                extra=(status.value,),
            )
        self._require_token(checkpoint_id, confirm, at)
        safety, _dropped = self.record(
            CheckpointDraft(created_at=at, label=f"safety {found.number}", files=found.files)
        )
        _ = self._tokens.pop(checkpoint_id, None)
        return RestoreResult(restored=True, safety_checkpoint=safety.id)

    def _require_token(self, checkpoint_id: str, confirm: str, at: datetime) -> None:
        stored = self._tokens.get(checkpoint_id)
        if stored is None or stored[0] != confirm or at >= stored[1]:
            raise DomainError(code=_BAD_TOKEN, name="CONFIRM_TOKEN_INVALID", retryable=False)
