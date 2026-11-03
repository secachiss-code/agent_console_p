"""Calls of the review page. No GTK here."""

from aconsole.contracts.app import (
    CheckpointIdParams,
    CheckpointListParams,
    CheckpointListResult,
    CheckpointView,
    CommandResult,
    DiffFileParams,
    DiffFileResult,
    DiffSummaryParams,
    DiffSummaryResult,
    RestoreParams,
    RestorePreviewResult,
    RestoreResult,
    SendCommentParams,
)
from aconsole.contracts.common import CheckpointId, TabId
from aconsole.ui.client.core_client import CoreClient

_GUTTER = 5
_MARKS = {"add": "+", "del": "-", "context": " ", "meta": "\\"}


class ReviewViewModel:
    def __init__(self, client: CoreClient, tab_id: TabId) -> None:
        self.client: CoreClient = client
        self.tab_id: TabId = tab_id

    async def summary(self) -> DiffSummaryResult:
        return await self.client.request("diff/summary", DiffSummaryParams(tab_id=self.tab_id), DiffSummaryResult)

    async def file(self, path: str) -> DiffFileResult:
        return await self.client.request("diff/file", DiffFileParams(tab_id=self.tab_id, path=path), DiffFileResult)

    async def comment(self, path: str, hunk_id: str, text: str) -> CommandResult:
        params = SendCommentParams(tab_id=self.tab_id, path=path, hunk_id=hunk_id, text=text)
        return await self.client.request("diff/sendComment", params, CommandResult)

    async def checkpoints(self) -> tuple[CheckpointView, ...]:
        params = CheckpointListParams(tab_id=self.tab_id)
        listed = await self.client.request("checkpoint/list", params, CheckpointListResult)
        return tuple(reversed(listed.checkpoints))

    async def preview(self, checkpoint_id: CheckpointId) -> RestorePreviewResult:
        params = CheckpointIdParams(checkpoint_id=checkpoint_id)
        return await self.client.request("checkpoint/restorePreview", params, RestorePreviewResult)

    async def restore(self, checkpoint_id: CheckpointId, token: str) -> RestoreResult:
        params = RestoreParams(checkpoint_id=checkpoint_id, confirm_token=token)
        return await self.client.request("checkpoint/restore", params, RestoreResult)


def gutter(old_no: int | None, new_no: int | None, kind: str) -> str:
    """Both line numbers and the change mark, in a fixed width."""
    old = "" if old_no is None else str(old_no)
    new = "" if new_no is None else str(new_no)
    return f"{old:>{_GUTTER}} {new:>{_GUTTER}} {_MARKS.get(kind, ' ')} "


def files_preview(files: tuple[str, ...], limit: int = 8) -> tuple[tuple[str, ...], int]:
    """The first files to name in a dialog, and how many stay unnamed."""
    return files[:limit], max(0, len(files) - limit)
