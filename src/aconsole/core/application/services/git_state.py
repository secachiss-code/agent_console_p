"""`git/state`, `git/graph`, and diffs by source. Git is a port; the tab book is read through `GitCatalog`.

Everything keyed by commit ids is cached: two commits have one merge result forever. Callers who arrive while a
state is being built wait and share the next finished one, so a dozen tabs ask git once.
"""

import hashlib
import itertools
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn, Protocol, final

from aconsole.contracts.app import DiffFileResult, DiffFileView, DiffHunk, DiffLine, DiffSummaryResult
from aconsole.contracts.common import AgentKind, NodeId, PlanId, ProjectId, TabId, TabStatus
from aconsole.contracts.git import (
    BranchView,
    DiffSource,
    GitGraphParams,
    GitGraphResult,
    GitNodeView,
    GitOperationView,
    GitRefView,
    GitStateResult,
    InProgress,
    MergeProbeView,
    WorktreeGitState,
)
from aconsole.core.application.ports.diff_reader import DiffReadError
from aconsole.core.application.ports.git_repo import (
    CommitRow,
    DiffSpec,
    GitReader,
    MergeProbe,
    RefRow,
    StatusRow,
    WorktreeRow,
)
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.git_state import (
    blocks_writes,
    checkpoint_ref_name,
    choose_base,
    require_oid,
    tab_segment,
)
from aconsole.core.domain.tab_status import TabStatus as DomainTabStatus

_PROJECT_MISSING: Final = 2001
_TAB_MISSING: Final = 2003
_WORKTREE_UNKNOWN: Final = 3021
_SNAPSHOT_STALE: Final = 3023
_TOO_LARGE: Final = 3008
_CACHE: Final = 2048
_HEADS: Final = "refs/heads/"
_TAGS: Final = "refs/tags/"
_REMOTES: Final = "refs/remotes/"
_CHECKPOINTS: Final = "refs/aconsole/checkpoints/"
_DEFAULT_LINES: Final = 2000
_IN_PROGRESS: Final[tuple[InProgress, ...]] = ("merge", "rebase", "cherry-pick", "revert", "bisect")


@dataclass(frozen=True, slots=True, kw_only=True)
class GitProject:
    project_id: str
    root: str
    default_branch: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class GitTab:
    tab_id: str
    title: str
    agent: AgentKind
    status: TabStatus
    worktree: str
    branch: str | None
    plan_id: str | None
    plan_node: str | None


class GitCatalog(Protocol):
    def git_project(self, project_id: str) -> GitProject | None: ...

    def git_tabs(self, project_id: str) -> tuple[GitTab, ...]: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class _Built:
    ticket: int
    state: GitStateResult


class _Lru[Key, Value]:
    """A bounded map. Values never go stale: their keys are commit ids."""

    def __init__(self) -> None:
        self._items: OrderedDict[Key, Value] = OrderedDict()
        self._lock: threading.Lock = threading.Lock()

    def get(self, key: Key, build: Callable[[], Value]) -> Value:
        with self._lock:
            found = self._items.get(key)
            if found is not None:
                self._items.move_to_end(key)
                return found
        value = build()
        with self._lock:
            self._items[key] = value
            while len(self._items) > _CACHE:
                _ = self._items.popitem(last=False)
        return value


def _hash(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:32]


def _same_path(left: str, right: str) -> bool:
    return Path(left).resolve() == Path(right).resolve()


