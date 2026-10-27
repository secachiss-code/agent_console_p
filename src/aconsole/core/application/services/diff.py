"""Diff review for one tab. Git is a port; this module does not import it."""

from pathlib import Path
from typing import NoReturn

from aconsole.contracts.app import (
    CommandResult,
    DiffFileResult,
    DiffFileView,
    DiffHunk,
    DiffLine,
    DiffSummaryResult,
)
from aconsole.contracts.common import TabId
from aconsole.contracts.events import InjectToTerminal
from aconsole.core.application.ports.diff_reader import (
    DiffFileStat,
    DiffFileText,
    DiffHunkView,
    DiffReader,
    DiffReadError,
    DiffSummary,
    DiffWordLine,
)
from aconsole.core.application.ports.events import EventSink
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.tab_status import TabStatus

_TAB_MISSING = 2003
_TOO_LARGE = 3008
_LINES = 2000
_QUEUED = "Комментарий поставлен в очередь вкладки"


class DiffTab:
    """The repo and status a diff call needs. The tab book stays in `TabService`."""

    repo: Path
    status: TabStatus

    def __init__(self, repo: Path, status: TabStatus) -> None:
        self.repo = repo
        self.status = status


class DiffService:
    """`diff/summary`, `diff/file`, and `diff/sendComment` for tabs this service knows."""

    _reader: DiffReader
    _events: EventSink
    _tabs: dict[str, DiffTab]
    _queued: dict[str, list[str]]

    def __init__(self, reader: DiffReader, events: EventSink) -> None:
        self._reader = reader
        self._events = events
        self._tabs = {}
        self._queued = {}

    def bind(self, tab_id: str, repo: Path, status: TabStatus) -> None:
        self._tabs[tab_id] = DiffTab(repo, status)
        _ = self._queued.setdefault(tab_id, [])

    def set_status(self, tab_id: str, status: TabStatus) -> None:
        """Idle delivers comments that waited while the agent was busy."""
        tab = self._require(tab_id)
        tab.status = status
        if status is TabStatus.IDLE:
            self._flush(tab_id)

    def summary(self, tab_id: str, base: str | None = None) -> DiffSummaryResult:
        tab = self._require(tab_id)
        try:
            captured = self._reader.summary(tab.repo, base)
        except DiffReadError as exc:
            _reraise(exc)
        return _summary(captured)

    def file(
        self,
        tab_id: str,
        path: str,
        base: str | None = None,
        *,
        max_lines: int = _LINES,
    ) -> DiffFileResult:
        if max_lines < 0:
            message = "max_lines cannot be negative"
            raise ValueError(message)
        tab = self._require(tab_id)
        try:
            captured = self._reader.file(tab.repo, path, base, max_lines)
        except DiffReadError as exc:
            _reraise(exc)
        return _file(captured)

    def send_comment(self, tab_id: str, path: str, hunk_id: str, text: str) -> CommandResult:
        """Inject the comment when the tab is idle. Otherwise keep it and say so."""
        tab = self._require(tab_id)
        message = f"Комментарий к `{path}` {hunk_id}: {text}\n"
        if tab.status is not TabStatus.IDLE:
            self._queued.setdefault(tab_id, []).append(message)
            return CommandResult(ok=True, toast=_QUEUED)
        self._flush(tab_id)
        self._emit(tab_id, message)
        return CommandResult(ok=True, toast=None)

    def _require(self, tab_id: str) -> DiffTab:
        found = self._tabs.get(tab_id)
        if found is None:
            raise DomainError(code=_TAB_MISSING, name="TAB_NOT_FOUND", retryable=False, extra=(tab_id,))
        return found

    def _flush(self, tab_id: str) -> None:
        pending = self._queued.pop(tab_id, [])
        for text in pending:
            self._emit(tab_id, text)

    def _emit(self, tab_id: str, text: str) -> None:
        self._events.emit(InjectToTerminal(tab_id=TabId(tab_id), text=text, submit=True))


def _reraise(exc: DiffReadError) -> NoReturn:
    if exc.too_large:
        raise DomainError(
            code=_TOO_LARGE,
            name="DIFF_TOO_LARGE",
            retryable=False,
            extra=(str(exc.files), str(exc.byte_count)),
        ) from exc
    raise exc


def _summary(captured: DiffSummary) -> DiffSummaryResult:
    return DiffSummaryResult(
        base=captured.base,
        head=captured.head,
        files=tuple(_view(item) for item in captured.files),
        insertions=sum(item.insertions for item in captured.files),
        deletions=sum(item.deletions for item in captured.files),
    )


def _view(item: DiffFileStat) -> DiffFileView:
    return DiffFileView(
        path=item.path,
        old_path=item.old_path,
        change=item.change,
        insertions=item.insertions,
        deletions=item.deletions,
    )


def _file(captured: DiffFileText) -> DiffFileResult:
    return DiffFileResult(
        hunks=tuple(_hunk(item) for item in captured.hunks),
        truncated=captured.truncated,
    )


def _hunk(item: DiffHunkView) -> DiffHunk:
    return DiffHunk(
        id=item.id,
        header=item.header,
        old_start=item.old_start,
        old_lines=item.old_lines,
        new_start=item.new_start,
        new_lines=item.new_lines,
        lines=tuple(_diff_line(line) for line in item.lines),
    )


def _diff_line(line: DiffWordLine) -> DiffLine:
    return DiffLine(
        kind=line.kind,
        old_no=line.old_no,
        new_no=line.new_no,
        text=line.text,
        word_spans=line.word_spans,
    )
