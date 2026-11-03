"""Checkpoint list for one tab. Git is a port; this module does not import it."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from secrets import token_hex

from aconsole.core.application.ports.checkpoint_store import CheckpointJournal, CheckpointStore
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
_LABEL_LIMIT = 200


class CheckpointService:
    """Record a prompt, list the refs, and restore one after its confirm token."""

    _store: CheckpointStore
    _keep: int
    _logs: dict[str, CheckpointLog]
    _journal: CheckpointJournal | None

    def __init__(self, store: CheckpointStore, *, keep: int = 100, journal: CheckpointJournal | None = None) -> None:
        self._store = store
        self._keep = keep
        self._logs = {}
        self._journal = journal

    def use_journal(self, journal: CheckpointJournal) -> None:
        """Rows from now on go to `journal`. A tab reads its list from there the first time it is asked for."""
        self._journal = journal
        self._logs.clear()

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
        if self._journal is not None:
            self._journal.save(created, captured.tree)
        self._drop(repo, tab_id, dropped)
        return created

    def list(self, tab_id: str) -> tuple[Checkpoint, ...]:
        return self._log(tab_id).items()

    def get(self, tab_id: str, checkpoint_id: str) -> Checkpoint:
        return self._log(tab_id).get(checkpoint_id)

    def preview(self, tab_id: str, checkpoint_id: str, *, now: datetime, repo: Path | None = None) -> RestorePreview:
        log = self._log(tab_id)
        found = log.get(checkpoint_id)
        if repo is not None and not found.files:
            _ = log.with_files(checkpoint_id, self._store.paths(repo, found.ref))
        token = token_hex(_TOKEN_BYTES)
        return log.preview(checkpoint_id, confirm=token, now=now, ttl=_TTL)

    def restore(self, call: CheckpointRestore) -> RestoreResult:
        """Check the token, keep a safety ref of the current tree, then put the checkpoint back."""
        log = self._log(call.tab_id)
        target = log.get(call.checkpoint_id)
        result = log.restore(call.checkpoint_id, call.confirm, status=call.status, at=call.at)
        safety = log.get(result.safety_checkpoint)
        captured = self._store.capture(call.repo, safety.ref, safety.label)
        if self._journal is not None:
            self._journal.save(safety, captured.tree)
        self._store.materialize(call.repo, target.ref)
        return result

    def forget(self, tab_id: str, repo: Path, numbers: tuple[int, ...]) -> None:
        """The retention sweep removed these rows: drop their refs, and the list the core holds."""
        log = self._log(tab_id)
        known = {item.number: item for item in log.items()}
        for number in numbers:
            if number in known:
                self._store.delete(repo, known[number].ref)
        log.forget(frozenset(numbers))

    def _log(self, tab_id: str) -> CheckpointLog:
        found = self._logs.get(tab_id)
        if found is None:
            found = CheckpointLog(tab_id, keep=self._keep)
            if self._journal is not None:
                found.adopt(self._journal.load(tab_id))
            self._logs[tab_id] = found
        return found

    def _drop(self, repo: Path, tab_id: str, dropped: tuple[Checkpoint, ...]) -> None:
        for item in dropped:
            self._store.delete(repo, item.ref)
        if dropped and self._journal is not None:
            self._journal.delete(tab_id, tuple(item.number for item in dropped))


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
        return line[:_LABEL_LIMIT]
    return _EMPTY_LABEL