@final
class GitStateService:
    def __init__(self, reader: GitReader, catalog: Callable[[], GitCatalog]) -> None:
        self.reader: GitReader = reader
        self._reader: GitReader = reader
        self._catalog: Callable[[], GitCatalog] = catalog
        self._probes: _Lru[tuple[str, str, str], MergeProbeView] = _Lru()
        self._distance: _Lru[tuple[str, str, str], tuple[int, int]] = _Lru()
        self._tickets: itertools.count[int] = itertools.count(1)
        self._guard: threading.Lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._built: dict[str, _Built] = {}
        self.operations: Callable[[str], tuple[GitOperationView, ...]] = lambda _project: ()

    def project(self, project_id: str) -> GitProject:
        found = self._catalog().git_project(project_id)
        if found is None:
            raise DomainError(code=_PROJECT_MISSING, name="PROJECT_NOT_FOUND", retryable=False, extra=(project_id,))
        return found

    def state(self, project_id: str) -> GitStateResult:
        """The newest state that started after this call did."""
        project = self.project(project_id)
        ticket = next(self._tickets)
        with self._guard:
            lock = self._locks.setdefault(project_id, threading.Lock())
        with lock:
            cached = self._built.get(project_id)
            if cached is not None and cached.ticket > ticket:
                return cached.state
            started = next(self._tickets)
            state = self._build(project)
            self._built[project_id] = _Built(ticket=started, state=state)
            return state

    def worktree_row(self, project_id: str, worktree: str) -> tuple[Path, WorktreeRow]:
        """A path the caller named, only if git says it is this project's. Nothing else reaches git."""
        root = Path(self.project(project_id).root)
        for row in self._reader.worktrees(root):
            if _same_path(row.path, worktree):
                return root, row
        raise DomainError(code=_WORKTREE_UNKNOWN, name="GIT_WORKTREE_UNKNOWN", retryable=False)

    def tab_at(self, project_id: str, worktree: str) -> GitTab | None:
        for tab in self._catalog().git_tabs(project_id):
            if _same_path(tab.worktree, worktree):
                return tab
        return None

    def graph(self, params: GitGraphParams) -> GitGraphResult:
        project_id = str(params.project_id)
        root = Path(self.project(project_id).root)
        refs = self._reader.refs(root)
        worktrees = self._reader.worktrees(root)
        tabs = self._catalog().git_tabs(project_id)
        base = self._base_name(self.project(project_id), refs, root)
        tips = self._tips(params, refs, worktrees, tabs, base)
        version = _hash(*sorted(tips))
        skip = _skip(params.cursor, version)
        rows = self._reader.log(root, tips, skip, params.limit + 1)
        page = rows[: params.limit]
        decorate = _Decorations(refs, worktrees, tabs, base)
        nodes = tuple(decorate.node(row) for row in page)
        following = f"{version}:{skip + len(page)}" if len(rows) > params.limit else None
        return GitGraphResult(version=version, nodes=nodes, next_cursor=following)

    def diff_summary(self, project_id: str, source: DiffSource) -> DiffSummaryResult:
        repo, spec = self._spec(project_id, source)
        captured = self._reader.diff_summary(repo, spec)
        views = tuple(
            DiffFileView(
                path=item.path,
                old_path=item.old_path,
                change=item.change,
                insertions=item.insertions,
                deletions=item.deletions,
            )
            for item in captured.files
        )
        return DiffSummaryResult(
            base=captured.base,
            head=captured.head,
            files=views,
            insertions=sum(item.insertions for item in views),
            deletions=sum(item.deletions for item in views),
        )

    def diff_file(
        self, project_id: str, source: DiffSource, path: str, max_lines: int = _DEFAULT_LINES,
    ) -> DiffFileResult:
        if max_lines < 0:
            message = "max_lines cannot be negative"
            raise ValueError(message)
        repo, spec = self._spec(project_id, source)
        try:
            captured = self._reader.diff_file(repo, spec, path, max_lines)
        except DiffReadError as exc:
            raise_too_large(exc)
        return DiffFileResult(
            hunks=tuple(
                DiffHunk(
                    id=hunk.id,
                    header=hunk.header,
                    old_start=hunk.old_start,
                    old_lines=hunk.old_lines,
                    new_start=hunk.new_start,
                    new_lines=hunk.new_lines,
                    lines=tuple(
                        DiffLine(
                            kind=line.kind,
                            old_no=line.old_no,
                            new_no=line.new_no,
                            text=line.text,
                            word_spans=line.word_spans,
                        )
                        for line in hunk.lines
                    ),
                )
                for hunk in captured.hunks
            ),
            truncated=captured.truncated,
        )

    def source_spec(self, project_id: str, source: DiffSource) -> tuple[Path, DiffSpec]:
        return self._spec(project_id, source)

    def _spec(self, project_id: str, source: DiffSource) -> tuple[Path, DiffSpec]:
        root = Path(self.project(project_id).root)
        if source.kind == "commit":
            oid = require_oid(source.oid or "")
            parent = self._reader.parent_of(root, oid)
            return root, DiffSpec(left=parent or self._reader.empty_tree(root), right=oid)
        if source.kind == "between":
            return root, DiffSpec(left=require_oid(source.against or ""), right=require_oid(source.oid or ""))
        repo, row = self.worktree_row(project_id, source.worktree or "")
        repo = Path(row.path)
        head = row.head or self._reader.empty_tree(root)
        if source.kind == "index":
            return repo, DiffSpec(left=head, cached=True)
        if source.kind == "worktree":
            return repo, DiffSpec(left=None, untracked=True)
        target = require_oid(source.against) if source.against else self._base_oid(project_id, root)
        start = None if target is None or row.head is None else self._reader.merge_base(root, target, row.head)
        return repo, DiffSpec(left=start or head, untracked=True)

    def _base_oid(self, project_id: str, root: Path) -> str | None:
        refs = self._reader.refs(root)
        name = self._base_name(self.project(project_id), refs, root)
        return next((ref.oid for ref in refs if ref.name == f"{_HEADS}{name}"), None)

    def _base_name(self, project: GitProject, refs: tuple[RefRow, ...], root: Path) -> str | None:
        branches = frozenset(ref.name.removeprefix(_HEADS) for ref in refs if ref.name.startswith(_HEADS))
        chosen = choose_base(project.default_branch, branches, None)
        if chosen is not None:
            return chosen
        return choose_base(None, branches, self._reader.remote_head(root))

    def _tips(
        self,
        params: GitGraphParams,
        refs: tuple[RefRow, ...],
        worktrees: tuple[WorktreeRow, ...],
        tabs: tuple[GitTab, ...],
        base: str | None,
    ) -> tuple[str, ...]:
        if params.tab_id is None:
            kept = [ref.oid for ref in refs if ref.name.startswith((_HEADS, _CHECKPOINTS))]
            kept.extend(row.head for row in worktrees if row.head is not None)
            return tuple(dict.fromkeys(kept))
        tab = next((item for item in tabs if item.tab_id == str(params.tab_id)), None)
        if tab is None:
            raise DomainError(code=_TAB_MISSING, name="TAB_NOT_FOUND", retryable=False, extra=(str(params.tab_id),))
        prefix = f"{_CHECKPOINTS}{tab_segment(tab.tab_id)}/"
        wanted = {f"{_HEADS}{name}" for name in (base, tab.branch) if name is not None}
        kept = [ref.oid for ref in refs if ref.name in wanted or ref.name.startswith(prefix)]
        kept.extend(row.head for row in worktrees if row.head is not None and _same_path(row.path, tab.worktree))
        return tuple(dict.fromkeys(kept))

    def _build(self, project: GitProject) -> GitStateResult:
        root = Path(project.root)
        refs = self._reader.refs(root)
        worktrees = self._reader.worktrees(root)
        base = self._base_name(project, refs, root)
        heads = {ref.name.removeprefix(_HEADS): ref.oid for ref in refs if ref.name.startswith(_HEADS)}
        if base is None and worktrees and worktrees[0].branch in heads:
            base = worktrees[0].branch
        base_oid = None if base is None else heads.get(base)
        tabs = self._catalog().git_tabs(project.project_id)
        statuses = {row.path: self._reader.status(Path(row.path)) for row in worktrees}
        views = tuple(self._worktree(root, row, statuses[row.path], tabs, base, base_oid) for row in worktrees)
        placed = {row.branch: row.path for row in worktrees if row.branch is not None}
        branches = tuple(
            self._branch(root, name, oid, placed.get(name), base_oid) for name, oid in sorted(heads.items())
        )
        operations = self.operations(project.project_id)
        revision = _hash(
            *(f"{ref.name}={ref.oid}" for ref in refs),
            *(f"{path}={status.digest}:{status.in_progress}" for path, status in sorted(statuses.items())),
            *(f"{tab.worktree}={tab.status}" for tab in tabs),
            *(f"{item.id}={item.status}:{item.follow_up_pending}" for item in operations),
        )
        return GitStateResult(
            project_id=ProjectId(project.project_id),
            revision=revision,
            root=project.root,
            empty=not heads,
            base_branch=base,
            base_oid=base_oid,
            worktrees=views,
            branches=branches,
            operations=operations,
        )

    def _branch(self, root: Path, name: str, oid: str, worktree: str | None, base_oid: str | None) -> BranchView:
        ahead, behind = self._between(root, oid, base_oid)
        return BranchView(name=name, oid=oid, worktree=worktree, ahead=ahead, behind=behind)

    def _between(self, root: Path, head: str | None, base_oid: str | None) -> tuple[int, int]:
        if head is None or base_oid is None or head == base_oid:
            return 0, 0
        return self._distance.get(
            (str(root), head, base_oid),
            lambda: self._reader.ahead_behind(root, head, base_oid),
        )

    def probe(self, root: Path, target: str | None, source: str | None) -> MergeProbeView:
        if target is None or source is None:
            return MergeProbeView(status="unknown", source_oid=source, target_oid=target)
        if target == source:
            return MergeProbeView(status="not_applicable", source_oid=source, target_oid=target)
        return self._probes.get((str(root), target, source), lambda: self._probe(root, target, source))

    def _probe(self, root: Path, target: str, source: str) -> MergeProbeView:
        found: MergeProbe = self._reader.merge_probe(root, target, source)
        start = self._reader.merge_base(root, target, source)
        insertions, deletions = (0, 0) if start is None else self._reader.counts(root, start, source)
        return MergeProbeView(
            status="clean" if found.clean else "conflict",
            source_oid=source,
            target_oid=target,
            conflicts=found.conflicts,
            insertions=insertions,
            deletions=deletions,
        )

    def _worktree(  # noqa: PLR0913, PLR0917 -- one row needs all of these
        self,
        root: Path,
        row: WorktreeRow,
        status: StatusRow,
        tabs: tuple[GitTab, ...],
        base: str | None,
        base_oid: str | None,
    ) -> WorktreeGitState:
        tab = next((item for item in tabs if _same_path(item.worktree, row.path)), None)
        ahead, behind = self._between(root, row.head, base_oid)
        start = self._reader.start_of(root, Path(row.path))
        if start is None and row.head is not None and base_oid is not None:
            start = self._reader.merge_base(root, base_oid, row.head)
        on_base = base is not None and row.branch == base
        merge = (
            MergeProbeView(status="not_applicable", source_oid=row.head, target_oid=base_oid)
            if on_base
            else self.probe(root, base_oid, row.head)
        )
        dirty = status.staged + status.unstaged + status.untracked + status.conflicted > 0
        busy = tab is not None and blocks_writes(DomainTabStatus(tab.status.value))
        return WorktreeGitState(
            path=row.path,
            is_main=row.is_main,
            branch=row.branch,
            head_oid=row.head,
            detached=row.detached,
            tab_id=None if tab is None else TabId(tab.tab_id),
            tab_title=None if tab is None else tab.title,
            agent=None if tab is None else tab.agent,
            tab_status=None if tab is None else tab.status,
            busy=busy,
            plan_id=None if tab is None or tab.plan_id is None else PlanId(tab.plan_id),
            plan_node=None if tab is None or tab.plan_node is None else NodeId(tab.plan_node),
            start_oid=start,
            ahead=ahead,
            behind=behind,
            staged=status.staged,
            unstaged=status.unstaged,
            untracked=status.untracked,
            conflicted=status.conflicted,
            dirty=dirty,
            index_digest=status.index_digest,
            in_progress=_in_progress(status),
            merge=merge,
        )


