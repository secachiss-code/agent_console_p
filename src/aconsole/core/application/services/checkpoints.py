"""Checkpoint list for one tab. Git is a port; this module does not import it."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from secrets import token_hex

from aconsole.core.application.ports.checkpoint_store import CheckpointStore
from aconsole.core.domain.checkpoints import (
    Checkpoint,
    CheckpointDraft,
    CheckpointLog,
    RestorePreview,
    RestoreResult,
    checkpoint_ref,
)
from aconsole.core.domain.tab_status import TabStatus

_TOKEN_BYTES = 16
_TTL = timedelta(minutes=10)
_EMPTY_LABEL = "prompt"


class CheckpointService:
    """Record a prompt, list the refs, and restore one after its confirm token."""

    _store: CheckpointStore
    _keep: int
    _logs: dict[str, CheckpointLog]

    def __init__(self, store: CheckpointStore, *, keep: int = 100) -> None:
        self._store = store
        self._keep = keep
        self._logs = {}

    def on_user_prompt(self, repo: Path, tab_id: str, text: str, at: datetime) -> Checkpoint:
        """`UserPromptSubmit`: one ref for the non-ignored worktree. The label is the first line."""
        log = self._log(tab_id)
        number = log.next_number()
        label = _label(text)
        captured = self._store.capture(repo, checkpoint_ref(tab_id, number), label)
        created, dropped = log.record(
            CheckpointDraft(
                created_at=at,
                label=label,
                files_changed=captured.files_changed,
                insertions=captured.insertions,
                deletions=captured.deletions,
                files=captured.files,
            )
        )
        self._drop(repo, dropped)
        return created

    def list(self, tab_id: str) -> tuple[Checkpoint, ...]:
        return self._log(tab_id).items()

    def preview(self, tab_id: str, checkpoint_id: str, *, now: datetime) -> RestorePreview:
        token = token_hex(_TOKEN_BYTES)
        return self._log(tab_id).preview(checkpoint_id, confirm=token, now=now, ttl=_TTL)

    def restore(self, call: CheckpointRestore) -> RestoreResult:
        """Check the token, keep a safety ref of the current tree, then put the checkpoint back."""
        log = self._log(call.tab_id)
        target = log.get(call.checkpoint_id)
        result = log.restore(call.checkpoint_id, call.confirm, status=call.status, at=call.at)
        safety = log.get(result.safety_checkpoint)
        _ = self._store.capture(call.repo, safety.ref, safety.label)
        self._store.materialize(call.repo, target.ref)
        return result

    def _log(self, tab_id: str) -> CheckpointLog:
        found = self._logs.get(tab_id)
        if found is None:
            found = CheckpointLog(tab_id, keep=self._keep)
            self._logs[tab_id] = found
        return found

    def _drop(self, repo: Path, dropped: tuple[Checkpoint, ...]) -> None:
        for item in dropped:
            self._store.delete(repo, item.ref)


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckpointRestore:
    """One restore. The confirm token comes from `preview`."""

    repo: Path
    tab_id: str
    checkpoint_id: str
    confirm: str
    status: TabStatus
    at: datetime


def _label(text: str) -> str:
    line = text.split("\n", maxsplit=1)[0].strip()
    if line:
        return line
    return _EMPTY_LABEL
