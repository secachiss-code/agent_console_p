"""`git/state`, `git/graph`, and diffs by source. Git is a port; the tab book is read through `GitCatalog`.

Everything keyed by commit ids is cached: two commits have one merge result forever. Callers who arrive while a
state is being built wait and share the next finished one, so a dozen tabs ask git once.
"""

import hashlib
import itertools
import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn, Protocol, final

import msgspec

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
from aconsole.core.domain.git_operation import commit_trailers
from aconsole.core.domain.git_state import (
    blocks_writes,
    checkpoint_ref_name,
    choose_base,
    require_oid,
    tab_segment,
)
from aconsole.core.domain.tab_status import TabStatus as DomainTabStatus
from aconsole.core.domain.work_session import WorkSession, WorkSessionSnapshot

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
    work_session_id: str | None = None


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


_STATUS_WORKERS: Final = 4


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
        self.node_held_by: Callable[[str], str | None] = lambda _tab: None
        self.session_at: Callable[[str, str], WorkSession | None] = lambda _project, _path: None
        self.session_by_id: Callable[[str, str], WorkSession] | None = None
        self.snapshots: Callable[[str], tuple[WorkSessionSnapshot, ...]] = lambda _session: ()

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

    def session(self, project_id: str, session_id: str) -> WorkSession:
        if self.session_by_id is None:
            raise DomainError(code=3040, name="WORK_SESSION_NOT_FOUND", retryable=False)
        return self.session_by_id(project_id, session_id)

    def session_state(self, state: GitStateResult, session_id: str) -> GitStateResult:
        record = self.session(str(state.project_id), session_id)
        base_oid = next((row.oid for row in state.branches if row.name == record.base_branch), None)
        root = Path(state.root)
        worktrees = tuple(msgspec.structs.replace(
            row, ahead=self._between(root, row.head_oid, base_oid)[0],
            behind=self._between(root, row.head_oid, base_oid)[1],
            merge=(MergeProbeView(status="not_applicable", source_oid=row.head_oid, target_oid=base_oid)
                   if row.branch == record.base_branch else self.probe(root, base_oid, row.head_oid)),
        ) for row in state.worktrees if row.work_session_id == record.id)
        branches = tuple(row for row in state.branches if row.name in {record.branch, record.base_branch})
        return msgspec.structs.replace(
            state, base_branch=record.base_branch,
            base_oid=base_oid,
            worktrees=worktrees, branches=branches,
            operations=tuple(row for row in state.operations if row.work_session_id == record.id),
        )

    def base_name(self, project_id: str) -> str | None:
        """The branch this project's work lands on, if it can be told."""
        project = self.project(project_id)
        root = Path(project.root)
        return self._base_name(project, self._reader.refs(root), root)

    def trailers_at(
        self, project_id: str, worktree: str, actor: str | None = None,
    ) -> tuple[tuple[str, str], ...]:
        """What a commit made in this worktree should say about its work."""
        members = self.tabs_at(project_id, worktree)
        tab = (
            next((row for row in members if row.tab_id == actor), None) if actor else self.tab_at(project_id, worktree)
        )
        if actor is not None and tab is None:
            raise DomainError(code=_TAB_MISSING, name="TAB_NOT_FOUND", retryable=False)
        record = self.session_at(project_id, worktree)
        session = () if record is None else (("AConsole-Session", record.id),)
        if tab is None:
            return session
        node = tab.plan_node or self.node_held_by(tab.tab_id)
        return (*session, *commit_trailers(node=node, agent=tab.agent.value, tab=tab.tab_id))

    def tabs(self, project_id: str) -> tuple[GitTab, ...]:
        return self._catalog().git_tabs(project_id)

    def merge_trailers_at(self, project_id: str, worktree: str) -> tuple[tuple[str, str], ...]:
        session = self.session_at(project_id, worktree)
        if session is None:
            return self.trailers_at(project_id, worktree)
        points = self.snapshots(session.id)
        members = tuple(tab for tab in self.tabs(project_id) if tab.work_session_id == session.id)
        actors = sorted(
            {point.actor_tab_id for point in points if point.actor_tab_id} | {tab.tab_id for tab in members}
        )
        nodes = sorted({point.plan_node for point in points if point.plan_node} | {
            node for tab in members if (node := tab.plan_node or self.node_held_by(tab.tab_id))
        })
        if len(actors) <= 1 and self.tab_at(project_id, worktree) is not None:
            extra = (("AConsole-Nodes", " ".join(nodes)),) if nodes else ()
            return (*self.trailers_at(project_id, worktree), *extra)
        agents = sorted({point.agent for point in points if point.agent} | {tab.agent.value for tab in members})
        metadata = (("AConsole-Session", session.id), ("AConsole-Participants", " ".join(actors)),
                    ("AConsole-Agents", " ".join(agents)), ("AConsole-Nodes", " ".join(nodes)))
        return tuple((key, value) for key, value in metadata if value)

    def tab_at(self, project_id: str, worktree: str) -> GitTab | None:
        members = self.tabs_at(project_id, worktree)
        return members[0] if len(members) == 1 else None

    def tabs_at(self, project_id: str, worktree: str) -> tuple[GitTab, ...]:
        return tuple(tab for tab in self.tabs(project_id) if _same_path(tab.worktree, worktree))

    def require_active(self, project_id: str, worktree: str) -> None:
        record = self.session_at(project_id, worktree)
        if record is not None and record.status != "active":
            raise DomainError(code=3041, name="WORK_SESSION_ARCHIVED", retryable=False)

    def graph(self, params: GitGraphParams) -> GitGraphResult:
        project_id = str(params.project_id)
        root = Path(self.project(project_id).root)
        refs = self._reader.refs(root)
        worktrees = self._reader.worktrees(root)
        tabs = self._catalog().git_tabs(project_id)
        base = self._base_name(self.project(project_id), refs, root)
        session = None if params.work_session_id is None else self.session(project_id, params.work_session_id)
        if session is not None:
            base = session.base_branch
        tips = self._tips(params, refs, worktrees, tabs, base)
        version = _hash(*sorted(tips))
        skip = _skip(params.cursor, version)
        rows = self._reader.log(root, tips, skip, params.limit + 1, node=params.plan_node)
        page = rows[: params.limit]
        snapshot_sessions = {ref.name.split("/")[3] for ref in refs if ref.name.startswith("refs/aconsole/sessions/")}
        snapshots = (
            tuple(point for ident in snapshot_sessions for point in self.snapshots(ident))
            if session is None else self.snapshots(session.id)
        )
        decorate = _Decorations(refs, worktrees, tabs, base, snapshots)
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
        if params.work_session_id is not None:
            session = self.session(str(params.project_id), params.work_session_id)
            wanted = {f"{_HEADS}{name}" for name in (session.branch, session.base_branch) if name}
            snapshots = self.snapshots(session.id)
            kept = [ref.oid for ref in refs if ref.name in wanted]
            kept.extend(
                row.oid for row in snapshots if row.oid and (params.tab_id is None or row.actor_tab_id == params.tab_id)
            )
            kept.extend(row.head for row in worktrees if row.head and any(
                _same_path(tab.worktree, row.path) and tab.work_session_id == session.id
                and (params.tab_id is None or tab.tab_id == params.tab_id) for tab in tabs
            ))
            return tuple(dict.fromkeys(kept))
        if params.tab_id is None:
            kept = [ref.oid for ref in refs if ref.name.startswith((_HEADS, _CHECKPOINTS))]
            kept.extend(
                ref.oid for ref in refs
                if "/checkpoints/" in ref.name and ref.name.startswith("refs/aconsole/sessions/")
            )
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

    def _statuses(self, worktrees: tuple[WorktreeRow, ...]) -> dict[str, StatusRow]:
        """Side by side: this is most of a look's cost, and each one waits on its own disk scan."""
        with ThreadPoolExecutor(max_workers=_STATUS_WORKERS) as pool:
            found = pool.map(self._reader.status, [Path(row.path) for row in worktrees])
            return {row.path: status for row, status in zip(worktrees, found, strict=True)}

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
        statuses = self._statuses(worktrees)
        views = tuple(
            self._worktree(root, row, statuses[row.path], tabs, base, base_oid, project.project_id) for row in worktrees
        )
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
        project_id: str,
    ) -> WorktreeGitState:
        members = tuple(item for item in tabs if _same_path(item.worktree, row.path))
        tab = members[0] if len(members) == 1 else None
        session = self.session_at(project_id, row.path)
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
        busy = any(blocks_writes(DomainTabStatus(item.status.value)) for item in members)
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
            work_session_id=None if session is None else session.id,
            tab_ids=tuple(TabId(item.tab_id) for item in members),
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
        snapshots: tuple[WorkSessionSnapshot, ...] = (),
    ) -> None:
        self._tab_by_branch = {tab.branch: tab for tab in tabs if tab.branch is not None}
        self._tab_by_segment = {tab_segment(tab.tab_id): tab for tab in tabs}
        self._base = base
        self._snapshots = {row.oid: row for row in snapshots if row.oid}
        self._refs: dict[str, list[GitRefView]] = {}
        self._points: dict[str, tuple[str, int, str | None]] = {}
        by_tab: dict[str, list[tuple[int, str]]] = {}
        for ref in refs:
            if ref.name.startswith("refs/aconsole/sessions/") and "/checkpoints/" not in ref.name:
                continue
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
        if ref.name.startswith("refs/aconsole/sessions/") and "/checkpoints/" in ref.name:
            return GitRefView(name=f"#{ref.name.rsplit('/', 1)[-1]}", kind="checkpoint")
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
        snapshot = self._snapshots.get(row.oid)
        tab = None
        if point is not None:
            tab = self._tab_by_segment.get(point[0])
        else:
            for ref in refs:
                tab = self._tab_by_branch.get(ref.name) or tab
        return GitNodeView(
            oid=row.oid,
            kind="checkpoint" if point is not None or snapshot is not None else "commit",
            parents=row.parents,
            refs=refs,
            subject=row.subject,
            author=row.author,
            at=row.at,
            tab_id=(
                TabId(snapshot.actor_tab_id) if snapshot is not None and snapshot.actor_tab_id is not None
                else None if tab is None else TabId(tab.tab_id)
            ),
            plan_node=NodeId(snapshot.plan_node) if snapshot is not None and snapshot.plan_node else _node_of(row, tab),
            agent=snapshot.agent if snapshot is not None else row.agent,
            checkpoint_id=(
                f"{snapshot.work_session_id}-{snapshot.sequence}" if snapshot is not None
                else None if point is None else f"{point[0]}-{point[1]}"
            ),
            turn=snapshot.sequence if snapshot is not None else None if point is None else point[1],
            previous_snapshot=snapshot.previous_oid if snapshot is not None else None if point is None else point[2],
        )


def _node_of(row: CommitRow, tab: GitTab | None) -> NodeId | None:
    """The commit's own word first: it outlives the tab."""
    named = row.node or (None if tab is None else tab.plan_node)
    return None if named is None else NodeId(named)


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