@final
class _Decorations:
    """Ref names and tab links for graph nodes. Snapshots of one tab get their order in time here."""

    def __init__(
        self,
        refs: tuple[RefRow, ...],
        worktrees: tuple[WorktreeRow, ...],
        tabs: tuple[GitTab, ...],
        base: str | None,
    ) -> None:
        self._tab_by_branch = {tab.branch: tab for tab in tabs if tab.branch is not None}
        self._tab_by_segment = {tab_segment(tab.tab_id): tab for tab in tabs}
        self._base = base
        self._refs: dict[str, list[GitRefView]] = {}
        self._points: dict[str, tuple[str, int, str | None]] = {}
        by_tab: dict[str, list[tuple[int, str]]] = {}
        for ref in refs:
            named = checkpoint_ref_name(ref.name)
            if named is not None:
                by_tab.setdefault(named.segment, []).append((named.number, ref.oid))
            self._refs.setdefault(ref.oid, []).append(self._view(ref))
        for segment, points in by_tab.items():
            previous: str | None = None
            for number, oid in sorted(points):
                self._points[oid] = (segment, number, previous)
                previous = oid
        for row in worktrees:
            if row.detached and row.head is not None:
                self._refs.setdefault(row.head, []).append(GitRefView(name="HEAD", kind="head"))

    def _view(self, ref: RefRow) -> GitRefView:
        named = checkpoint_ref_name(ref.name)
        if named is not None:
            tab = self._tab_by_segment.get(named.segment)
            return GitRefView(
                name=f"#{named.number}",
                kind="checkpoint",
                tab_id=None if tab is None else TabId(tab.tab_id),
            )
        if ref.name.startswith(_TAGS):
            return GitRefView(name=ref.name.removeprefix(_TAGS), kind="tag")
        if ref.name.startswith(_REMOTES):
            return GitRefView(name=ref.name.removeprefix(_REMOTES), kind="remote")
        name = ref.name.removeprefix(_HEADS)
        tab = self._tab_by_branch.get(name)
        return GitRefView(
            name=name,
            kind="base" if name == self._base else "branch",
            tab_id=None if tab is None else TabId(tab.tab_id),
        )

    def node(self, row: CommitRow) -> GitNodeView:
        refs = tuple(self._refs.get(row.oid, ()))
        point = self._points.get(row.oid)
        tab = None
        if point is not None:
            tab = self._tab_by_segment.get(point[0])
        else:
            for ref in refs:
                tab = self._tab_by_branch.get(ref.name) or tab
        return GitNodeView(
            oid=row.oid,
            kind="checkpoint" if point is not None else "commit",
            parents=row.parents,
            refs=refs,
            subject=row.subject,
            author=row.author,
            at=row.at,
            tab_id=None if tab is None else TabId(tab.tab_id),
            plan_node=None if tab is None or tab.plan_node is None else NodeId(tab.plan_node),
            checkpoint_id=None if point is None else f"{point[0]}-{point[1]}",
            turn=None if point is None else point[1],
            previous_snapshot=None if point is None else point[2],
        )


def _skip(cursor: str | None, version: str) -> int:
    if cursor is None:
        return 0
    seen, _, count = cursor.partition(":")
    if seen != version:
        raise DomainError(code=_SNAPSHOT_STALE, name="GIT_SNAPSHOT_STALE", retryable=True)
    if not count.isdecimal():
        message = "unreadable graph cursor"
        raise ValueError(message)
    return int(count)


def _in_progress(status: StatusRow) -> InProgress | None:
    found = status.in_progress
    return next((name for name in _IN_PROGRESS if name == found), None)


def raise_too_large(exc: DiffReadError) -> NoReturn:
    if exc.too_large:
        raise DomainError(
            code=_TOO_LARGE,
            name="DIFF_TOO_LARGE",
            retryable=False,
            extra=(str(exc.files), str(exc.byte_count)),
        ) from exc
    raise exc
