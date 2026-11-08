"""`checkpoint/*` and `diff/*` for the tabs of the tab book. Git stays behind the two services."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, final

import msgspec

from aconsole.contracts.app import (
    CheckpointIdParams,
    CheckpointListParams,
    CheckpointListResult,
    CheckpointView,
    CommandResult,
    DiffFileParams,
    DiffSummaryParams,
    RestoreParams,
    RestorePreviewResult,
    RestoreResult,
    SendCommentParams,
)
from aconsole.contracts.common import CheckpointId, TabId
from aconsole.contracts.events import CheckpointCreated, TabUpdated, UiEvent
from aconsole.contracts.messages import UiMessage
from aconsole.core.application.services.checkpoints import CheckpointRestore, CheckpointService
from aconsole.core.application.services.diff import DiffService
from aconsole.core.application.services.tabs import ReviewTarget, TabRequestError, TabService
from aconsole.core.domain.checkpoints import Checkpoint
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.tab_status import TabStatus
from aconsole.core.domain.workspace import path_segment

REVIEW_METHODS: Final = frozenset(
    {
        "checkpoint/list",
        "checkpoint/restorePreview",
        "checkpoint/restore",
        "diff/summary",
        "diff/file",
        "diff/sendComment",
    }
)
_NOT_FOUND: Final = 3005
_MISSING_TAB: Final = "TAB_NOT_FOUND"


@final
class ReviewService:
    """Binds the tab book to the checkpoint and diff services. Calls run on the core's tab worker."""

    def __init__(
        self,
        tabs: Callable[[], TabService],
        checkpoints: CheckpointService,
        diff: DiffService,
        *,
        emit: Callable[[UiEvent], None],
    ) -> None:
        self._tabs: Callable[[], TabService] = tabs
        self.checkpoints: CheckpointService = checkpoints
        self._diff: DiffService = diff
        self._emit: Callable[[UiEvent], None] = emit

    def dispatch(self, method: str, raw: bytes) -> msgspec.Struct:
        """One request. A contract error is raised as `DomainError` or `TabRequestError`."""
        if method == "checkpoint/list":
            return self._list(msgspec.json.decode(raw, type=CheckpointListParams))
        if method == "checkpoint/restorePreview":
            return self._preview(msgspec.json.decode(raw, type=CheckpointIdParams))
        if method == "checkpoint/restore":
            return self._restore(msgspec.json.decode(raw, type=RestoreParams))
        if method == "diff/summary":
            params = msgspec.json.decode(raw, type=DiffSummaryParams)
            base = self._bind(str(params.tab_id), params.base)
            return self._diff.summary(str(params.tab_id), base)
        if method == "diff/file":
            detail = msgspec.json.decode(raw, type=DiffFileParams)
            base = self._bind(str(detail.tab_id), detail.base)
            return self._diff.file(str(detail.tab_id), detail.path, base, max_lines=detail.max_lines)
        comment = msgspec.json.decode(raw, type=SendCommentParams)
        return self._send_comment(comment)

    def _send_comment(self, comment: SendCommentParams) -> CommandResult:
        if not self._target(str(comment.tab_id)).started:
            return CommandResult(ok=False, toast=UiMessage.AGENT_NOT_STARTED)
        _ = self._bind(str(comment.tab_id), None)
        return self._diff.send_comment(str(comment.tab_id), comment.path, comment.hunk_id, comment.text)

    def on_prompt(self, tab_id: str, text: str) -> None:
        """The user sent a prompt: keep the worktree before the agent answers. A gone tab has nothing to keep."""
        target = self._tabs().review_target(tab_id)
        if target is None or not self._tabs().current_settings().checkpoints_enabled:
            return
        # Keep the legacy tab endpoint usable while the session timeline is the durable task history.
        _ = self._tabs().session_checkpoint(tab_id, text.splitlines()[0] if text else "prompt", "checkpoint")
        created = self.checkpoints.on_user_prompt(Path(target.worktree), tab_id, text, datetime.now(UTC))
        self._emit(CheckpointCreated(checkpoint=_view(created)))

    def send_text(self, tab_id: str, message: str) -> CommandResult:
        """Text for one tab's agent, waiting its turn when the agent is busy."""
        if not self._target(tab_id).started:
            return CommandResult(ok=False, toast=UiMessage.AGENT_NOT_STARTED)
        _ = self._bind(tab_id, None)
        return self._diff.send_text(tab_id, message)

    def keep_before_change(self, tab_id: str, label: str) -> None:
        """A checkpoint for what is about to be replaced. Taken even with checkpoints off: the user can go back."""
        target = self._target(tab_id)
        _ = self._tabs().session_checkpoint(tab_id, label, "safety", lock_held=True)
        created = self.checkpoints.on_user_prompt(Path(target.worktree), tab_id, label, datetime.now(UTC))
        self._emit(CheckpointCreated(checkpoint=_view(created)))

    def on_event(self, event: UiEvent) -> None:
        """Comments that waited for the agent go out when its tab turns idle."""
        if not isinstance(event, TabUpdated) or "status" not in event.changed:
            return
        tab_id = str(event.tab.id)
        _ = self._bind(tab_id, None)
        self._diff.set_status(tab_id, TabStatus(event.tab.status.value))

    def forget_refs(self, refs: tuple[str, ...]) -> tuple[str, ...]:
        """Refs the sweep dropped, for tabs still open. Returns the refs of tabs this core no longer knows."""
        unknown: list[str] = []
        by_tab: dict[str, list[int]] = {}
        for ref in refs:
            parts = ref.rsplit("/", maxsplit=2)
            tab_id = self._tab_of_segment(parts[-2])
            if tab_id is None:
                unknown.append(ref)
            else:
                by_tab.setdefault(tab_id, []).append(int(parts[-1]))
        for tab_id, numbers in by_tab.items():
            target = self._tabs().review_target(tab_id)
            if target is not None:
                self.checkpoints.forget(tab_id, Path(target.worktree), tuple(numbers))
        return tuple(unknown)

    def _list(self, params: CheckpointListParams) -> CheckpointListResult:
        tab_id = str(params.tab_id)
        _ = self._target(tab_id)
        return CheckpointListResult(checkpoints=tuple(_view(item) for item in self.checkpoints.list(tab_id)))

    def _preview(self, params: CheckpointIdParams) -> RestorePreviewResult:
        tab_id = self._owner(str(params.checkpoint_id))
        target = self._target(tab_id)
        found = self.checkpoints.get(tab_id, str(params.checkpoint_id))
        preview = self.checkpoints.preview(
            tab_id, str(params.checkpoint_id), now=datetime.now(UTC), repo=Path(target.worktree),
        )
        return RestorePreviewResult(
            diffstat=f"{found.files_changed} / +{found.insertions} / -{found.deletions}",
            files=preview.files,
            confirm_token=preview.confirm_token,
            scope_note=preview.scope_note,
        )

    def _restore(self, params: RestoreParams) -> RestoreResult:
        self._tabs().require_session_writable(self._owner(str(params.checkpoint_id)))
        tab_id = self._owner(str(params.checkpoint_id))
        target = self._target(tab_id)
        result = self.checkpoints.restore(
            CheckpointRestore(
                repo=Path(target.worktree),
                tab_id=tab_id,
                checkpoint_id=str(params.checkpoint_id),
                confirm=params.confirm_token,
                status=TabStatus(target.status.value),
                at=datetime.now(UTC),
            )
        )
        return RestoreResult(restored=result.restored, safety_checkpoint=CheckpointId(result.safety_checkpoint))

    def _bind(self, tab_id: str, base: str | None) -> str | None:
        """Tell the diff service where the tab is now. No explicit base means the branch the tab started from."""
        target = self._target(tab_id)
        self._diff.bind(tab_id, Path(target.worktree), TabStatus(target.status.value))
        return base if base is not None else target.base_branch

    def _target(self, tab_id: str) -> ReviewTarget:
        target = self._tabs().review_target(tab_id)
        if target is None:
            raise TabRequestError(_MISSING_TAB)
        return target

    def _owner(self, checkpoint_id: str) -> str:
        segment, _, _number = checkpoint_id.rpartition("-")
        tab_id = self._tab_of_segment(segment)
        if tab_id is None:
            raise DomainError(code=_NOT_FOUND, name="CHECKPOINT_NOT_FOUND", retryable=False, extra=(checkpoint_id,))
        return tab_id

    def _tab_of_segment(self, segment: str) -> str | None:
        for tab_id in self._tabs().tab_ids():
            if path_segment(tab_id) == segment:
                return tab_id
        return None


def _view(item: Checkpoint) -> CheckpointView:
    return CheckpointView(
        id=CheckpointId(item.id),
        tab_id=TabId(item.tab_id),
        turn=item.number,
        created_at=item.created_at,
        files_changed=item.files_changed,
        insertions=item.insertions,
        deletions=item.deletions,
        label=item.label,
    )
